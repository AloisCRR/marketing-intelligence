"""Thin-triggered Markdown enrichment (ticket 02).

Deterministic extraction (no language-model calls), on the user-ordered fetch
chain: impersonated Chrome, then Jina reader, then Firecrawl.

- :func:`is_thin` decides whether an RSS body is worth enriching.
- :func:`clean_to_markdown` converts article HTML to clean Markdown, with
  per-domain post-processing for the National Jeweler family (chrome blocks
  stripped, inline editorial anchors flattened to plain words, boilerplate
  trailer cut) so harvested and enriched bodies both stay clean.
- :func:`fetch_and_clean` retrieves an article URL over HTTPS and cleans it
  with the single primary backend: Chrome browser impersonation
  (``curl_cffi``: TLS fingerprint plus genuine browser headers — never any
  crawler/bot UA, no cookies/credentials ever). ``curl_cffi`` is a hard
  dependency; when it is unavailable the primary raises an explicit
  :class:`FetchFailed` — there is no stdlib fetch lane.
- :func:`enrich_document` enriches one thin document (raises typed errors).
- :func:`enrich_document_or_keep` never raises: failure keeps the RSS body.

Gated fallback chain (ticket 03): when the primary extractor misses a page — a
bot/challenge/rate-limit fetch error, an empty primary output, or a JS-shell
output — :func:`enrich_document` walks the fallback chain once: first
:func:`try_fallback_reader` (a zero-ops Jina-reader-style HTTPS GET with
data-minimizing headers: explicit User-Agent, ``Accept: text/*``, never any
Cookie/Authorization credentials, backoff on 429 honoring Retry-After up to 2
retries), then ``fetch_via_firecrawl`` (the paid last resort, imported lazily
from :mod:`marketing_intelligence.firecrawl`; Bearer key from
``FIRECRAWL_API_KEY`` read at call time, never logged, absent key an explicit
skipped cause). Any fallback failure raises a typed error that chains every
prior cause, so :func:`enrich_document_or_keep` still keeps the RSS body.

Per-source enrichment policy (threshold overrides, force-on/off) is ticket 03;
the threshold here is a hardcoded default with an optional per-call override.
The `force` flag on :func:`enrich_document` / :func:`enrich_document_or_keep`
is the policy seam the flow uses for ``force_on`` sources.
"""

from __future__ import annotations

import gzip
import html as _html
import http
import re
import time
import urllib.error
import urllib.request
import zlib
from urllib.parse import urlsplit

from marketing_intelligence.ingest import USER_AGENT
from marketing_intelligence.normalize import NormalizedDocument, make_document, normalize_text

#: RSS bodies shorter than this (after whitespace collapse) trigger enrichment.
DEFAULT_THIN_THRESHOLD = 500

#: Real-Chrome browser identity for the primary fetch. Chrome impersonation
#: plus genuine browser headers only: never Googlebot or any crawler UA, no
#: Referer (no third-party endorsement implied), no cookies/credentials ever.
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

#: Genuine browser headers sent by both primary backends (document navigation).
BROWSER_HEADERS: dict[str, str] = {
    "User-Agent": BROWSER_USER_AGENT,
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}

try:  # primary backend: TLS + browser impersonation (hard dep)
    from curl_cffi import requests as _curl_cffi_requests
except Exception:  # pragma: no cover - primary raises explicitly when absent
    _curl_cffi_requests = None  # type: ignore[assignment]

try:  # preferred article extractor (optional dep, deterministic, no LLM)
    import trafilatura as _trafilatura
except Exception:  # pragma: no cover - regex fallback below
    _trafilatura = None  # type: ignore[assignment]

#: Jina-reader-style zero-ops fallback endpoint: GET <base><article-url>.
FALLBACK_READER_BASE = "https://r.jina.ai/"

#: Fallback GETs per call: the initial attempt plus this many 429 retries.
FALLBACK_MAX_RETRIES = 2

#: Upper bound honoring a 429 Retry-After (ingestion must never sleep unbounded).
FALLBACK_RETRY_CAP_SECONDS = 60.0

#: Native timeout (s) for the primary article fetch (opt 4 split: 10s feeds,
#: 15s articles, 30s fallback reader). Prefect `timeout_seconds` cannot
#: preempt blocking I/O, so the split lives in the clients.
PRIMARY_TIMEOUT = 15

#: Native timeout (s) for the gated fallback reader only.
FALLBACK_TIMEOUT = 30

#: Failure headers the fallback always sends (and the only ones it ever sends).
FALLBACK_ACCEPT = "text/*"

METHOD_RSS = "rss"
METHOD_ENRICHED = "enriched"

_BLOCK_RE = re.compile(
    r"</?(?:p|div|article|section|header|footer|h[1-6]|li|ul|ol|blockquote|pre|br)[^>]*>",
    re.IGNORECASE,
)
_TAG_RE = re.compile(r"<[^>]+>")
_ANCHOR_RE = re.compile(
    r'<a\s[^>]*?href=(?:"([^"]*)"|\'([^\']*)\'|([^\s>]+))[^>]*>(.*?)</a\s*>',
    re.IGNORECASE | re.DOTALL,
)
_WS_RE = re.compile(r"\s+")

#: Hosts in the National Jeweler family. Their article pages share one CMS
#: shape: a ``trix-content`` body wrapped in related-articles and "The Latest"
#: sidebar blocks that generic extraction renders into the stored body as
#: navigation chrome (and inline editorial anchors that render as links).
NJ_FAMILY_HOSTS = ("nationaljeweler.com",)

#: Container tags whose class/id markers identify NJ boilerplate chrome.
#: Inline tags (a/img) are never stripped here: editorial anchors are handled
#: at the Markdown level, where they become plain words instead of vanishing.
_NJ_CONTAINER_TAGS = frozenset(
    {"div", "aside", "section", "nav", "ul", "ol", "header", "footer", "form", "figure", "table"}
)

#: HTML void elements: opening tags that never own a closing tag.
_VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)

#: class/id substrings marking NJ boilerplate containers (removed with
#: contents before conversion). Scoped to the NJ family only; NJ article-body
#: containers are ``trix-content`` / ``article__head`` and never carry these.
_NJ_BOILERPLATE_MARKERS = (
    "related",
    "the-latest",
    "thelatest",
    "latest-news",
    "latest",
    "sidebar",
    "side-bar",
    "most-read",
    "most-popular",
    "popular",
    "recommended",
    "read-more",
    "readmore",
    "newsletter",
    "outbrain",
    "taboola",
    "site-nav",
    "main-nav",
    "breadcrumb",
    "site-header",
    "site-footer",
    "social-share",
    "share-tools",
)

#: Markdown headings that begin a boilerplate trailer on NJ pages; anything
#: from such a heading onward is chrome, never article text.
_NJ_TRAILER_HEADING_RE = re.compile(
    r"^(?:#{1,6}\s*)?(?:the latest|related (?:articles|stories|posts|content)|"
    r"most (?:popular|read)|recommended|read more|more stories|more from|"
    r"you may also like|from our partners)\s*:?\s*$",
    re.IGNORECASE,
)

#: class/id attribute values (quoted or bare), scanned for boilerplate markers.
_CLASS_OR_ID_RE = re.compile(
    r"""(?:class|id)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""",
    re.IGNORECASE,
)

#: Opening/closing HTML tags, for the NJ boilerplate scanner.
_HTML_TAG_RE = re.compile(r"<[^>]*>")
_OPEN_TAG_RE = re.compile(r"<\s*([a-zA-Z][\w:-]*)((?:\s[^>]*?)?)\s*/?>")
_CLOSE_TAG_RE = re.compile(r"</\s*([a-zA-Z][\w:-]*)\s*>")

#: Markdown image and link syntax, for the NJ link-flattening pass.
_MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_MD_LINK_RE = re.compile(r"\[([^\]\[]*)\]\([^)]*\)")

#: Non-visible blocks whose *contents* are never article text (scripts carry
#: code and embedded JSON-LD, styles carry CSS). Stripped with contents by
#: the regex fallback so Next.js shells and structured-data blobs cannot
#: masquerade as article bodies; the trafilatura path already drops these.
_NON_VISIBLE_RE = re.compile(
    r"<(script|style|noscript|template)\b[^>]*>.*?</\1\s*>",
    re.IGNORECASE | re.DOTALL,
)

#: Markers that, inside a fetch *failure message* (HTTP status line plus any
#: captured error-body snippet), identify bot/challenge protection or rate
#: limiting. Deliberately specific: a plain "403 paywall" with no challenge
#: evidence stays a keep-RSS failure and never triggers fallback traffic.
_CHALLENGE_KEYWORDS = (
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
    "__next_data__",
)

#: Signals that the primary miss was rate limiting rather than a block.
_RATE_LIMIT_SIGNALS = (
    "429",
    "too many requests",
    "rate limit",
    "rate-limit",
    "rate_limited",
)

#: Markers of a JS application shell (no article text rendered server-side).
#: Only treated as a miss when the cleaned output is *also* thin, so a full
#: article that merely mentions JavaScript is never refetched.
_JS_SHELL_MARKERS = (
    "__next_data__",
    "__nuxt__",
    "enable javascript",
    "javascript is required",
    "javascript must be enabled",
    "you need to enable javascript",
    "please enable javascript",
)


class EnrichmentError(Exception):
    """Base class for deterministic enrichment failures (keeps RSS body)."""


class FetchFailed(EnrichmentError):
    """Article URL could not be retrieved (timeout, paywall, bot protection)."""


class UnparseableBody(EnrichmentError):
    """Retrieved bytes held no usable text after cleaning."""


def is_thin(text: str | None, threshold: int = DEFAULT_THIN_THRESHOLD) -> bool:
    """True when `text` is empty or shorter than `threshold` characters.

    Whitespace is stripped and collapsed before measuring, so padding and
    feed boilerplate cannot masquerade as substance.
    """
    collapsed = normalize_text(text or "")
    return not collapsed or len(collapsed) < threshold


def _is_nj_family(url: str) -> bool:
    """True when `url` belongs to the National Jeweler host family."""
    host = urlsplit(url or "").hostname or ""
    host = host.lower()
    return any(host == base or host.endswith("." + base) for base in NJ_FAMILY_HOSTS)


def _nj_marker_in(tag_attrs: str) -> bool:
    """True when a tag's class/id value carries an NJ boilerplate marker."""
    for quoted, single, bare in _CLASS_OR_ID_RE.findall(tag_attrs):
        value = (quoted or single or bare).lower()
        if any(marker in value for marker in _NJ_BOILERPLATE_MARKERS):
            return True
    return False


def _strip_nj_boilerplate(html: str) -> str:
    """Drop NJ chrome containers (related blocks, "The Latest", nav) from HTML.

    Depth-aware scan: a marked container and everything nested inside it is
    removed up to its matching close tag, so nested markup never leaks the
    remainder of a sidebar back into the body. ``<nav>`` is always chrome on
    the NJ family; other containers need a class/id marker. Inline tags are
    left for the Markdown pass, which keeps editorial anchor text.
    """
    source = html or ""
    out: list[str] = []
    pos = 0
    skip_depth = 0
    skip_tag = ""
    for match in _HTML_TAG_RE.finditer(source):
        if not skip_depth:
            out.append(source[pos : match.start()])
        token = match.group(0)
        if skip_depth == 0:
            opening = _OPEN_TAG_RE.match(token)
            if opening:
                tag = opening.group(1).lower()
                attrs = opening.group(2) or ""
                strip = tag == "nav" or (tag in _NJ_CONTAINER_TAGS and _nj_marker_in(attrs))
                if strip and not token.rstrip().endswith("/>") and tag not in _VOID_TAGS:
                    skip_depth, skip_tag = 1, tag
                    pos = match.end()
                    continue
            out.append(token)
        else:
            closing = _CLOSE_TAG_RE.match(token)
            if closing:
                if closing.group(1).lower() == skip_tag:
                    skip_depth -= 1
                    if skip_depth == 0:
                        skip_tag = ""
            else:
                opening = _OPEN_TAG_RE.match(token)
                if opening and opening.group(1).lower() == skip_tag:
                    if not token.rstrip().endswith("/>"):
                        skip_depth += 1
        pos = match.end()
    if not skip_depth:
        out.append(source[pos:])
    return "".join(out)


def _nj_clean_markdown(markdown: str) -> str:
    """Flatten NJ anchors to plain words and cut any boilerplate trailer.

    Inline editorial anchors keep their label text (never a URL), images are
    dropped, and everything from a known chrome heading ("The Latest",
    "Related Articles", "Most Popular", ...) onward is discarded, so the lede
    stays first and sentence text stays intact.
    """
    text = _MD_IMAGE_RE.sub("", markdown or "")
    text = _MD_LINK_RE.sub(r"\1", text)
    paragraphs = [block.strip() for block in re.split(r"\n{2,}", text)]
    for index, block in enumerate(paragraphs):
        if _NJ_TRAILER_HEADING_RE.match(block):
            paragraphs = paragraphs[:index]
            break
    return "\n\n".join(block for block in paragraphs if block).strip()


def _anchor_to_markdown(match: re.Match[str], links: bool = True) -> str:
    href = match.group(1) or match.group(2) or match.group(3) or ""
    label = normalize_text(_TAG_RE.sub(" ", match.group(4)))
    if not label:
        return ""
    if links and href.strip():
        return f"[{label}]({href.strip()})"
    return label


def _regex_to_markdown(html_or_text: str, links: bool = True) -> str:
    """Legacy regex HTML-to-Markdown path (fallback when trafilatura yields nothing).

    Block elements become paragraph breaks, anchors become `[label](href)`
    (or plain labels when `links` is false, the National Jeweler family
    shape), remaining tags are stripped and entities unescaped. Paragraphs
    are preserved (joined with blank lines).
    """
    text = _NON_VISIBLE_RE.sub(" ", html_or_text or "")
    text = _ANCHOR_RE.sub(lambda match: _anchor_to_markdown(match, links), text)
    text = _BLOCK_RE.sub("\n", text)
    text = _TAG_RE.sub(" ", text)
    text = _html.unescape(text)
    paragraphs = [_WS_RE.sub(" ", block.strip()) for block in text.split("\n") if block.strip()]
    return "\n\n".join(paragraphs)


def clean_to_markdown(html_or_text: str, url: str = "") -> str:
    """Convert article HTML (or plain text) to clean Markdown.

    HTML input goes through trafilatura's deterministic article extractor
    (Markdown output, links kept, dedupe on, precision favored; scripts,
    styles, and boilerplate dropped — no LLM features). When trafilatura is
    unavailable or yields nothing usable (stubs, lock pages, non-HTML
    input), the legacy regex path runs instead, so cleaning never
    hard-fails. `url` feeds trafilatura's dedupe/canonical hints and keys the
    per-domain National Jeweler post-processing (chrome blocks stripped
    before conversion, anchors flattened to plain words, boilerplate trailer
    cut) so harvested and enriched bodies are clean on both paths.
    """
    source = html_or_text or ""
    national_jeweler = _is_nj_family(url)
    if national_jeweler and "<" in source and ">" in source:
        source = _strip_nj_boilerplate(source)
    if _trafilatura is not None and "<" in source and ">" in source:
        try:
            # NJ family: segment dedupe stays off. trafilatura's `deduplicate`
            # also keeps a process-global document cache; re-extracting the
            # same article (every rerun of an Ingestion Run) then discards the
            # body and leaks metadata chrome back through the regex fallback.
            # Other sources keep the established behavior.
            extracted = _trafilatura.extract(
                source,
                output_format="markdown",
                include_links=True,
                deduplicate=not national_jeweler,
                favor_precision=True,
                include_comments=False,
                url=url or None,
            )
        except Exception:
            extracted = None
        if extracted and extracted.strip():
            markdown = extracted.strip()
            return _nj_clean_markdown(markdown) if national_jeweler else markdown
    markdown = _regex_to_markdown(source, links=not national_jeweler)
    return _nj_clean_markdown(markdown) if national_jeweler else markdown


#: HTTP statuses whose error bodies may carry bot-challenge evidence.
_ERROR_BODY_STATUSES = (401, 402, 403, 429)

#: Marker recording that a 403 carried a non-empty body rendering to almost
#: no text (a keyword-less bot lock page): trips the gated fallback.
_LOCK_PAGE_MARKER = "lock page (thin rendered text)"


def _reason_for(status: int) -> str:
    """Best-effort reason phrase for an HTTP status code."""
    try:
        return http.HTTPStatus(status).phrase
    except ValueError:
        return "Unknown"


def _failure_for_status(url: str, status: int, reason: str, body: bytes) -> FetchFailed:
    """Build the FetchFailed for an HTTP error status, with body evidence.

    Appends a short body snippet (challenge guards name themselves there),
    plus the lock-page marker when a 403 body is non-empty yet renders to
    almost no text — a keyword-less bot lock page the gated fallback should
    still attempt. Empty bodies stay marker-free (plain block: keep RSS
    without fallback traffic). Lock detection uses the default threshold
    (conservative); the per-source threshold only drives the thin trigger.
    """
    detail = f"HTTP Error {status}: {reason}"
    if status in _ERROR_BODY_STATUSES and body:
        snippet = body[:512].decode("utf-8", errors="replace").strip()
        if snippet:
            detail += f" — body: {snippet[:512]}"
        if status == 403:
            rendered = clean_to_markdown(body.decode("utf-8", errors="replace"), url)
            if rendered.strip() and is_thin(rendered):
                detail += f" — {_LOCK_PAGE_MARKER}"
    return FetchFailed(f"fetch failed for {url}: {detail}")


def _fetch_via_impersonation(url: str, timeout: int = PRIMARY_TIMEOUT) -> bytes:
    """GET `url` with curl_cffi Chrome impersonation plus browser headers.

    The only primary backend. ``curl_cffi`` is a hard dependency; when it is
    missing the failure is explicit — there is no stdlib fetch lane and never
    a silent downgrade of the browser identity.
    """
    if _curl_cffi_requests is None:
        raise FetchFailed(
            f"fetch failed for {url}: curl_cffi unavailable (no impersonation backend)"
        )
    try:
        response = _curl_cffi_requests.get(
            url, impersonate="chrome", headers=dict(BROWSER_HEADERS), timeout=timeout
        )
    except Exception as exc:
        raise FetchFailed(f"fetch failed for {url}: {exc}") from exc
    status = int(response.status_code)
    if status >= 400:
        raise _failure_for_status(url, status, _reason_for(status), bytes(response.content or b""))
    return bytes(response.content)


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


def _fetch_primary_bytes(url: str, timeout: int = PRIMARY_TIMEOUT) -> bytes:
    """GET article bytes through the impersonated-Chrome primary, or fail.

    Single backend: ``curl_cffi`` Chrome impersonation beats bot-guard 403s on
    article pages. Missing dependency or transport/HTTP failure raises
    :class:`FetchFailed` (chained causes preserved) for the caller to gate a
    fallback attempt on — never a stdlib retry, never cookies.
    """
    return _fetch_via_impersonation(url, timeout)


def fetch_and_clean(url: str, timeout: int = PRIMARY_TIMEOUT) -> str:
    """Fetch `url` and return clean Markdown.

    Single primary backend: impersonated-Chrome HTTPS (``curl_cffi``);
    timeouts, the FetchFailed/UnparseableBody taxonomy, and 401/402/403/429
    error-body snippet capture hold there. No stdlib lane, no cookies.

    Raises :class:`FetchFailed` on network/HTTP errors (and on a missing
    ``curl_cffi``) and :class:`UnparseableBody` when nothing usable survives
    cleaning.
    """
    raw = _fetch_primary_bytes(url, timeout)
    try:
        payload = raw.decode("utf-8", errors="replace")
    except Exception as exc:
        raise UnparseableBody(f"unparseable body for {url}: {exc}") from exc
    markdown = clean_to_markdown(payload, url)
    if not markdown.strip():
        raise UnparseableBody(f"unparseable body for {url}: empty after cleaning")
    return markdown


def _retry_after_seconds(exc: urllib.error.HTTPError) -> float:
    """Parse a 429 Retry-After delay, bounded so ingestion never stalls."""
    headers = getattr(exc, "headers", None) or getattr(exc, "hdrs", None)
    try:
        raw = headers.get("Retry-After") if headers is not None else None
        delay = float(str(raw).strip().split(",")[0]) if raw is not None else 0.0
    except (TypeError, ValueError, AttributeError):
        delay = 0.0
    return max(0.0, min(delay, FALLBACK_RETRY_CAP_SECONDS))


def try_fallback_reader(url: str, timeout: int = FALLBACK_TIMEOUT) -> str:
    """Fetch `url` through the zero-ops reader fallback; return clean Markdown.

    Gated by the caller (:func:`enrich_document` only calls this on a
    primary-miss signal — never unconditionally). The request carries only
    data-minimizing headers (explicit User-Agent, ``Accept: text/*``; no
    Cookie/Authorization credentials ever). On HTTP 429 the Retry-After delay
    is honored up to ``FALLBACK_MAX_RETRIES`` retries, then the call gives up
    with a ``rate-limited`` cause.

    Raises :class:`FetchFailed` on network/HTTP/rate-limit failures and
    :class:`UnparseableBody` when nothing usable survives cleaning.
    Stdlib + urllib only: no paid deps, no model calls.
    """
    reader_url = FALLBACK_READER_BASE + url
    request = urllib.request.Request(
        reader_url, headers={"User-Agent": USER_AGENT, "Accept": FALLBACK_ACCEPT}
    )
    for attempt in range(1 + FALLBACK_MAX_RETRIES):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = bytes(response.read())
                try:
                    _encoding = response.getheader("Content-Encoding")
                except Exception:
                    _encoding = None
                raw = _decode_body(raw, _encoding)
        except EnrichmentError:
            raise
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                if attempt < FALLBACK_MAX_RETRIES:
                    time.sleep(_retry_after_seconds(exc))
                    continue
                raise FetchFailed(
                    f"fallback rate-limited for {url}: reader returned 429 "
                    f"({FALLBACK_MAX_RETRIES} retries exhausted)"
                ) from exc
            raise FetchFailed(
                f"fallback fetch failed for {url}: HTTP Error {exc.code}: {exc.reason}"
            ) from exc
        except Exception as exc:
            raise FetchFailed(f"fallback fetch failed for {url}: {exc}") from exc
        try:
            payload = raw.decode("utf-8", errors="replace")
        except Exception as exc:
            raise UnparseableBody(f"fallback unparseable body for {url}: {exc}") from exc
        markdown = clean_to_markdown(payload, url)
        if not markdown.strip():
            raise UnparseableBody(f"fallback unparseable body for {url}: empty after cleaning")
        return markdown
    raise FetchFailed(f"fallback rate-limited for {url}: reader unavailable")


def _primary_miss_from_error(exc: Exception) -> bool:
    """True when a primary fetch failure warrants one gated fallback attempt.

    Bot/challenge evidence, rate-limit signals, or a 403 lock page (non-empty
    body rendering to almost no text — keyword-less, so only the marker trips
    it) qualify; plain paywalls, timeouts, and other failures keep the RSS
    body without fallback traffic.
    """
    message = str(exc).lower()
    return (
        any(key in message for key in _CHALLENGE_KEYWORDS)
        or any(signal in message for signal in _RATE_LIMIT_SIGNALS)
        or _LOCK_PAGE_MARKER in message
    )


def _looks_like_js_shell(markdown: str, threshold: int = DEFAULT_THIN_THRESHOLD) -> bool:
    """True when cleaned output is a JS app shell: shell markers plus thin text."""
    if not any(marker in markdown.lower() for marker in _JS_SHELL_MARKERS):
        return False
    return is_thin(markdown, threshold)


def _fallback_after_primary_miss(
    url: str,
    timeout: int,
    primary_desc: str,
    threshold: int = DEFAULT_THIN_THRESHOLD,
) -> str:
    """Walk the gated fallback chain; raise (chaining every cause) on failure.

    Fixed order: Jina reader (:func:`try_fallback_reader`) first, then the
    paid Firecrawl scrape (imported lazily, key read inside that module — it
    never touches this one). A leg whose output is thin, missing, or failed
    counts as a miss — reader stubs, error pages, and empty scrape payloads
    must never become the canonical stored body. The raised error names the
    primary cause plus every fallback leg cause, so the operator sees exactly
    why the chain gave up.
    """
    causes: list[str] = []
    try:
        markdown = try_fallback_reader(url, timeout)
    except EnrichmentError as exc:
        causes.append(f"reader: {exc}")
    except Exception as exc:  # defensive: unexpected reader errors stay typed
        causes.append(f"reader: {exc}")
    else:
        if not is_thin(markdown, threshold):
            return markdown
        causes.append("reader: delivered no substantive text")

    from marketing_intelligence.firecrawl import fetch_via_firecrawl  # lazy: no cycle

    try:
        scraped = fetch_via_firecrawl(url, timeout).decode("utf-8", errors="replace")
        markdown = clean_to_markdown(scraped, url)
    except Exception as exc:  # FirecrawlFailed: missing key, transport, HTTP, payload
        causes.append(f"firecrawl: {exc}")
    else:
        if not is_thin(markdown, threshold):
            return markdown
        causes.append("firecrawl: delivered no substantive text")

    raise FetchFailed(f"fallback failed for {url} (primary: {primary_desc}; {'; '.join(causes)})")


def enrich_document(
    doc: NormalizedDocument,
    threshold: int = DEFAULT_THIN_THRESHOLD,
    timeout: int = PRIMARY_TIMEOUT,
    force: bool = False,
) -> tuple[NormalizedDocument, str, str | None]:
    """Enrich one document when its RSS body is thin.

    Returns `(document, method, cause)`: sufficient bodies come back
    byte-identical (`new_doc is doc`, method ``"rss"``, cause None) unless
    `force` is set (the ``force_on`` policy seam: enrich regardless of thin);
    thin bodies are rebuilt via :func:`make_document` with the same metadata and
    the enriched Markdown (hash over stored text). On a primary-miss signal
    (bot/challenge/rate-limit error, empty primary output, JS-shell output)
    the gated fallback chain (Jina reader, then Firecrawl) is walked once.
    Fetch/clean/fallback failures raise
    :class:`EnrichmentError` subclasses — use :func:`enrich_document_or_keep`
    for the never-raises variant the flow relies on.
    """
    if not force and not is_thin(doc.content, threshold):
        return (doc, METHOD_RSS, None)
    try:
        markdown = fetch_and_clean(doc.url, timeout)
    except UnparseableBody as exc:
        markdown = _fallback_after_primary_miss(
            doc.url, timeout, f"empty primary output ({exc})", threshold
        )
    except FetchFailed as exc:
        if not _primary_miss_from_error(exc):
            raise
        markdown = _fallback_after_primary_miss(doc.url, timeout, str(exc), threshold)
    else:
        if _looks_like_js_shell(markdown, threshold):
            markdown = _fallback_after_primary_miss(
                doc.url, timeout, "primary output looked like a JS shell", threshold
            )
    enriched = make_document(
        source=doc.source,
        url=doc.url,
        title=doc.title,
        content=markdown,
        author=doc.author,
        published_at=doc.published_at,
        retrieved_at=doc.retrieved_at,
        language=doc.language,
    )
    return (enriched, METHOD_ENRICHED, None)


def enrich_document_or_keep(
    doc: NormalizedDocument,
    threshold: int = DEFAULT_THIN_THRESHOLD,
    timeout: int = PRIMARY_TIMEOUT,
    force: bool = False,
) -> tuple[NormalizedDocument, str, str | None]:
    """Like :func:`enrich_document` but never raises.

    Any failure keeps the original RSS document and returns its cause string
    (`method` stays ``"rss"``), so one bad page never aborts an Ingestion Run.
    """
    try:
        return enrich_document(doc, threshold, timeout, force)
    except EnrichmentError as exc:
        return (doc, METHOD_RSS, f"{doc.url}: {exc}")
    except Exception as exc:  # defensive: enrichment never blocks ingestion
        return (doc, METHOD_RSS, f"{doc.url}: enrichment failed ({exc})")
