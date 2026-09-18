"""Period intelligence context (Ticket 04) — grounded evidence, no trend claims.

``get_period_context`` prepares the evidence bundle for period synthesis over
an explicit caller-supplied period interpreted in ``America/Panama``:

- naive ``date``/``datetime`` inputs are assumed America/Panama (never
  server-local time); aware datetimes are converted;
- a plain ``date`` covers its whole calendar day: ``from`` is inclusive at
  00:00, ``to`` is inclusive of the full day (implemented as an exclusive
  upper bound at the next midnight);
- ``datetime`` bounds are exact instants (``from`` inclusive, ``to`` exclusive).

V1 deliberately makes NO velocity, emerging-topic, entity, or convergence
claims: those signals need accumulated history and enrichment that do not
exist yet. The bundle therefore carries only what is real — a recency-ordered
``recent_articles`` list, each item stamped with its 1-based ``rank`` in that
order plus its provenance — and no placeholder analytics fields.

Each item also carries the per-reader `readers` list (ADR-0015 ``document_reads``):
``readers`` is a list of ``{reader, read_at}`` sorted by reader, ``[]`` when
nobody has marked the Document, alongside the unchanged anyone-read summary
(``read``/``read_at``/``read_by`` from the ``documents`` latest-mark cache).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from marketing_intelligence import importance as _importance
from marketing_intelligence.db import get_connection
from marketing_intelligence.sources import catalog_names

PANAMA_TZ = ZoneInfo("America/Panama")
PANAMA_NAME = "America/Panama"

DEFAULT_LIMIT = 50

# `has_image_text` is presence only: True when the Document has at least one
# frame whose vision-read text is real text. Frames the vision lane found no
# text in are stored with the `image_text.NO_TEXT` sentinel (ADR-0014), so a
# text-free carousel reports False rather than "the lane ran". The sentinel is
# inlined as a literal below — this lane must not import the vision module
# (which pulls httpx) — and `tests/test_image_text_retrieval.py` pins it to
# `image_text.NO_TEXT`, so a changed sentinel cannot leave this predicate
# matching nothing.
_PERIOD_SELECT = """\
SELECT d.title, d.url, d.canonical_url, s.name AS source,
       d.published_at, d.author,
       d.flag_reason, d.flag_detail, d.flagged_at, d.flagged_by,
       d.read_at, d.read_by,
       imp.score AS importance_score,
       imp.rationale AS importance_rationale,
       imp.reporter AS importance_reporter,
       imp.created_at AS importance_updated_at,
       tps.topics AS topics,
       d.content,
       EXISTS (
           SELECT 1
             FROM document_image_texts it
            WHERE it.document_id = d.id
              AND it.image_text <> 'NO_TEXT'
       ) AS has_image_text,
       rdrs.readers AS readers, rdrs.read_ats AS read_ats"""

_PERIOD_FROM = f"""\
  FROM documents d
  JOIN sources s ON s.id = d.source_id
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
  ) tps ON true
  LEFT JOIN LATERAL (
      SELECT array_agg(r.reader ORDER BY r.reader) AS readers,
             array_agg(r.read_at ORDER BY r.reader) AS read_ats
        FROM document_reads r
       WHERE r.document_id = d.id
  ) rdrs ON true
 WHERE d.published_at >= %s
   AND d.published_at < %s
   AND s.name = ANY(%s)"""

#: Recency-first ordering — the default: annotations never re-order a bundle.
_ORDER_RECENCY = "d.published_at DESC"
#: Importance-first ordering, used only when an importance floor is supplied.
#: ``imp.score`` is the capped effective score (see
#: :data:`marketing_intelligence.importance.EFFECTIVE_SCORE_SQL`), so a score
#: written before a later flag cannot outrank clean evidence. ``NULLS LAST`` is
#: defensive: a NULL score never satisfies a floor anyway, so an unannotated
#: Document can never be sorted above annotated evidence.
_ORDER_IMPORTANCE = "imp.score DESC NULLS LAST, d.published_at DESC"


def _annotation_filters(
    *, exclude_read: bool, min_importance: float | None, topics: list[str] | None
) -> tuple[str, list[Any]]:
    """Compose the optional WHERE additions and their parameters, in order.

    The fixed order — read, importance, topics — matches the parameter order
    used by :func:`get_period_context`. Unannotated Documents fail the
    annotation predicates, which is exactly the documented filter semantics:
    with no importance row ``imp.score >= %s`` is never true (NULL), and with
    no topic rows ``tps.topics && %s`` is never true.
    """
    clauses: list[str] = []
    params: list[Any] = []
    if exclude_read:
        clauses.append("   AND d.read_at IS NULL")
    if min_importance is not None:
        clauses.append("   AND imp.score >= %s")
        params.append(min_importance)
    if topics:
        clauses.append("   AND tps.topics && %s::text[]")
        params.append(list(topics))
    return (("\n" + "\n".join(clauses)) if clauses else ""), params


def _bundle_sql(*, where: str, importance_first: bool, per_source_limit: int | None) -> str:
    """Bundle SQL for the composed filter clause.

    Without ``per_source_limit`` this is one flat, bounded SELECT ordered by
    recency (or by importance when a floor is set). With a cap, a per-source
    window rank is computed over the *filtered* rows using the same ordering,
    only ranks within the cap survive, and the outer query takes the overall
    ``limit`` rows — so slots a capped source cannot fill go to the next
    sources under that ordering.
    """
    order = _ORDER_IMPORTANCE if importance_first else _ORDER_RECENCY
    if per_source_limit is None:
        return f"{_PERIOD_SELECT}\n{_PERIOD_FROM}{where}\n ORDER BY {order}\n LIMIT %s"
    outer_order = (
        "ranked.importance_score DESC NULLS LAST, ranked.published_at DESC"
        if importance_first
        else "ranked.published_at DESC"
    )
    return (
        "SELECT title, url, canonical_url, source, published_at, author,\n"
        "       flag_reason, flag_detail, flagged_at, flagged_by, read_at, read_by,\n"
        "       importance_score, importance_rationale, importance_reporter,\n"
        "       importance_updated_at, topics, content, has_image_text,\n"
        "       readers, read_ats\n"
        "  FROM (\n"
        f"{_PERIOD_SELECT},\n"
        "       ROW_NUMBER() OVER (\n"
        f"           PARTITION BY s.name ORDER BY {order}\n"
        "       ) AS source_rank\n"
        f"{_PERIOD_FROM}{where}"
        "\n       ) ranked\n"
        " WHERE ranked.source_rank <= %s\n"
        f" ORDER BY {outer_order}\n"
        " LIMIT %s"
    )


def _coerce_bound(value: date | datetime, *, is_end: bool) -> datetime:
    """Normalise a period bound to a tz-aware Panama datetime."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=PANAMA_TZ)
        return value.astimezone(PANAMA_TZ)
    if isinstance(value, date):
        start = datetime(value.year, value.month, value.day, tzinfo=PANAMA_TZ)
        return start + timedelta(days=1) if is_end else start
    raise TypeError(f"period bounds must be date or datetime, got {type(value).__name__}")


def _iso_tz_aware(value: Any) -> Any:
    """Normalise a published_at value to an isoformat tz-aware string."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.isoformat()
    return value


def _readers_list(names: Any, timestamps: Any) -> list[dict[str, Any]]:
    """Zip the ``document_reads`` reader names with their mark times.

    Returns ``[]`` when the Document has no read-state rows — unread, or a
    legacy reader-less mark that only set the ``documents.read_at`` cache (those
    have no reader identity to attribute). The two arrays come from one
    ``array_agg`` pair over the same rows, so they are index-aligned; the
    result is sorted by reader (the SQL ordering, re-applied defensively) with
    each ``read_at`` an isoformat tz-aware string.
    """
    if names is None or timestamps is None:
        return []
    if isinstance(names, (str, bytes)) or isinstance(timestamps, (str, bytes)):
        return []
    try:
        pairs = list(zip(names, timestamps, strict=False))
    except TypeError:
        return []
    readers = [
        {"reader": str(reader), "read_at": _iso_tz_aware(read_at)} for reader, read_at in pairs
    ]
    readers.sort(key=lambda item: item["reader"])
    return readers


def _topics_list(value: Any) -> list[str]:
    """Coerce a DB topic array to a list of slugs (``[]`` when unannotated)."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    return [str(value)]


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


def get_period_context(
    from_date: date | datetime,
    to_date: date | datetime,
    *,
    sources: list[str] | None = None,
    limit: int = DEFAULT_LIMIT,
    conn: Any | None = None,
    exclude_read: bool = False,
    per_source_limit: int | None = None,
    min_importance: float | None = None,
    topics: list[str] | None = None,
) -> dict[str, Any]:
    """Return the period evidence bundle for ``[from_date, to_date]``.

    (``from`` is a Python keyword, hence ``from_date``/``to_date``; positional
    ``get_period_context(a, b)`` reads as (from, to). Plain dates are
    day-inclusive; datetimes are half-open ``[from, to)``. Explicit
    ``sources`` must have seed rows in the ``sources`` table (the V1 twenty
    are seeded by migrations 001 + 006) or they match no articles.
    ``exclude_read`` filters out marked (read) articles via
    ``AND d.read_at IS NULL``; default False annotates without filtering.
    ``per_source_limit`` (default None) additionally caps how many items any
    one source may contribute: rows are ranked within each source (recency,
    or importance-first when ``min_importance`` is set) and only the first
    ``per_source_limit`` survive, then the overall ``limit`` rows are taken.
    Slots a capped source cannot fill go to other sources, so the bundle
    stays at most ``limit`` items while no source floods it.

    ``min_importance`` (default None) keeps only Documents whose latest
    Importance score is ``>=`` the floor; ``topics`` (default None) keeps only
    Documents carrying at least one of the given canonical slugs (array
    overlap). All filters compose with each other and with the ``sources``
    allowlist in one call.

    Unannotated behavior (deliberate, not incidental): a Document with no
    importance row has a NULL score, so it is **excluded only when a floor is
    set** (NULL never satisfies ``>=``) and is never top-ranked — with no
    floor the order stays purely recency-first, with a floor the order is
    importance-first with ``NULLS LAST``. Likewise a Document with no topics
    is excluded only when a ``topics`` filter is set. Pass ``topics=[]`` to
    add no topic constraint.

    Returns ``period {from, to, timezone}`` plus ``recent_articles``. With no
    ``min_importance`` the list is purely recency-ordered (never ranked by
    importance); with a floor it is importance-ordered (descending, ties by
    recency). Each item carries ``rank`` (its 1-based position in that final
    order — an ordering signal, not a score) alongside
    ``title, url, canonical_url, source, published_at, author`` provenance,
    the Extraction Flag annotation (``flag_reason, flag_detail, flagged_at,
    flagged_by`` — ``None`` when unflagged), the Read State annotation
    (``read`` bool derived from ``read_at IS NOT NULL``, plus ``read_at,
    read_by`` — ``None`` when unread), the latest Importance annotation
    (``importance_score, importance_rationale, importance_reporter,
    importance_updated_at`` — ``None`` when unannotated), ``topics`` —
    the Document's effective canonical Topic slugs (sorted, ``[]`` when
    unannotated), and ``has_image_text`` (True when the Document has stored
    frame image text from the vision lane, ADR-0014; frames the model found
    no text in store the ``NO_TEXT`` sentinel and do not count), and
    ``readers`` — every named reader with their mark time (``[{"reader":
    str, "read_at": <iso tz-aware str>}]``, sorted by reader, ``[]`` when
    unread; ADR-0015 ``document_reads``). ``read`` stays the anyone-read
    summary derived from the ``documents.read_at`` cache, so a legacy
    reader-less mark reads as ``read`` true with ``readers`` ``[]``. No empty
    analytics placeholders are emitted.

    Raises:
        ValueError: invalid ``limit``, invalid ``per_source_limit`` (non-bool
            positive int or None), non-bool ``exclude_read``, invalid
            ``min_importance`` (non-number or outside [0, 1]), or invalid
            ``topics`` (not a list of non-blank strings) — alongside the
            existing empty-period failure.
    """
    start = _coerce_bound(from_date, is_end=False)
    end = _coerce_bound(to_date, is_end=True)
    if start > end:
        raise ValueError(
            f"empty period: from_date {start.isoformat()} is after to_date {end.isoformat()}"
        )
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError(f"limit must be a positive int, got {limit!r}")
    if not isinstance(exclude_read, bool):
        raise ValueError(f"exclude_read must be a bool, got {exclude_read!r}")
    if per_source_limit is not None and (
        not isinstance(per_source_limit, int)
        or isinstance(per_source_limit, bool)
        or per_source_limit < 1
    ):
        raise ValueError(
            f"per_source_limit must be a positive int or None, got {per_source_limit!r}"
        )
    floor = _validate_min_importance(min_importance)
    topic_filter = _validate_topics(topics)
    names = list(sources) if sources is not None else list(catalog_names())
    where, filter_params = _annotation_filters(
        exclude_read=exclude_read, min_importance=floor, topics=topic_filter
    )
    sql = _bundle_sql(
        where=where, importance_first=floor is not None, per_source_limit=per_source_limit
    )
    params: list[Any] = [start, end, names, *filter_params]
    if per_source_limit is not None:
        params.append(per_source_limit)
    params.append(limit)

    owns_connection = False
    if conn is None:
        conn = get_connection()
        owns_connection = True
    assert conn is not None
    try:
        cursor = conn.execute(sql, params)
        rows = cursor.fetchall()
    finally:
        if owns_connection:
            try:
                conn.close()
            except Exception:
                pass

    articles: list[dict[str, Any]] = []
    for index, row in enumerate(rows or [], start=1):
        if isinstance(row, dict):
            title = row.get("title")
            url = row.get("url")
            canonical_url = row.get("canonical_url")
            source = row.get("source")
            published_at = row.get("published_at")
            author = row.get("author")
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
            content = row.get("content")
            has_image_text = row.get("has_image_text")
            readers = row.get("readers")
            read_ats = row.get("read_ats")
        else:
            # Tuple rows predate the read annotation (10 cols); 12-col rows
            # carry read_at/read_by; the current SELECT appends the 4
            # importance columns at [12..15], the topic array at [16], the
            # raw body at [17] (used only for the read-time cap), the
            # `has_image_text` presence flag at [18] and, after it, the
            # `document_reads` reader names + mark times at [19]/[20]. All
            # shapes are accepted; short rows have no reader rows to report.
            items = tuple(row)
            (
                title,
                url,
                canonical_url,
                source,
                published_at,
                author,
                flag_reason,
                flag_detail,
                flagged_at,
                flagged_by,
            ) = items[:10]
            read_at = items[10] if len(items) > 10 else None
            read_by = items[11] if len(items) > 11 else None
            importance_score = items[12] if len(items) > 12 else None
            importance_rationale = items[13] if len(items) > 13 else None
            importance_reporter = items[14] if len(items) > 14 else None
            importance_updated_at = items[15] if len(items) > 15 else None
            topics = items[16] if len(items) > 16 else None
            content = items[17] if len(items) > 17 else None
            has_image_text = items[18] if len(items) > 18 else None
            readers = items[19] if len(items) > 19 else None
            read_ats = items[20] if len(items) > 20 else None
        articles.append(
            {
                "title": title,
                "url": url,
                "canonical_url": canonical_url,
                "source": source,
                "published_at": _iso_tz_aware(published_at),
                "rank": index,
                "author": author,
                "flag_reason": flag_reason,
                "flag_detail": flag_detail,
                "flagged_at": _iso_tz_aware(flagged_at),
                "flagged_by": flagged_by,
                "read": read_at is not None,
                "read_at": _iso_tz_aware(read_at),
                "read_by": read_by,
                "importance_score": _importance.effective_score(
                    importance_score, flag_reason, content
                ),
                "importance_rationale": importance_rationale,
                "importance_reporter": importance_reporter,
                "importance_updated_at": _iso_tz_aware(importance_updated_at),
                "topics": _topics_list(topics),
                "has_image_text": bool(has_image_text),
                "readers": _readers_list(readers, read_ats),
            }
        )
    return {
        "period": {
            "from": start.isoformat(),
            "to": end.isoformat(),
            "timezone": PANAMA_NAME,
        },
        "recent_articles": articles,
    }
