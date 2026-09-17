"""Read State lane — per-reader read/unread marks on documents.

Domain contract (stable): ``mark_article_read(identifier, read_by,
clear)`` writes the ``document_reads`` set (one row per reader, keyed
``(document_id, reader)``) and refreshes the ``documents.read_at, read_by``
latest-mark cache in the same transaction, then returns the article dict
produced by ``marketing_intelligence.article.get_article`` plus the
``read_at, read_by`` keys (``None`` when unread). A second reader's mark never
overwrites the first's row; re-marking the same reader refreshes only its own
``read_at`` — idempotent. ``clear=True`` drops one reader's row when
``read_by`` is given (no-op success when that reader never marked, with the
latest-mark cache left exactly as it was — a per-reader clear only re-caches
when it actually removed a row) or every reader's row, then re-caches the
newest remaining mark (NULL/NULL when the set is empty). Legacy single-slot
marks (``documents.read_at`` set, no ``document_reads`` row) still read as read
from the cache until re-marked.
Read state survives re-ingest (ingestion upserts are ``ON CONFLICT DO
NOTHING``; no flow changes here).

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

#: Identity resolution (ADR-0013 keying: url then canonical_url). Runs before
#: any write, so an unknown identifier can never leave a partial mark.
_READ_DOCUMENT_SQL = """\
SELECT id FROM documents
 WHERE url = %s OR canonical_url = %s
 LIMIT 1\
"""

#: One row per (document, reader); re-marking upserts in place. RETURNING gives
#: back the timestamp actually written, so the cache below mirrors the row
#: exactly instead of re-deriving it from a possible now() tie.
_READ_MARK_SQL = """\
INSERT INTO document_reads (document_id, reader, read_at)
VALUES (%s, %s, now())
ON CONFLICT (document_id, reader)
DO UPDATE SET read_at = now()
RETURNING read_at\
"""

_READ_CLEAR_READER_SQL = """\
DELETE FROM document_reads
 WHERE document_id = %s AND reader = %s\
"""

_READ_CLEAR_ALL_SQL = """\
DELETE FROM document_reads
 WHERE document_id = %s\
"""

#: Newest remaining mark, used to rebuild the cache after a delete.
_READ_LATEST_SQL = """\
SELECT read_at, reader FROM document_reads
 WHERE document_id = %s
 ORDER BY read_at DESC, reader
 LIMIT 1\
"""

#: Latest-mark cache (NULL/NULL when no reader has the document marked). Read
#: side (`article` / `search` / `period`) keeps reading these two columns.
_READ_CACHE_SQL = """\
UPDATE documents
   SET read_at = %s, read_by = %s
 WHERE id = %s\
"""

_READ_FETCH_SQL = """\
SELECT read_at, read_by FROM documents
 WHERE url = %s OR canonical_url = %s\
 """


def _fetch_one(conn: Any, sql: str, params: tuple) -> Any:
    """Run a SELECT expected to yield at most one row; ``None`` when empty."""
    cursor = conn.cursor()
    try:
        cursor.execute(sql, params)
        rows = cursor.fetchall()
    finally:
        close = getattr(cursor, "close", None)
        if callable(close):
            close()
    return rows[0] if rows else None


#: Affected-row count assumed when a driver reports none (``cursor.rowcount``
#: missing, ``None`` or non-int): the pre-guard behaviour, as in `db`/`ingest`.
_ROWS_ASSUMED = 1


def _run(conn: Any, sql: str, params: tuple) -> int:
    """Run a write statement; return its affected-row count.

    ``cursor.rowcount`` is read before the cursor closes — real drivers refuse
    attribute access afterwards (same read as `flag._execute`). Drivers that
    report no count fall back to ``_ROWS_ASSUMED``.
    """
    cursor = conn.cursor()
    try:
        cursor.execute(sql, params)
        count = getattr(cursor, "rowcount", None)
    finally:
        close = getattr(cursor, "close", None)
        if callable(close):
            close()
    return count if isinstance(count, int) else _ROWS_ASSUMED


def _field(row: Any, column: str, index: int) -> Any:
    """Extract one column from a dict-or-tuple row (``None`` when no row)."""
    if row is None:
        return None
    if isinstance(row, dict):
        return row.get(column)
    return row[index]


def _resolve_document_id(conn: Any, key: str, canonical: str) -> Any:
    """Resolve one caller identifier to its Document id.

    Raises:
        ValueError: no Document matches the URL or its canonical form.
    """
    document_id = _field(_fetch_one(conn, _READ_DOCUMENT_SQL, (key, canonical)), "id", 0)
    if document_id is None:
        raise ValueError(f"unknown article: {key!r}")
    return document_id


def _latest_mark(conn: Any, document_id: Any) -> tuple[Any, Any]:
    """Newest remaining mark for one document: ``(read_at, reader)``."""
    row = _fetch_one(conn, _READ_LATEST_SQL, (document_id,))
    return _field(row, "read_at", 0), _field(row, "reader", 1)


def _write_cache(conn: Any, document_id: Any, read_at: Any, reader: Any) -> None:
    """Point the documents latest-mark cache at one mark (or NULL/NULL)."""
    _run(conn, _READ_CACHE_SQL, (read_at, reader, document_id))


def _fetch_read_state(conn: Any, key: str, canonical: str) -> tuple[Any, Any]:
    """Return the cached ``(read_at, read_by)`` pair for one document."""
    row = _fetch_one(conn, _READ_FETCH_SQL, (key, canonical))
    return _field(row, "read_at", 0), _field(row, "read_by", 1)


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
    """Light lane-side validation; failures raise ValueError/TypeError.

    A mark needs a reader (``None``/blank -> ``ValueError``); a clear takes an
    optional reader, a blank one meaning "every reader". Non-string and
    overlong readers are rejected on both paths, mirroring the service
    adapter (which 422s them before the lane is reached).
    """
    if read_by is not None and not isinstance(read_by, str):
        raise TypeError(f"read_by must be a string or null, got {type(read_by).__name__}")
    clean_by = read_by.strip() if isinstance(read_by, str) else None
    clean_by = clean_by or None
    if clean_by is not None and len(clean_by) > READ_BY_MAX:
        raise ValueError(f"read_by must be at most {READ_BY_MAX} chars, got {len(clean_by)}")
    if clear:
        return clean_by
    if clean_by is None:
        raise ValueError("read_by is required to mark an article read")
    return clean_by


def mark_article_read(
    identifier: str,
    read_by: str | None = None,
    clear: bool = False,
    conn: Any | None = None,
) -> dict[str, Any]:
    """Mark one article read for one reader (or clear marks with ``clear=True``).

    The write is a ``document_reads`` upsert/delete plus a refresh of the
    ``documents.read_at, read_by`` latest-mark cache, all on one connection
    (the injected connection is never committed or closed here).

    Args:
        identifier: exact article URL, or anything that canonicalizes to the
            stored canonical URL. Blank/non-string identifiers raise
            ``ValueError``.
        read_by: the reader the mark belongs to, max 100 chars. Required when
            marking (blank/``None`` -> ``ValueError``); with ``clear=True`` it
            selects the single reader row to drop, and is absent/blank to drop
            every reader's row.
        clear: when True, remove marks instead of adding one.
        conn: optional injected DB-API connection (fake-friendly). When None
            a connection is opened via `get_connection` and closed afterwards;
            an injected connection is never committed or closed here.

    Returns:
        The updated article dict (same keys as ``marketing_intelligence.article.get_article``
        plus ``read_at, read_by`` read back from the documents cache, i.e. the
        newest remaining mark).

    Raises:
        ValueError: blank identifier, unknown article, missing/blank/overlong read_by.
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
        document_id = _resolve_document_id(conn, key, canonical)
        if clear:
            if clean_by is None:
                _run(conn, _READ_CLEAR_ALL_SQL, (document_id,))
                _write_cache(conn, document_id, None, None)
            else:
                removed = _run(conn, _READ_CLEAR_READER_SQL, (document_id, clean_by))
                # Clear is a no-op success when that reader never marked: the
                # cache then still describes a mark this delete did not touch
                # (a legacy single-slot mark has no `document_reads` row to
                # rebuild from), so rewriting it would flip a read document
                # unread without a re-mark.
                if removed > 0:
                    _write_cache(conn, document_id, *_latest_mark(conn, document_id))
        else:
            marked = _fetch_one(conn, _READ_MARK_SQL, (document_id, clean_by))
            _write_cache(conn, document_id, _field(marked, "read_at", 0), clean_by)
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
