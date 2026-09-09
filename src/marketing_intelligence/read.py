"""Read State lane — explicit read/unread markers on documents.

Domain contract (stable): ``mark_article_read(identifier, read_by,
clear)`` sets (or, with ``clear=True``, NULLs) the nullable ``read_at,
read_by`` columns on ``documents`` and returns the article dict produced by
``marketing_intelligence.article.get_article`` plus the ``read_at, read_by``
keys (``None`` when unread). Re-mark overwrites — idempotent. Read state
survives re-ingest (ingestion upserts are ``ON CONFLICT DO NOTHING``; no
flow changes here).

Error contract: this lane raises ``ValueError``/``TypeError`` only
(unknown URL -> ``ValueError``); the service adapter maps those to
``InvalidRequest``.
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


from marketing_intelligence import article as _article
from marketing_intelligence.normalize import canonicalize_url

READ_BY_MAX = 100

_READ_SET_SQL = """\
UPDATE documents
   SET read_at = now(), read_by = %s
 WHERE url = %s OR canonical_url = %s\
 """

_READ_CLEAR_SQL = """\
UPDATE documents
   SET read_at = NULL, read_by = NULL
 WHERE url = %s OR canonical_url = %s\
 """

_READ_FETCH_SQL = """\
SELECT read_at, read_by FROM documents
 WHERE url = %s OR canonical_url = %s\
 """


def _execute(conn: Any, sql: str, params: tuple) -> int:
    """Run a read UPDATE; return the matched-row count (0 when unknown).

    Reads ``cursor.rowcount`` directly — an UPDATE returns no rows, so
    fetching (as real psycopg raises ``no results to fetch``) is an error.
    """
    cursor = conn.cursor()
    try:
        cursor.execute(sql, params)
        rowcount = cursor.rowcount
    finally:
        close = getattr(cursor, "close", None)
        if callable(close):
            close()
    try:
        return int(rowcount)
    except (TypeError, ValueError):
        return -1


def _fetch_read_state(conn: Any, key: str, canonical: str) -> tuple[Any, Any]:
    """Return the stored (read_at, read_by) pair for one document."""
    cursor = conn.cursor()
    try:
        cursor.execute(_READ_FETCH_SQL, (key, canonical))
        rows = cursor.fetchall()
    finally:
        close = getattr(cursor, "close", None)
        if callable(close):
            close()
    if not rows:
        return None, None
    row = rows[0]
    if isinstance(row, dict):
        return row.get("read_at"), row.get("read_by")
    return row[0], row[1]


def _to_iso_tz_aware(value: Any) -> Any:
    """Normalise a read_at value to an isoformat tz-aware string."""
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


def _validate_read_by(read_by: Any, *, clear: bool) -> Any:
    """Light lane-side validation; failures raise ValueError/TypeError."""
    if clear:
        return None
    if read_by is None:
        return None
    if not isinstance(read_by, str):
        raise TypeError(f"read_by must be a string or null, got {type(read_by).__name__}")
    clean_by = read_by.strip() or None
    if clean_by is not None and len(clean_by) > READ_BY_MAX:
        raise ValueError(f"read_by must be at most {READ_BY_MAX} chars, got {len(clean_by)}")
    return clean_by


def mark_article_read(
    identifier: str,
    read_by: str | None = None,
    clear: bool = False,
    conn: Any | None = None,
) -> dict[str, Any]:
    """Mark one article read (or unread with ``clear=True``).

    Args:
        identifier: exact article URL, or anything that canonicalizes to the
            stored canonical URL. Blank/non-string identifiers raise
            ``ValueError``.
        read_by: optional reader tag, max 100 chars (ignored and nulled
            when ``clear`` is True).
        clear: when True, NULL the two read columns and update nothing else.
        conn: optional injected DB-API connection (fake-friendly). When None
            a connection is opened via `get_connection` and closed afterwards;
            an injected connection is never committed or closed here.

    Returns:
        The updated article dict (same keys as ``marketing_intelligence.article.get_article``
        plus ``read_at, read_by``).

    Raises:
        ValueError: blank identifier, unknown article, bad read_by.
        TypeError: non-string read_by.
    """
    if not isinstance(identifier, str) or not identifier.strip():
        raise ValueError(f"identifier must be a non-empty string, got {identifier!r}")
    key = identifier.strip()
    try:
        canonical = canonicalize_url(key)
    except Exception:
        canonical = key

    clean_by = _validate_read_by(read_by, clear=clear)

    owns_connection = conn is None
    if conn is None:
        conn = get_connection()
    assert conn is not None
    try:
        if clear:
            matched = _execute(conn, _READ_CLEAR_SQL, (key, canonical))
        else:
            matched = _execute(conn, _READ_SET_SQL, (clean_by, key, canonical))
        if matched == 0:
            raise ValueError(f"unknown article: {key!r}")
        base = _article.get_article(key, conn=conn)
        read_at, stored_by = _fetch_read_state(conn, key, canonical)
        base["read_at"] = _to_iso_tz_aware(read_at)
        base["read_by"] = stored_by
        base["read"] = base["read_at"] is not None
        return base
    finally:
        if owns_connection:
            commit = getattr(conn, "commit", None)
            if callable(commit):
                commit()
            close_conn = getattr(conn, "close", None)
            if callable(close_conn):
                close_conn()
