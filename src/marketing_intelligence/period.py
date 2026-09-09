"""Period intelligence context (Ticket 04) — grounded evidence, no trend claims.

``get_period_context`` prepares the evidence bundle for period synthesis over
an explicit caller-supplied period interpreted in ``America/Panama``:

- naive ``date``/``datetime`` inputs are assumed America/Panama (never
  server-local time); aware datetimes are converted;
- a plain ``date`` covers its whole calendar day: ``from`` is inclusive at
  00:00, ``to`` is inclusive of the full day (implemented as an exclusive
  upper bound at the next midnight);
- ``datetime`` bounds are exact instants (``from`` inclusive, ``to`` exclusive).

V1 deliberately makes NO velocity or emerging-topic claims: ranking signals
need accumulated history that does not exist yet. The trend-shaped keys
(``top_stories``, ``emerging_topics``, ``topic_movements``,
``notable_entities``, ``source_convergence``) are therefore present as
explicit empty lists following spec §13's conceptual contract, so future
lanes can fill them without breaking callers.
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
       d.flag_reason, d.flag_detail, d.flagged_at, d.flagged_by
  FROM documents d
  JOIN sources s ON s.id = d.source_id
 WHERE d.published_at >= %s
   AND d.published_at < %s
   AND s.name = ANY(%s)
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
) -> dict[str, Any]:
    """Return the period evidence bundle for ``[from_date, to_date]``.

    (``from`` is a Python keyword, hence ``from_date``/``to_date``; positional
    ``get_period_context(a, b)`` reads as (from, to). Plain dates are
    day-inclusive; datetimes are half-open ``[from, to)``. Explicit
    ``sources`` must have seed rows in the ``sources`` table (the V1 twenty
    are seeded by migrations 001 + 006) or they match no articles.

    Returns ``period {from, to, timezone}`` plus ``important_articles`` —
    each with ``title, url, canonical_url, source, published_at, author``
    provenance plus the Extraction Flag annotation (``flag_reason,
    flag_detail, flagged_at, flagged_by`` — ``None`` when unflagged),
    newest-first, bounded by ``limit`` — plus the V1-empty
    trend keys documented above.
    """
    start = _coerce_bound(from_date, is_end=False)
    end = _coerce_bound(to_date, is_end=True)
    if start > end:
        raise ValueError(
            f"empty period: from_date {start.isoformat()} is after to_date {end.isoformat()}"
        )
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError(f"limit must be a positive int, got {limit!r}")
    names = list(sources) if sources is not None else list(V1_SOURCES)

    owns_connection = False
    if conn is None:
        conn = get_connection()
        owns_connection = True
    assert conn is not None
    try:
        cursor = conn.execute(PERIOD_SQL, (start, end, names, limit))
        rows = cursor.fetchall()
    finally:
        if owns_connection:
            try:
                conn.close()
            except Exception:
                pass

    articles: list[dict[str, Any]] = []
    for row in rows or []:
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
        else:
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
            ) = row
        articles.append(
            {
                "title": title,
                "url": url,
                "canonical_url": canonical_url,
                "source": source,
                "published_at": _iso_tz_aware(published_at),
                "author": author,
                "flag_reason": flag_reason,
                "flag_detail": flag_detail,
                "flagged_at": _iso_tz_aware(flagged_at),
                "flagged_by": flagged_by,
            }
        )
    return {
        "period": {
            "from": start.isoformat(),
            "to": end.isoformat(),
            "timezone": PANAMA_NAME,
        },
        "important_articles": articles,
        # V1: no accumulated history yet — no velocity/emerging claims.
        "top_stories": [],
        "emerging_topics": [],
        "topic_movements": [],
        "notable_entities": [],
        "source_convergence": [],
    }
