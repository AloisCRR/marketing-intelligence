"""Shared validated service adapter (Ticket 05) — one interface for API + MCP.

Stdlib-only: no fastapi/mcp imports here. The thin adapters
(`src/api/app.py`, `src/mcp/server.py`) call these functions so HTTP and MCP
payloads stay identical by construction. Validation failures raise
:class:`InvalidRequest` (adapters map it to 422 / tool errors); domain
`ValueError`/`TypeError` from the underlying lanes is normalised to
`InvalidRequest` as well.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from marketing_intelligence import article as _article
from marketing_intelligence import digests as _digests
from marketing_intelligence import flag as _flag
from marketing_intelligence import importance as _importance
from marketing_intelligence import period as _period
from marketing_intelligence import read as _read
from marketing_intelligence import search as _search
from marketing_intelligence import topics as _topics
from marketing_intelligence.db import get_connection
from marketing_intelligence.sources import catalog_names, get_cadence

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

IMPORTANCE_KEYS = (
    "importance_score",
    "importance_rationale",
    "importance_reporter",
    "importance_updated_at",
)

#: Visibility key for the controlled Topic vocabulary (Ticket 20).
TOPICS_KEYS = ("topics",)

#: Per-reader `readers` list (Ticket 02): every named reader with their mark time.
READERS_KEYS = ("readers",)

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
    + IMPORTANCE_KEYS
    + TOPICS_KEYS
    # Vision image-text presence (ADR-0014): list payloads stay presence-only.
    + ("has_image_text",)
    # Ticket 02: the `readers` list, appended last.
    + READERS_KEYS
)

PERIOD_ARTICLE_KEYS = (
    (
        "title",
        "url",
        "canonical_url",
        "source",
        "published_at",
        "rank",
        "author",
    )
    + FLAG_KEYS
    + READ_KEYS
    + IMPORTANCE_KEYS
    + TOPICS_KEYS
    + ("has_image_text",)
    + READERS_KEYS
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
    + IMPORTANCE_KEYS
    + TOPICS_KEYS
    # The vision lane's frame texts, only on the one-item read (ADR-0014).
    + ("image_texts",)
    + READERS_KEYS
)


class InvalidRequest(ValueError):
    """Caller-side validation failure (blank keyword, bad limit/dates, ...)."""


def _validate_limit(limit: int, *, default: int) -> int:
    """Coerce/validate a result limit: positive int within [1, MAX_LIMIT]."""
    _ = default
    return _validate_bounded(limit, label="limit")


def _validate_bounded(value: Any, *, label: str) -> int:
    """Validate an int within [1, MAX_LIMIT]; failure is InvalidRequest."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidRequest(f"{label} must be an int in [1, {MAX_LIMIT}], got {value!r}")
    if value < 1 or value > MAX_LIMIT:
        raise InvalidRequest(f"{label} must be an int in [1, {MAX_LIMIT}], got {value!r}")
    return value


def _validate_per_source_limit(value: Any) -> int | None:
    """Validate the optional per-source cap: None, or int within [1, MAX_LIMIT]."""
    if value is None:
        return None
    return _validate_bounded(value, label="per_source_limit")


def _known_source_names() -> set[str]:
    """Catalog names plus every registry name (the catalog covers all 22;
    the union is kept for safety)."""
    names = set(catalog_names())
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


def _validate_min_importance(value: Any) -> float | None:
    """Validate the optional Importance floor: None, or a number in [0, 1]."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidRequest(
            f"min_importance must be a number in [0, 1] or null, got {type(value).__name__}"
        )
    number = float(value)
    if not (_importance.IMPORTANCE_MIN <= number <= _importance.IMPORTANCE_MAX):
        raise InvalidRequest(f"min_importance must be in [0, 1] or null, got {value!r}")
    return number


def _validate_topic_filter(topics: Any) -> list[str] | None:
    """Validate + canonicalize the optional Topic filter; unknown tags are 422.

    Caller synonyms, case/whitespace variants and retired aliases resolve to
    their canonical slug via the vocabulary lane; an unknown tag raises
    `InvalidRequest` before any query. Duplicates collapse, order is kept. An
    empty list is accepted and adds no constraint.
    """
    if topics is None:
        return None
    if isinstance(topics, (str, bytes)) or not isinstance(topics, (list, tuple)):
        raise InvalidRequest("topics must be a list of topic tags or null")
    slugs: list[str] = []
    for item in topics:
        if not isinstance(item, str) or not item.strip():
            raise InvalidRequest(f"topic tags must be non-empty strings, got {item!r}")
        try:
            slug = _topics.canonicalize_topic(item)
        except (TypeError, ValueError) as exc:
            raise InvalidRequest(str(exc)) from None
        if slug not in slugs:
            slugs.append(slug)
    return slugs


def search_articles(
    keyword: str,
    limit: int = DEFAULT_SEARCH_LIMIT,
    conn: Any | None = None,
    *,
    exclude_read: bool = False,
    min_importance: float | None = None,
    topics: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Validated keyword search; returns the 21-key provenance dicts, newest first.

    `exclude_read=True` filters out marked (read) articles; the default
    False annotates every result (`read`/`read_at`/`read_by`) without
    filtering. Every result also carries the latest Importance annotation
    (`importance_score`/`_rationale`/`_reporter`/`_updated_at`; ``None`` when
    unannotated), `topics` — the Document's effective canonical Topic
    slugs (sorted, ``[]`` when unannotated) — `has_image_text` (True when
    the Document has stored frame image text; ADR-0014) and `readers` — the
    per-reader `readers` list (`{reader, read_at}`, sorted by reader, ``[]`` when
    unread; Ticket 02). `read`/`exclude_read` stay anyone-read semantics.

    `min_importance` (None default) keeps only Documents whose latest score is
    `>=` the floor, ordered by importance (descending, ties by recency); with
    no floor ordering stays purely recency-first. Unannotated Documents (NULL
    score) are excluded **only** when a floor is set — they are never silently
    dropped from an unfiltered search and never silently top-ranked.
    `topics` (None default) keeps only Documents carrying at least one of the
    given tags — synonyms/case variants/retired aliases are canonicalized
    server-side, unknown tags raise `InvalidRequest` (422), and an empty list
    adds no constraint; unannotated Documents (no topics) are excluded only
    when a non-empty filter is set.
    """
    if not isinstance(keyword, str) or not keyword.strip():
        raise InvalidRequest("keyword must be a non-empty string")
    bound = _validate_limit(limit, default=DEFAULT_SEARCH_LIMIT)
    hide_read = _validate_exclude_read(exclude_read)
    floor = _validate_min_importance(min_importance)
    topic_filter = _validate_topic_filter(topics)
    try:
        return _search.search_articles(
            keyword.strip(),
            limit=bound,
            conn=conn,
            exclude_read=hide_read,
            min_importance=floor,
            topics=topic_filter,
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
    per_source_limit: int | None = None,
    min_importance: float | None = None,
    topics: list[str] | None = None,
) -> dict[str, Any]:
    """Validated period evidence bundle for [from_date, to_date].

    Bounds accept `date`, `datetime`, or ISO strings (Panama interpretation
    downstream). Explicit `sources` must all be known names. The bundle
    contains only real data: a `recent_articles` list whose items each carry a
    1-based `rank` ordering signal (its position in the final returned list),
    with no empty analytics placeholders.
    `exclude_read=True` filters out marked (read) articles; the default False
    annotates every article (`read`/`read_at`/`read_by`) without filtering.
    Every article also carries the latest Importance annotation
    (`importance_score`/`_rationale`/`_reporter`/`_updated_at`; ``None`` when
    unannotated), `topics` — the Document's effective canonical Topic
    slugs (sorted, ``[]`` when unannotated) — `has_image_text` (True when
    the Document has stored frame image text; ADR-0014) and `readers` — the
    per-reader `readers` list (`{reader, read_at}`, sorted by reader, ``[]`` when
    unread; Ticket 02).
    `per_source_limit` (None default) caps how many of the bundle's items any
    one source may contribute — `limit` still bounds the bundle overall, slots
    a capped source cannot fill go to other sources, and both compose with
    `sources`. It must be an int in [1, 100] or None.

    `min_importance` (None default) keeps only Documents whose latest score is
    `>=` the floor and orders the bundle by importance (descending, ties by
    recency); without a floor the bundle stays purely recency-ordered.
    `topics` (None default) keeps only Documents carrying at least one of the
    given tags (array overlap). Tags are canonicalized server-side (synonyms,
    case/whitespace variants and retired aliases resolve to the canonical
    slug); unknown tags raise `InvalidRequest` (422); an empty list adds no
    constraint. All filters compose with `sources`, `per_source_limit` and
    `exclude_read` in one call.

    Unannotated Documents (no importance row / no topics): a NULL score is
    excluded only when an importance floor is set (never silently top-ranked —
    unfiltered order is recency-first, floored order is importance-first with
    NULLS LAST), and a Document with no topics is excluded only when a
    non-empty topic filter is set. So the annotations sharpen filtered queries
    without ever silently dropping unannotated evidence from unfiltered ones.
    """
    start = _coerce_bound(from_date, label="from_date")
    end = _coerce_bound(to_date, label="to_date")
    names = _validate_sources(sources)
    bound = _validate_limit(limit, default=DEFAULT_PERIOD_LIMIT)
    hide_read = _validate_exclude_read(exclude_read)
    per_source = _validate_per_source_limit(per_source_limit)
    floor = _validate_min_importance(min_importance)
    topic_filter = _validate_topic_filter(topics)
    try:
        return _period.get_period_context(
            start,
            end,
            sources=names,
            limit=bound,
            conn=conn,
            exclude_read=hide_read,
            per_source_limit=per_source,
            min_importance=floor,
            topics=topic_filter,
        )
    except InvalidRequest:
        raise
    except (ValueError, TypeError) as exc:
        raise InvalidRequest(str(exc)) from None


def get_article(identifier: str, conn: Any | None = None) -> dict[str, Any]:
    """Validated one-item lookup; full stored body plus provenance.

    The returned dict also carries `topics` — the Document's effective
    canonical Topic slugs (sorted, ``[]`` when unannotated) — and
    `image_texts`: the vision lane's frame texts for this Document
    (ADR-0014), ordered by `frame_index`, each item
    `{frame_index, image_text, model, extracted_at}` (``[]`` when none).
    `readers` is the per-reader `readers` list (`{reader, read_at}` sorted by
    reader, ``[]`` when unread; Ticket 02) alongside the anyone-read
    `read`/`read_at`/`read_by` cache.
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


def _validate_read_by(read_by: Any, *, required: bool = False) -> Any:
    """Validate the reader tag: string-or-null, max 100 chars.

    Mirrors the read lane (`READ_BY_MAX`); blank strings normalise to None.
    ``required=True`` (the mark path, per-reader Read State) rejects a missing
    or blank reader: an unattributed mark has no reader row to write.
    """
    if read_by is None:
        if required:
            raise InvalidRequest("read_by is required when marking an article read")
        return None
    if not isinstance(read_by, str):
        raise InvalidRequest(f"read_by must be a string or null, got {type(read_by).__name__}")
    cleaned = read_by.strip() or None
    if cleaned is None:
        if required:
            raise InvalidRequest("read_by is required when marking an article read")
        return None
    if len(cleaned) > _read.READ_BY_MAX:
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
    Read State is per-reader, so marking (`clear=False`) requires a
    non-blank `read_by` — a reader-less mark is rejected here, before any
    DB round-trip. Clearing (`clear=True`) keeps `read_by` optional: with a
    reader only that reader's mark is dropped, without one every reader's
    mark is dropped. Unknown identifiers surface from the lane as
    `ValueError` (also `TypeError`/`LookupError`) and are normalised to
    `InvalidRequest`.

    The returned article dict carries the lane's anyone-read cache
    (`read`/`read_at`/`read_by`) plus `readers` — the per-reader `readers` list
    (`{reader, read_at}`, sorted by reader; Ticket 02) — so a named mark is
    visible in both views.
    """
    if not isinstance(identifier, str) or not identifier.strip():
        raise InvalidRequest("identifier must be a non-empty string")
    key = identifier.strip()
    clean_by = _validate_read_by(read_by, required=not clear)
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


# --- Source inventory (Ticket 18) ------------------------------------------
#
# Read-only per-Source listing for operators/agents: stored Document counts
# and the last successful Ingestion Run come from the durable tables; cadence
# comes from the curated registry. Both queries take the V1 name list as one
# parameter and never write.

_SOURCE_DOCUMENT_COUNTS_SQL = """\
SELECT s.name, COUNT(d.id)
  FROM sources s
  LEFT JOIN documents d ON d.source_id = s.id
 WHERE s.name = ANY(%s)
 GROUP BY s.name\
"""

_SOURCE_LAST_SUCCESS_SQL = """\
SELECT source_name, MAX(finished_at)
  FROM ingestion_runs
 WHERE error IS NULL
   AND source_name = ANY(%s)
 GROUP BY source_name\
"""


def _iso_or_none(value: Any) -> Any:
    """Normalise a timestamp to an isoformat tz-aware string; None passes through."""
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.isoformat()
    return value


def _pair_rows(conn: Any, sql: str, params: tuple) -> dict[str, Any]:
    """Run a ``(name, value)`` SELECT and map rows by name (missing keys absent)."""
    cursor = conn.execute(sql, params)
    rows = cursor.fetchall() or []
    result: dict[str, Any] = {}
    for row in rows:
        if row is None or row[0] is None:
            continue
        result[str(row[0])] = row[1]
    return result


def list_sources_inventory(conn: Any | None = None) -> list[dict[str, Any]]:
    """Read-only inventory of every curated Source, in catalog order (all 22).

    Each item is ``{"name", "article_count", "last_ingest_at", "cadence"}``:
    the stored Document count, the most recent *successful* Ingestion Run's
    finish time (ISO-8601 tz-aware, ``None`` when never successfully
    ingested), and the curated cadence label (``None`` when undeclared).
    Empty and never-ingested Sources stay in the listing with explicit
    ``0``/``None`` — they never disappear.

    Args:
        conn: optional injected DB-API connection (fake-friendly). When None
            the adapter opens one via ``get_connection`` and closes only that;
            an injected connection is never closed or committed here.

    Returns:
        List of 22 dicts in ``catalog_names()`` order. Read-only.
    """
    names = list(catalog_names())
    owns_connection = conn is None
    if conn is None:
        conn = get_connection()
    assert conn is not None
    try:
        counts = _pair_rows(conn, _SOURCE_DOCUMENT_COUNTS_SQL, (names,))
        last = _pair_rows(conn, _SOURCE_LAST_SUCCESS_SQL, (names,))
    finally:
        if owns_connection:
            try:
                conn.close()
            except Exception:
                pass
    inventory: list[dict[str, Any]] = []
    for name in names:
        try:
            count = int(counts.get(name, 0))
        except (TypeError, ValueError):
            count = 0
        inventory.append(
            {
                "name": name,
                "article_count": count,
                "last_ingest_at": _iso_or_none(last.get(name)),
                "cadence": get_cadence(name),
            }
        )
    return inventory


# --- Importance annotations (Ticket 19) ------------------------------------
#
# Agent-writable 0-1 importance per Document with rationale + reporter,
# append-only history and latest-wins. The lane caps flagged/paywalled
# Documents at 0.3 server-side before writing; read paths annotate every
# returned article without extra calls. Both caller surfaces go through
# these two functions so payloads/errors stay identical by construction.


def _validate_importance_score(score: Any) -> float:
    """Validate a 0-1 importance score; failure is InvalidRequest."""
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        raise InvalidRequest(f"score must be a number in [0, 1], got {type(score).__name__}")
    value = float(score)
    if not (_importance.IMPORTANCE_MIN <= value <= _importance.IMPORTANCE_MAX):
        raise InvalidRequest(f"score must be in [0, 1], got {score!r}")
    return value


def _validate_importance_rationale(rationale: Any) -> Any:
    """Validate the optional rationale: string-or-null, max 2000 chars."""
    if rationale is None:
        return None
    if not isinstance(rationale, str):
        raise InvalidRequest(f"rationale must be a string or null, got {type(rationale).__name__}")
    cleaned = rationale.strip() or None
    if cleaned is not None and len(cleaned) > _importance.RATIONALE_MAX_LENGTH:
        raise InvalidRequest(
            f"rationale must be at most {_importance.RATIONALE_MAX_LENGTH} chars, "
            f"got {len(cleaned)}"
        )
    return cleaned


def _validate_importance_reporter(reporter: Any) -> Any:
    """Validate the optional reporter tag: string-or-null, max 100 chars."""
    if reporter is None:
        return None
    if not isinstance(reporter, str):
        raise InvalidRequest(f"reporter must be a string or null, got {type(reporter).__name__}")
    cleaned = reporter.strip() or None
    if cleaned is not None and len(cleaned) > _importance.REPORTER_MAX_LENGTH:
        raise InvalidRequest(
            f"reporter must be at most {_importance.REPORTER_MAX_LENGTH} chars, got {len(cleaned)}"
        )
    return cleaned


def set_importance(
    identifier: str,
    score: float,
    rationale: str | None = None,
    reporter: str | None = None,
    conn: Any | None = None,
) -> dict[str, Any]:
    """Validated importance write; returns the updated article dict.

    Blank/non-string identifiers raise `InvalidRequest` without a DB
    round-trip, as do non-numeric or out-of-range scores and overlong
    rationale (>2000 chars) / reporter (>100 chars). Flagged or paywalled
    Documents are hard-capped at 0.3 by the lane before the row is written.
    Unknown identifiers surface from the lane as `ValueError` (also
    `TypeError`/`LookupError`) and are normalised to `InvalidRequest`.
    """
    if not isinstance(identifier, str) or not identifier.strip():
        raise InvalidRequest("identifier must be a non-empty string")
    key = identifier.strip()
    clean_score = _validate_importance_score(score)
    clean_rationale = _validate_importance_rationale(rationale)
    clean_reporter = _validate_importance_reporter(reporter)
    try:
        return _importance.set_importance(
            key,
            clean_score,
            rationale=clean_rationale,
            reporter=clean_reporter,
            conn=conn,
        )
    except InvalidRequest:
        raise
    except (ValueError, TypeError, LookupError) as exc:
        raise InvalidRequest(str(exc)) from None


def get_importance(identifier: str, conn: Any | None = None) -> dict[str, Any]:
    """Validated read of one Document's latest importance annotation.

    Returns the four ``importance_*`` keys (all ``None`` when unannotated).
    Blank/non-string identifiers raise `InvalidRequest` without a DB
    round-trip; unknown identifiers are normalised to `InvalidRequest`.
    """
    if not isinstance(identifier, str) or not identifier.strip():
        raise InvalidRequest("identifier must be a non-empty string")
    try:
        return _importance.get_importance(identifier.strip(), conn=conn)
    except InvalidRequest:
        raise
    except (ValueError, TypeError, LookupError) as exc:
        raise InvalidRequest(str(exc)) from None


# --- Controlled Topic vocabulary (Ticket 20) -------------------------------
#
# Canonical pillar/region/content-type tags per Document. Reads expose the
# effective slug list on every Document (search, single read, period bundle)
# without extra calls; writes canonicalize caller synonyms server-side and
# reject unknown tags (never stored as-is), keeping an append-only history
# with latest-wins. Both caller surfaces go through these functions so
# payloads/errors stay identical by construction.


def _validate_topic_list(topics: Any) -> list[str]:
    """Validate a caller topic list: list/tuple of non-blank strings, or 422."""
    if isinstance(topics, (str, bytes)) or not isinstance(topics, (list, tuple)):
        raise InvalidRequest("topics must be a list of topic tags")
    for item in topics:
        if not isinstance(item, str) or not item.strip():
            raise InvalidRequest(f"topic tags must be non-empty strings, got {item!r}")
    return [item.strip() for item in topics]


def _validate_topic_reporter(reporter: Any) -> Any:
    """Validate the optional topic reporter tag: string-or-null, max 100 chars."""
    if reporter is None:
        return None
    if not isinstance(reporter, str):
        raise InvalidRequest(f"reporter must be a string or null, got {type(reporter).__name__}")
    cleaned = reporter.strip() or None
    if cleaned is not None and len(cleaned) > _topics.REPORTER_MAX_LENGTH:
        raise InvalidRequest(
            f"reporter must be at most {_topics.REPORTER_MAX_LENGTH} chars, got {len(cleaned)}"
        )
    return cleaned


def list_vocabulary() -> list[dict[str, Any]]:
    """Read-only controlled Topic vocabulary (canonical slugs + synonyms).

    Every entry is ``{slug, kind, label, synonyms, retired_alias_of}`` (see
    ``marketing_intelligence.topics.list_vocabulary``). Additive by
    construction: retired slugs stay listed with their replacement, so
    callers can always discover the current canonical form.
    """
    return _topics.list_vocabulary()


def set_document_topics(
    identifier: str,
    topics: list[str],
    reporter: str | None = None,
    conn: Any | None = None,
) -> dict[str, Any]:
    """Validated Topic write; returns the updated article dict.

    Blank/non-string identifiers raise `InvalidRequest` without a DB
    round-trip, as do a non-list `topics` argument, non-string/blank tags,
    and an overlong reporter (>100 chars). Tags are canonicalized
    server-side (synonyms, case/whitespace variants and retired aliases all
    land on the canonical slug); unknown tags raise `InvalidRequest` before
    any write and are never stored as-is. The effective set becomes exactly
    `topics` (empty list clears it); earlier rows stay, so history is
    retained and a later write can restore a tag.
    """
    if not isinstance(identifier, str) or not identifier.strip():
        raise InvalidRequest("identifier must be a non-empty string")
    clean_topics = _validate_topic_list(topics)
    clean_reporter = _validate_topic_reporter(reporter)
    try:
        return _topics.set_document_topics(
            identifier.strip(), clean_topics, reporter=clean_reporter, conn=conn
        )
    except InvalidRequest:
        raise
    except (ValueError, TypeError, LookupError) as exc:
        raise InvalidRequest(str(exc)) from None


# --- Digest picks trace (Ticket 22) ----------------------------------------
#
# Which Documents each digest date selected. `record_digest_picks` reconciles
# the stored set to the caller's (possibly human-edited) URL list and returns
# the added/removed diff — the re-scoring signal for a published digest;
# `get_digest_picks` reads a date's set back; `clear_digest_picks` removes it.
# Both caller surfaces go through these three functions so payloads/errors
# stay identical by construction (unknown URL / bad date -> 422).


def _validate_digest_date(value: Any) -> date:
    """Accept a date/datetime or ISO date/datetime string; failure is 422.

    Only the calendar date is kept (`datetime`/timestamp inputs lose the time
    part) because a digest edition is named by its date.
    """
    bound = _coerce_bound(value, label="digest_date")
    return bound.date() if isinstance(bound, datetime) else bound


def _validate_digest_identifiers(identifiers: Any) -> list[str]:
    """Validate a digest pick list: list/tuple of non-blank article URLs, or 422.

    An empty list is accepted and reconciles the set to empty; duplicates and
    URL/canonical-URL spellings of one Document collapse to a single pick
    downstream.
    """
    if isinstance(identifiers, (str, bytes)) or not isinstance(identifiers, (list, tuple)):
        raise InvalidRequest("identifiers must be a list of article URLs")
    for item in identifiers:
        if not isinstance(item, str) or not item.strip():
            raise InvalidRequest(f"identifiers must be non-empty strings, got {item!r}")
    return [item.strip() for item in identifiers]


def _validate_digest_reporter(reporter: Any) -> Any:
    """Validate the optional digest reporter tag: string-or-null, max 100 chars."""
    if reporter is None:
        return None
    if not isinstance(reporter, str):
        raise InvalidRequest(f"reporter must be a string or null, got {type(reporter).__name__}")
    cleaned = reporter.strip() or None
    if cleaned is not None and len(cleaned) > _digests.REPORTER_MAX_LENGTH:
        raise InvalidRequest(
            f"reporter must be at most {_digests.REPORTER_MAX_LENGTH} chars, got {len(cleaned)}"
        )
    return cleaned


def record_digest_picks(
    digest_date: date | datetime | str,
    identifiers: list[str],
    reporter: str | None = None,
    conn: Any | None = None,
) -> dict[str, Any]:
    """Validated digest-pick write; returns the set plus the added/removed diff.

    The stored pick set for `digest_date` becomes exactly the Documents named
    by `identifiers` (new ones inserted, dropped ones deleted; re-recording an
    unchanged pick does not duplicate it or reset its `picked_at`). The
    `added`/`removed` lists in the result are the re-scoring signal: pass the
    URL set a human edited the digest down to and the diff names the Documents
    whose importance was under- or over-weighted. A blank/non-list
    `identifiers` argument, a non-string/blank URL, an overlong reporter
    (>100 chars) or a malformed date raise `InvalidRequest` without a DB
    round-trip; unknown URLs surface from the lane and are normalised too.
    """
    clean_date = _validate_digest_date(digest_date)
    clean_identifiers = _validate_digest_identifiers(identifiers)
    clean_reporter = _validate_digest_reporter(reporter)
    try:
        return _digests.record_digest_picks(
            clean_date, clean_identifiers, reporter=clean_reporter, conn=conn
        )
    except InvalidRequest:
        raise
    except (ValueError, TypeError, LookupError) as exc:
        raise InvalidRequest(str(exc)) from None


def get_digest_picks(digest_date: date | datetime | str, conn: Any | None = None) -> dict[str, Any]:
    """Validated read of one digest date's pick set.

    Returns `{"digest_date", "picks"}`; each pick carries the Document
    identity (`url`, `canonical_url`, `title`, `source`, `published_at`) plus
    `reporter` and `picked_at`. A malformed date raises `InvalidRequest`
    without a DB round-trip; an empty set is a normal `picks: []`.
    """
    clean_date = _validate_digest_date(digest_date)
    try:
        return _digests.get_digest_picks(clean_date, conn=conn)
    except InvalidRequest:
        raise
    except (ValueError, TypeError, LookupError) as exc:
        raise InvalidRequest(str(exc)) from None


def clear_digest_picks(
    digest_date: date | datetime | str, conn: Any | None = None
) -> dict[str, Any]:
    """Validated clear of one digest date's pick set.

    Returns `{"digest_date", "cleared", "picks"}` (`cleared` is the number of
    rows removed, `picks` the read-back set, always `[]` after a successful
    clear). A malformed date raises `InvalidRequest` without a DB round-trip.
    """
    clean_date = _validate_digest_date(digest_date)
    try:
        return _digests.clear_digest_picks(clean_date, conn=conn)
    except InvalidRequest:
        raise
    except (ValueError, TypeError, LookupError) as exc:
        raise InvalidRequest(str(exc)) from None
