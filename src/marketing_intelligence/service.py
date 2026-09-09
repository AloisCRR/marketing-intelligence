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

from marketing_intelligence import article as _article
from marketing_intelligence import flag as _flag
from marketing_intelligence import period as _period
from marketing_intelligence import read as _read
from marketing_intelligence import search as _search
from marketing_intelligence.sources import V1_SOURCES

MAX_LIMIT = 100
DEFAULT_SEARCH_LIMIT = 20
DEFAULT_PERIOD_LIMIT = _period.DEFAULT_LIMIT

FLAG_KEYS = (
    "flag_reason",
    "flag_detail",
    "flagged_at",
    "flagged_by",
)

READ_KEYS = (
    "read",
    "read_at",
    "read_by",
)

SEARCH_RESULT_KEYS = (
    (
        "title",
        "url",
        "canonical_url",
        "source",
        "published_at",
        "author",
        "snippet",
    )
    + FLAG_KEYS
    + READ_KEYS
)

PERIOD_ARTICLE_KEYS = (
    (
        "title",
        "url",
        "canonical_url",
        "source",
        "published_at",
        "author",
    )
    + FLAG_KEYS
    + READ_KEYS
)

ARTICLE_KEYS = (
    (
        "title",
        "url",
        "canonical_url",
        "source",
        "published_at",
        "author",
        "content",
    )
    + FLAG_KEYS
    + READ_KEYS
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
    """V1 names plus every registry name (V1 covers all 20; kept union for safety)."""
    names = set(V1_SOURCES)
    try:
        from marketing_intelligence.sources import list_sources

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


def _validate_exclude_read(exclude_read: Any) -> bool:
    """Validate the read filter flag: strict bool, failure is InvalidRequest."""
    if not isinstance(exclude_read, bool):
        raise InvalidRequest(f"exclude_read must be a bool, got {exclude_read!r}")
    return exclude_read


def search_articles(
    keyword: str,
    limit: int = DEFAULT_SEARCH_LIMIT,
    conn: Any | None = None,
    *,
    exclude_read: bool = False,
) -> list[dict[str, Any]]:
    """Validated keyword search; returns the 14-key provenance dicts, newest first.

    `exclude_read=True` filters out marked (read) articles; the default
    False annotates every result (`read`/`read_at`/`read_by`) without
    filtering.
    """
    if not isinstance(keyword, str) or not keyword.strip():
        raise InvalidRequest("keyword must be a non-empty string")
    bound = _validate_limit(limit, default=DEFAULT_SEARCH_LIMIT)
    hide_read = _validate_exclude_read(exclude_read)
    try:
        return _search.search_articles(
            keyword.strip(), limit=bound, conn=conn, exclude_read=hide_read
        )
    except InvalidRequest:
        raise
    except (ValueError, TypeError) as exc:
        raise InvalidRequest(str(exc)) from None


def get_period_context(
    from_date: date | datetime | str,
    to_date: date | datetime | str,
    *,
    sources: list[str] | None = None,
    limit: int = DEFAULT_PERIOD_LIMIT,
    conn: Any | None = None,
    exclude_read: bool = False,
) -> dict[str, Any]:
    """Validated period evidence bundle for [from_date, to_date].

    Bounds accept `date`, `datetime`, or ISO strings (Panama interpretation
    downstream). Explicit `sources` must all be known names. V1 trend keys are
    present as explicit `[]` (no accumulated history yet). `exclude_read=True`
    filters out marked (read) articles; the default False annotates every
    article (`read`/`read_at`/`read_by`) without filtering.
    """
    start = _coerce_bound(from_date, label="from_date")
    end = _coerce_bound(to_date, label="to_date")
    names = _validate_sources(sources)
    bound = _validate_limit(limit, default=DEFAULT_PERIOD_LIMIT)
    hide_read = _validate_exclude_read(exclude_read)
    try:
        return _period.get_period_context(
            start, end, sources=names, limit=bound, conn=conn, exclude_read=hide_read
        )
    except InvalidRequest:
        raise
    except (ValueError, TypeError) as exc:
        raise InvalidRequest(str(exc)) from None


def get_article(identifier: str, conn: Any | None = None) -> dict[str, Any]:
    """Validated one-item lookup; full stored body plus provenance.

    Blank/non-string identifiers raise `InvalidRequest` without a DB
    round-trip; unknown identifiers surface from the lane as `ValueError`
    (also `TypeError`/`LookupError`) and are normalised to `InvalidRequest`.
    """
    if not isinstance(identifier, str) or not identifier.strip():
        raise InvalidRequest("identifier must be a non-empty string")
    try:
        return _article.get_article(identifier.strip(), conn=conn)
    except InvalidRequest:
        raise
    except (ValueError, TypeError, LookupError) as exc:
        raise InvalidRequest(str(exc)) from None


def _validate_flag_reason(reason: Any) -> str:
    """Validate a flag reason against FLAG_REASONS; failure is InvalidRequest."""
    if not isinstance(reason, str) or reason.strip() not in _flag.FLAG_REASONS:
        raise InvalidRequest(
            f"flag reason must be one of {list(_flag.FLAG_REASONS)}, got {reason!r}"
        )
    return reason.strip()


def _validate_flag_detail(detail: Any, *, reason: str) -> Any:
    """Validate flag detail: string-or-null, max 2000 chars, required for other."""
    if detail is not None and not isinstance(detail, str):
        raise InvalidRequest(f"flag detail must be a string or null, got {type(detail).__name__}")
    if isinstance(detail, str) and len(detail) > _flag.DETAIL_MAX_LENGTH:
        raise InvalidRequest(
            f"flag detail must be at most {_flag.DETAIL_MAX_LENGTH} chars, got {len(detail)}"
        )
    if reason == "other" and (not isinstance(detail, str) or not detail.strip()):
        raise InvalidRequest("flag reason 'other' requires a non-blank detail")
    return detail


def _validate_flagged_by(flagged_by: Any) -> Any:
    """Validate the optional reporter tag: string-or-null, max 100 chars."""
    if flagged_by is None:
        return None
    if not isinstance(flagged_by, str):
        raise InvalidRequest(
            f"flagged_by must be a string or null, got {type(flagged_by).__name__}"
        )
    cleaned = flagged_by.strip() or None
    if cleaned is not None and len(cleaned) > _flag.FLAGGED_BY_MAX_LENGTH:
        raise InvalidRequest(
            f"flagged_by must be at most {_flag.FLAGGED_BY_MAX_LENGTH} chars, got {len(cleaned)}"
        )
    return cleaned


def _validate_read_by(read_by: Any) -> Any:
    """Validate the optional reader tag: string-or-null, max 100 chars.

    Mirrors the read lane (`READ_BY_MAX`); blank strings normalise to None.
    """
    if read_by is None:
        return None
    if not isinstance(read_by, str):
        raise InvalidRequest(f"read_by must be a string or null, got {type(read_by).__name__}")
    cleaned = read_by.strip() or None
    if cleaned is not None and len(cleaned) > _read.READ_BY_MAX:
        raise InvalidRequest(
            f"read_by must be at most {_read.READ_BY_MAX} chars, got {len(cleaned)}"
        )
    return cleaned


def mark_article_read(
    identifier: str,
    read_by: str | None = None,
    clear: bool = False,
    conn: Any | None = None,
) -> dict[str, Any]:
    """Validated Read State write; returns the updated article dict.

    Blank/non-string identifiers raise `InvalidRequest` without a DB
    round-trip, as do non-string or overlong `read_by` (>100 chars).
    `clear=True` ignores `read_by` (no validation) and NULLs the two read
    columns. Unknown identifiers surface from the lane as `ValueError`
    (also `TypeError`/`LookupError`) and are normalised to `InvalidRequest`.
    """
    if not isinstance(identifier, str) or not identifier.strip():
        raise InvalidRequest("identifier must be a non-empty string")
    key = identifier.strip()
    if clear:
        clean_by = None
    else:
        clean_by = _validate_read_by(read_by)
    try:
        result = _read.mark_article_read(key, read_by=clean_by, clear=clear, conn=conn)
    except InvalidRequest:
        raise
    except (ValueError, TypeError, LookupError) as exc:
        raise InvalidRequest(str(exc)) from None
    # The lane returns the article plus read_at/read_by; pin the derived
    # `read` bool to the returned read_at so fakes and live rows agree.
    try:
        result["read"] = result.get("read_at") is not None
    except AttributeError:
        pass
    return result


def flag_extraction(
    identifier: str,
    reason: str | None = None,
    detail: str | None = None,
    flagged_by: str | None = None,
    clear: bool = False,
    conn: Any | None = None,
) -> dict[str, Any]:
    """Validated Extraction Flag write; returns the updated article dict.

    Blank/non-string identifiers raise `InvalidRequest` without a DB
    round-trip, as do bad reasons, overlong detail (>2000 chars), a missing
    detail for reason `"other"`, and overlong `flagged_by` (>100 chars).
    `clear=True` ignores reason/detail (no validation) and NULLs the four
    flag columns. Unknown identifiers surface from the lane as `ValueError`
    and are normalised to `InvalidRequest`.
    """
    if not isinstance(identifier, str) or not identifier.strip():
        raise InvalidRequest("identifier must be a non-empty string")
    key = identifier.strip()
    if clear:
        clean_reason, clean_detail, clean_by = None, None, None
    else:
        clean_reason = _validate_flag_reason(reason)
        clean_detail = _validate_flag_detail(detail, reason=clean_reason)
        clean_by = _validate_flagged_by(flagged_by)
    try:
        return _flag.flag_extraction(
            key,
            reason=clean_reason,
            detail=clean_detail,
            flagged_by=clean_by,
            clear=clear,
            conn=conn,
        )
    except InvalidRequest:
        raise
    except (ValueError, TypeError, LookupError) as exc:
        raise InvalidRequest(str(exc)) from None
