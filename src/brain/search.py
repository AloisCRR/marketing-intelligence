"""Keyword search over ingested articles with source provenance.

Domain contract (stable): ``search_articles(keyword, limit)`` returns a list
of dicts with keys ``title, url, canonical_url, source, published_at,
author, snippet`` — no raw SQL exposure beyond this function.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

try:  # foundation seam (preferred)
    from brain.db import get_connection  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - defensive fallback when absent

    def get_connection() -> Any:  # type: ignore[misc]
        raise RuntimeError(
            "No database connection available: brain.db.get_connection "
            "is missing and no fallback is configured."
        )


_SEARCH_SQL = """\
SELECT d.title, d.url, d.canonical_url, s.name AS source,
       d.published_at, d.author, d.content
  FROM documents d
  LEFT JOIN sources s ON s.id = d.source_id
 WHERE d.title ILIKE %s OR d.content ILIKE %s
 ORDER BY d.published_at DESC
 LIMIT %s\
"""

_SNIPPET_RADIUS = 120

_RESULT_KEYS = (
    "title",
    "url",
    "canonical_url",
    "source",
    "published_at",
    "author",
    "snippet",
)


def _to_iso_tz_aware(value: Any) -> Any:
    """Normalise a published_at value to an isoformat tz-aware string."""
    if value is None:
        return None
    if isinstance(value, _dt.datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=_dt.timezone.utc)
        return value.isoformat()
    if isinstance(value, str):
        try:
            parsed = _dt.datetime.fromisoformat(value)
        except ValueError:
            return value
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=_dt.timezone.utc)
            return parsed.isoformat()
        return value
    return value


def _snippet(content: Any, title: Any, keyword: str) -> str:
    text = content if isinstance(content, str) and content else (title or "")
    if not isinstance(text, str) or not text:
        return ""
    idx = text.lower().find(keyword.lower())
    if idx == -1:
        excerpt = text[: 2 * _SNIPPET_RADIUS]
        return excerpt + ("…" if len(text) > len(excerpt) else "")
    start = max(0, idx - _SNIPPET_RADIUS)
    end = min(len(text), idx + len(keyword) + _SNIPPET_RADIUS)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return prefix + text[start:end].strip() + suffix


def _row_to_dict(row: Any) -> dict[str, Any]:
    if isinstance(row, dict):
        title = row.get("title")
        url = row.get("url")
        canonical_url = row.get("canonical_url")
        source = row.get("source")
        published_at = row.get("published_at")
        author = row.get("author")
        content = row.get("content")
    else:
        title, url, canonical_url, source, published_at, author, content = row
    return {
        "title": title,
        "url": url,
        "canonical_url": canonical_url,
        "source": source,
        "published_at": published_at,
        "author": author,
        "_content": content,
    }


def search_articles(keyword: str, limit: int = 20) -> list[dict[str, Any]]:
    """Search ingested articles by keyword, newest first.

    Args:
        keyword: matched (case-insensitively) against title and content.
        limit: maximum number of results.

    Returns:
        List of dicts with keys ``title, url, canonical_url, source,
        published_at, author, snippet``. ``published_at`` is an isoformat
        tz-aware string; ``author`` may be ``None``; ``snippet`` is a
        content excerpt around the match. Empty/blank keyword returns ``[]``.
    """
    if not isinstance(keyword, str) or not keyword.strip():
        return []
    if not isinstance(limit, int) or limit <= 0:
        return []

    term = keyword.strip()
    pattern = f"%{term}%"
    params = (pattern, pattern, limit)

    conn = get_connection()
    try:
        cursor = conn.cursor()
        try:
            cursor.execute(_SEARCH_SQL, params)
            rows = cursor.fetchall()
        finally:
            close = getattr(cursor, "close", None)
            if callable(close):
                close()
    finally:
        close_conn = getattr(conn, "close", None)
        if callable(close_conn):
            close_conn()

    results: list[dict[str, Any]] = []
    for row in rows or []:
        item = _row_to_dict(row)
        content = item.pop("_content")
        item["published_at"] = _to_iso_tz_aware(item["published_at"])
        item["snippet"] = _snippet(content, item["title"], term)
        results.append({k: item[k] for k in _RESULT_KEYS})
    return results
