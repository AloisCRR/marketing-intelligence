"""RSS retrieval, feed parsing, and idempotent persistence.

- Retrieval is plain HTTP (timeout + UA). No Firecrawl (per CONTEXT.md cuts).
- Parsing accepts RSS or Atom and produces the normalized contract.
- Persistence is rerun-safe: `ON CONFLICT DO NOTHING` (no conflict target,
  so url, canonical_url, and content_hash collisions all collapse) plus
  per-item error capture, so a repeated run inserts nothing new and one bad
  row never aborts the batch.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import feedparser
from dateutil import parser as date_parser

from brain.db import get_connection
from brain.normalize import NormalizedDocument, coerce_tz_aware, make_document

USER_AGENT = "TrendIntelligenceBrain/1.0 (+rss-ingest; local)"
DEFAULT_SOURCE = "Social Media Today"

try:  # optional impersonated-feed backend (mirrors brain.enrich primary)
    from curl_cffi import requests as _curl_cffi_requests
except Exception:  # pragma: no cover - stdlib-only environments
    _curl_cffi_requests = None  # type: ignore[assignment]

#: Allowed feed retrieval policies (validated in fetch_rss; see brain.sources).
_FEED_POLICIES: tuple[str, ...] = ("stdlib-only", "impersonated-feed")

#: Markers identifying bot/challenge protection in a failed feed fetch.
_FEED_CHALLENGE_MARKERS: tuple[str, ...] = (
    "captcha",
    "challenge",
    "cloudflare",
    "just a moment",
    "verify you are",
    "are you human",
    "datadome",
    "perimeterx",
    "akamai",
    "incapsula",
    "kasada",
    "access denied",
    "enable javascript",
)

#: Error-body bytes inspected for challenge evidence on the stdlib attempt.
_FEED_ERROR_BODY_CAP = 65536

INSERT_SQL = """\
INSERT INTO documents
  (source_id, url, canonical_url, title, author,
   published_at, retrieved_at, language, content, content_hash)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT DO NOTHING\
"""

SOURCE_ID_SQL = "SELECT id FROM sources WHERE name = %s"


def _policy_for_feed_url(url: str) -> str:
    """Resolve the retrieval policy for a feed URL via the source registry.

    Matches the URL against registry `rss_url` values and returns that
    source's policy; unregistered URLs (and any lookup failure) yield
    "stdlib-only". Never raises.
    """
    try:
        from brain.sources import get_retrieval_policy, list_sources

        for entry in list_sources():
            if entry.get("rss_url") == url:
                policy = get_retrieval_policy(str(entry.get("name", ""))).get("policy")
                if policy in _FEED_POLICIES:
                    return str(policy)
                return "stdlib-only"
    except Exception:
        pass
    return "stdlib-only"


def _fetch_feed_stdlib(url: str, timeout: int = 30) -> bytes:
    """Download a feed over plain HTTP. Raises on network/HTTP failure."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return bytes(response.read())


def _feed_blocked(status: int | None, snippet: str) -> bool:
    """True when a failed stdlib attempt carries bot/challenge evidence.

    Any 403 qualifies; other statuses need a challenge marker in the captured
    body snippet. Deliberately specific: a plain 404/500 with no challenge
    evidence stays an explicit fetch error without impersonated retry traffic.
    """
    if status == 403:
        return True
    haystack = snippet.lower()
    return any(marker in haystack for marker in _FEED_CHALLENGE_MARKERS)


def _fetch_feed_impersonated(url: str, timeout: int = 30) -> bytes:
    """GET `url` with curl_cffi Chrome impersonation plus a browser identity."""
    from brain.enrich import BROWSER_USER_AGENT  # canonical browser identity

    assert _curl_cffi_requests is not None  # guarded by fetch_rss
    headers = {
        "User-Agent": BROWSER_USER_AGENT,
        "Accept": "application/rss+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.7",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        response = _curl_cffi_requests.get(
            url, impersonate="chrome", headers=headers, timeout=timeout
        )
    except Exception as exc:
        raise RuntimeError(
            f"fetch failed for {url} (retrieval policy=impersonated-feed): {exc}"
        ) from exc
    body = bytes(response.content or b"")
    status = int(response.status_code)
    if status >= 400:
        snippet = body[:512].decode("utf-8", errors="replace").strip()
        detail = f"HTTP Error {status}"
        if snippet:
            detail += f" — body: {snippet[:512]}"
        raise RuntimeError(f"fetch failed for {url} (retrieval policy=impersonated-feed): {detail}")
    return body


def fetch_rss(url: str, timeout: int = 30, policy: str | None = None) -> bytes:
    """Download a feed over plain HTTP. Raises on network/HTTP failure.

    `policy` selects the retrieval lane: "stdlib-only" keeps the current
    plain-urllib behavior; "impersonated-feed" tries stdlib first and, only
    when that attempt meets 403/challenge evidence, retries once with curl_cffi
    Chrome impersonation — any other failure is an explicit fetch error.
    None resolves the policy from the source registry by feed URL
    (unregistered URLs default to stdlib-only); unknown policy values fall
    back to stdlib-only. Never raises on bad policy input.
    """
    if policy in _FEED_POLICIES:
        effective = str(policy)
    elif policy is None:
        effective = _policy_for_feed_url(url)
    else:
        effective = "stdlib-only"
    if effective != "impersonated-feed":
        return _fetch_feed_stdlib(url, timeout)
    try:
        return _fetch_feed_stdlib(url, timeout)
    except urllib.error.HTTPError as exc:
        body = b""
        try:
            body = bytes(exc.read(_FEED_ERROR_BODY_CAP) or b"")
        except Exception:
            body = b""
        snippet = body[:512].decode("utf-8", errors="replace").strip()
        status = int(exc.code)
        if not _feed_blocked(status, snippet):
            raise RuntimeError(
                f"fetch failed for {url} (retrieval policy=impersonated-feed): "
                f"HTTP Error {status}: {exc.reason}"
            ) from exc
        blocked_detail = f"HTTP Error {status}: {exc.reason}"
        if snippet:
            blocked_detail += f" — body: {snippet[:512]}"
    except Exception as exc:
        raise RuntimeError(
            f"fetch failed for {url} (retrieval policy=impersonated-feed): {exc}"
        ) from exc
    if _curl_cffi_requests is None:
        raise RuntimeError(
            f"fetch failed for {url} (retrieval policy=impersonated-feed): "
            f"{blocked_detail} (curl_cffi unavailable for impersonated retry)"
        )
    return _fetch_feed_impersonated(url, timeout)


def _published_at(entry: Any, fallback: datetime) -> datetime:
    raw = entry.get("published") or entry.get("updated") or ""
    if not raw:
        return fallback
    try:
        return coerce_tz_aware(date_parser.parse(str(raw)))
    except (ValueError, OverflowError, TypeError):
        return fallback


def _registry_language(source: str) -> str:
    """Look up the registry language for `source`; defaults to 'en'."""
    try:
        from brain.sources import get_source

        lang = get_source(source).get("language") or "en"
        return str(lang).strip().lower() or "en"
    except Exception:
        return "en"


@dataclass
class ParseReport:
    """Outcome of parsing one feed: documents plus visible skip accounting.

    `skipped` counts entries that could not become documents (missing
    title/link, unparseable item); `skipped_reasons` identifies each one
    (entry index plus link when present) for reprocessing/exclusion.
    """

    documents: list[NormalizedDocument] = field(default_factory=list)
    skipped: int = 0
    skipped_reasons: list[str] = field(default_factory=list)


def count_feed_entries(xml: bytes | str) -> int:
    """Count entries in a feed payload; 0 when the payload has none."""
    try:
        payload = xml.decode("utf-8", errors="replace") if isinstance(xml, bytes) else xml
        return len(feedparser.parse(payload).entries)
    except Exception:
        return 0


def parse_feed_with_report(
    xml: bytes | str,
    source: str = DEFAULT_SOURCE,
    language: str | None = None,
) -> ParseReport:
    """Parse RSS/Atom bytes into documents with explicit skip accounting.

    Malformed items (missing title/link, unparseable structure) are skipped
    individually and recorded in `skipped_reasons` — partial failure is
    visible, never a batch abort. A totally unparseable feed raises
    ValueError.
    """
    payload = xml.decode("utf-8", errors="replace") if isinstance(xml, bytes) else xml
    feed = feedparser.parse(payload)
    if feed.bozo and not feed.entries:
        raise ValueError(f"Unparseable feed: {feed.bozo_exception!r}")
    retrieved_at = datetime.now(UTC)
    resolved_language = language or _registry_language(source)
    report = ParseReport()
    for index, entry in enumerate(feed.entries):
        try:
            title = (entry.get("title") or "").strip()
            link = (entry.get("link") or "").strip()
            if not title or not link:
                missing = "title" if not title else "link"
                hint = f" ({link})" if link else ""
                report.skipped += 1
                report.skipped_reasons.append(f"entry {index}{hint}: missing {missing}")
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
            report.documents.append(
                make_document(
                    source=source,
                    url=link,
                    title=title,
                    content=body,
                    author=author,
                    published_at=_published_at(entry, retrieved_at),
                    retrieved_at=retrieved_at,
                    language=resolved_language,
                )
            )
        except Exception as exc:
            report.skipped += 1
            report.skipped_reasons.append(f"entry {index}: unparseable item ({exc})")
    return report


def parse_feed(
    xml: bytes | str,
    source: str = DEFAULT_SOURCE,
    language: str | None = None,
) -> list[NormalizedDocument]:
    """Parse RSS/Atom bytes into normalized documents.

    Malformed items (missing title/link, unparseable structure) are skipped
    individually — partial failure is explicit, never a batch abort. A
    totally unparseable feed raises ValueError. For skip accounting see
    :func:`parse_feed_with_report`.
    """
    return parse_feed_with_report(xml, source=source, language=language).documents


def upsert_documents(docs: list[NormalizedDocument], conn: Any | None = None) -> tuple[int, int]:
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
