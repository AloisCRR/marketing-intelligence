"""Keyword search over ingested articles with source provenance.

Domain contract (stable): ``search_articles(keyword, limit)`` returns a list
of dicts with keys ``title, url, canonical_url, source, published_at,
author, snippet`` — no raw SQL exposure beyond this function.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

try:  # foundation seam (preferred)
    from marketing_intelligence.db import get_connection  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - defensive fallback when absent

    def get_connection() -> Any:  # type: ignore[misc]
        raise RuntimeError(
            "No database connection available: marketing_intelligence.db.get_connection "
            "is missing and no fallback is configured."
        )


from marketing_intelligence import importance as _importance

_SEARCH_SELECT = f"""\
SELECT d.title, d.url, d.canonical_url, s.name AS source,
       d.published_at, d.author, d.content,
       d.flag_reason, d.flag_detail, d.flagged_at, d.flagged_by,
       d.read_at, d.read_by,
       imp.score AS importance_score,
       imp.rationale AS importance_rationale,
       imp.reporter AS importance_reporter,
       imp.created_at AS importance_updated_at,
       tps.topics AS topics
  FROM documents d
  LEFT JOIN sources s ON s.id = d.source_id
  LEFT JOIN LATERAL (
      SELECT {_importance.EFFECTIVE_SCORE_SQL} AS score,
             i.rationale, i.reporter, i.created_at
        FROM document_importance i
       WHERE i.document_id = d.id
       ORDER BY i.created_at DESC, i.id DESC
       LIMIT 1
  ) imp ON true
  LEFT JOIN LATERAL (
      SELECT array_agg(t.topic_slug ORDER BY t.topic_slug) AS topics
        FROM (
              SELECT DISTINCT ON (dt.topic_slug) dt.topic_slug, dt.assigned
                FROM document_topics dt
               WHERE dt.document_id = d.id
               ORDER BY dt.topic_slug, dt.created_at DESC, dt.id DESC
             ) t
       WHERE t.assigned
  ) tps ON true"""

#: Recency-first ordering — the default: annotations never re-order results.
_ORDER_RECENCY = "d.published_at DESC"
#: Importance-first ordering, used only when an importance floor is supplied.
#: ``imp.score`` is the capped effective score (see
#: :data:`marketing_intelligence.importance.EFFECTIVE_SCORE_SQL`), so a score
#: written before a later flag cannot outrank clean evidence. ``NULLS LAST`` is
#: defensive: a NULL score never satisfies a floor anyway, so an unannotated
#: Document can never be sorted above annotated evidence.
_ORDER_IMPORTANCE = "imp.score DESC NULLS LAST, d.published_at DESC"


def _search_sql(
    *, exclude_read: bool, min_importance: float | None, topics: list[str] | None
) -> str:
    """Compose the search SELECT for the given filters.

    Parameter order is keyword, keyword, importance, topics, limit — matching
    :func:`search_articles`. Unannotated Documents fail the annotation
    predicates (NULL is never true for ``>=`` or array overlap); that is the
    documented filter semantics, not an accident.
    """
    filters = ["(d.title ILIKE %s OR d.content ILIKE %s)"]
    if exclude_read:
        filters.append("d.read_at IS NULL")
    if min_importance is not None:
        filters.append("imp.score >= %s")
    if topics:
        filters.append("tps.topics && %s::text[]")
    order = _ORDER_IMPORTANCE if min_importance is not None else _ORDER_RECENCY
    where = "\n   AND ".join(filters)
    return f"{_SEARCH_SELECT}\n WHERE {where}\n ORDER BY {order}\n LIMIT %s"


_SNIPPET_RADIUS = 120

_RESULT_KEYS = (
    "title",
    "url",
    "canonical_url",
    "source",
    "published_at",
    "author",
    "snippet",
    "flag_reason",
    "flag_detail",
    "flagged_at",
    "flagged_by",
    "read",
    "read_at",
    "read_by",
    "importance_score",
    "importance_rationale",
    "importance_reporter",
    "importance_updated_at",
    "topics",
)


def _topics_list(value: Any) -> list[str]:
    """Coerce a DB topic array to a list of slugs (``[]`` when unannotated)."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    return [str(value)]


def _to_iso_tz_aware(value: Any) -> Any:
    """Normalise a published_at value to an isoformat tz-aware string."""
    if value is None:
        return None
    if isinstance(value, _dt.datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=_dt.UTC)
        return value.isoformat()
    if isinstance(value, str):
        try:
            parsed = _dt.datetime.fromisoformat(value)
        except ValueError:
            return value
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=_dt.UTC)
            return parsed.isoformat()
        return value
    return value


def _snippet(content: Any, title: Any, keyword: str) -> str:
    text = content if isinstance(content, str) and content else (title or "")
    if not isinstance(text, str) or not text:
        return ""
    idx = text.lower().find(keyword.lower())
    if idx == -1:
        excerpt = text[: 2 * _SNIPPET_RADIUS]
        return excerpt + ("…" if len(text) > len(excerpt) else "")
    start = max(0, idx - _SNIPPET_RADIUS)
    end = min(len(text), idx + len(keyword) + _SNIPPET_RADIUS)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return prefix + text[start:end].strip() + suffix


def _row_to_dict(row: Any) -> dict[str, Any]:
    if isinstance(row, dict):
        title = row.get("title")
        url = row.get("url")
        canonical_url = row.get("canonical_url")
        source = row.get("source")
        published_at = row.get("published_at")
        author = row.get("author")
        content = row.get("content")
        flag_reason = row.get("flag_reason")
        flag_detail = row.get("flag_detail")
        flagged_at = row.get("flagged_at")
        flagged_by = row.get("flagged_by")
        read_at = row.get("read_at")
        read_by = row.get("read_by")
        importance_score = row.get("importance_score")
        importance_rationale = row.get("importance_rationale")
        importance_reporter = row.get("importance_reporter")
        importance_updated_at = row.get("importance_updated_at")
        topics = row.get("topics")
    else:
        # Tuple rows predate the read annotation (11 cols); newer rows carry
        # read_at/read_by (13 cols); current rows append the 4 importance
        # columns (17 cols) and, after them, the topic array (18 cols). All
        # shapes are accepted.
        items = tuple(row)
        (
            title,
            url,
            canonical_url,
            source,
            published_at,
            author,
            content,
            flag_reason,
            flag_detail,
            flagged_at,
            flagged_by,
        ) = items[:11]
        read_at = items[11] if len(items) > 11 else None
        read_by = items[12] if len(items) > 12 else None
        importance_score = items[13] if len(items) > 13 else None
        importance_rationale = items[14] if len(items) > 14 else None
        importance_reporter = items[15] if len(items) > 15 else None
        importance_updated_at = items[16] if len(items) > 16 else None
        topics = items[17] if len(items) > 17 else None
    return {
        "title": title,
        "url": url,
        "canonical_url": canonical_url,
        "source": source,
        "published_at": published_at,
        "author": author,
        "_content": content,
        "flag_reason": flag_reason,
        "flag_detail": flag_detail,
        "flagged_at": flagged_at,
        "flagged_by": flagged_by,
        "read": read_at is not None,
        "read_at": read_at,
        "read_by": read_by,
        "importance_score": _importance.effective_score(importance_score, flag_reason, content),
        "importance_rationale": importance_rationale,
        "importance_reporter": importance_reporter,
        "importance_updated_at": importance_updated_at,
        "topics": _topics_list(topics),
    }


def _validate_min_importance(value: float | None) -> float | None:
    """Validate the optional importance floor: None, or a number in [0, 1]."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"min_importance must be a number in [0, 1] or None, got {value!r}")
    number = float(value)
    if not (0.0 <= number <= 1.0):
        raise ValueError(f"min_importance must be a number in [0, 1] or None, got {value!r}")
    return number


def _validate_topics(topics: list[str] | None) -> list[str] | None:
    """Validate the optional Topic filter: None, or a list of non-blank slugs.

    Values are treated as opaque canonical slugs here; the vocabulary lane
    (``service``) owns canonicalization and unknown-tag rejection.
    """
    if topics is None:
        return None
    if isinstance(topics, (str, bytes)) or not isinstance(topics, (list, tuple)):
        raise ValueError(f"topics must be a list of topic slugs or None, got {topics!r}")
    cleaned: list[str] = []
    for item in topics:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"topics must be non-empty strings, got {item!r}")
        cleaned.append(item.strip())
    return cleaned


def search_articles(
    keyword: str,
    limit: int = 20,
    conn: Any | None = None,
    *,
    exclude_read: bool = False,
    min_importance: float | None = None,
    topics: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Search ingested articles by keyword, newest first.

    Args:
        keyword: matched (case-insensitively) against title and content.
        limit: maximum number of results.
        conn: optional injected DB-API connection (fake-friendly). When None
            a connection is opened via `get_connection` and closed afterwards;
            an injected connection is never committed or closed here.
        exclude_read: when True, marked (read) articles are filtered out via
            ``AND d.read_at IS NULL``. Default False annotates without
            filtering. Must be a bool.
        min_importance: optional Importance floor in [0, 1]. When set, only
            Documents whose latest score is ``>=`` that value are returned,
            ordered by importance (descending, ties by recency). Unannotated
            Documents have a NULL score, so a floor **excludes** them; with no
            floor they are included and never sorted by score (the order stays
            purely recency-first), so they are never silently dropped or
            silently top-ranked.
        topics: optional canonical Topic slugs; a Document matches when it
            carries at least one of them (array overlap). Unknown-tag
            rejection/canonicalization happens in the vocabulary lane
            (``service``); an unannotated Document (no topics) is excluded
            only when this filter is non-empty, and an empty list adds no
            constraint.

    Returns:
        List of dicts with keys ``title, url, canonical_url, source,
        published_at, author, snippet`` plus the Extraction Flag annotation
        (``flag_reason, flag_detail, flagged_at, flagged_by``), the Read
        State annotation (``read`` bool derived from ``read_at IS NOT NULL``,
        plus ``read_at, read_by`` — ``None`` when unread), the latest
        Importance annotation (``importance_score, importance_rationale,
        importance_reporter, importance_updated_at`` — ``None`` when
        unannotated), and ``topics`` — the Document's effective canonical
        Topic slugs (sorted, ``[]`` when unannotated). ``published_at``
        is an isoformat tz-aware string; ``author`` may be ``None``;
        ``snippet`` is a content excerpt around the match. Empty/blank
        keyword returns ``[]``.

    Raises:
        ValueError: non-bool ``exclude_read``, non-numeric or out-of-range
            ``min_importance``, or non-list/blank ``topics``.
    """
    if not isinstance(keyword, str) or not keyword.strip():
        return []
    if not isinstance(limit, int) or limit <= 0:
        return []
    if not isinstance(exclude_read, bool):
        raise ValueError(f"exclude_read must be a bool, got {exclude_read!r}")
    floor = _validate_min_importance(min_importance)
    topic_filter = _validate_topics(topics)

    term = keyword.strip()
    pattern = f"%{term}%"
    params: list[Any] = [pattern, pattern]
    if floor is not None:
        params.append(floor)
    if topic_filter:
        params.append(list(topic_filter))
    params.append(limit)
    sql = _search_sql(exclude_read=exclude_read, min_importance=floor, topics=topic_filter)

    owns_connection = conn is None
    if conn is None:
        conn = get_connection()
    assert conn is not None
    try:
        cursor = conn.cursor()
        try:
            cursor.execute(sql, params)
            rows = cursor.fetchall()
        finally:
            close = getattr(cursor, "close", None)
            if callable(close):
                close()
    finally:
        if owns_connection:
            close_conn = getattr(conn, "close", None)
            if callable(close_conn):
                close_conn()

    results: list[dict[str, Any]] = []
    for row in rows or []:
        item = _row_to_dict(row)
        content = item.pop("_content")
        item["published_at"] = _to_iso_tz_aware(item["published_at"])
        item["flagged_at"] = _to_iso_tz_aware(item["flagged_at"])
        item["read_at"] = _to_iso_tz_aware(item["read_at"])
        item["importance_updated_at"] = _to_iso_tz_aware(item["importance_updated_at"])
        item["snippet"] = _snippet(content, item["title"], term)
        results.append({k: item[k] for k in _RESULT_KEYS})
    return results
