"""RSS retrieval, feed parsing, and idempotent persistence.

- Retrieval: curl_cffi Chrome impersonation (browser headers) is the only
  feed lane. Feed URLs are fetched as raw bytes and are never sent to a
  Markdown reader or scraper; a dead feed is recovered by the candidate-URL
  retry (`feed_candidate_urls`), not by a content-extraction fallback.
- Parsing accepts RSS or Atom and produces the normalized contract.
- Persistence is rerun-safe: `ON CONFLICT DO NOTHING` (no conflict target,
  so url, canonical_url, and content_hash collisions all collapse) plus
  per-item error capture, so a repeated run inserts nothing new and one bad
   row never aborts the Ingestion Run.
"""

from __future__ import annotations

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

#: Native timeout (s) floor for the impersonated feed fetch.
IMPERSONATED_TIMEOUT = 30

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

#: Lane corrections live in the curated stanzas, never in code. A source
#: whose published lane is retired is moved onto the working lane by editing
#: its registry stanza — the MarTech / MarketingDirecto / Swarovski
#: precedent. The effective stanza is whatever
#: `sources.get_retrieval_config` returns, and the flow/task routing logic
#: reads that stanza and nothing else: the stanza is the single lane seam.


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
                return "impersonated-feed"  # dead alias: stdlib-only resolves to the one lane
    except Exception:
        pass
    return "impersonated-feed"


def _cause_text(exc: Exception, cap: int = 512) -> str:
    """One collapsed, capped failure detail for error messages; never secrets."""
    return " ".join(str(exc).split())[:cap]


def _fetch_feed_impersonated(url: str, timeout: int = IMPERSONATED_TIMEOUT) -> bytes:
    """GET `url` with curl_cffi Chrome impersonation plus browser headers.

    The only feed lane. curl_cffi is a hard dependency: when it is missing
    this raises explicitly — it never degrades to a different transport.
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


def fetch_rss(url: str, timeout: int = FEED_TIMEOUT, policy: str | None = None) -> bytes:
    """Download a feed as raw bytes. Raises on network/HTTP failure.

    One lane only: curl_cffi Chrome impersonation with browser headers. A
    failed fetch raises immediately — the feed URL is never routed to a
    Markdown reader or scraper (their output could not be parsed as feed
    XML). A dead feed is recovered one level up by the candidate-URL retry
    (:func:`feed_candidate_urls`); an exhausted candidate list surfaces the
    explicit empty-feed error.

    `policy` is accepted for back-compat: "impersonated-feed" and the dead
    "stdlib-only" alias are the same lane, as are unknown values. None
    resolves the policy from the source registry by feed URL (unregistered
    URLs yield "impersonated-feed"). Never raises on bad policy input.
    """
    normalized = str(policy).strip() if isinstance(policy, str) else ""
    if normalized and normalized not in _FEED_POLICIES:
        normalized = "impersonated-feed"  # dead alias + unknown values: the one lane
    if normalized:
        effective = normalized
    elif policy is None:
        effective = _policy_for_feed_url(url)
    else:
        effective = "impersonated-feed"
    # The impersonated fetch keeps the 30s floor it always carried; the
    # caller's `timeout` is honored when larger.
    primary_timeout = max(int(timeout), IMPERSONATED_TIMEOUT)
    try:
        return _fetch_feed_impersonated(url, primary_timeout)
    except Exception as primary_exc:
        raise RuntimeError(
            f"{_cause_text(primary_exc)} (retrieval policy={effective})"
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
