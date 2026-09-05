"""Shared validated service adapter (Ticket 05) — one interface for API + MCP.

Stdlib-only: no fastapi/mcp imports here. The thin adapters
(`src/api/app.py`, `src/mcp/server.py`) call these functions so HTTP and MCP
payloads stay identical by construction. Validation failures raise
:class:`InvalidRequest` (adapters map it to 422 / tool errors); domain
`ValueError`/`TypeError` from the underlying lanes is normalised to
`InvalidRequest` as well.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from brain import search as _search
from brain import weekly as _weekly
from brain.sources import V1_SOURCES

MAX_LIMIT = 100
DEFAULT_SEARCH_LIMIT = 20
DEFAULT_WEEKLY_LIMIT = _weekly.DEFAULT_LIMIT

SEARCH_RESULT_KEYS = (
    "title",
    "url",
    "canonical_url",
    "source",
    "published_at",
    "author",
    "snippet",
)

WEEKLY_ARTICLE_KEYS = (
    "title",
    "url",
    "canonical_url",
    "source",
    "published_at",
    "author",
)

TREND_KEYS = (
    "top_stories",
    "emerging_topics",
    "topic_movements",
    "notable_entities",
    "source_convergence",
)


class InvalidRequest(ValueError):
    """Caller-side validation failure (blank keyword, bad limit/dates, ...)."""


def _validate_limit(limit: int, *, default: int) -> int:
    """Coerce/validate a result limit: positive int within [1, MAX_LIMIT]."""
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise InvalidRequest(f"limit must be an int in [1, {MAX_LIMIT}], got {limit!r}")
    if limit < 1 or limit > MAX_LIMIT:
        raise InvalidRequest(f"limit must be an int in [1, {MAX_LIMIT}], got {limit!r}")
    _ = default
    return limit


def _known_source_names() -> set[str]:
    """V1 names plus every RSS-capable registry name (extras stay valid)."""
    names = set(V1_SOURCES)
    try:
        from brain.sources import list_sources

        for entry in list_sources():
            name = entry.get("name")
            if name:
                names.add(str(name))
    except Exception:
        pass
    return names


def _validate_sources(sources: list[str] | None) -> list[str] | None:
    """Validate an explicit source filter; None keeps the V1 default."""
    if sources is None:
        return None
    if not isinstance(sources, (list, tuple)) or isinstance(sources, (str, bytes)):
        raise InvalidRequest("sources must be a list of known source names or null")
    known = _known_source_names()
    cleaned: list[str] = []
    for item in sources:
        if not isinstance(item, str) or not item.strip():
            raise InvalidRequest(f"unknown source: {item!r}")
        name = item.strip()
        if name not in known:
            raise InvalidRequest(f"unknown source: {name!r}")
        cleaned.append(name)
    return cleaned


def _coerce_bound(value: date | datetime | str, *, label: str) -> date | datetime:
    """Accept date/datetime/ISO-string bounds; anything else is InvalidRequest.

    Date-only strings stay `date` (whole-day Panama semantics downstream);
    datetime strings become `datetime`. Unparseable strings and other types
    raise `InvalidRequest` (never leak domain TypeError to callers).
    """
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise InvalidRequest(f"{label} must be an ISO date/datetime, got {value!r}")
        try:
            return date.fromisoformat(text)
        except ValueError:
            pass
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            raise InvalidRequest(f"{label} must be an ISO date/datetime, got {value!r}") from None
    raise InvalidRequest(
        f"{label} must be a date, datetime, or ISO string, got {type(value).__name__}"
    )


def search_articles(
    keyword: str, limit: int = DEFAULT_SEARCH_LIMIT, conn: Any | None = None
) -> list[dict[str, Any]]:
    """Validated keyword search; returns the 7-key provenance dicts, newest first."""
    if not isinstance(keyword, str) or not keyword.strip():
        raise InvalidRequest("keyword must be a non-empty string")
    bound = _validate_limit(limit, default=DEFAULT_SEARCH_LIMIT)
    return _search.search_articles(keyword.strip(), limit=bound, conn=conn)


def get_weekly_context(
    from_date: date | datetime | str,
    to_date: date | datetime | str,
    *,
    sources: list[str] | None = None,
    limit: int = DEFAULT_WEEKLY_LIMIT,
    conn: Any | None = None,
) -> dict[str, Any]:
    """Validated weekly evidence bundle for [from_date, to_date].

    Bounds accept `date`, `datetime`, or ISO strings (Panama interpretation
    downstream). Explicit `sources` must all be known names. V1 trend keys are
    present as explicit `[]` (no accumulated history yet).
    """
    start = _coerce_bound(from_date, label="from_date")
    end = _coerce_bound(to_date, label="to_date")
    names = _validate_sources(sources)
    bound = _validate_limit(limit, default=DEFAULT_WEEKLY_LIMIT)
    try:
        return _weekly.get_weekly_context(start, end, sources=names, limit=bound, conn=conn)
    except InvalidRequest:
        raise
    except (ValueError, TypeError) as exc:
        raise InvalidRequest(str(exc)) from None
