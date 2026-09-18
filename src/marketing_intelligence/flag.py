"""Extraction Flag lane (ADR-0005) — extraction quality markers.

Markers are filed by agents (``flag_extraction``) or by the ingestion pipeline
itself: when a document's whole article-content chain fails, the thin RSS body
is stored carrying the terminal ``unrecoverable`` reason with the chained cause
and :data:`SYSTEM_REPORTER` as the reporter (see ``flag_unrecoverable``). Both
lanes share one storage contract.

Domain contract (stable): ``flag_extraction(identifier, reason, detail,
flagged_by, clear)`` sets (or, with ``clear=True``, NULLs) the nullable
``flag_reason, flag_detail, flagged_at, flagged_by`` columns on ``documents``
and returns the updated article dict (``ARTICLE_KEYS`` + the four flag keys,
as produced by ``marketing_intelligence.article.get_article``). Re-flag overwrites — the
current schema keeps no history table. Flags survive re-ingest (ingestion upserts are
``ON CONFLICT DO NOTHING``; no flow changes here).

Error contract: this lane raises ``ValueError``/``TypeError``/``LookupError``
only (unknown URL -> ``ValueError``); the service adapter maps those to
``InvalidRequest``.
"""

from __future__ import annotations

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

FLAG_REASONS = (
    "thin",
    "js_shell",
    "paywall_challenge",
    "truncated",
    "wrong_body",
    "unrecoverable",
    "other",
)

#: Reporter tag for flags filed by the ingestion pipeline itself (never an agent):
#: the terminal ``unrecoverable`` marker on documents whose whole article-content
#: chain failed. Agents clear it through the normal flag tool once they verify.
SYSTEM_REPORTER = "system"

DETAIL_MAX_LENGTH = 2000
FLAGGED_BY_MAX_LENGTH = 100

_FLAG_SET_SQL = """\
UPDATE documents
   SET flag_reason = %s, flag_detail = %s, flagged_at = now(), flagged_by = %s
 WHERE url = %s OR canonical_url = %s\
 """

_FLAG_CLEAR_SQL = """\
UPDATE documents
   SET flag_reason = NULL, flag_detail = NULL, flagged_at = NULL, flagged_by = NULL
 WHERE url = %s OR canonical_url = %s\
 """


def _execute(conn: Any, sql: str, params: tuple) -> int:
    """Run a flag UPDATE; return the matched-row count (0 when unknown).

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


def _validate(
    reason: Any,
    detail: Any,
    flagged_by: Any,
    *,
    clear: bool,
) -> tuple[Any, Any, Any]:
    """Light lane-side validation; failures raise ValueError/TypeError."""
    if clear:
        return None, None, None
    if not isinstance(reason, str) or reason.strip() not in FLAG_REASONS:
        raise ValueError(f"flag reason must be one of {list(FLAG_REASONS)}, got {reason!r}")
    clean_reason = reason.strip()
    if detail is not None and not isinstance(detail, str):
        raise TypeError(f"flag detail must be a string or null, got {type(detail).__name__}")
    if isinstance(detail, str) and len(detail) > DETAIL_MAX_LENGTH:
        raise ValueError(
            f"flag detail must be at most {DETAIL_MAX_LENGTH} chars, got {len(detail)}"
        )
    if clean_reason == "other" and (not isinstance(detail, str) or not detail.strip()):
        raise ValueError("flag reason 'other' requires a non-blank detail")
    if flagged_by is not None and not isinstance(flagged_by, str):
        raise TypeError(f"flagged_by must be a string or null, got {type(flagged_by).__name__}")
    clean_by: Any = None
    if isinstance(flagged_by, str):
        clean_by = flagged_by.strip() or None
        if clean_by is not None and len(clean_by) > FLAGGED_BY_MAX_LENGTH:
            raise ValueError(
                f"flagged_by must be at most {FLAGGED_BY_MAX_LENGTH} chars, got {len(clean_by)}"
            )
    return clean_reason, detail, clean_by


def flag_extraction(
    identifier: str,
    reason: str | None = None,
    detail: str | None = None,
    flagged_by: str | None = None,
    clear: bool = False,
    conn: Any | None = None,
) -> dict[str, Any]:
    """Flag (or unflag with ``clear=True``) one article's extraction quality.

    Args:
        identifier: exact article URL, or anything that canonicalizes to the
            stored canonical URL. Blank/non-string identifiers raise
            ``ValueError``.
        reason: one of ``FLAG_REASONS`` (ignored when ``clear`` is True).
        detail: free-text note, max 2000 chars; required non-blank when
            ``reason`` is ``"other"`` (ignored when ``clear`` is True).
        flagged_by: optional reporter tag, max 100 chars (ignored and nulled
            when ``clear`` is True).
        clear: when True, NULL the four flag columns and update nothing else.
        conn: optional injected DB-API connection (fake-friendly). When None
            a connection is opened via `get_connection` and closed afterwards;
            an injected connection is never committed or closed here.

    Returns:
        The updated article dict (same keys as ``marketing_intelligence.article.get_article``,
        flag fields included).

    Raises:
        ValueError: blank identifier, unknown article, bad reason/detail.
        TypeError: non-string detail/flagged_by.
    """
    if not isinstance(identifier, str) or not identifier.strip():
        raise ValueError(f"identifier must be a non-empty string, got {identifier!r}")
    key = identifier.strip()
    try:
        canonical = canonicalize_url(key)
    except Exception:
        canonical = key

    clean_reason, clean_detail, clean_by = _validate(reason, detail, flagged_by, clear=clear)

    owns_connection = conn is None
    if conn is None:
        conn = get_connection()
    assert conn is not None
    try:
        if clear:
            matched = _execute(conn, _FLAG_CLEAR_SQL, (key, canonical))
        else:
            matched = _execute(
                conn, _FLAG_SET_SQL, (clean_reason, clean_detail, clean_by, key, canonical)
            )
        if matched == 0:
            raise ValueError(f"unknown article: {key!r}")
        return _article.get_article(key, conn=conn)
    finally:
        if owns_connection:
            commit = getattr(conn, "commit", None)
            if callable(commit):
                commit()
            close_conn = getattr(conn, "close", None)
            if callable(close_conn):
                close_conn()


def flag_unrecoverable(
    identifier: str,
    detail: str | None = None,
    conn: Any | None = None,
) -> dict[str, Any]:
    """File the terminal system flag on an article whose content chain failed.

    Convenience over :func:`flag_extraction` with ``reason="unrecoverable"`` and
    ``flagged_by=SYSTEM_REPORTER``: the ingestion pipeline calls this once the
    saved row exists (the flag is an UPDATE, so it must run after the upsert),
    passing the chained failure cause — every chain leg named once. ``detail``
    is collapsed and truncated to :data:`DETAIL_MAX_LENGTH` so a long chained
    cause can never fail validation.

    Raises the same ``ValueError``/``TypeError`` as :func:`flag_extraction`
    (unknown URL included); the caller decides whether a failure is tolerable.
    """
    clean_detail: str | None = None
    if detail is not None:
        clean_detail = " ".join(str(detail).split())[:DETAIL_MAX_LENGTH] or None
    return flag_extraction(
        identifier,
        reason="unrecoverable",
        detail=clean_detail,
        flagged_by=SYSTEM_REPORTER,
        conn=conn,
    )
