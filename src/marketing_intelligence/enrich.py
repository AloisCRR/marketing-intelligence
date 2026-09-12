"""Thin-triggered Markdown enrichment (ticket 02).

Deterministic extraction (no language-model calls), on the user-ordered fetch
chain: impersonated Chrome, then Jina reader, then Firecrawl.

- :func:`is_thin` decides whether an RSS body is worth enriching.
- :func:`clean_to_markdown` converts article HTML to clean Markdown through
  trafilatura, the single converter (there is no regex fallback). Per-domain
  post-processing for the National Jeweler family flattens inline editorial
  anchors to plain words and cuts any boilerplate trailer, so harvested and
  enriched bodies both stay clean.
- :func:`fetch_and_clean` retrieves an article URL over HTTPS and cleans it
  with the single primary backend: Chrome browser impersonation
  (``curl_cffi``: TLS fingerprint plus genuine browser headers — never any
  crawler/bot UA, no cookies/credentials ever). ``curl_cffi`` is a hard
  dependency; when it is unavailable the primary raises an explicit
  :class:`FetchFailed` — there is no stdlib fetch lane.
- :func:`enrich_document` enriches one thin document (raises typed errors).
- :func:`enrich_document_or_keep` never raises: failure keeps the RSS body and
  notifies its ``on_unrecoverable`` sink, so a total-chain failure is filed as
  the terminal ``unrecoverable`` Extraction Flag instead of a silent thin body.

Gated fallback chain (tickets 03/05): when the primary extractor misses a page
— a bot/challenge/rate-limit fetch error, an empty primary output, or a
JS-shell output — :func:`enrich_document` walks the fallback chain once
through :func:`article_content_chain`, the one three-leg implementation shared
with sitemap discovery: the impersonated primary (:func:`fetch_impersonated`),
the Jina reader (:func:`fetch_reader`, a zero-ops HTTPS GET with
data-minimizing headers: explicit User-Agent, ``Accept: text/*``, never any
Cookie/Authorization credentials, backoff on 429 honoring Retry-After up to 2
retries), then ``fetch_via_firecrawl`` (the paid last resort, imported lazily
from :mod:`marketing_intelligence.firecrawl`; Bearer key from
``FIRECRAWL_API_KEY`` read at call time, never logged, absent key an explicit
skipped cause). Enrichment enters the chain only on a primary-miss signal
(discovery walks it ungated) and thin-checks each provider payload; provider
Markdown is stored as-is — never run back through the HTML cleaner. Any
fallback failure raises a typed error that chains every prior cause through
one credential-free detail, so :func:`enrich_document_or_keep` still keeps the
RSS body.

Per-source enrichment policy (threshold overrides, force-on/off) is ticket 03;
the threshold here is a hardcoded default with an optional per-call override.
The `force` flag on :func:`enrich_document` / :func:`enrich_document_or_keep`
is the policy seam the flow uses for ``force_on`` sources.
"""

from __future__ import annotations

import gzip
import http
import re
import time
import urllib.error
import urllib.request
import zlib
from collections.abc import Callable
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

try:  # the single HTML-to-Markdown converter (hard dep, deterministic, no LLM)
    import trafilatura as _trafilatura
except Exception:  # pragma: no cover - declared hard dep; no fallback exists
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

#: Sink notified when one document's whole article-content chain fails and the
#: kept RSS body is still thin: ``on_unrecoverable(url, cause)``, with ``cause``
#: the chained reason (every chain leg named once, no URL prefix). The flow
#: wires it to file the terminal ``unrecoverable`` Extraction Flag through
#: :func:`marketing_intelligence.flag.flag_unrecoverable` after the row exists
#: (the flag lane is an UPDATE, so it must run after the upsert). Never allowed
#: to raise into enrichment.
UnrecoverableSink = Callable[[str, str], None]

#: Hosts in the National Jeweler family. Their article pages share one CMS
#: shape: a ``trix-content`` body wrapped in related-articles and "The Latest"
#: sidebar blocks that generic extraction renders into the stored body as
#: navigation chrome (and inline editorial anchors that render as links).
NJ_FAMILY_HOSTS = ("nationaljeweler.com",)

#: Markdown headings that begin a boilerplate trailer on NJ pages; anything
#: from such a heading onward is chrome, never article text.
_NJ_TRAILER_HEADING_RE = re.compile(
    r"^(?:#{1,6}\s*)?(?:the latest|related (?:articles|stories|posts|content)|"
    r"most (?:popular|read)|recommended|read more|more stories|more from|"
    r"you may also like|from our partners)\s*:?\s*$",
    re.IGNORECASE,
)

#: Markdown image and link syntax, for the NJ link-flattening pass.
_MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_MD_LINK_RE = re.compile(r"\[([^\]\[]*)\]\([^)]*\)")

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


class ProviderMarkdown(bytes):
    """Article body delivered as Markdown by a reader/scrape provider leg.

    The fetch contract stays a plain ``(final_url, bytes)`` pair; tagging the
    payload as provider Markdown tells the extract site the body is already
    extracted, so it is stored after a thin-check only and never run back
    through the HTML cleaner (links, images, and formatting survive intact).
    The impersonated primary returns HTML and is never tagged. Both article
    callers share the tag through :func:`article_content_chain`.
    """


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


def clean_to_markdown(html_or_text: str, url: str = "") -> str:
    """Convert article HTML to clean Markdown through trafilatura.

    Single converter: trafilatura's deterministic article extractor
    (Markdown output, links kept, precision favored; scripts, styles, and
    boilerplate dropped — no LLM features). There is no regex fallback:
    HTML trafilatura cannot extract (stubs, lock pages, non-HTML input)
    yields ``""``, so callers keep their existing empty-body semantics
    (thin-check, keep-RSS, :class:`UnparseableBody`) instead of storing a
    tag-stripped dump. Deduplication is never enabled (its process-global
    cache would discard a re-extracted article). `url` feeds trafilatura's
    canonical hints and keys the per-domain National Jeweler post-processing
    (inline editorial anchors flattened to plain words, boilerplate trailer
    cut).
    """
    source = html_or_text or ""
    if _trafilatura is None or "<" not in source or ">" not in source:
        return ""
    try:
        # Deduplication stays off: trafilatura's `deduplicate` keeps a
        # process-global LRU segment cache, so re-extracting the same article
        # (every rerun of an Ingestion Run, or a second document in the same
        # batch) is "discarding data" and yields None. With no fallback that
        # would store an empty body, so dedupe is never enabled;
        # `favor_precision` already drops repeated boilerplate segments.
        extracted = _trafilatura.extract(
            source,
            output_format="markdown",
            include_links=True,
            deduplicate=False,
            favor_precision=True,
            include_comments=False,
            url=url or None,
        )
    except Exception:
        return ""
    markdown = (extracted or "").strip()
    if not markdown:
        return ""
    return _nj_clean_markdown(markdown) if _is_nj_family(url) else markdown


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


def fetch_impersonated(url: str, timeout: int = PRIMARY_TIMEOUT) -> tuple[str, bytes]:
    """Primary leg: GET `url` with curl_cffi Chrome impersonation; (final_url, body).

    The only primary backend, shared by both article callers (enrichment's
    :func:`fetch_and_clean` and discovery's ``policy_get``). ``curl_cffi`` is a
    hard dependency; when it is missing the failure is explicit — there is no
    stdlib fetch lane and never a silent downgrade of the browser identity.
    Raises :class:`FetchFailed` on transport/HTTP failure.
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
    body = bytes(response.content or b"")
    status = int(response.status_code)
    if status >= 400:
        raise _failure_for_status(url, status, _reason_for(status), body)
    return (str(getattr(response, "url", url) or url), body)


def _fetch_via_impersonation(url: str, timeout: int = PRIMARY_TIMEOUT) -> bytes:
    """Bytes-only view of the shared impersonation leg (:func:`fetch_impersonated`)."""
    return fetch_impersonated(url, timeout)[1]


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


#: Error-body bytes inspected for reader-leg HTTP error evidence.
_ERROR_BODY_CAP = 65536


def fetch_reader(url: str, timeout: int = FALLBACK_TIMEOUT) -> tuple[str, bytes]:
    """Reader leg: GET ``<reader-base><url>``; return (final_url, raw body).

    Plain urllib against :data:`FALLBACK_READER_BASE` carrying only
    data-minimizing headers (explicit User-Agent, ``Accept: text/*``; never
    cookies or credentials). On HTTP 429 the Retry-After delay is honored up
    to :data:`FALLBACK_MAX_RETRIES` retries, then the leg gives up with a
    ``rate-limited`` cause; non-429 HTTP errors carry body evidence. The body
    comes back exactly as delivered — reader Markdown, possibly empty; the
    caller decides what counts as substantive. Raises :class:`FetchFailed`
    with a bare cause (the shared chain names the leg). Stdlib + urllib only:
    no paid deps, no model calls.
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
                    encoding = response.getheader("Content-Encoding")
                except Exception:
                    encoding = None
                try:
                    final_url = str(response.geturl() or reader_url)
                except Exception:
                    final_url = reader_url
                return (final_url, _decode_body(raw, encoding))
        except urllib.error.HTTPError as exc:
            status = int(exc.code)
            if status == 429:
                if attempt < FALLBACK_MAX_RETRIES:
                    time.sleep(_retry_after_seconds(exc))
                    continue
                raise FetchFailed(
                    f"rate-limited (429, {FALLBACK_MAX_RETRIES} retries exhausted)"
                ) from exc
            body = b""
            try:
                body = bytes(exc.read(_ERROR_BODY_CAP) or b"")
            except Exception:
                body = b""
            snippet = body[:512].decode("utf-8", errors="replace").strip()
            detail = f"HTTP Error {status}: {exc.reason}"
            if snippet:
                detail += f" — body: {snippet[:512]}"
            raise FetchFailed(detail) from exc
        except Exception as exc:
            raise FetchFailed(str(exc)) from exc
    raise FetchFailed("rate-limited (reader unavailable)")


def try_fallback_reader(url: str, timeout: int = FALLBACK_TIMEOUT) -> str:
    """Markdown view of the shared reader leg, for enrichment's gated entry.

    Gated by the caller (:func:`enrich_document` only calls this on a
    primary-miss signal — never unconditionally). The reader delivers
    already-extracted Markdown: it is returned as-is, never run back through
    the HTML cleaner, so links, images, and formatting survive intact; the
    caller's thin-check decides whether it counts.

    Raises :class:`FetchFailed` on network/HTTP/rate-limit failures (bare leg
    causes from :func:`fetch_reader`) and :class:`UnparseableBody` when the
    reader delivered no text.
    """
    markdown = fetch_reader(url, timeout)[1].decode("utf-8", errors="replace")
    if not markdown.strip():
        raise UnparseableBody(
            f"fallback reader delivered no substantive text for {url}: empty Markdown"
        )
    return markdown


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


#: Cap on the chained failure detail (never unbounded, never secrets).
_DETAIL_CAP = 1024

#: Credential-shaped tokens scrubbed from any chained failure detail.
_SECRET_RE = re.compile(r"(?i)\b(?:bearer\s+\S+|fc-[A-Za-z0-9_-]{8,})")

#: Leg label for the reader leg in the one chained failure detail: names the
#: Jina reader while keeping the ``reader:`` operator wording.
_READER_LEG_LABEL = "jina reader"


def _chain_detail(loc: str, primary: str, reader: str, firecrawl: str) -> str:
    """Bounded, credential-free ``{loc}: {primary} | jina reader: {e} | firecrawl: {e}``."""
    detail = f"{loc}: {primary} | {_READER_LEG_LABEL}: {reader} | firecrawl: {firecrawl}"
    return _SECRET_RE.sub("[redacted]", detail)[:_DETAIL_CAP]


def _leg_cause(exc: Exception) -> str:
    """Bare failure cause for one article-content leg.

    Discovery's typed fetch errors carry their loc-free ``detail``; every
    other leg error (``FetchFailed``, ``FirecrawlFailed``) already stringifies
    to its cause.
    """
    detail = getattr(exc, "detail", None)
    return detail if isinstance(detail, str) and detail else str(exc)


#: One chain leg: ``(url, timeout) -> (final_url, payload)``. A provider leg's
#: payload is already-extracted Markdown; the primary's is raw HTML.
ContentLeg = Callable[[str, int], tuple[str, bytes]]


def article_content_chain(
    url: str,
    *,
    reader: ContentLeg,
    timeout: int = PRIMARY_TIMEOUT,
    primary: ContentLeg | None = None,
    primary_cause: str | None = None,
    min_chars: int | None = None,
) -> tuple[str, bytes]:
    """Walk the one article-content chain: impersonation → Jina reader → Firecrawl.

    `primary` is the impersonated-Chrome leg; ``None`` means it already ran
    (enrichment's gated path) and `primary_cause` carries its miss reason.
    `reader` is the Jina-reader leg. The paid Firecrawl scrape is the shared
    final leg, imported lazily so its API key never touches this module.

    Provider payloads come back tagged :class:`ProviderMarkdown`, so the
    extract site stores them as-is instead of re-cleaning. With `min_chars`
    set (enrichment) a payload thinner than the threshold counts as a miss and
    the next leg runs; with ``None`` (discovery) the first leg that returns
    bytes wins. When every leg fails, one bounded, credential-free detail
    names each leg exactly once.
    """
    primary_detail = primary_cause or ""
    if primary is not None:
        try:
            return primary(url, timeout)
        except Exception as exc:
            primary_detail = _leg_cause(exc)
    try:
        final_url, payload = reader(url, timeout)
    except Exception as exc:
        reader_detail = _leg_cause(exc)
    else:
        if min_chars is None or not is_thin(payload.decode("utf-8", errors="replace"), min_chars):
            return (final_url, ProviderMarkdown(payload))
        reader_detail = "delivered no substantive text"

    from marketing_intelligence.firecrawl import fetch_via_firecrawl  # lazy: no cycle

    try:
        payload = fetch_via_firecrawl(url, timeout)
    except Exception as exc:  # FirecrawlFailed: missing key, transport, HTTP, payload
        firecrawl_detail = _leg_cause(exc)
    else:
        if min_chars is None or not is_thin(payload.decode("utf-8", errors="replace"), min_chars):
            return (url, ProviderMarkdown(payload))
        firecrawl_detail = "delivered no substantive text"
    raise FetchFailed(
        _chain_detail(url, primary_detail or "primary failed", reader_detail, firecrawl_detail)
    )


def _enrichment_reader_leg(url: str, timeout: int) -> tuple[str, bytes]:
    """Reader leg for the chain: :func:`try_fallback_reader` Markdown as bytes."""
    return (url, try_fallback_reader(url, timeout).encode("utf-8"))


def _fallback_after_primary_miss(
    url: str,
    timeout: int,
    primary_desc: str,
    threshold: int = DEFAULT_THIN_THRESHOLD,
) -> str:
    """Walk the shared gated fallback chain; raise (chaining every cause) on failure.

    Enrichment's entry into :func:`article_content_chain`: the primary already
    ran, `primary_desc` carries its miss reason, and the reader then the paid
    Firecrawl scrape are walked in the shared order with the thin-check applied
    to each leg's payload. A leg whose output is thin, missing, or failed
    counts as a miss — reader stubs, error pages, and empty scrape payloads
    must never become the canonical stored body — and the raised error names
    the primary cause plus every fallback leg cause.
    """
    _, payload = article_content_chain(
        url,
        reader=_enrichment_reader_leg,
        timeout=timeout,
        primary_cause=primary_desc,
        min_chars=threshold,
    )
    return payload.decode("utf-8", errors="replace")


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


def _emit_unrecoverable(
    doc: NormalizedDocument,
    threshold: int,
    cause: str,
    sink: UnrecoverableSink | None,
) -> None:
    """Notify `sink` that a failed enrichment left a still-thin RSS body.

    The terminal ``unrecoverable`` marker is only true when the body that
    survives the failure is the thin RSS teaser: a ``force_on`` source may fail
    enrichment while its RSS body was already substantive, and that document is
    not unrecoverable, so the sink stays silent. Best-effort by design — a
    raising sink never disturbs the keep-the-RSS-body contract.
    """
    if sink is None or not is_thin(doc.content, threshold):
        return
    try:
        sink(doc.url, cause)
    except Exception:  # defensive: flag persistence never blocks ingestion
        pass


def enrich_document_or_keep(
    doc: NormalizedDocument,
    threshold: int = DEFAULT_THIN_THRESHOLD,
    timeout: int = PRIMARY_TIMEOUT,
    force: bool = False,
    *,
    on_unrecoverable: UnrecoverableSink | None = None,
) -> tuple[NormalizedDocument, str, str | None]:
    """Like :func:`enrich_document` but never raises.

    Any failure keeps the original RSS document and returns its cause string
    (`method` stays ``"rss"``), so one bad page never aborts an Ingestion Run.
    When the kept body is still thin (a genuine total-chain failure) the
    optional `on_unrecoverable` sink receives ``(doc.url, cause)`` — `cause` is
    the chained failure reason — exactly once, so the flow can file the
    terminal ``unrecoverable`` Extraction Flag after persisting the row.
    """
    try:
        return enrich_document(doc, threshold, timeout, force)
    except EnrichmentError as exc:
        _emit_unrecoverable(doc, threshold, str(exc), on_unrecoverable)
        return (doc, METHOD_RSS, f"{doc.url}: {exc}")
    except Exception as exc:  # defensive: enrichment never blocks ingestion
        _emit_unrecoverable(doc, threshold, f"enrichment failed ({exc})", on_unrecoverable)
        return (doc, METHOD_RSS, f"{doc.url}: enrichment failed ({exc})")
