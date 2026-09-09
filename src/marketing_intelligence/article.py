"""One-item article lookup by URL (Ticket 01) — full text plus provenance.

Domain contract (stable): ``get_article(identifier)`` returns a single dict
with keys ``title, url, canonical_url, source, published_at, author,
content`` plus the Extraction Flag annotation (``flag_reason, flag_detail,
flagged_at, flagged_by`` — ``None`` when unflagged) — the full stored body
(clean Markdown/text, never a snippet).
Matching tries the exact URL first, then the canonical URL via
``marketing_intelligence.normalize.canonicalize_url``. Unknown identifiers raise
``ValueError`` (the service adapter maps it to ``InvalidRequest``).
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

_ARTICLE_BY_URL_SQL = """\
SELECT d.title, d.url, d.canonical_url, s.name AS source,
       d.published_at, d.author, d.content,
       d.flag_reason, d.flag_detail, d.flagged_at, d.flagged_by
  FROM documents d
  LEFT JOIN sources s ON s.id = d.source_id
 WHERE d.url = %s
 LIMIT 1\
"""

_ARTICLE_BY_CANONICAL_SQL = """\
SELECT d.title, d.url, d.canonical_url, s.name AS source,
       d.published_at, d.author, d.content,
       d.flag_reason, d.flag_detail, d.flagged_at, d.flagged_by
  FROM documents d
  LEFT JOIN sources s ON s.id = d.source_id
 WHERE d.canonical_url = %s
 LIMIT 1\
"""

_RESULT_KEYS = (
    "title",
    "url",
    "canonical_url",
    "source",
    "published_at",
    "author",
    "content",
    "flag_reason",
    "flag_detail",
    "flagged_at",
    "flagged_by",
)


def _to_iso_tz_aware(value: Any) -> Any:
    """Normalise a published_at value to an isoformat tz-aware string."""
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


def _row_to_dict(row: Any) -> dict[str, Any]:
    if isinstance(row, dict):
        title = row.get("title")
        url = row.get("url")
        canonical_url = row.get("canonical_url")
        source = row.get("source")
        published_at = row.get("published_at")
        author = row.get("author")
        content = row.get("content")
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
            content,
            flag_reason,
            flag_detail,
            flagged_at,
            flagged_by,
        ) = row
    return {
        "title": title,
        "url": url,
        "canonical_url": canonical_url,
        "source": source,
        "published_at": published_at,
        "author": author,
        "content": content,
        "flag_reason": flag_reason,
        "flag_detail": flag_detail,
        "flagged_at": flagged_at,
        "flagged_by": flagged_by,
    }


def _fetch_one(conn: Any, sql: str, params: tuple) -> Any | None:
    """Return the first row for a single-row lookup, or None on no match."""
    cursor = conn.cursor()
    try:
        cursor.execute(sql, params)
        rows = cursor.fetchall()
    finally:
        close = getattr(cursor, "close", None)
        if callable(close):
            close()
    if not rows:
        return None
    return rows[0]


def get_article(identifier: str, conn: Any | None = None) -> dict[str, Any]:
    """Fetch one article's full stored body plus provenance by URL.

    Args:
        identifier: exact article URL, or anything that canonicalizes to the
            stored canonical URL (query/fragment stripped, scheme+host
            lowercased). Blank/non-string identifiers raise ``ValueError``.
        conn: optional injected DB-API connection (fake-friendly). When None
            a connection is opened via `get_connection` and closed afterwards;
            an injected connection is never committed or closed here.

    Returns:
        Dict with keys ``title, url, canonical_url, source, published_at,
        author, content`` plus ``flag_reason, flag_detail, flagged_at,
        flagged_by`` (``None`` when unflagged). ``published_at`` and a set
        ``flagged_at`` are isoformat tz-aware strings; ``content`` is the
        full stored body (never a snippet).

    Raises:
        ValueError: blank identifier or no stored article matches.
    """
    if not isinstance(identifier, str) or not identifier.strip():
        raise ValueError(f"identifier must be a non-empty string, got {identifier!r}")
    key = identifier.strip()

    owns_connection = conn is None
    if conn is None:
        conn = get_connection()
    assert conn is not None
    try:
        row = _fetch_one(conn, _ARTICLE_BY_URL_SQL, (key,))
        if row is None:
            try:
                canonical = canonicalize_url(key)
            except Exception:
                canonical = key
            row = _fetch_one(conn, _ARTICLE_BY_CANONICAL_SQL, (canonical,))
        if row is None:
            raise ValueError(f"unknown article: {key!r}")
    finally:
        if owns_connection:
            close_conn = getattr(conn, "close", None)
            if callable(close_conn):
                close_conn()

    item = _row_to_dict(row)
    item["published_at"] = _to_iso_tz_aware(item["published_at"])
    item["flagged_at"] = _to_iso_tz_aware(item["flagged_at"])
    return {k: item[k] for k in _RESULT_KEYS}
