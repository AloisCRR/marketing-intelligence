"""Digest picks trace (Ticket 22) — which Documents each digest selected.

Domain contract (stable): a *digest date* names one digest edition (normally
one week's Monday). ``record_digest_picks(digest_date, identifiers,
reporter)`` reconciles the stored pick set for that date to exactly the given
Documents: added Documents are inserted (idempotently per
``(digest_date, document_id)``), Documents no longer in the set are deleted,
and the call returns the read-back picks plus the ``added``/``removed``
difference. Passing an edited set therefore captures a human's revision of a
published digest, and the returned diff is the re-scoring signal: the added
Documents were under-weighted, the removed ones over-weighted.

``get_digest_picks(digest_date)`` reads the set back (each pick carries the
Document identity plus the ``reporter`` and ``picked_at`` timestamp), and
``clear_digest_picks(digest_date)`` removes the date's trace. Identifiers are
article URLs (or anything that canonicalizes to a stored canonical URL);
unknown identifiers raise ``ValueError`` and nothing is written.

Unlike the importance/topic lanes this table is a current set, not an
append-only history: a pick row means "this Document is a pick for that date
right now". See ``migrations/011_digest_picks.sql``.

Error contract: this lane raises ``ValueError``/``TypeError``/``LookupError``
only (unknown URL -> ``ValueError``); the service adapter maps those to
``InvalidRequest`` (HTTP 422).
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


from marketing_intelligence.normalize import canonicalize_url

REPORTER_MAX_LENGTH = 100

_DOCUMENT_SQL = """\
SELECT d.id, d.url, d.canonical_url
  FROM documents d
 WHERE d.url = %s OR d.canonical_url = %s
 LIMIT 1\
"""

_EXISTING_SQL = """\
SELECT p.document_id, d.url, d.canonical_url
  FROM digest_picks p
  JOIN documents d ON d.id = p.document_id
 WHERE p.digest_date = %s\
"""

_INSERT_SQL = """\
INSERT INTO digest_picks (digest_date, document_id, reporter, created_at)
VALUES (%s, %s, %s, now())
ON CONFLICT (digest_date, document_id)
DO UPDATE SET reporter = COALESCE(EXCLUDED.reporter, digest_picks.reporter)\
"""

_DELETE_SQL = """\
DELETE FROM digest_picks
 WHERE digest_date = %s AND document_id = ANY(%s::uuid[])\
"""

_CLEAR_SQL = """\
DELETE FROM digest_picks
 WHERE digest_date = %s\
"""

#: Read-back shape: Document identity (newest publication first, then URL)
#: plus who recorded the pick and when.
_PICKS_SQL = """\
SELECT d.url, d.canonical_url, d.title, s.name AS source, d.published_at,
       p.reporter, p.created_at
  FROM digest_picks p
  JOIN documents d ON d.id = p.document_id
  LEFT JOIN sources s ON s.id = d.source_id
 WHERE p.digest_date = %s
 ORDER BY d.published_at DESC NULLS LAST, d.url\
"""


def _to_iso_tz_aware(value: Any) -> Any:
    """Normalise a timestamp value to an isoformat tz-aware string."""
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


def _validate_digest_date(value: Any) -> _dt.date:
    """Accept a date/datetime or ISO date/datetime string; failures raise."""
    if isinstance(value, _dt.datetime):
        return value.date()
    if isinstance(value, _dt.date):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError(f"digest_date must be an ISO date, got {value!r}")
        try:
            return _dt.date.fromisoformat(text)
        except ValueError:
            pass
        try:
            return _dt.datetime.fromisoformat(text).date()
        except ValueError:
            raise ValueError(f"digest_date must be an ISO date, got {value!r}") from None
    raise TypeError(f"digest_date must be a date or ISO date string, got {type(value).__name__}")


def _validate_identifiers(identifiers: Any) -> list[str]:
    """Validate a non-empty-entries URL list (order-preserving, de-blanked)."""
    if isinstance(identifiers, (str, bytes)) or not isinstance(identifiers, (list, tuple)):
        raise TypeError(
            f"identifiers must be a list of article URLs, got {type(identifiers).__name__}"
        )
    cleaned: list[str] = []
    for item in identifiers:
        if not isinstance(item, str):
            raise TypeError(f"identifiers must be strings, got {type(item).__name__}")
        if not item.strip():
            raise ValueError("identifiers must be non-empty strings")
        cleaned.append(item.strip())
    return cleaned


def _validate_reporter(reporter: Any) -> Any:
    """Optional reporter tag, max 100 chars; failures raise TypeError/ValueError."""
    if reporter is None:
        return None
    if not isinstance(reporter, str):
        raise TypeError(f"reporter must be a string or null, got {type(reporter).__name__}")
    cleaned = reporter.strip() or None
    if cleaned is not None and len(cleaned) > REPORTER_MAX_LENGTH:
        raise ValueError(
            f"reporter must be at most {REPORTER_MAX_LENGTH} chars, got {len(cleaned)}"
        )
    return cleaned


def _canonical(key: str) -> str:
    try:
        return canonicalize_url(key)
    except Exception:
        return key


def _identity(url: Any, canonical_url: Any) -> Any:
    """Stable public identifier for diff output (canonical URL, URL fallback)."""
    return canonical_url or url


def _fetch_rows(conn: Any, sql: str, params: tuple) -> list[Any]:
    cursor = conn.cursor()
    try:
        cursor.execute(sql, params)
        return list(cursor.fetchall() or [])
    finally:
        close = getattr(cursor, "close", None)
        if callable(close):
            close()


def _execute(conn: Any, sql: str, params: tuple) -> None:
    cursor = conn.cursor()
    try:
        cursor.execute(sql, params)
    finally:
        close = getattr(cursor, "close", None)
        if callable(close):
            close()


def _row_identity(row: Any) -> tuple[Any, Any, Any]:
    """Return ``(document_id, url, canonical_url)`` from an existing-pick row."""
    if isinstance(row, dict):
        return row.get("document_id"), row.get("url"), row.get("canonical_url")
    items = tuple(row)
    canonical_url = items[2] if len(items) > 2 else None
    return items[0], items[1], canonical_url


def _resolve(conn: Any, key: str) -> tuple[Any, Any, Any]:
    """Resolve one caller identifier to ``(document_id, url, canonical_url)``."""
    rows = _fetch_rows(conn, _DOCUMENT_SQL, (key, _canonical(key)))
    if not rows:
        raise ValueError(f"unknown article: {key!r}")
    row = rows[0]
    if isinstance(row, dict):
        return row.get("id"), row.get("url"), row.get("canonical_url")
    items = tuple(row)
    canonical_url = items[2] if len(items) > 2 else None
    return items[0], items[1], canonical_url


def _pick_from_row(row: Any) -> dict[str, Any]:
    if isinstance(row, dict):
        url = row.get("url")
        canonical_url = row.get("canonical_url")
        title = row.get("title")
        source = row.get("source")
        published_at = row.get("published_at")
        reporter = row.get("reporter")
        created_at = row.get("created_at")
    else:
        items = tuple(row)
        url, canonical_url, title, source, published_at = items[:5]
        reporter = items[5] if len(items) > 5 else None
        created_at = items[6] if len(items) > 6 else None
    return {
        "url": url,
        "canonical_url": canonical_url,
        "title": title,
        "source": source,
        "published_at": _to_iso_tz_aware(published_at),
        "reporter": reporter,
        "picked_at": _to_iso_tz_aware(created_at),
    }


def _read_picks(conn: Any, digest_date: _dt.date) -> list[dict[str, Any]]:
    """Read one digest date's picks back as public dicts (newest first)."""
    return [_pick_from_row(row) for row in _fetch_rows(conn, _PICKS_SQL, (digest_date,))]


def get_digest_picks(digest_date: _dt.date | str, conn: Any | None = None) -> dict[str, Any]:
    """Read one digest date's pick set.

    Args:
        digest_date: digest edition date (``date`` or ISO ``YYYY-MM-DD`` string).
        conn: optional injected DB-API connection (fake-friendly). When None
            a connection is opened via `get_connection` and closed afterwards.

    Returns:
        ``{"digest_date": <ISO date>, "picks": [...]}`` where each pick carries
        ``url``, ``canonical_url``, ``title``, ``source``, ``published_at``,
        ``reporter`` and ``picked_at`` — newest publication first, ``[]`` when
        the date never picked anything.

    Raises:
        ValueError: malformed digest date.
        TypeError: non-date, non-string digest date.
    """
    date_value = _validate_digest_date(digest_date)

    owns_connection = conn is None
    if conn is None:
        conn = get_connection()
    assert conn is not None
    try:
        picks = _read_picks(conn, date_value)
    finally:
        if owns_connection:
            close_conn = getattr(conn, "close", None)
            if callable(close_conn):
                close_conn()
    return {"digest_date": date_value.isoformat(), "picks": picks}


def record_digest_picks(
    digest_date: _dt.date | str,
    identifiers: list[str],
    reporter: str | None = None,
    conn: Any | None = None,
) -> dict[str, Any]:
    """Reconcile one digest date's pick set to ``identifiers`` (idempotent).

    The stored set becomes exactly the Documents named by ``identifiers``:
    added Documents are inserted (a re-record of an existing pick does not
    duplicate it and keeps its original ``picked_at``), removed Documents are
    deleted. Recording the same set twice changes nothing the second time.

    The returned ``added``/``removed`` lists are the re-scoring signal: when a
    human edits a published digest, call this again with the edited URL set
    and the diff names the Documents whose importance was under- or
    over-weighted. An empty ``identifiers`` list reconciles the set to empty
    (equivalent to :func:`clear_digest_picks`, which states the intent).

    Args:
        digest_date: digest edition date (``date`` or ISO ``YYYY-MM-DD`` string).
        identifiers: article URLs (or canonicalizations thereof). Unknown
            identifiers raise before anything is written.
        reporter: optional reporter tag, max 100 chars.
        conn: optional injected DB-API connection (fake-friendly). When None
            a connection is opened via `get_connection` and closed afterwards;
            an injected connection is never committed or closed here.

    Returns:
        ``{"digest_date", "reporter", "picks", "added", "removed"}`` — the
        date, this recording's reporter, the read-back pick set, and the
        canonical identifiers added/removed by this call.

    Raises:
        ValueError: malformed date, blank identifier, unknown article.
        TypeError: bad date/identifiers/reporter type.
    """
    date_value = _validate_digest_date(digest_date)
    keys = _validate_identifiers(identifiers)
    clean_reporter = _validate_reporter(reporter)

    owns_connection = conn is None
    if conn is None:
        conn = get_connection()
    assert conn is not None
    try:
        incoming: dict[Any, Any] = {}
        for key in keys:
            document_id, url, canonical_url = _resolve(conn, key)
            incoming.setdefault(document_id, _identity(url, canonical_url))
        existing: dict[Any, Any] = {}
        for row in _fetch_rows(conn, _EXISTING_SQL, (date_value,)):
            document_id, url, canonical_url = _row_identity(row)
            existing.setdefault(document_id, _identity(url, canonical_url))

        added_ids = [doc for doc in incoming if doc not in existing]
        removed_ids = [doc for doc in existing if doc not in incoming]
        for document_id in added_ids:
            _execute(conn, _INSERT_SQL, (date_value, document_id, clean_reporter))
        if removed_ids:
            _execute(conn, _DELETE_SQL, (date_value, removed_ids))

        picks = _read_picks(conn, date_value)
        return {
            "digest_date": date_value.isoformat(),
            "reporter": clean_reporter,
            "picks": picks,
            "added": [incoming[doc] for doc in added_ids],
            "removed": [existing[doc] for doc in removed_ids],
        }
    finally:
        if owns_connection:
            commit = getattr(conn, "commit", None)
            if callable(commit):
                commit()
            close_conn = getattr(conn, "close", None)
            if callable(close_conn):
                close_conn()


def clear_digest_picks(digest_date: _dt.date | str, conn: Any | None = None) -> dict[str, Any]:
    """Remove one digest date's pick set (the trace for a discarded digest).

    Args:
        digest_date: digest edition date (``date`` or ISO ``YYYY-MM-DD`` string).
        conn: optional injected DB-API connection (fake-friendly). When None
            a connection is opened via `get_connection` and closed afterwards.

    Returns:
        ``{"digest_date", "cleared", "picks"}`` — the date, how many picks
        were removed (``0`` when the date had none), and the read-back set
        (always ``[]`` after a successful clear).

    Raises:
        ValueError: malformed digest date.
        TypeError: non-date, non-string digest date.
    """
    date_value = _validate_digest_date(digest_date)

    owns_connection = conn is None
    if conn is None:
        conn = get_connection()
    assert conn is not None
    try:
        cursor = conn.cursor()
        try:
            cursor.execute(_CLEAR_SQL, (date_value,))
            cleared = getattr(cursor, "rowcount", None)
        finally:
            close = getattr(cursor, "close", None)
            if callable(close):
                close()
        picks = _read_picks(conn, date_value)
        return {
            "digest_date": date_value.isoformat(),
            "cleared": cleared if isinstance(cleared, int) and cleared >= 0 else 0,
            "picks": picks,
        }
    finally:
        if owns_connection:
            commit = getattr(conn, "commit", None)
            if callable(commit):
                commit()
            close_conn = getattr(conn, "close", None)
            if callable(close_conn):
                close_conn()
