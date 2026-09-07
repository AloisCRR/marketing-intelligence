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
  Still-thin results are kept-aside with explicit causes (flag path), never
  bypassed.
- Politeness: robots.txt crawl-delay honored (effective pacing is
  max(stanza pacing, crawl-delay) plus jitter), sequential requests with
  explicit timeouts; transient 403s retried once under the impersonated-feed
  policy, persistent denials recorded as explicit per-document errors.
- Canonical guard: the final URL after redirects is the fetch identity; the
  declared canonical/og URL is stored only when same-host, else the final
  URL wins — a wrong article is never stored, and slug changes collapse on
  the content-hash dedupe downstream.
"""

from __future__ import annotations

import html as _html
import json
import random
import re
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin, urlsplit

from dateutil import parser as date_parser

from brain.enrich import clean_to_markdown
from brain.ingest import USER_AGENT, _feed_blocked
from brain.normalize import (
    NormalizedDocument,
    canonicalize_url,
    coerce_tz_aware,
    content_hash_for,
    normalize_text,
)
from brain.sources import DEFAULT_MAX_URLS, DEFAULT_PACING_MS, EXTRACTOR_FAMILIES

try:  # optional impersonated-retry backend (mirrors brain.ingest)
    from curl_cffi import requests as _curl_cffi_requests
except Exception:  # pragma: no cover - stdlib-only environments
    _curl_cffi_requests = None  # type: ignore[assignment]

#: Explicit timeout (s) for every discovery/extraction request.
DEFAULT_TIMEOUT = 30

#: Maximum sitemap-nesting depth traversed (index → nested index → URL set).
MAX_SITEMAP_DEPTH = 2

#: Error-body bytes inspected for challenge evidence on the stdlib attempt.
_ERROR_BODY_CAP = 65536


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


#: Identity for discovery fetches (sitemaps, robots, articles): the honest
#: feed-lane UA. Verified live 2026-09-06: MarketingDirecto serves sitemaps
#: and articles (200) to this UA while refusing browser-mimicking headers
#: (403) on the same endpoints — impersonation would be both ruder and broken.
DISCOVERY_HEADERS: dict[str, str] = {"User-Agent": USER_AGENT}


def _stdlib_get(url: str, timeout: int = DEFAULT_TIMEOUT) -> tuple[str, bytes]:
    """GET `url` with plain urllib + feed-lane identity; return (final_url, body)."""
    request = urllib.request.Request(url, headers=dict(DISCOVERY_HEADERS))
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return (str(response.geturl() or url), bytes(response.read()))


def _impersonated_get(url: str, timeout: int = DEFAULT_TIMEOUT) -> tuple[str, bytes]:
    """GET `url` with curl_cffi Chrome impersonation; return (final_url, body)."""
    from brain.enrich import BROWSER_HEADERS

    assert _curl_cffi_requests is not None  # guarded by policy_get
    try:
        response = _curl_cffi_requests.get(
            url, impersonate="chrome", headers=dict(BROWSER_HEADERS), timeout=timeout
        )
    except Exception as exc:
        raise ArticleFetchError(url, f"fetch failed for {url}: {exc}") from exc
    body = bytes(response.content or b"")
    status = int(response.status_code)
    if status >= 400:
        snippet = body[:512].decode("utf-8", errors="replace").strip()
        detail = f"HTTP Error {status}"
        if snippet:
            detail += f" — body: {snippet[:512]}"
        raise ArticleFetchError(url, f"fetch failed for {url}: {detail}")
    return (str(getattr(response, "url", url) or url), body)


def policy_get(
    url: str, *, policy: str = "stdlib-only", timeout: int = DEFAULT_TIMEOUT
) -> tuple[str, bytes]:
    """GET `url` under the stanza policy; return (final_url, body).

    Mirrors the feed lane (`brain.ingest.fetch_rss`): stdlib first, and only
    when that attempt meets 403/challenge evidence does ``impersonated-feed``
    retry once via curl_cffi — any other failure is an explicit
    :class:`ArticleFetchError`. Never returns a wrong article: HTTP errors
    raise, they never yield bytes.
    """
    try:
        return _stdlib_get(url, timeout)
    except urllib.error.HTTPError as exc:
        body = b""
        try:
            body = bytes(exc.read(_ERROR_BODY_CAP) or b"")
        except Exception:
            body = b""
        snippet = body[:512].decode("utf-8", errors="replace").strip()
        status = int(exc.code)
        if policy != "impersonated-feed" or not _feed_blocked(status, snippet):
            raise ArticleFetchError(
                url, f"fetch failed for {url}: HTTP Error {status}: {exc.reason}"
            ) from exc
        blocked_detail = f"HTTP Error {status}: {exc.reason}"
        if snippet:
            blocked_detail += f" — body: {snippet[:512]}"
    except ArticleFetchError:
        raise
    except Exception as exc:
        raise ArticleFetchError(url, f"fetch failed for {url}: {exc}") from exc
    if _curl_cffi_requests is None:
        raise ArticleFetchError(
            url,
            f"fetch failed for {url}: {blocked_detail} "
            "(curl_cffi unavailable for impersonated retry)",
        )
    return _impersonated_get(url, timeout)


def _normalize_exclude(raw: Any) -> list[str]:
    """Normalize a sitemap-exclude stanza value to non-empty substrings.

    Accepts a single string or a list of strings (anything else yields []);
    whitespace-only entries are dropped. Never raises.
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
    payload = xml.decode("utf-8", errors="replace") if isinstance(xml, bytes) else xml
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
    `max_urls` backfill budget — genuine articles fill it instead.
    Traversal stops fetching new children once `max_urls` is reached — the
    bounded backfill. One bad sitemap is recorded in `errors` and never aborts
    the rest. Results are sorted newest-first by lastmod (undated last),
    deduped by canonical URL, and bounded to `max_urls`.
    """
    collected: list[SitemapUrl] = []
    errors: list[str] = []
    visited: set[str] = set()
    stop = False
    excludes = _normalize_exclude(sitemap_exclude)

    def _collect(sitemap_url: str, depth: int) -> None:
        nonlocal stop
        if stop or sitemap_url in visited:
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
            urls = [u for u in urls if not any(x in _path_of(u.loc) for x in excludes)]
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
    """Article URLs from hub-page anchors matching the stanza link pattern.

    Pure function over markup: every ``<a href>`` is absolutized against the
    hub URL, kept only when same-host http(s) and its path contains
    `link_pattern` (e.g. ``/posts/``), deduped in document order. Never
    raises on garbled markup — unparseable hrefs are skipped.
    """
    try:
        host = urlsplit(base_url).netloc.lower()
    except ValueError:
        return []
    found: list[str] = []
    seen: set[str] = set()
    for match in _ANCHOR_HREF_RE.finditer(html or ""):
        raw = next((g for g in match.groups() if g is not None), None)
        if not raw or not raw.strip():
            continue
        href = raw.strip()
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

    One bad hub page is recorded in `errors` and never aborts the rest.
    Hub URLs carry no lastmod (undated → sorted last downstream).
    """
    collected: list[SitemapUrl] = []
    errors: list[str] = []
    seen: set[str] = set()
    for hub_url in hub_urls:
        try:
            body = fetch_body(hub_url)
        except Exception as exc:
            errors.append(f"{hub_url}: {exc}")
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


def _robots_star_rules(robots_txt: str) -> tuple[list[str], list[str]]:
    """Allow/Disallow paths declared for `User-agent: *` groups.

    Same grouping semantics as :func:`robots_crawl_delay`: consecutive
    user-agent lines share one block, and any directive line starts a fresh
    group. Returns (allows, disallows) in document order, deduped. Absolute-URL
    values (e.g. ``Allow: https://host/sitemap.xml``) are reduced to their
    path; empty Disallow values mean allow-all and are dropped. Never raises.
    """
    allows: list[str] = []
    disallows: list[str] = []
    try:
        agents: list[str] = []
        seen_directive = False
        for raw_line in (robots_txt or "").splitlines():
            line = raw_line.split("#", 1)[0].strip()
            if not line or ":" not in line:
                continue
            field, _, value = line.partition(":")
            field = field.strip().lower()
            value = value.strip().split()[0] if value.strip() else ""
            if field == "user-agent":
                if seen_directive:
                    agents = []
                    seen_directive = False
                agents.append(value.lower())
            elif field in ("allow", "disallow"):
                seen_directive = True
                if not any(a == "*" for a in agents):
                    continue
                if not value:
                    continue  # empty Disallow/Allow: allow-all, no rule
                if "://" in value:
                    try:
                        value = urlsplit(value).path or "/"
                    except ValueError:
                        continue
                if not value.startswith("/"):
                    continue  # not a path rule; ignore
                target = allows if field == "allow" else disallows
                if value not in target:
                    target.append(value)
            else:
                seen_directive = True
    except Exception:
        return ([], [])
    return (allows, disallows)


def robots_disallowed_paths(robots_txt: str) -> list[str]:
    """Disallow paths declared for `User-agent: *` in robots.txt.

    Best-effort: missing/garbled input yields [] — never raises.
    """
    try:
        _, disallows = _robots_star_rules(robots_txt)
        return list(disallows)
    except Exception:
        return []


def robots_is_disallowed(url: str, robots_txt: str) -> bool:
    """True when `url` falls under a `User-agent: *` Disallow.

    Longest-prefix match per RFC (``Disallow: /posts/private`` covers
    ``/posts/private*``); on ties the Allow wins, so an explicit Allow
    exception beats a Disallow covering it. Query strings participate in the
    match (``path[?query]``). Empty Disallow means allow-all; missing/garbled
    input (or an unparseable URL) allows — never raises.
    """
    try:
        allows, disallows = _robots_star_rules(robots_txt)
        if not disallows:
            return False
        try:
            parts = urlsplit(url)
            target = parts.path or "/"
            if parts.query:
                target += "?" + parts.query
        except ValueError:
            return False
        best_allow = max((len(a) for a in allows if target.startswith(a)), default=-1)
        best_deny = max((len(d) for d in disallows if target.startswith(d)), default=-1)
        return best_deny >= 0 and best_deny > best_allow
    except Exception:
        return False


_TAG_RE = re.compile(r"<[^>]+>")
_LINK_TAG_RE = re.compile(r"<link\b[^>]*>", re.IGNORECASE)
_ATTR_RE_TEMPLATE = r"""{name}\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'`>]+))"""
_ANCHOR_HREF_RE = re.compile(
    r'<a\b[^>]*?\bhref\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|([^\s"\'`>]+))',
    re.IGNORECASE,
)


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
    related-article stubs without bodies are ignored. Returns None when no
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
            cleaned = clean_to_markdown(body, "")
            if cleaned.strip():
                return cleaned.strip()
        elif fallback is None:
            cleaned = clean_to_markdown(body, "")
            if cleaned.strip():
                fallback = cleaned.strip()
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


def _extract_body(html: str, final_url: str, url: str, extractor: str) -> str:
    """Select the article body text under the stanza extractor family.

    ``json-ld-first`` reads the embedded JSON-LD ``articleBody`` first and
    falls back to generic HTML-to-Markdown; every other value cleans the
    HTML directly. Raises :class:`ArticleExtractError` when nothing usable
    survives.
    """
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
) -> NormalizedDocument:
    """Build a NormalizedDocument from one fetched article (family-switched).

    `extractor` selects the body strategy: ``"generic"`` cleans the HTML
    directly; ``"json-ld-first"`` reads the embedded JSON-LD ``articleBody``
    first (Next.js/Sanity family, where generic extraction goes thin) and
    falls back to generic extraction. Unknown values yield generic.
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
    if not title:
        raise ArticleExtractError(url, "unparseable article: missing title")
    body = _extract_body(html, final_url, url, extractor)
    if not body.strip():
        raise ArticleExtractError(url, "unparseable article body: empty after cleaning")
    published_at = fallback_published
    raw_published = extract_published_raw(html)
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
    """
    policy = config.get("policy")
    if policy not in ("stdlib-only", "impersonated-feed"):
        policy = "stdlib-only"
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
    robots_txt = ""
    if host:
        try:
            _, robots_body = fetch_fn(f"https://{host}/robots.txt")
            robots_txt = robots_body.decode("utf-8", errors="replace")
            crawl_delay = robots_crawl_delay(robots_txt)
        except Exception:
            crawl_delay = 0.0
            robots_txt = ""
    gap = max(pacing_ms / 1000.0, crawl_delay)

    def _paced_fetch(url: str) -> tuple[str, bytes]:
        sleep(gap * (1.0 + random.uniform(0.0, 0.25)))
        return fetch_fn(url)

    def _fetch_body(url: str) -> bytes:
        _, body = _paced_fetch(url)
        return body

    discovered, errors = discover_urls(
        sitemap_urls,
        fetch_body=_fetch_body,
        max_urls=max_urls,
        prefer_pattern=sitemap_pattern or link_pattern or None,
        url_filter=sitemap_pattern or None,
        sitemap_exclude=excludes or None,
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
            if excludes and any(x in _path_of(entry.loc) for x in excludes):
                continue
            if canonicalize_url(entry.loc) not in known:
                known.add(canonicalize_url(entry.loc))
                discovered.append(entry)
        discovered = discovered[:max_urls]
    if robots_txt:
        # Robots Disallow filtering: discovered article URLs under a
        # `User-agent: *` Disallow are explicit skips (never fetched or
        # extracted). Hub listing pages themselves are never filtered —
        # only the article URLs discovered from sitemaps/hubs.
        kept: list[SitemapUrl] = []
        robots_skipped = 0
        for entry in discovered:
            if robots_is_disallowed(entry.loc, robots_txt):
                errors.append(f"{entry.loc}: disallowed by robots.txt")
                robots_skipped += 1
            else:
                kept.append(entry)
        discovered = kept
    else:
        robots_skipped = 0
    report = HarvestReport(causes=list(errors))
    report.skipped += robots_skipped
    if not discovered:
        detail = "; ".join(errors) if errors else "no sitemap URLs declared"
        raise DiscoveryError(f"sitemap discovery for {source_label} yielded no URLs: {detail}")
    for entry in discovered:
        try:
            final_url, raw = _paced_fetch(entry.loc)
        except ArticleFetchError as exc:
            report.skipped += 1
            report.causes.append(f"{entry.loc}: {exc.detail}")
            continue
        except Exception as exc:  # defensive: fetch never aborts the harvest
            report.skipped += 1
            report.causes.append(f"{entry.loc}: fetch failed ({exc})")
            continue
        try:
            html = raw.decode("utf-8", errors="replace")
        except Exception as exc:
            report.skipped += 1
            report.causes.append(f"{entry.loc}: undecodable body ({exc})")
            continue
        try:
            report.documents.append(
                extract_article(
                    url=entry.loc,
                    final_url=final_url,
                    html=html,
                    source=source_label,
                    language=language,
                    retrieved_at=retrieved_at,
                    fallback_published=entry.lastmod,
                    extractor=str(extractor),
                    id_guard=id_guard,
                )
            )
        except ArticleExtractError as exc:
            report.skipped += 1
            report.causes.append(f"{entry.loc}: {exc.detail}")
        except Exception as exc:  # defensive: extraction never aborts the harvest
            report.skipped += 1
            report.causes.append(f"{entry.loc}: extraction failed ({exc})")
    return report
