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


_SEARCH_SQL = """\
SELECT d.title, d.url, d.canonical_url, s.name AS source,
       d.published_at, d.author, d.content,
       d.flag_reason, d.flag_detail, d.flagged_at, d.flagged_by,
       d.read_at, d.read_by,
       imp.score AS importance_score,
       imp.rationale AS importance_rationale,
       imp.reporter AS importance_reporter,
       imp.created_at AS importance_updated_at
  FROM documents d
  LEFT JOIN sources s ON s.id = d.source_id
  LEFT JOIN LATERAL (
      SELECT i.score, i.rationale, i.reporter, i.created_at
        FROM document_importance i
       WHERE i.document_id = d.id
       ORDER BY i.created_at DESC, i.id DESC
       LIMIT 1
  ) imp ON true
 WHERE (d.title ILIKE %s OR d.content ILIKE %s)
 ORDER BY d.published_at DESC
 LIMIT %s\
"""

_SEARCH_SQL_EXCLUDE_READ = """\
SELECT d.title, d.url, d.canonical_url, s.name AS source,
       d.published_at, d.author, d.content,
       d.flag_reason, d.flag_detail, d.flagged_at, d.flagged_by,
       d.read_at, d.read_by,
       imp.score AS importance_score,
       imp.rationale AS importance_rationale,
       imp.reporter AS importance_reporter,
       imp.created_at AS importance_updated_at
  FROM documents d
  LEFT JOIN sources s ON s.id = d.source_id
  LEFT JOIN LATERAL (
      SELECT i.score, i.rationale, i.reporter, i.created_at
        FROM document_importance i
       WHERE i.document_id = d.id
       ORDER BY i.created_at DESC, i.id DESC
       LIMIT 1
  ) imp ON true
 WHERE (d.title ILIKE %s OR d.content ILIKE %s)
   AND d.read_at IS NULL
 ORDER BY d.published_at DESC
 LIMIT %s\
"""

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
)


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
    else:
        # Tuple rows predate the read annotation (11 cols); newer rows carry
        # read_at/read_by (13 cols); current rows append the 4 importance
        # columns (17 cols). All shapes are accepted.
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
        "importance_score": importance_score,
        "importance_rationale": importance_rationale,
        "importance_reporter": importance_reporter,
        "importance_updated_at": importance_updated_at,
    }


def search_articles(
    keyword: str, limit: int = 20, conn: Any | None = None, *, exclude_read: bool = False
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

    Returns:
        List of dicts with keys ``title, url, canonical_url, source,
        published_at, author, snippet`` plus the Extraction Flag annotation
        (``flag_reason, flag_detail, flagged_at, flagged_by``), the Read
        State annotation (``read`` bool derived from ``read_at IS NOT NULL``,
        plus ``read_at, read_by`` — ``None`` when unread), and the latest
        Importance annotation (``importance_score, importance_rationale,
        importance_reporter, importance_updated_at`` — ``None`` when
        unannotated). ``published_at``
        is an isoformat tz-aware string; ``author`` may be ``None``;
        ``snippet`` is a content excerpt around the match. Empty/blank
        keyword returns ``[]``.

    Raises:
        ValueError: non-bool ``exclude_read``.
    """
    if not isinstance(keyword, str) or not keyword.strip():
        return []
    if not isinstance(limit, int) or limit <= 0:
        return []
    if not isinstance(exclude_read, bool):
        raise ValueError(f"exclude_read must be a bool, got {exclude_read!r}")

    term = keyword.strip()
    pattern = f"%{term}%"
    params = (pattern, pattern, limit)
    sql = _SEARCH_SQL_EXCLUDE_READ if exclude_read else _SEARCH_SQL

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
