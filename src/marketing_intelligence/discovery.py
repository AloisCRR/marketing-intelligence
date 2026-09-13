"""Sitemap-first URL discovery + family-switched article extraction.

Single retrieval-adapter seam (spec 06): one discovery Ingestion Stage plus an
extractor switch, behind the unchanged normalized-Document and upsert
contracts. Everything downstream (normalization, hash dedupe, enrichment-keep
semantics, per-source rerun isolation, explicit partial failure) behaves
exactly as the RSS lane.

- Discovery order: declared sitemaps first (index → nested index → URL set),
  hub-anchor fallback second (hub + declared pagination pages, per-source link
  pattern). Hub extends sitemap coverage; it never replaces it.
- Extractor families: generic HTML-to-Markdown default; JSON-LD
  ``articleBody``-first for the Next.js/Sanity family with generic fallback.
  Provider Markdown (reader/scrape legs, ``articleBody``) is stored as-is
  after a thin-check, never re-cleaned. Still-thin results are kept-aside
  with explicit causes (flag path), never bypassed.
- Politeness: robots.txt crawl-delay honored (effective pacing is
  max(stanza pacing, crawl-delay) plus jitter), sequential requests with
  explicit timeouts; the article chain is curl_cffi Chrome impersonation
  first, then the Jina reader, then Firecrawl under the impersonated-feed
  policy — there is no stdlib fetch lane, and persistent denials are
  recorded as explicit per-document errors.
- Canonical guard: the final URL after redirects is the fetch identity; the
  declared canonical/og URL is stored only when same-host, else the final
  URL wins — a wrong article is never stored, and slug changes collapse on
  the content-hash dedupe downstream.
"""

from __future__ import annotations

import gzip
import html as _html
import json
import random
import re
import threading
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urljoin, urlsplit

from dateutil import parser as date_parser

from marketing_intelligence.enrich import (
    _CHALLENGE_KEYWORDS,
    _LOCK_PAGE_MARKER,
    FetchFailed,
    ProviderMarkdown,
    article_content_chain,
    clean_to_markdown,
    fetch_impersonated,
    fetch_reader,
)
from marketing_intelligence.normalize import (
    NormalizedDocument,
    canonicalize_url,
    coerce_tz_aware,
    content_hash_for,
    normalize_text,
)
from marketing_intelligence.sources import (
    DEFAULT_MAX_URLS,
    DEFAULT_PACING_MS,
    DEFAULT_RETRIEVAL_POLICY,
    EXTRACTOR_FAMILIES,
    RETRIEVAL_POLICIES,
    RETRIEVAL_POLICY_ALIASES,
)

#: Explicit timeout (s) for article/discovery fetches (opt 4 split: 10s feeds
#: in marketing_intelligence.ingest, 15s articles here). Every lane of the
#: article chain honors the caller's timeout.
DEFAULT_TIMEOUT = 15

#: Maximum sitemap-nesting depth traversed (index → nested index → URL set).
MAX_SITEMAP_DEPTH = 2


class DiscoveryError(Exception):
    """Source-level discovery failure (no URLs discovered, unparseable index)."""


class ArticleFetchError(DiscoveryError):
    """One article URL could not be retrieved; carries the URL and detail."""

    def __init__(self, url: str, detail: str) -> None:
        super().__init__(f"{url}: {detail}")
        self.url = url
        self.detail = detail


class ArticleExtractError(DiscoveryError):
    """One retrieved article held no usable document; carries URL and detail."""

    def __init__(self, url: str, detail: str) -> None:
        super().__init__(f"{url}: {detail}")
        self.url = url
        self.detail = detail


@dataclass
class SitemapUrl:
    """One discovered article URL with its sitemap lastmod (when declared)."""

    loc: str
    lastmod: datetime | None = None


@dataclass
class HarvestReport:
    """Outcome of one sitemap harvest: documents plus visible skip accounting."""

    documents: list[NormalizedDocument] = field(default_factory=list)
    skipped: int = 0
    causes: list[str] = field(default_factory=list)


def _impersonated_get(url: str, timeout: int = DEFAULT_TIMEOUT) -> tuple[str, bytes]:
    """Impersonation leg of the shared article chain; return (final_url, body).

    Delegates to :func:`marketing_intelligence.enrich.fetch_impersonated`
    (curl_cffi Chrome + genuine browser headers, no cookies or credentials,
    caller's `timeout`) and re-raises the shared failure as
    :class:`ArticleFetchError`, so discovery keeps its typed per-URL contract.
    """
    try:
        return fetch_impersonated(url, timeout)
    except FetchFailed as exc:
        raise ArticleFetchError(url, str(exc)) from exc


def _jina_reader_get(url: str, timeout: int = DEFAULT_TIMEOUT) -> tuple[str, bytes]:
    """Reader leg of the shared article chain; return (final_url, Markdown bytes).

    Delegates to :func:`marketing_intelligence.enrich.fetch_reader` (plain
    urllib, data-minimizing headers, bounded 429 retries) and re-raises the
    shared failure as :class:`ArticleFetchError`. The payload comes back as
    delivered — possibly empty; :func:`article_content_chain` tags it
    :class:`ProviderMarkdown` for the extract site. The fetch identity stays
    the requested URL: the reader base (``r.jina.ai``) is a provider hop, not
    the article's final URL, so an article served by this leg keeps its real
    identity (and the planned URL is never trusted to a provider-supplied
    ``URL Source:`` header).
    """
    try:
        _, body = fetch_reader(url, timeout)
        return (url, body)
    except FetchFailed as exc:
        raise ArticleFetchError(url, str(exc)) from exc


def policy_get(
    url: str, *, policy: str = "impersonated-feed", timeout: int = DEFAULT_TIMEOUT
) -> tuple[str, bytes]:
    """GET `url` under the stanza policy; return (final_url, body).

    Article and hub/listing fetches only — sitemap discovery is
    impersonated-only through :func:`fetch_sitemap_bytes` and never walks this
    chain. Every policy walks the one shared article-content chain
    (:func:`marketing_intelligence.enrich.article_content_chain`): the
    impersonated primary (:func:`_impersonated_get`) first, then the Jina
    reader (:func:`_jina_reader_get`) on any primary failure, then the
    Firecrawl scrape — ungated, every leg on fetch failure. ``stdlib-only``
    is a dead alias for ``impersonated-feed`` (accepted for back-compat,
    never a distinct mode); no stdlib fetch lane exists. Never returns a
    wrong article: HTTP errors raise, they never yield bytes. Provider legs
    come back tagged :class:`ProviderMarkdown`, so extraction stores them
    as-is. When every leg fails, the raised :class:`ArticleFetchError` names
    each leg once in the shared, credential-free detail.
    """
    policy = RETRIEVAL_POLICY_ALIASES.get(policy, policy)
    if policy != "impersonated-feed":
        policy = "impersonated-feed"  # dead alias: no primary-only mode remains
    try:
        return article_content_chain(
            url, primary=_impersonated_get, reader=_jina_reader_get, timeout=timeout
        )
    except FetchFailed as exc:
        raise ArticleFetchError(url, str(exc)) from exc


#: Payload bytes inspected for challenge/bot markers on a sitemap fetch.
_SITEMAP_CHALLENGE_SCAN_BYTES = 65536

#: Opening tags of a genuine sitemap document. Their presence short-circuits
#: the challenge scan, so a real urlset that merely mentions a marker word in
#: an article slug (e.g. ``/challenge/``) is never misreported as a block page.
_SITEMAP_ROOT_MARKERS = (b"<urlset", b"<sitemapindex")


def _sitemap_challenge_detail(body: bytes) -> str | None:
    """Explicit ``challenge`` cause when `body` is a block/non-XML page; else None.

    Sitemaps are fetched impersonated-only, so a payload that is not XML is a
    block/challenge page rather than something the XML parser should diagnose
    as ``Unparseable sitemap``: reader-leg Markdown (``Title: ...``), HTML
    login walls, and JS challenge shells all land here. Detection is (1) a
    challenge marker from the shared
    :data:`marketing_intelligence.enrich._CHALLENGE_KEYWORDS` family, or (2) a
    payload whose first non-BOM/whitespace byte is not ``<``. Gzipped payloads
    are decompressed for inspection only, so a genuine gzipped sitemap still
    passes through as the served bytes. Never raises.
    """
    probe = bytes(body)
    if probe[:2] == b"\x1f\x8b":
        try:
            probe = gzip.decompress(probe)
        except Exception:
            return None  # opaque gzip: parse_sitemap reports it, not a block page
    stripped = probe.lstrip(b"\xef\xbb\xbf \t\r\n\x00")
    if any(marker in stripped[:1024].lower() for marker in _SITEMAP_ROOT_MARKERS):
        return None  # genuine sitemap root: pass through untouched
    lowered = probe[:_SITEMAP_CHALLENGE_SCAN_BYTES].lower()
    marker = next((key for key in _CHALLENGE_KEYWORDS if key.encode("ascii") in lowered), None)
    snippet = stripped[:120].decode("utf-8", errors="replace").strip()
    if marker is not None:
        return f"challenge: sitemap URL served a block page (marker {marker!r}; starts {snippet!r})"
    if not stripped.startswith(b"<"):
        return f"challenge: sitemap URL served a non-XML payload (starts {snippet!r})"
    return None


def fetch_sitemap_bytes(url: str, timeout: int = DEFAULT_TIMEOUT) -> bytes:
    """Fetch one sitemap document impersonated-only; return the served bytes.

    A sitemap is XML, not an article body: the fetch goes straight through the
    impersonated Chrome leg (:func:`_impersonated_get`) and never through the
    Jina reader, the Firecrawl scrape, or the shared article-content chain — a
    reader leg would hand the XML parser Markdown (``Title: XML News
    Sitemap...``) and turn a reachable sitemap into ``Unparseable sitemap``.
    Block/challenge payloads raise :class:`ArticleFetchError` carrying an
    explicit ``challenge`` cause (see :func:`_sitemap_challenge_detail`)
    instead of reaching :func:`parse_sitemap`; genuine XML, gzipped included,
    passes through untouched.
    """
    _, body = _impersonated_get(url, timeout=timeout)
    detail = _sitemap_challenge_detail(body)
    if detail is not None:
        raise ArticleFetchError(url, detail)
    return body


def _challenge_marker(detail: str) -> str | None:
    """Shared-family challenge marker found in `detail`; None when clean.

    The keyword family is :data:`marketing_intelligence.enrich._CHALLENGE_KEYWORDS`
    (never a second copy); the keyword-less 403 lock-page marker joins it so a
    blocked hub that names no guard still reads as a challenge rather than a
    bare status. Never raises.
    """
    lowered = detail.lower()
    if _LOCK_PAGE_MARKER in lowered:
        return _LOCK_PAGE_MARKER
    return next((key for key in _CHALLENGE_KEYWORDS if key in lowered), None)


def _hub_challenge_detail(detail: str) -> str | None:
    """``challenge`` cause for a hub fetch failure carrying block evidence.

    Mirrors the :func:`_sitemap_challenge_detail` wording family: the same
    keyword family decides, and the cause keeps the raw failure detail (so the
    URL and HTTP status stay visible) after the explicit marker.
    """
    marker = _challenge_marker(detail)
    if marker is None:
        return None
    return f"challenge: hub URL served a block page (marker {marker!r}; {detail})"


def _hub_payload_challenge_detail(payload: str) -> str | None:
    """``challenge`` cause when a served hub payload is not a usable listing.

    A payload with neither an anchor nor a Markdown link *and* no HTML markup
    at all is a block/interstitial page the reader leg mangled into Markdown
    (a real listing, HTML or provider Markdown, always carries links). Such a
    payload would otherwise yield zero links silently; it is recorded as an
    explicit challenge instead. Never raises.
    """
    if _ANCHOR_HREF_RE.search(payload) or _MD_LINK_RE.search(payload):
        return None  # link-bearing listing: never a block page
    if re.search(r"<[A-Za-z!/]", payload):
        return None  # HTML markup present: an empty listing, not a wall
    snippet = payload.strip()[:120]
    return f"challenge: hub URL served a non-HTML payload (starts {snippet!r})"


def _normalize_exclude(raw: Any) -> list[str]:
    """Normalize a sitemap-exclude stanza value to non-empty path patterns.

    Accepts a single string or a list of strings (anything else yields []);
    whitespace-only entries are dropped. Entries are matched by
    :func:`_path_excluded`: substrings by default, optionally anchored with
    ``^``/``$``. Never raises.
    """
    try:
        items: list[Any] = [raw] if isinstance(raw, str) else list(raw or [])
    except TypeError:
        return []
    patterns: list[str] = []
    for item in items:
        if item is None:
            continue
        pattern = str(item).strip()
        if pattern:
            patterns.append(pattern)
    return patterns


def _path_of(url: str) -> str:
    """URL path for pattern matching; "" when unparseable (never raises)."""
    try:
        return urlsplit(url).path
    except ValueError:
        return ""


def _path_excluded(path: str, excludes: list[str]) -> bool:
    """True when `path` matches any sitemap-exclude entry.

    Entries are path *substrings* by default (the historical contract, e.g.
    ``/webstories/``). An entry wrapped in ``^…$`` matches that exact path
    only — needed when a listing page shares its path with the article
    children beneath it: ``^/digital-general/social-media-marketing$`` drops
    the section front while ``/digital-general/social-media-marketing/<slug>``
    (its articles) stays, which no substring can express. Anchor characters
    come from the registry stanza; a bare ``^``/``$`` stays literal.
    """
    for pattern in excludes:
        if pattern.startswith("^") and pattern.endswith("$") and len(pattern) > 2:
            if path == pattern[1:-1]:
                return True
        elif pattern and pattern in path:
            return True
    return False


#: How long an excluded sitemap child still counts as fresh (days).
_EXCLUDED_CHILD_FRESH_DAYS = 60

#: ``/<yyyy>/<mm|month-name>/`` inside a child sitemap loc (the Dive archive
#: shape: ``/news/archive/2026/august.xml``); the month token must end at a
#: path separator, a file extension dot, or the end of the path.
_ARCHIVE_MONTH_RE = re.compile(r"/(\d{4})/([A-Za-z]{3,9}|\d{1,2})(?=[./]|$)")

_MONTH_NUMBERS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


def _month_number(token: str) -> int | None:
    """Month 1–12 for a numeric or English month-name token; None when invalid."""
    token = token.strip().lower()
    if token.isdigit():
        month = int(token)
        return month if 1 <= month <= 12 else None
    return _MONTH_NUMBERS.get(token[:3])


def _excluded_child_is_fresh(loc: str, reference: datetime) -> bool:
    """True when an excluded child's loc carries a year-month in the window.

    The exclude test gates traversal of *children* too (a Dive
    ``/news/archive/<year>/<month>.xml`` subtree), which would drop the newest
    archive children — and those hold the current and previous month's
    articles, i.e. live content, not the stale backfill the exclude targets. A
    child loc whose ``/<yyyy>/<mm|month-name>/`` falls inside the last
    :data:`_EXCLUDED_CHILD_FRESH_DAYS` days is therefore still traversed. A
    loc without a parseable year-month, or dated outside the window, is
    excluded exactly as before, so ancient subtrees still cost no traffic.
    """
    match = _ARCHIVE_MONTH_RE.search(_path_of(loc))
    if match is None:
        return False
    month = _month_number(match.group(2))
    if month is None:
        return False
    try:
        child_month = datetime(int(match.group(1)), month, 1, tzinfo=UTC)
    except ValueError:
        return False
    age = reference - child_month
    return timedelta(0) <= age <= timedelta(days=_EXCLUDED_CHILD_FRESH_DAYS)


def _local(tag: str) -> str:
    """Strip any XML namespace from `tag` (Yoast/news sitemaps are namespaced)."""
    return tag.rsplit("}", 1)[-1]


def _parse_lastmod(raw: str | None) -> datetime | None:
    """Parse a sitemap lastmod/publication_date; None when missing/unparseable."""
    if not raw or not raw.strip():
        return None
    try:
        return coerce_tz_aware(date_parser.parse(raw.strip()))
    except (ValueError, OverflowError, TypeError):
        return None


def parse_sitemap(xml: bytes | str) -> tuple[list[SitemapUrl], list[SitemapUrl]]:
    """Parse sitemap XML into (article_urls, child_sitemaps).

    Namespace-agnostic: urlset entries yield ``(loc, lastmod)`` where lastmod
    falls back to the news ``publication_date``; sitemapindex entries yield
    ``(loc, lastmod)`` children. Entries without a loc are dropped. Raises
    ValueError on unparseable XML or an unknown root element.
    """
    if isinstance(xml, bytes):
        raw = bytes(xml)
        if raw[:2] == b"\x1f\x8b":
            try:
                raw = gzip.decompress(raw)
            except Exception:
                pass
        payload = raw.decode("utf-8", errors="replace")
    else:
        payload = xml
    payload = payload.lstrip("\ufeff \r\n\t")
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise ValueError(f"Unparseable sitemap: {exc}") from exc
    kind = _local(root.tag)
    urls: list[SitemapUrl] = []
    children: list[SitemapUrl] = []
    if kind == "sitemapindex":
        for entry in root.iter():
            if _local(entry.tag) != "sitemap":
                continue
            loc = next((c.text or "" for c in entry if _local(c.tag) == "loc"), "").strip()
            if not loc:
                continue
            lastmod = next((c.text or "" for c in entry if _local(c.tag) == "lastmod"), "")
            children.append(SitemapUrl(loc=loc, lastmod=_parse_lastmod(lastmod)))
        return ([], children)
    if kind == "urlset":
        for entry in root.iter():
            if _local(entry.tag) != "url":
                continue
            loc = next((c.text or "" for c in entry if _local(c.tag) == "loc"), "").strip()
            if not loc:
                continue
            lastmod_raw = next((c.text or "" for c in entry if _local(c.tag) == "lastmod"), "")
            if not lastmod_raw:
                for news in entry.iter():
                    if _local(news.tag) == "publication_date" and news.text:
                        lastmod_raw = news.text
                        break
            urls.append(SitemapUrl(loc=loc, lastmod=_parse_lastmod(lastmod_raw)))
        return (urls, [])
    raise ValueError(f"Unknown sitemap root: {root.tag!r}")


def discover_urls(
    sitemap_urls: list[str],
    *,
    fetch_body: Callable[[str], bytes],
    max_urls: int = DEFAULT_MAX_URLS,
    prefer_pattern: str | None = None,
    url_filter: str | None = None,
    sitemap_exclude: list[str] | str | None = None,
    now: datetime | None = None,
) -> tuple[list[SitemapUrl], list[str]]:
    """Traverse declared sitemaps newest-first; return (urls, errors).

    Index children are traversed in reverse document order (Yoast-style
    indexes list oldest first, so reversal visits the newest child first);
    when `prefer_pattern` is set (the stanza link pattern, e.g. ``/posts/``),
    children rank by how shallow the pattern sits in the path, so the core
    section wins over nested lookalikes (/posts/x beats /intels/posts/x) and
    non-matching sections go last. When `url_filter` is set (the stanza
    sitemap pattern, e.g. ``/press-releases-news/``), urlset entries whose
    path lacks it are skipped silently — they are out of the corpus, not
    failures — so large whole-site urlsets distill to the declared beat.
    `sitemap_exclude` (the stanza sitemap-exclude list, e.g.
    ``["/webstories/"]``) drops urlset entries whose path contains any of
    its substrings, likewise silently. Inclusion and exclusion both apply
    per urlset *before* collection, so excluded URLs never consume the
    `max_urls` backfill budget — genuine articles fill it instead. The same
    substring test gates traversal itself: a declared sitemap or index child
    whose path matches (e.g. a Dive ``/news/archive/<year>/<month>.xml``) is
    normally never fetched and never recorded as an error, so stale subtrees
    cost no traffic and the bound refills from fresh sitemaps and the hub —
    except when the child's own loc carries a year-month inside the last 60
    days versus `now` (default: current UTC), because the newest archive
    children also hold the current/previous month's live articles
    (:func:`_excluded_child_is_fresh`). `now` is tz-aware-or-naive like the
    rest of the module (naive is read as UTC). Traversal stops fetching new
    children once `max_urls` is reached — the bounded backfill. One bad
    sitemap is recorded in `errors` and never aborts the rest. Results are
    sorted newest-first by lastmod (undated last), deduped by canonical URL,
    and bounded to `max_urls`.
    """
    collected: list[SitemapUrl] = []
    errors: list[str] = []
    visited: set[str] = set()
    stop = False
    reference = coerce_tz_aware(now or datetime.now(UTC))
    excludes = _normalize_exclude(sitemap_exclude)

    def _collect(sitemap_url: str, depth: int) -> None:
        nonlocal stop
        if stop or sitemap_url in visited:
            return
        if excludes and _path_excluded(_path_of(sitemap_url), excludes):
            if not _excluded_child_is_fresh(sitemap_url, reference):
                # Excluded subtree (declared sitemap or index child, e.g. a
                # Dive `/news/archive/<year>/<month>.xml`): never fetched,
                # never an error — the same silent drop urlset entries get,
                # applied one level up so stale trees cost no traffic and the
                # bound refills from fresh sitemaps/hub. A child dated inside
                # the freshness window is live content and still traversed.
                return
        visited.add(sitemap_url)
        try:
            body = fetch_body(sitemap_url)
        except Exception as exc:
            errors.append(f"{sitemap_url}: {exc}")
            return
        try:
            urls, children = parse_sitemap(body)
        except ValueError as exc:
            errors.append(f"{sitemap_url}: {exc}")
            return
        if url_filter:
            urls = [u for u in urls if url_filter in _path_of(u.loc)]
        if excludes:
            urls = [u for u in urls if not _path_excluded(_path_of(u.loc), excludes)]
        collected.extend(urls)
        if len(collected) >= max_urls:
            stop = True
            return
        if depth < MAX_SITEMAP_DEPTH:
            children_newest_first = list(reversed(children))
            pattern = prefer_pattern or ""
            if pattern:
                # Core section first: rank children by how shallow the
                # pattern sits in the path (/posts/x beats /intels/posts/x;
                # non-matching children go last), stable within ranks.
                def _rank(child: SitemapUrl) -> tuple[bool, int]:
                    try:
                        path = urlsplit(child.loc).path
                    except ValueError:
                        return (True, 0)
                    at = path.find(pattern)
                    return (at < 0, at if at >= 0 else 0)

                children_newest_first = sorted(children_newest_first, key=_rank)
            for child in children_newest_first:
                _collect(child.loc, depth + 1)
                if stop:
                    return

    for sitemap_url in sitemap_urls:
        _collect(sitemap_url, 0)
        if stop:
            break
    ordered = sorted(
        collected,
        key=lambda e: (e.lastmod is None, -(e.lastmod.timestamp() if e.lastmod else 0.0)),
    )
    seen: set[str] = set()
    deduped: list[SitemapUrl] = []
    for entry in ordered:
        key = canonicalize_url(entry.loc)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(entry)
    return (deduped[:max_urls], errors)


def extract_hub_links(html: str, base_url: str, link_pattern: str) -> list[str]:
    """Article URLs from hub-page anchors and reader Markdown links.

    Pure function over markup: every ``<a href>`` and every Markdown link
    ``[label](target)`` — including labels that embed a thumbnail image
    (``[![alt](src) Kicker ## Headline](target)``), the reader-leg listing
    shape — is absolutized against the hub URL, kept only when same-host
    http(s) and its path contains `link_pattern` (e.g. ``/posts/``), deduped
    in document order. A bare image is not a link; an image wrapped in a link
    contributes the link target. Never raises on garbled markup —
    unparseable hrefs are skipped.
    """
    try:
        host = urlsplit(base_url).netloc.lower()
    except ValueError:
        return []
    candidates: list[tuple[int, str]] = []
    for match in _ANCHOR_HREF_RE.finditer(html or ""):
        raw = next((g for g in match.groups() if g is not None), None)
        if raw and raw.strip():
            candidates.append((match.start(), raw.strip()))
    for match in _MD_LINK_RE.finditer(html or ""):
        raw = match.group(1)
        if raw and raw.strip():
            candidates.append((match.start(), raw.strip()))
    candidates.sort(key=lambda item: item[0])
    found: list[str] = []
    seen: set[str] = set()
    for _, href in candidates:
        if href.lower().startswith(("javascript:", "mailto:", "#")):
            continue
        try:
            absolute = urljoin(base_url, href)
            parts = urlsplit(absolute)
        except ValueError:
            continue
        if parts.scheme not in ("http", "https"):
            continue
        if parts.netloc.lower() != host:
            continue
        if link_pattern not in parts.path:
            continue
        canonical = canonicalize_url(absolute)
        if canonical in seen:
            continue
        seen.add(canonical)
        found.append(absolute)
    return found


def discover_hub_urls(
    hub_urls: list[str],
    *,
    fetch_body: Callable[[str], bytes],
    link_pattern: str,
) -> tuple[list[SitemapUrl], list[str]]:
    """Fetch hub listings and extract pattern-matching anchors.

    One bad hub page is recorded in `errors` and never aborts the rest. A
    failure whose detail carries block evidence from the shared challenge
    family — or a served payload that is neither HTML nor link-bearing
    Markdown, i.e. an interstitial the reader leg mangled — is recorded as an
    explicit ``challenge`` cause naming the hub URL, never as a bare status.
    Hub URLs carry no lastmod (undated → sorted last downstream).
    """
    collected: list[SitemapUrl] = []
    errors: list[str] = []
    seen: set[str] = set()
    for hub_url in hub_urls:
        try:
            body = fetch_body(hub_url)
        except Exception as exc:
            detail = str(exc)
            challenge = _hub_challenge_detail(detail)
            errors.append(f"{hub_url}: {challenge if challenge else detail}")
            continue
        try:
            html = body.decode("utf-8", errors="replace")
        except Exception as exc:
            errors.append(f"{hub_url}: undecodable body ({exc})")
            continue
        try:
            links = extract_hub_links(html, hub_url, link_pattern)
        except Exception as exc:  # defensive: markup never aborts discovery
            errors.append(f"{hub_url}: anchor extraction failed ({exc})")
            continue
        if not links:
            mangled = _hub_payload_challenge_detail(html)
            if mangled is not None:
                errors.append(f"{hub_url}: {mangled}")
        for link in links:
            key = canonicalize_url(link)
            if key in seen:
                continue
            seen.add(key)
            collected.append(SitemapUrl(loc=link))
    return (collected, errors)


def robots_crawl_delay(robots_txt: str) -> float:
    """Extract the `User-agent: *` crawl-delay (seconds) from robots.txt.

    Best-effort: missing/garbled input yields 0.0 — never raises.
    """
    try:
        text = robots_txt or ""
        agents: list[str] = []
        seen_directive = False
        for raw_line in text.splitlines():
            line = raw_line.split("#", 1)[0].strip()
            if not line or ":" not in line:
                continue
            field, _, value = line.partition(":")
            field = field.strip().lower()
            value = value.strip()
            if field == "user-agent":
                # Consecutive user-agent lines share one block; a directive
                # in between starts a fresh group.
                if seen_directive:
                    agents = []
                    seen_directive = False
                agents.append(value.lower())
            elif field == "crawl-delay":
                seen_directive = True
                if any(a == "*" for a in agents):
                    try:
                        return max(0.0, float(value.split()[0]))
                    except (ValueError, IndexError):
                        continue
            else:
                seen_directive = True
        return 0.0
    except Exception:
        return 0.0


_TAG_RE = re.compile(r"<[^>]+>")
_LINK_TAG_RE = re.compile(r"<link\b[^>]*>", re.IGNORECASE)
_ATTR_RE_TEMPLATE = r"""{name}\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'`>]+))"""
_ANCHOR_HREF_RE = re.compile(
    r'<a\b[^>]*?\bhref\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|([^\s"\'`>]+))',
    re.IGNORECASE,
)
#: Markdown links, for the hub payloads the reader leg serves as provider
#: Markdown (Jina emits no HTML anchors at all). One form covers both a plain
#: link (``[label](target)``) and a link whose label embeds a thumbnail image,
#: with or without kicker text around it — ``[![alt](src)](target)`` and
#: ``[![alt](src) Kicker ## Headline](target)`` (the listing shape of several
#: hubs) both contribute the link target. A bare image is never a link: the
#: negative lookbehind keeps ``![alt](src)`` out.
_MD_LINK_RE = re.compile(
    r"(?<!!)\[(?:!\[[^\]]*\]\([^)]*\))?[^\]]*\]\(\s*([^)\s]+)",
    re.MULTILINE,
)
#: Reader-leg (Jina) Markdown header block: ``Title:``, ``URL Source:``,
#: ``Published Time:``, ``Author:`` lines before the body marker. The start
#: pattern identifies a payload that really carries that block, so scrape-leg
#: Markdown (no header) is never truncated at a body marker it happens to
#: mention.
_MD_HEADER_RE = re.compile(
    r"^[ \t]*(Title|URL Source|Published Time|Author)[ \t]*:[ \t]*(.*?)[ \t]*$",
    re.MULTILINE,
)
_MD_HEADER_START_RE = re.compile(r"[ \t\r\n]*Title[ \t]*:", re.IGNORECASE)
_MD_CONTENT_MARKER = "Markdown Content:"


def _tag_attr(tag: str, name: str) -> str | None:
    """Extract attribute `name` from an HTML tag string (any quote style)."""
    match = re.search(_ATTR_RE_TEMPLATE.format(name=re.escape(name)), tag, re.IGNORECASE)
    if not match:
        return None
    return next((g for g in match.groups() if g is not None), None)


def _meta_content(html: str, *, attr: str, value: str) -> str | None:
    """Content of the first <meta attr=value> tag (og:/article:/name matching).

    Entities are unescaped (``&amp;`` → ``&``), which is correct for both
    text (og:title) and URLs (og:url hrefs).
    """
    for match in re.finditer(r"<meta\b[^>]*>", html, re.IGNORECASE):
        tag = match.group(0)
        found = _tag_attr(tag, attr)
        if found is not None and found.strip().lower() == value.lower():
            content = _tag_attr(tag, "content")
            if content and content.strip():
                return _html.unescape(content.strip())
    return None


def extract_declared_canonical(html: str, base_url: str) -> str | None:
    """Declared canonical (link[rel=canonical], else og:url), absolutized.

    Returns None when undeclared — the caller falls back to the final URL.
    """
    for match in _LINK_TAG_RE.finditer(html):
        tag = match.group(0)
        rel = _tag_attr(tag, "rel")
        if rel is not None and "canonical" in rel.strip().lower().split():
            href = _tag_attr(tag, "href")
            if href and href.strip():
                return urljoin(base_url, _html.unescape(href.strip()))
    og_url = _meta_content(html, attr="property", value="og:url")
    if og_url:
        return urljoin(base_url, og_url)
    return None


_AUTHOR_ANCHOR_RE = re.compile(
    r'<a\b[^>]*\bclass\s*=\s*(?:"[^"]*author[^"]*"|\'[^\']*author[^\']*\')[^>]*>'
    r"([^<]{1,80})</a\s*>",
    re.IGNORECASE,
)
_TIME_DATETIME_RE = re.compile(
    r"<time\b[^>]*\bdatetime\s*=\s*(?:\"([^\"]+)\"|'([^']+)')",
    re.IGNORECASE,
)
_ID_RUN_RE = re.compile(r"\d+")
_LD_SCRIPT_RE = re.compile(
    r'<script\b[^>]*?\btype\s*=\s*(?:"application/ld\+json"|\'application/ld\+json\')[^>]*>(.*?)</script\s*>',
    re.IGNORECASE | re.DOTALL,
)

#: JSON-LD article types preferred for body selection (spec 06 Jing family).
_PREFERRED_LD_TYPES = frozenset({"newsarticle", "article", "blogposting"})


def _json_ld_blocks(html: str) -> list[dict[str, Any]]:
    """All parsed JSON-LD objects in document order (flattens @graph/lists).

    Best-effort: garbled blocks are skipped, never raise.
    """
    blocks: list[dict[str, Any]] = []
    try:
        scripts = list(_LD_SCRIPT_RE.finditer(html or ""))
    except Exception:
        return []
    for match in scripts:
        raw = (match.group(1) or "").strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            continue
        stack: list[Any] = [data]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                graph = item.get("@graph")
                if isinstance(graph, list):
                    stack.extend(reversed(graph))
                blocks.append(item)
            elif isinstance(item, list):
                stack.extend(reversed(item))
    return blocks


def _json_ld_value(html: str, key: str) -> str | None:
    """First non-empty string `key` across JSON-LD blocks (preferred types first)."""
    try:
        blocks = _json_ld_blocks(html)
    except Exception:
        return None
    ordered = sorted(
        blocks,
        key=lambda b: str(b.get("@type") or "").lower() not in _PREFERRED_LD_TYPES,
    )
    for block in ordered:
        try:
            value = block.get(key)
        except Exception:
            continue
        if isinstance(value, str) and value.strip():
            return _html.unescape(value.strip())
    return None


def _json_ld_author(html: str) -> str | None:
    """Author from JSON-LD: string, {"name"} dict, or first of a list."""
    try:
        blocks = _json_ld_blocks(html)
    except Exception:
        return None
    for block in blocks:
        try:
            author = block.get("author")
        except Exception:
            continue
        if isinstance(author, str) and author.strip():
            return _html.unescape(author.strip())
        if isinstance(author, dict):
            name = author.get("name")
            if isinstance(name, str) and name.strip():
                return _html.unescape(name.strip())
        if isinstance(author, list):
            for item in author:
                if isinstance(item, str) and item.strip():
                    return _html.unescape(item.strip())
                if isinstance(item, dict):
                    name = item.get("name")
                    if isinstance(name, str) and name.strip():
                        return _html.unescape(name.strip())
    return None


def extract_json_ld_body(html: str) -> str | None:
    """Full body from embedded JSON-LD ``articleBody`` (Next.js/Sanity family).

    Selects the first ``articleBody`` on a preferred article type
    (NewsArticle/Article/BlogPosting), else the first ``articleBody`` anywhere;
    related-article stubs without bodies are ignored. The body arrives already
    extracted, so it is returned as-is — never run back through the HTML
    cleaner — and callers decide on thinness downstream. Returns None when no
    usable structured body exists — the caller falls back to generic
    extraction. Never raises on garbled markup.
    """
    try:
        blocks = _json_ld_blocks(html)
    except Exception:
        return None
    fallback: str | None = None
    for block in blocks:
        try:
            body = block.get("articleBody")
        except Exception:
            continue
        if isinstance(body, list):
            body = " ".join(str(part) for part in body)
        if not isinstance(body, str) or not body.strip():
            continue
        if str(block.get("@type") or "").lower() in _PREFERRED_LD_TYPES:
            return body
        if fallback is None:
            fallback = body
    return fallback


def extract_title(html: str) -> str | None:
    """Article title: og:title, else twitter:title, else JSON-LD headline, else <title>."""
    for attr, value in (
        ("property", "og:title"),
        ("name", "twitter:title"),
        ("property", "twitter:title"),
    ):
        found = _meta_content(html, attr=attr, value=value)
        if found:
            return found
    headline = _json_ld_value(html, "headline")
    if headline:
        return headline
    match = re.search(r"<title[^>]*>(.*?)</title\s*>", html, re.IGNORECASE | re.DOTALL)
    if match:
        title = normalize_text(_html.unescape(_TAG_RE.sub(" ", match.group(1))))
        if title:
            return title
    return None


def extract_author(html: str) -> str | None:
    """Article author: meta name=author, article:author, JSON-LD author,
    else the first author-class anchor (widespread CMS byline pattern).

    Anchor text with "@" (mailto links) or without letters is rejected, so
    contact links never become bylines.
    """
    author = (
        _meta_content(html, attr="name", value="author")
        or _meta_content(html, attr="property", value="article:author")
        or _json_ld_author(html)
    )
    if author:
        return author
    try:
        match = _AUTHOR_ANCHOR_RE.search(html or "")
    except Exception:
        return None
    if not match:
        return None
    text = normalize_text(_html.unescape(match.group(1) or ""))
    if not text or "@" in text or "http" in text.lower():
        return None
    if not re.search(r"[A-Za-zÀ-ÿĀ-ž]", text):
        return None
    return text


def extract_published_raw(html: str) -> str | None:
    """Raw published timestamp: article:published_time, JSON-LD datePublished,
    <time datetime>, else an embedded datePublished string."""
    found = _meta_content(html, attr="property", value="article:published_time")
    if found:
        return found
    ld_date = _json_ld_value(html, "datePublished")
    if ld_date:
        return ld_date
    try:
        time_match = _TIME_DATETIME_RE.search(html or "")
    except Exception:
        time_match = None
    if time_match:
        raw = (time_match.group(1) or time_match.group(2) or "").strip()
        if raw:
            return raw
    match = re.search(r'"datePublished"\s*:\s*"([^"]+)"', html, re.IGNORECASE)
    if match:
        return match.group(1).strip() or None
    return None


def _markdown_header(text: str, name: str) -> str | None:
    """Value of a reader-leg Markdown header line (`Title: X`, ...); else None.

    The reader leg serves provider Markdown whose head is a ``key: value``
    block (``Title:``, ``URL Source:``, ``Published Time:``, ``Author:``)
    before the ``Markdown Content:`` marker. Never raises.
    """
    try:
        for match in _MD_HEADER_RE.finditer(text or ""):
            if match.group(1).lower() == name.lower():
                value = match.group(2).strip()
                return value or None
    except Exception:  # defensive: garbled headers are simply absent
        return None
    return None


def _markdown_body(text: str) -> str:
    """Reader-leg Markdown without its leading provider header block.

    Only a payload that actually starts with the reader's ``Title:`` header
    block is stripped (a scrape-leg payload carries no header, so it is
    returned unchanged); the stored body never includes the reader chrome.
    """
    body = text or ""
    if not _MD_HEADER_START_RE.match(body):
        return body
    marker = body.find(_MD_CONTENT_MARKER)
    if marker < 0:
        return body
    return body[marker + len(_MD_CONTENT_MARKER) :].lstrip("\r\n")


def _extract_body(
    html: str, final_url: str, url: str, extractor: str, markdown: bool = False
) -> str:
    """Select the article body text under the stanza extractor family.

    `markdown` marks a provider payload (reader/scrape leg): it arrives
    already extracted and is returned after the caller's emptiness check —
    never run back through the HTML cleaner — with the reader's header block
    stripped when present. Otherwise ``json-ld-first`` reads the embedded
    JSON-LD ``articleBody`` first (also provider text, returned as-is) and
    falls back to generic HTML-to-Markdown; every other value cleans the HTML
    directly. Raises :class:`ArticleExtractError` when nothing usable
    survives.
    """
    if markdown:
        return _markdown_body(html)
    if extractor == "json-ld-first":
        try:
            structured = extract_json_ld_body(html)
        except Exception:
            structured = None
        if structured and structured.strip():
            return structured.strip()
    try:
        return clean_to_markdown(html, final_url)
    except Exception as exc:
        raise ArticleExtractError(url, f"unparseable article body ({exc})") from exc


def _path_ids(url: str) -> set[str]:
    """Digit runs in the URL path (article IDs); empty set when none."""
    try:
        return set(_ID_RUN_RE.findall(urlsplit(url).path))
    except ValueError:
        return set()


def extract_article(
    *,
    url: str,
    final_url: str,
    html: str,
    source: str,
    language: str,
    retrieved_at: datetime,
    fallback_published: datetime | None = None,
    extractor: str = "generic",
    id_guard: bool = False,
    markdown: bool = False,
) -> NormalizedDocument:
    """Build a NormalizedDocument from one fetched article (family-switched).

    `extractor` selects the body strategy: ``"generic"`` cleans the HTML
    directly; ``"json-ld-first"`` reads the embedded JSON-LD ``articleBody``
    first (Next.js/Sanity family, where generic extraction goes thin) and
    falls back to generic extraction. Unknown values yield generic.
    `markdown` marks a provider payload (``ProviderMarkdown`` from the
    reader/scrape legs): it is already extracted, so it is stored as-is
    instead of being run back through the HTML cleaner, and its reader
    header block (``Title:`` / ``Published Time:`` / ``Author:`` before
    ``Markdown Content:``) supplies the title, timestamp, and author the
    HTML meta tags would have carried.
    Thin bodies are still returned — the thin threshold governs keep-vs-flag
    downstream (enrichment-keep + Extraction Flag path), never circumvention.

    `id_guard` (stanza-driven, e.g. National Jeweler) defeats numeric-ID
    reuse: when the declared same-host canonical carries digit runs disjoint
    from the fetched URL's, the page is a *different* article under a recycled
    ID and is skipped, never stored. Same-ID slug changes still store with
    the declared canonical; ID-free URLs are unaffected.

    Provenance mirrors the RSS lane: Source, final URL after redirects,
    declared canonical/og URL when same-host (else the final URL — never a
    wrong article), tz-aware timestamps, language, content hash over stored
    text. Raises :class:`ArticleExtractError` when the page holds no usable
    document (missing title, empty body, ID-reused) — the caller records an
    explicit per-document skip.
    """
    if extractor not in EXTRACTOR_FAMILIES:
        extractor = "generic"
    title = extract_title(html)
    if not title and markdown:
        # Reader-leg Markdown carries no HTML head; its header block does.
        title = _markdown_header(html, "Title")
    if not title:
        raise ArticleExtractError(url, "unparseable article: missing title")
    body = _extract_body(html, final_url, url, extractor, markdown)
    if not body.strip():
        raise ArticleExtractError(url, "unparseable article body: empty after cleaning")
    published_at = fallback_published
    raw_published = extract_published_raw(html)
    if not raw_published and markdown:
        raw_published = _markdown_header(html, "Published Time")
    if raw_published:
        try:
            published_at = coerce_tz_aware(date_parser.parse(raw_published))
        except (ValueError, OverflowError, TypeError):
            pass
    if published_at is None:
        published_at = retrieved_at
    else:
        published_at = coerce_tz_aware(published_at)
    declared = extract_declared_canonical(html, final_url)
    canonical = final_url
    if declared:
        try:
            same_host = urlsplit(declared).netloc.lower() == urlsplit(final_url).netloc.lower()
        except ValueError:
            same_host = False
        if same_host:
            if id_guard and canonicalize_url(declared) != canonicalize_url(final_url):
                final_ids = _path_ids(final_url)
                canon_ids = _path_ids(declared)
                if final_ids and canon_ids and final_ids.isdisjoint(canon_ids):
                    raise ArticleExtractError(
                        url,
                        f"canonical id mismatch (numeric-ID reuse): {final_url} "
                        f"declares {declared}",
                    )
            canonical = declared
    clean_title = normalize_text(title)
    clean_body = body.strip()
    author = extract_author(html)
    if not author and markdown:
        author = _markdown_header(html, "Author")
    return NormalizedDocument(
        source=source,
        url=final_url,
        canonical_url=canonicalize_url(canonical),
        title=clean_title,
        author=author.strip() or None if author else None,
        published_at=published_at,
        retrieved_at=coerce_tz_aware(retrieved_at),
        language=language,
        content=clean_body,
        content_hash=content_hash_for(clean_title, clean_body),
    )


#: Per-host in-flight fetch bound for the concurrent path (opt 1): one
#: fetch per host at a time preserves the serial pacing contract under
#: flow-level fan-out. Slots are process-global so every
#: ThreadPoolTaskRunner worker honors the same politeness budget.
_HOST_CONCURRENCY = 1

_host_slots: dict[str, threading.Semaphore] = {}
_host_slots_lock = threading.Lock()


def _host_slot(host: str) -> threading.Semaphore:
    """Process-global per-host fetch slot (created on first use). Never raises."""
    with _host_slots_lock:
        slot = _host_slots.get(host)
        if slot is None:
            slot = threading.Semaphore(_HOST_CONCURRENCY)
            _host_slots[host] = slot
        return slot


def paced_policy_fetch(
    url: str,
    *,
    policy: str = "impersonated-feed",
    gap_s: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
    timeout: int = DEFAULT_TIMEOUT,
) -> tuple[str, bytes]:
    """One polite fetch for the concurrent path: per-host slot + pacing + jitter.

    Holds the host slot across the pacing sleep and the fetch, so concurrent
    workers stay sequential per host with the same
    ``gap * (1 + jitter)`` rhythm as the serial harvest. `timeout` is the
    caller-side timeout every lane of the article chain honors.
    """
    try:
        host = urlsplit(url).netloc.lower()
    except ValueError:
        host = ""
    with _host_slot(host):
        sleep(max(0.0, gap_s) * (1.0 + random.uniform(0.0, 0.25)))
        return policy_get(url, policy=policy, timeout=timeout)


@dataclass(frozen=True)
class ArticleJob:
    """Immutable per-article work unit for flow-level fan-out (opt 1).

    Frozen (all immutables) so concurrent task workers never share mutable
    references: the V3 ``unmapped()`` aliasing hazard does not apply.
    Timestamps travel as ISO strings and are restored inside the worker.
    """

    loc: str
    lastmod_iso: str | None = None
    source_label: str = ""
    language: str = "en"
    retrieved_at_iso: str = ""
    extractor: str = "generic"
    id_guard: bool = False
    policy: str = "impersonated-feed"
    gap_s: float = 1.0
    timeout_s: int = DEFAULT_TIMEOUT


@dataclass
class HarvestPlan:
    """Serial, cheap discovery outcome: article jobs plus visible skip accounting.

    Everything up to the article loop (crawl-delay, sitemap traversal, hub
    fallback, empty→DiscoveryError) stays serial and
    ordered; only the fetch→decode→extract per-article work fans out.
    `fetch_fn` is the resolved ``(final_url, body)`` fetcher the plan used,
    so the serial driver reproduces identical pacing with the same callable.
    """

    jobs: list[ArticleJob] = field(default_factory=list)
    skipped: int = 0
    causes: list[str] = field(default_factory=list)
    source_label: str = ""
    policy: str = "impersonated-feed"
    gap_s: float = 1.0
    fetch_fn: Callable[[str], tuple[str, bytes]] | None = None


def fetch_extract_one(
    job: ArticleJob,
    *,
    fetch_one: Callable[[str], tuple[str, bytes]] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[NormalizedDocument | None, str | None]:
    """Fetch → decode → extract one planned article; never raises.

    Returns ``(document, None)`` on success else ``(None, cause)`` with the
    exact per-URL cause shapes the serial harvest records (``"<url>:
    <detail>"``), so concurrent assembly preserves skipped/causes counting.
    The default fetch is the polite concurrent fetch (per-host slot +
    pacing + jitter); the serial driver injects its own paced fetch.
    A body tagged :class:`ProviderMarkdown` (reader/scrape leg) is already
    extracted and is stored as-is instead of being cleaned again.
    """
    fetch = fetch_one or (
        lambda url: paced_policy_fetch(
            url, policy=job.policy, gap_s=job.gap_s, sleep=sleep, timeout=job.timeout_s
        )
    )
    try:
        final_url, raw = fetch(job.loc)
    except ArticleFetchError as exc:
        return (None, f"{job.loc}: {exc.detail}")
    except Exception as exc:  # defensive: fetch never aborts the harvest
        return (None, f"{job.loc}: fetch failed ({exc})")
    provider_markdown = isinstance(raw, ProviderMarkdown)
    try:
        html = raw.decode("utf-8", errors="replace")
    except Exception as exc:
        return (None, f"{job.loc}: undecodable body ({exc})")
    try:
        retrieved_at = coerce_tz_aware(datetime.fromisoformat(job.retrieved_at_iso))
        fallback = (
            coerce_tz_aware(datetime.fromisoformat(job.lastmod_iso)) if job.lastmod_iso else None
        )
        doc = extract_article(
            url=job.loc,
            final_url=final_url,
            html=html,
            source=job.source_label,
            language=job.language,
            retrieved_at=retrieved_at,
            fallback_published=fallback,
            extractor=job.extractor,
            id_guard=job.id_guard,
            markdown=provider_markdown,
        )
    except ArticleExtractError as exc:
        return (None, f"{job.loc}: {exc.detail}")
    except Exception as exc:  # defensive: extraction never aborts the harvest
        return (None, f"{job.loc}: extraction failed ({exc})")
    return (doc, None)


def plan_harvest(
    config: dict[str, Any],
    source_label: str,
    language: str,
    *,
    fetch: Callable[[str], tuple[str, bytes]] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    now: datetime | None = None,
) -> HarvestPlan:
    """Serial discovery planning for one hub/sitemap Source; never stores.

    Runs everything in :func:`harvest_sitemap_source` up to the article
    loop (stanza validation, robots crawl-delay, sitemap-first traversal,
    hub-anchor fallback) and returns a
    :class:`HarvestPlan` of immutable :class:`ArticleJob` units. Every
    discovered article URL is planned and fetched alike — robots Disallow
    is not an exclusion ground; only crawl-delay floors the pacing gap.
    Sitemap traversal fetches impersonated-only (:func:`fetch_sitemap_bytes`);
    the injected `fetch` override covers robots.txt, hub listings, and article
    bodies, whose shared chain keeps its reader/scrape fallback. A
    block/challenge payload on a sitemap URL is an explicit ``challenge``
    cause, never an XML-parse error; a hub whose fetch detail or served
    payload carries block evidence reads the same way. Excluded sitemap
    children are still traversed while their own loc's year-month is inside
    the freshness window (:func:`_excluded_child_is_fresh`), so the newest
    archive children do not drop the current month's live articles. `now`
    also anchors that window (default: current UTC).
    Raises
    :class:`DiscoveryError` when discovery yields zero URLs — the flow
    converts that into the explicit per-source error, exactly as a dead RSS
    feed surfaces. One bad sitemap or hub page never aborts the rest.
    """
    policy = str(config.get("policy") or "")
    policy = RETRIEVAL_POLICY_ALIASES.get(policy, policy)
    if policy not in RETRIEVAL_POLICIES:
        policy = str(DEFAULT_RETRIEVAL_POLICY["policy"])
    extractor = config.get("extractor")
    if extractor not in EXTRACTOR_FAMILIES:
        extractor = "generic"
    retrieval_type = config.get("type")
    pacing_ms = config.get("pacing_ms")
    if not isinstance(pacing_ms, int) or isinstance(pacing_ms, bool) or pacing_ms <= 0:
        pacing_ms = DEFAULT_PACING_MS
    max_urls = config.get("max_urls")
    if not isinstance(max_urls, int) or isinstance(max_urls, bool) or max_urls <= 0:
        max_urls = DEFAULT_MAX_URLS
    fetch_fn = fetch or (lambda url: policy_get(url, policy=str(policy)))
    retrieved_at = coerce_tz_aware(now or datetime.now(UTC))

    sitemap_urls = [str(u) for u in (config.get("sitemaps") or []) if str(u).strip()]
    hub = config.get("hub")
    hub_url = hub.strip() if isinstance(hub, str) and hub.strip() else ""
    hub_pages = [str(u) for u in (config.get("hub_pages") or []) if str(u).strip()]
    link_pattern = config.get("link_pattern")
    if not isinstance(link_pattern, str) or not link_pattern.strip():
        link_pattern = ""
    sitemap_pattern = config.get("sitemap_pattern")
    if not isinstance(sitemap_pattern, str) or not sitemap_pattern.strip():
        sitemap_pattern = ""
    excludes = _normalize_exclude(config.get("sitemap_exclude"))
    id_guard = bool(config.get("id_guard", False))
    host = ""
    for candidate in sitemap_urls + ([hub_url] if hub_url else []):
        if candidate.strip():
            host = urlsplit(candidate.strip()).netloc.lower()
            break
    crawl_delay = 0.0
    if host:
        try:
            _, robots_body = fetch_fn(f"https://{host}/robots.txt")
            crawl_delay = robots_crawl_delay(robots_body.decode("utf-8", errors="replace"))
        except Exception:
            crawl_delay = 0.0
    gap = max(pacing_ms / 1000.0, crawl_delay)

    def _pace() -> None:
        """One pacing gap: the shared gap plus bounded jitter."""
        sleep(gap * (1.0 + random.uniform(0.0, 0.25)))

    def _paced_fetch(url: str) -> tuple[str, bytes]:
        _pace()
        return fetch_fn(url)

    def _fetch_body(url: str) -> bytes:
        _, body = _paced_fetch(url)
        return body

    def _fetch_sitemap_body(url: str) -> bytes:
        """Paced sitemap fetch: impersonated-only, never the reader/scrape legs.

        Sitemap traversal is the one lane that must not fall back to a
        Markdown provider (see :func:`fetch_sitemap_bytes`), so it bypasses
        the injected `fetch` — that override stays the article/hub/robots
        seam. Same pacing rhythm as every other planning fetch.
        """
        _pace()
        return fetch_sitemap_bytes(url)

    discovered, errors = discover_urls(
        sitemap_urls,
        fetch_body=_fetch_sitemap_body,
        max_urls=max_urls,
        prefer_pattern=sitemap_pattern or link_pattern or None,
        url_filter=sitemap_pattern or None,
        sitemap_exclude=excludes or None,
        now=retrieved_at,
    )
    if hub_url:
        # The hub is the listing, not an article: never ingest it as one.
        # (Hub content enters via anchor extraction for hub-phase types.)
        hub_key = canonicalize_url(hub_url)
        discovered = [e for e in discovered if canonicalize_url(e.loc) != hub_key]
    if retrieval_type in ("sitemap+hub", "hub", "url-set+hub") and hub_url and link_pattern:
        hub_found, hub_errors = discover_hub_urls(
            [hub_url, *hub_pages],
            fetch_body=_fetch_body,
            link_pattern=link_pattern,
        )
        errors.extend(hub_errors)
        known = {canonicalize_url(entry.loc) for entry in discovered}
        for entry in hub_found:
            if _path_excluded(_path_of(entry.loc), excludes):
                continue
            if canonicalize_url(entry.loc) not in known:
                known.add(canonicalize_url(entry.loc))
                discovered.append(entry)
        discovered = discovered[:max_urls]
    if not discovered:
        detail = "; ".join(errors) if errors else "no sitemap URLs declared"
        raise DiscoveryError(f"sitemap discovery for {source_label} yielded no URLs: {detail}")
    retrieved_iso = retrieved_at.isoformat()
    jobs = [
        ArticleJob(
            loc=entry.loc,
            lastmod_iso=entry.lastmod.isoformat() if entry.lastmod else None,
            source_label=source_label,
            language=language,
            retrieved_at_iso=retrieved_iso,
            extractor=str(extractor),
            id_guard=id_guard,
            policy=str(policy),
            gap_s=gap,
            timeout_s=DEFAULT_TIMEOUT,
        )
        for entry in discovered
    ]
    return HarvestPlan(
        jobs=jobs,
        causes=list(errors),
        source_label=source_label,
        policy=str(policy),
        gap_s=gap,
        fetch_fn=fetch_fn,
    )


def harvest_sitemap_source(
    config: dict[str, Any],
    source_label: str,
    language: str,
    *,
    fetch: Callable[[str], tuple[str, bytes]] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    now: datetime | None = None,
) -> HarvestReport:
    """Discover → fetch → extract one hub/sitemap Source; never stores, only builds.

    Discovery order is sitemap-first, hub-anchor fallback second: declared
    sitemaps are traversed newest-first (distilled by the stanza sitemap
    pattern when declared, so whole-site urlsets yield the declared beat;
    entries matching the stanza sitemap-exclude list, e.g. ``/webstories/``,
    are dropped before the backfill budget applies),
    then hub listings (hub + declared pagination pages) contribute
    pattern-matching anchors not already discovered, all bounded to
    `max_urls`. The stanza `extractor` family switches body selection per
    article; the stanza `id_guard` defeats numeric-ID reuse per article.
    Returns a :class:`HarvestReport` (documents plus explicit per-document
    skips). Raises
    :class:`DiscoveryError` when discovery yields zero URLs — the flow
    converts that into the explicit per-source error, exactly as a dead RSS
    feed surfaces. One bad sitemap, hub page, or article never aborts the rest.

    Serial driver over :func:`plan_harvest` + :func:`fetch_extract_one`:
    the flow fans the same immutable jobs out concurrently, so both paths
    share planning, cause shapes, and skip accounting exactly.
    """
    plan = plan_harvest(config, source_label, language, fetch=fetch, sleep=sleep, now=now)
    fetch_fn = plan.fetch_fn or (lambda url: policy_get(url, policy=plan.policy))

    def _serial_fetch(url: str) -> tuple[str, bytes]:
        sleep(plan.gap_s * (1.0 + random.uniform(0.0, 0.25)))
        return fetch_fn(url)

    report = HarvestReport(documents=[], skipped=plan.skipped, causes=list(plan.causes))
    for job in plan.jobs:
        doc, cause = fetch_extract_one(job, fetch_one=_serial_fetch)
        if doc is not None:
            report.documents.append(doc)
        else:
            report.skipped += 1
            report.causes.append(str(cause))
    return report
