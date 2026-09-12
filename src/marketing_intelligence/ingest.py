"""RSS retrieval, feed parsing, and idempotent persistence.

- Retrieval chain: curl_cffi Chrome impersonation (primary, with browser
  headers) → Jina reader → Firecrawl; there is no plain-urllib fetch lane.
- Parsing accepts RSS or Atom and produces the normalized contract.
- Persistence is rerun-safe: `ON CONFLICT DO NOTHING` (no conflict target,
  so url, canonical_url, and content_hash collisions all collapse) plus
  per-item error capture, so a repeated run inserts nothing new and one bad
   row never aborts the Ingestion Run.
"""

from __future__ import annotations

import gzip
import time
import urllib.error
import urllib.request
import zlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import feedparser
from dateutil import parser as date_parser

from marketing_intelligence.db import get_connection
from marketing_intelligence.normalize import NormalizedDocument, coerce_tz_aware, make_document

USER_AGENT = "TrendIntelligenceBrain/1.0 (+rss-ingest; local)"
DEFAULT_SOURCE = "Social Media Today"

try:  # hard dependency: curl_cffi Chrome impersonation (pyproject curl-cffi>=0.11)
    from curl_cffi import requests as _curl_cffi_requests
except Exception:  # pragma: no cover - broken/absent install: primary raises explicitly
    _curl_cffi_requests = None  # type: ignore[assignment]

#: Allowed feed retrieval policies (validated in fetch_rss; see marketing_intelligence.sources).
_FEED_POLICIES: tuple[str, ...] = ("impersonated-feed",)

#: Feed XML preference for the primary impersonated fetch.
_FEED_ACCEPT = "application/rss+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.7"

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

#: Native timeout (s) for the feed lane (opt 4 split: fast feeds, slower
#: articles, slowest fallback reader). Prefect `timeout_seconds` cannot
#: preempt blocking socket I/O, so the split lives in the clients.
FEED_TIMEOUT = 10

#: Native timeout (s) floor for the primary impersonated feed fetch.
IMPERSONATED_TIMEOUT = 30

#: Native timeout (s) shared by the Jina and Firecrawl fallback legs
#: (mirrors marketing_intelligence.enrich.FALLBACK_TIMEOUT).
FEED_FALLBACK_TIMEOUT = 30

#: Error-body bytes inspected for challenge/bot evidence in a feed payload.
_FEED_ERROR_BODY_CAP = 65536

#: Code-level feed fallback routing (ticket 13): canonical feed URLs that are
#: known-dead/emptied map to working same-publisher replacements, tried in
#: order after the primary. Curated sources JSON stays untouched; this is the
#: routing correction for a dead feed URL, not a registry edit.
_FEED_FALLBACK_URLS: dict[str, tuple[str, ...]] = {
    # Live 2026-09-06: the JCK root feed returns HTTP 200 with a channel
    # skeleton but 0 entries (emptied/dead), while the Retail category feed
    # (the source's hub vertical) carries live items.
    "https://www.jckonline.com/feed/": (
        "https://www.jckonline.com/category/news-trends/retail/feed/",
    ),
}


class EmptyFeedError(ValueError):
    """A feed that arrived without transport error but carries zero entries.

    Subclasses ValueError so the Ingestion Run parent (`ingest_sources_flow`) converts
    it to an explicit per-source `{inserted: 0, skipped: 0, error}` outcome —
    the same lane totally unparseable feeds already travel. Never a silent
    zero: the message diagnoses dead/changed URL vs. blocked/challenge page.
    """


def feed_candidate_urls(url: str) -> tuple[str, ...]:
    """Primary feed URL plus any code-level fallbacks, in try order.

    Sources without a declared correction keep the exact single-URL contract.
    Never raises.
    """
    return (url, *_FEED_FALLBACK_URLS.get(url, ()))


def diagnose_empty_feed(
    xml: bytes | str,
    *,
    url: str | None = None,
    source: str | None = None,
) -> str:
    """Classify a zero-entry feed payload: dead/changed URL vs. blocked page.

    A body carrying bot/challenge markers diagnoses as a blocked/challenge
    page; anything else (e.g. a channel skeleton with no items after an
    HTTP 200) diagnoses as a dead/changed feed URL. Never raises.
    """
    try:
        payload = xml.decode("utf-8", errors="replace") if isinstance(xml, bytes) else xml
        haystack = payload[:_FEED_ERROR_BODY_CAP].lower()
        blocked = any(marker in haystack for marker in _FEED_CHALLENGE_MARKERS)
    except Exception:
        blocked = False
    where = ""
    if source is not None:
        where += f" source {source!r}"
    if url is not None:
        where += f" url={url}"
    if blocked:
        return f"blocked/challenge page suspected for{where or ' feed'}"
    return f"dead/changed feed URL suspected for{where or ' feed'}: 0 entries, no bot evidence"


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
    "impersonated-feed". Never raises.
    """
    try:
        from marketing_intelligence.sources import get_retrieval_policy, list_sources

        for entry in list_sources():
            if entry.get("rss_url") == url:
                policy = get_retrieval_policy(str(entry.get("name", ""))).get("policy")
                if policy == "impersonated-feed":
                    return str(policy)
                return "impersonated-feed"  # dead alias: stdlib-only runs the full chain
    except Exception:
        pass
    return "impersonated-feed"


def _decode_body(raw: bytes, encoding: str | None) -> bytes:
    """Decode `raw` per Content-Encoding (gzip/deflate/br); never raises.

    Multi-value headers handled case-insensitively. `br` decodes only when
    `brotli` is already importable (no new dep). Defense-in-depth: a missing
    or empty header with gzip-magic bytes gunzips anyway. Any decode failure
    falls back to the raw bytes.
    """
    data = bytes(raw or b"")
    try:
        tokens = {(t.strip().lower()) for t in (encoding or "").split(",") if t.strip()}
        if not tokens and data[:2] == b"\x1f\x8b":
            try:
                return gzip.decompress(data)
            except Exception:
                return data
        if "gzip" in tokens or "x-gzip" in tokens:
            try:
                return gzip.decompress(data)
            except Exception:
                return data
        if "deflate" in tokens:
            try:
                try:
                    return zlib.decompress(data)
                except Exception:
                    return zlib.decompress(data, -15)
            except Exception:
                return data
        if "br" in tokens:
            try:
                import brotli as _brotli  # type: ignore[import-not-found]
            except Exception:
                return data
            try:
                return bytes(_brotli.decompress(data))
            except Exception:
                return data
        return data
    except Exception:
        return bytes(raw or b"")


def _cause_text(exc: Exception, cap: int = 512) -> str:
    """One collapsed, capped failure detail for chain messages; never secrets."""
    return " ".join(str(exc).split())[:cap]


def _fetch_feed_impersonated(url: str, timeout: int = IMPERSONATED_TIMEOUT) -> bytes:
    """GET `url` with curl_cffi Chrome impersonation plus browser headers.

    Primary feed lane. curl_cffi is a hard dependency: when it is missing this
    raises explicitly — it never degrades to a plain-urllib fetch.
    """
    from marketing_intelligence.enrich import BROWSER_HEADERS  # canonical browser identity

    if _curl_cffi_requests is None:
        raise RuntimeError(
            f"fetch failed for {url}: curl_cffi unavailable "
            "(hard dependency for the impersonated feed fetch)"
        )
    headers = {**BROWSER_HEADERS, "Accept": _FEED_ACCEPT}
    try:
        response = _curl_cffi_requests.get(
            url, impersonate="chrome", headers=headers, timeout=timeout
        )
    except Exception as exc:
        raise RuntimeError(f"fetch failed for {url}: {exc}") from exc
    body = bytes(response.content or b"")
    status = int(response.status_code)
    if status >= 400:
        snippet = body[:512].decode("utf-8", errors="replace").strip()
        detail = f"HTTP Error {status}"
        if snippet:
            detail += f" — body: {snippet[:512]}"
        raise RuntimeError(f"fetch failed for {url}: {detail}")
    return body


def _retry_after_seconds(exc: urllib.error.HTTPError, cap: float) -> float:
    """Parse a 429 Retry-After delay, bounded so ingestion never stalls."""
    headers = getattr(exc, "headers", None) or getattr(exc, "hdrs", None)
    try:
        raw = headers.get("Retry-After") if headers is not None else None
        delay = float(str(raw).strip().split(",")[0]) if raw is not None else 0.0
    except (TypeError, ValueError, AttributeError):
        delay = 0.0
    return max(0.0, min(delay, cap))


def _fetch_feed_via_jina(url: str, timeout: int = FEED_FALLBACK_TIMEOUT) -> bytes:
    """Fetch a feed through the zero-ops reader fallback; return raw bytes.

    Mirrors :func:`marketing_intelligence.enrich.try_fallback_reader` semantics
    (data-minimizing headers only — explicit User-Agent plus ``Accept: text/*``,
    never any Cookie/Authorization credentials; 429 Retry-After honored up to
    ``FALLBACK_MAX_RETRIES`` retries) but returns the reader's payload bytes
    rather than cleaned Markdown: feedparser needs the XML.
    """
    from marketing_intelligence.enrich import (
        FALLBACK_ACCEPT,
        FALLBACK_MAX_RETRIES,
        FALLBACK_READER_BASE,
        FALLBACK_RETRY_CAP_SECONDS,
    )

    request = urllib.request.Request(
        FALLBACK_READER_BASE + url,
        headers={"User-Agent": USER_AGENT, "Accept": FALLBACK_ACCEPT},
    )
    for attempt in range(1 + FALLBACK_MAX_RETRIES):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = bytes(response.read())
                try:
                    encoding = response.getheader("Content-Encoding")
                except Exception:
                    encoding = None
                return _decode_body(raw, encoding)
        except urllib.error.HTTPError as exc:
            if exc.code != 429:
                raise RuntimeError(
                    f"jina fetch failed for {url}: HTTP Error {exc.code}: {exc.reason}"
                ) from exc
            if attempt < FALLBACK_MAX_RETRIES:
                time.sleep(_retry_after_seconds(exc, FALLBACK_RETRY_CAP_SECONDS))
                continue
            raise RuntimeError(
                f"jina rate-limited for {url}: reader returned 429 "
                f"({FALLBACK_MAX_RETRIES} retries exhausted)"
            ) from exc
        except Exception as exc:
            raise RuntimeError(f"jina fetch failed for {url}: {exc}") from exc
    raise RuntimeError(f"jina rate-limited for {url}: reader unavailable")


def _fetch_feed_via_firecrawl(url: str, timeout: int = FEED_FALLBACK_TIMEOUT) -> bytes:
    """Fetch a feed through Firecrawl (lazy import; key read at call time)."""
    from marketing_intelligence.firecrawl import fetch_via_firecrawl

    return fetch_via_firecrawl(url, timeout)


def fetch_rss(url: str, timeout: int = FEED_TIMEOUT, policy: str | None = None) -> bytes:
    """Download a feed. Raises on network/HTTP failure.

    Every policy value runs the same fallback chain: primary curl_cffi Chrome
    impersonation (browser headers) → Jina reader → Firecrawl on any primary
    failure. "stdlib-only" is a dead alias for "impersonated-feed" (accepted
    for back-compat, never a distinct mode). None resolves the policy from
    the source registry by feed URL (unregistered URLs yield
    "impersonated-feed"); unknown policy values fall back to
    "impersonated-feed". Never raises on bad policy input.
    """
    normalized = str(policy).strip() if isinstance(policy, str) else ""
    if normalized and normalized not in _FEED_POLICIES:
        normalized = "impersonated-feed"  # dead alias + unknown values: full chain
    if normalized:
        effective = normalized
    elif policy is None:
        effective = _policy_for_feed_url(url)
    else:
        effective = "impersonated-feed"
    # The impersonated primary keeps the 30s floor it always carried; the
    # caller's `timeout` is honored when larger.
    primary_timeout = max(int(timeout), IMPERSONATED_TIMEOUT)
    try:
        return _fetch_feed_impersonated(url, primary_timeout)
    except Exception as primary_exc:
        primary_detail = _cause_text(primary_exc)
        try:
            return _fetch_feed_via_jina(url)
        except Exception as exc:
            jina_detail = _cause_text(exc)
        try:
            return _fetch_feed_via_firecrawl(url)
        except Exception as exc:
            firecrawl_detail = _cause_text(exc)
        raise RuntimeError(
            f"fetch failed for {url} (retrieval policy={effective}): "
            f"{primary_detail}; jina: {jina_detail}; firecrawl: {firecrawl_detail}"
        ) from primary_exc


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
        from marketing_intelligence.sources import get_source

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
    visible, never an Ingestion Run abort. A totally unparseable feed raises
    ValueError; a cleanly parsed feed with zero entries raises
    EmptyFeedError (an explicit per-source error, never a silent zero).
    """
    payload = xml.decode("utf-8", errors="replace") if isinstance(xml, bytes) else xml
    feed = feedparser.parse(payload)
    if not feed.entries:
        if feed.bozo:
            raise ValueError(f"Unparseable feed: {feed.bozo_exception!r}")
        try:
            title = str(feed.feed.get("title") or "").strip()
        except Exception:
            title = ""
        size = len(xml) if isinstance(xml, bytes) else len(xml.encode("utf-8"))
        detail = f"Empty feed for source {source!r}: 0 entries in {size} bytes"
        if title:
            detail += f" (channel {title!r})"
        diagnosis = diagnose_empty_feed(xml, source=source)
        raise EmptyFeedError(f"{detail} — {diagnosis}")
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
    individually — partial failure is explicit, never an Ingestion Run abort. A
    totally unparseable feed raises ValueError; a cleanly parsed feed with
    zero entries raises EmptyFeedError. For skip accounting see
    :func:`parse_feed_with_report`.
    """
    return parse_feed_with_report(xml, source=source, language=language).documents


def upsert_documents(docs: list[NormalizedDocument], conn: Any | None = None) -> tuple[int, int]:
    """Persist documents idempotently. Returns (inserted, skipped).

    When `conn` is None a connection is opened via `marketing_intelligence.db.get_connection`
    (caller may inject any DB-API connection — fakes welcome in tests).

    Fail-fast source guard: the source row must exist — an unknown source
    raises ValueError instead of inserting rows with a NULL `source_id`.
    Genuine DB failures during the lookup propagate unchanged.
    """
    if not docs:
        return (0, 0)
    owns_connection = False
    if conn is None:
        conn = get_connection()
        owns_connection = True
    assert conn is not None
    try:
        cursor = conn.execute(SOURCE_ID_SQL, (docs[0].source,))
        row = cursor.fetchone()
        if row is None or row[0] is None:
            raise ValueError(
                f"Unknown source for upsert: {docs[0].source!r} "
                "(no row in sources; refusing NULL source_id insert)"
            )
        source_id = row[0]
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
