"""RSS retrieval, feed parsing, and idempotent persistence.

- Retrieval is plain HTTP (timeout + UA). No Firecrawl (per CONTEXT.md cuts).
- Parsing accepts RSS or Atom and produces the normalized contract.
- Persistence is rerun-safe: `ON CONFLICT (url) DO NOTHING` plus per-item
  error capture, so a repeated run inserts nothing new and one bad row never
  aborts the batch. (`content_hash` has its own UNIQUE guard in the schema;
  hash collisions surface as skipped rows via the same per-item handling.)
"""

from __future__ import annotations

import urllib.request
from datetime import datetime, timezone
from typing import Any

import feedparser
from dateutil import parser as date_parser

from brain.db import get_connection
from brain.normalize import NormalizedDocument, coerce_tz_aware, make_document

USER_AGENT = "TrendIntelligenceBrain/1.0 (+rss-ingest; local)"
DEFAULT_SOURCE = "Social Media Today"

INSERT_SQL = """\
INSERT INTO documents
  (source_id, url, canonical_url, title, author,
   published_at, retrieved_at, language, content, content_hash)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (url) DO NOTHING\
"""

SOURCE_ID_SQL = "SELECT id FROM sources WHERE name = %s"


def fetch_rss(url: str, timeout: int = 30) -> bytes:
    """Download a feed over plain HTTP. Raises on network/HTTP failure."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return bytes(response.read())


def _published_at(entry: Any, fallback: datetime) -> datetime:
    raw = entry.get("published") or entry.get("updated") or ""
    if not raw:
        return fallback
    try:
        return coerce_tz_aware(date_parser.parse(str(raw)))
    except (ValueError, OverflowError, TypeError):
        return fallback


def parse_feed(xml: bytes | str, source: str = DEFAULT_SOURCE) -> list[NormalizedDocument]:
    """Parse RSS/Atom bytes into normalized documents.

    Malformed items (missing title/link, unparseable structure) are skipped
    individually — partial failure is explicit, never a batch abort. A
    totally unparseable feed raises ValueError.
    """
    payload = xml.decode("utf-8", errors="replace") if isinstance(xml, bytes) else xml
    feed = feedparser.parse(payload)
    if feed.bozo and not feed.entries:
        raise ValueError(f"Unparseable feed: {feed.bozo_exception!r}")
    retrieved_at = datetime.now(timezone.utc)
    docs: list[NormalizedDocument] = []
    for entry in feed.entries:
        try:
            title = (entry.get("title") or "").strip()
            link = (entry.get("link") or "").strip()
            if not title or not link:
                continue
            content_parts: list[str] = []
            for block in entry.get("content", []) or []:
                value = block.get("value") if isinstance(block, dict) else None
                if value:
                    content_parts.append(str(value))
            body = "\n".join(content_parts) or str(
                entry.get("summary") or entry.get("description") or ""
            )
            author = (entry.get("author") or "").strip() or None
            docs.append(
                make_document(
                    source=source,
                    url=link,
                    title=title,
                    content=body,
                    author=author,
                    published_at=_published_at(entry, retrieved_at),
                    retrieved_at=retrieved_at,
                )
            )
        except Exception:
            continue
    return docs


def upsert_documents(
    docs: list[NormalizedDocument], conn: Any | None = None
) -> tuple[int, int]:
    """Persist documents idempotently. Returns (inserted, skipped).

    When `conn` is None a connection is opened via `brain.db.get_connection`
    (caller may inject any DB-API connection — fakes welcome in tests).
    """
    if not docs:
        return (0, 0)
    owns_connection = False
    if conn is None:
        conn = get_connection()
        owns_connection = True
    assert conn is not None
    try:
        try:
            cursor = conn.execute(SOURCE_ID_SQL, (docs[0].source,))
            row = cursor.fetchone()
            source_id = row[0] if row else None
        except Exception:
            source_id = None
        inserted = 0
        skipped = 0
        for doc in docs:
            try:
                cursor = conn.execute(
                    INSERT_SQL,
                    (
                        source_id,
                        doc.url,
                        doc.canonical_url,
                        doc.title,
                        doc.author,
                        doc.published_at,
                        doc.retrieved_at,
                        doc.language,
                        doc.content,
                        doc.content_hash,
                    ),
                )
                if cursor is not None and getattr(cursor, "rowcount", 1) == 0:
                    skipped += 1
                else:
                    inserted += 1
            except Exception:
                skipped += 1
        conn.commit()
        return (inserted, skipped)
    finally:
        if owns_connection:
            try:
                conn.close()
            except Exception:
                pass
