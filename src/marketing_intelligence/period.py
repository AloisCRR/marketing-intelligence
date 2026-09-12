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
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from marketing_intelligence.db import get_connection
from marketing_intelligence.sources import V1_SOURCES

PANAMA_TZ = ZoneInfo("America/Panama")
PANAMA_NAME = "America/Panama"

DEFAULT_LIMIT = 50

PERIOD_SQL = """\
SELECT d.title, d.url, d.canonical_url, s.name AS source,
       d.published_at, d.author,
       d.flag_reason, d.flag_detail, d.flagged_at, d.flagged_by,
       d.read_at, d.read_by
  FROM documents d
  JOIN sources s ON s.id = d.source_id
 WHERE d.published_at >= %s
   AND d.published_at < %s
   AND s.name = ANY(%s)
 ORDER BY d.published_at DESC
 LIMIT %s\
"""

PERIOD_SQL_EXCLUDE_READ = """\
SELECT d.title, d.url, d.canonical_url, s.name AS source,
       d.published_at, d.author,
       d.flag_reason, d.flag_detail, d.flagged_at, d.flagged_by,
       d.read_at, d.read_by
  FROM documents d
  JOIN sources s ON s.id = d.source_id
 WHERE d.published_at >= %s
   AND d.published_at < %s
   AND s.name = ANY(%s)
   AND d.read_at IS NULL
 ORDER BY d.published_at DESC
 LIMIT %s\
"""


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


def get_period_context(
    from_date: date | datetime,
    to_date: date | datetime,
    *,
    sources: list[str] | None = None,
    limit: int = DEFAULT_LIMIT,
    conn: Any | None = None,
    exclude_read: bool = False,
) -> dict[str, Any]:
    """Return the period evidence bundle for ``[from_date, to_date]``.

    (``from`` is a Python keyword, hence ``from_date``/``to_date``; positional
    ``get_period_context(a, b)`` reads as (from, to). Plain dates are
    day-inclusive; datetimes are half-open ``[from, to)``. Explicit
    ``sources`` must have seed rows in the ``sources`` table (the V1 twenty
    are seeded by migrations 001 + 006) or they match no articles.
    ``exclude_read`` filters out marked (read) articles via
    ``AND d.read_at IS NULL``; default False annotates without filtering.

    Returns ``period {from, to, timezone}`` plus ``recent_articles`` — a
    purely recency-ordered list (never ranked by importance), bounded by
    ``limit``. Each item carries ``rank`` (its 1-based position in that
    order; an ordering signal, not a score) alongside
    ``title, url, canonical_url, source, published_at, author`` provenance,
    the Extraction Flag annotation (``flag_reason, flag_detail, flagged_at,
    flagged_by`` — ``None`` when unflagged), and the Read State annotation
    (``read`` bool derived from ``read_at IS NOT NULL``, plus ``read_at,
    read_by`` — ``None`` when unread). No empty analytics placeholders are
    emitted.

    Raises:
        ValueError: non-bool ``exclude_read`` (alongside the existing
            empty-period / bad-limit failures).
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
    names = list(sources) if sources is not None else list(V1_SOURCES)
    sql = PERIOD_SQL_EXCLUDE_READ if exclude_read else PERIOD_SQL

    owns_connection = False
    if conn is None:
        conn = get_connection()
        owns_connection = True
    assert conn is not None
    try:
        cursor = conn.execute(sql, (start, end, names, limit))
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
        else:
            # Tuple rows predate the read annotation (10 cols); newer rows
            # carry read_at/read_by (12 cols). Both shapes are accepted.
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
