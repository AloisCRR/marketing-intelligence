"""Thin-triggered Markdown enrichment (ticket 02).

Deterministic, stdlib-only extraction (no language-model calls, no paid deps):

- :func:`is_thin` decides whether an RSS body is worth enriching.
- :func:`clean_to_markdown` converts article HTML to clean Markdown.
- :func:`fetch_and_clean` retrieves an article URL over plain HTTP and cleans it.
- :func:`enrich_document` enriches one thin document (raises typed errors).
- :func:`enrich_document_or_keep` never raises: failure keeps the RSS body.

Gated fallback (ticket 03): when the primary extractor misses a page — a
bot/challenge/rate-limit fetch error, an empty primary output, or a JS-shell
output — :func:`enrich_document` tries :func:`try_fallback_reader` once (a
zero-ops Jina-reader-style HTTPS GET with data-minimizing headers: explicit
User-Agent, ``Accept: text/*``, never any Cookie/Authorization credentials,
backoff on 429 honoring Retry-After up to 2 retries). Any fallback failure
raises a typed error that chains the primary cause, so
:func:`enrich_document_or_keep` still keeps the RSS body.

Per-source enrichment policy (threshold overrides, force-on/off) is ticket 03;
the threshold here is a hardcoded default with an optional per-call override.
The `force` flag on :func:`enrich_document` / :func:`enrich_document_or_keep`
is the policy seam the flow uses for ``force_on`` sources.
"""

from __future__ import annotations

import html as _html
import re
import time
import urllib.error
import urllib.request

from brain.ingest import USER_AGENT
from brain.normalize import NormalizedDocument, make_document, normalize_text

#: RSS bodies shorter than this (after whitespace collapse) trigger enrichment.
DEFAULT_THIN_THRESHOLD = 500

#: Jina-reader-style zero-ops fallback endpoint: GET <base><article-url>.
FALLBACK_READER_BASE = "https://r.jina.ai/"

#: Fallback GETs per call: the initial attempt plus this many 429 retries.
FALLBACK_MAX_RETRIES = 2

#: Upper bound honoring a 429 Retry-After (ingestion must never sleep unbounded).
FALLBACK_RETRY_CAP_SECONDS = 60.0

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


def _anchor_to_markdown(match: re.Match[str]) -> str:
    href = match.group(1) or match.group(2) or match.group(3) or ""
    label = normalize_text(_TAG_RE.sub(" ", match.group(4)))
    if not label:
        return ""
    if href.strip():
        return f"[{label}]({href.strip()})"
    return label


def clean_to_markdown(html_or_text: str, url: str = "") -> str:
    """Deterministically convert article HTML (or plain text) to Markdown.

    Block elements become paragraph breaks, anchors become `[label](href)`,
    remaining tags are stripped and entities unescaped. Paragraphs are
    preserved (joined with blank lines); `url` is accepted for signature
    symmetry with fetch-stage callers and future relative-link resolution.
    """
    _ = url
    text = _ANCHOR_RE.sub(_anchor_to_markdown, html_or_text or "")
    text = _BLOCK_RE.sub("\n", text)
    text = _TAG_RE.sub(" ", text)
    text = _html.unescape(text)
    paragraphs = [_WS_RE.sub(" ", block.strip()) for block in text.split("\n") if block.strip()]
    return "\n\n".join(paragraphs)


def fetch_and_clean(url: str, timeout: int = 30) -> str:
    """Fetch `url` over plain HTTP and return clean Markdown.

    Raises :class:`FetchFailed` on network/HTTP errors and
    :class:`UnparseableBody` when nothing usable survives cleaning.
    """
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = bytes(response.read())
    except EnrichmentError:
        raise
    except urllib.error.HTTPError as exc:
        # Capture a short error-body snippet on auth/challenge/rate-limit
        # statuses (401/402/403/429): guarded pages name their protection
        # (Cloudflare/captcha/...) in the body, and the gated fallback keys
        # off exactly that signal.
        detail = f"HTTP Error {exc.code}: {exc.reason}"
        if exc.code in (401, 402, 403, 429):
            try:
                snippet = bytes(exc.read()[:512]).decode("utf-8", errors="replace").strip()
            except Exception:
                snippet = ""
            if snippet:
                detail += f" — body: {snippet[:512]}"
        raise FetchFailed(f"fetch failed for {url}: {detail}") from exc
    except Exception as exc:
        raise FetchFailed(f"fetch failed for {url}: {exc}") from exc
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


def try_fallback_reader(url: str, timeout: int = 30) -> str:
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

    Only bot/challenge evidence or rate-limit signals qualify; plain paywalls,
    timeouts, and other failures keep the RSS body without fallback traffic.
    """
    message = str(exc).lower()
    return any(key in message for key in _CHALLENGE_KEYWORDS) or any(
        signal in message for signal in _RATE_LIMIT_SIGNALS
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
    """Try the gated fallback; raise (chaining the primary cause) on failure.

    A fallback output that is itself thin counts as a miss — reader stubs and
    error pages must never become the canonical stored body.
    """
    try:
        markdown = try_fallback_reader(url, timeout)
    except EnrichmentError as exc:
        raise FetchFailed(
            f"fallback failed for {url} (primary: {primary_desc}; fallback: {exc})"
        ) from exc
    except Exception as exc:  # defensive: unexpected reader errors stay typed
        raise FetchFailed(
            f"fallback failed for {url} (primary: {primary_desc}; fallback: {exc})"
        ) from exc
    if is_thin(markdown, threshold):
        raise FetchFailed(
            f"fallback failed for {url} (primary: {primary_desc}; "
            "fallback delivered no substantive text)"
        )
    return markdown


def enrich_document(
    doc: NormalizedDocument,
    threshold: int = DEFAULT_THIN_THRESHOLD,
    timeout: int = 30,
    force: bool = False,
) -> tuple[NormalizedDocument, str, str | None]:
    """Enrich one document when its RSS body is thin.

    Returns `(document, method, cause)`: sufficient bodies come back
    byte-identical (`new_doc is doc`, method ``"rss"``, cause None) unless
    `force` is set (the ``force_on`` policy seam: enrich regardless of thin);
    thin bodies are rebuilt via :func:`make_document` with the same metadata and
    the enriched Markdown (hash over stored text). On a primary-miss signal
    (bot/challenge/rate-limit error, empty primary output, JS-shell output)
    the gated reader fallback is tried once. Fetch/clean/fallback failures raise
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
    timeout: int = 30,
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
