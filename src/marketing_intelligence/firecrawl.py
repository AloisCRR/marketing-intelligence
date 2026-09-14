"""Firecrawl scrape fallback: the paid last resort in the fetch chain.

Called only after the impersonated-Chrome primary and the Jina-reader
fallback have both missed (see :mod:`marketing_intelligence.enrich`). The API
key is read from ``FIRECRAWL_API_KEY`` at call time — never at import — sent
only as a Bearer header, and never echoed into a return value, log line, or
exception message: a missing key is an explicit "skipped" cause, not an
import-time failure and not an exception raised while importing this module.

The request targets the v2 scrape endpoint with a deterministic, Markdown-only
body: no language-model-backed options (no ``onlyCleanContent``, no JSON or
prompt extraction) are ever sent. A response is accepted only when the API
call *and* the page load both succeeded — HTTP 2xx, a non-``False``
``success`` flag, and a ``data.metadata.statusCode`` of ``None``/304/2xx — so
a block or error page is never returned as article bytes. Retryable HTTP
statuses (``RETRYABLE_STATUSES``) and client timeouts are retried a bounded
number of times with ``Retry-After``-aware, capped sleeps.
"""

from __future__ import annotations

import http
import json
import os
import re
import time

import httpx

#: Native timeout (s) for one Firecrawl scrape request.
FIRECRAWL_TIMEOUT = 30

#: Firecrawl cloud scrape endpoint (v2).
FIRECRAWL_ENDPOINT = "https://api.firecrawl.dev/v2/scrape"

#: Extra attempts after the first (mirrors enrich's FALLBACK_MAX_RETRIES).
FIRECRAWL_MAX_RETRIES = 2

#: HTTP statuses the docs mark retryable-with-backoff; everything else is final.
RETRYABLE_STATUSES = frozenset({408, 429, 500, 502, 503, 504})

#: Error-body bytes carried into a failure cause (truncated, never the key).
_ERROR_BODY_CAP = 512

#: Stand-in for anything that could echo the API key.
_REDACTED = "<redacted>"

#: Matches header-shaped credentials even when the literal key is unknown. The
#: Bearer alternative stops at whitespace, a quote, or a backslash: the JSON
#: text it scrubs may be parsed again (the with-payload envelope), so swallowing
#: a closing quote would leave an unterminated string behind.
_TOKEN_RE = re.compile(r"(?i)\b(?:bearer\s+[^\s\"\\]+|fc-[A-Za-z0-9_-]{8,})")

#: Response header carrying 429's backoff hint (seconds, or an HTTP-date).
_RETRY_AFTER_HEADER = "Retry-After"

#: Cap (s) on any single retry sleep, so a scrape never stalls the chain.
_RETRY_CAP_SECONDS = 60.0

#: Firecrawl's server-side freshness window (ms): v2's documented 2-day default.
_CACHE_MAX_AGE_MS = 172_800_000

#: Bounds and client margin for Firecrawl's own request budget (ms).
_MIN_BODY_TIMEOUT_MS = 1_000
_MAX_BODY_TIMEOUT_MS = 300_000
_BODY_TIMEOUT_MARGIN_MS = 5_000

#: Actionable, non-retryable HTTP statuses: the operator, not a backoff, fixes these.
_STATUS_HINTS = {
    401: "invalid or revoked Firecrawl API key",
    402: "Firecrawl credits exhausted; enable pay-as-you-go or upgrade the plan",
    403: "Firecrawl API key lacks scope for this endpoint",
}


class FirecrawlFailed(Exception):
    """Firecrawl delivered no Markdown (missing key, transport, HTTP, payload)."""


def _strip_quotes(value: str) -> str:
    """Drop one layer of surrounding single/double quotes (misconfigured env)."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1].strip()
    return value


def _api_key() -> str:
    """Read the Firecrawl API key at call time (never cached at import)."""
    key = _strip_quotes((os.environ.get("FIRECRAWL_API_KEY") or "").strip())
    if key[:7].lower() == "bearer ":
        key = _strip_quotes(key[7:].strip())
    return key


def _redact(message: str, key: str) -> str:
    """Scrub the literal key and any Bearer/``fc-`` token shape from `message`."""
    if key:
        message = message.replace(key, _REDACTED)
    return _TOKEN_RE.sub(_REDACTED, message)


def _reason_for(status: int) -> str:
    """Best-effort reason phrase for an HTTP status code."""
    try:
        return http.HTTPStatus(status).phrase
    except ValueError:
        return "Unknown"


def _body_timeout_ms(timeout: int) -> int:
    """Firecrawl's own budget (ms), always below the client-side timeout."""
    budget = int(timeout * 1000 - _BODY_TIMEOUT_MARGIN_MS)
    return min(max(budget, _MIN_BODY_TIMEOUT_MS), _MAX_BODY_TIMEOUT_MS)


def _details_text(details: object) -> str:
    """Render a JSON error body's `details` field as one redactable string."""
    if isinstance(details, str):
        return details.strip()
    if details is None:
        return ""
    try:
        return json.dumps(details, ensure_ascii=False, sort_keys=True)
    except Exception:
        return str(details)


def _structured_error(payload: dict, key: str) -> str:
    """Compose the documented `error`/`code`/`details` fields ("" when absent)."""
    parts: list[str] = []
    error = payload.get("error")
    if isinstance(error, str) and error.strip():
        parts.append(_redact(error.strip(), key))
    code = payload.get("code")
    if isinstance(code, str) and code.strip():
        parts.append(f"[{_redact(code.strip(), key)}]")
    details = payload.get("details")
    if details is not None:
        text = _details_text(details)
        if text:
            parts.append(f"— details: {_redact(text, key)}")
    return " ".join(parts)


def _api_error(response: httpx.Response, key: str) -> str:
    """Structured error text from a JSON error body, or "" when unavailable."""
    try:
        payload = response.json()
    except Exception:
        return ""
    if not isinstance(payload, dict):
        return ""
    return _structured_error(payload, key)


def _retry_after_seconds(response: httpx.Response) -> float | None:
    """`Retry-After` as seconds; None when absent, non-numeric (HTTP-date), or bad."""
    headers = getattr(response, "headers", None)
    if not headers:
        return None
    try:
        raw = headers.get(_RETRY_AFTER_HEADER)
        if raw is None:
            raw = headers.get(_RETRY_AFTER_HEADER.lower())
    except Exception:
        return None
    if raw is None:
        return None
    try:
        seconds = float(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return max(0.0, seconds)


def _sleep_seconds(response: httpx.Response | None, attempt: int) -> float:
    """Retry-After when numeric, else 1s/2s backoff; capped at ``_RETRY_CAP_SECONDS``."""
    if response is not None:
        retry_after = _retry_after_seconds(response)
        if retry_after is not None:
            return min(retry_after, _RETRY_CAP_SECONDS)
    return min(float(2**attempt), _RETRY_CAP_SECONDS)


def _http_error_detail(status: int, response: httpx.Response, key: str) -> str:
    """Redacted HTTP failure detail: status, status-specific hint, API error/snippet."""
    detail = f"HTTP Error {status}: {_reason_for(status)}"
    hint = _STATUS_HINTS.get(status)
    if hint:
        detail += f" — {hint}"
    if status == 429:
        retry_after = _retry_after_seconds(response)
        if retry_after is not None:
            detail += f" — retryable; Retry-After: {retry_after:g}s"
        else:
            detail += " — retryable"
    api_error = _api_error(response, key)
    if api_error:
        detail += f" — error: {api_error}"
    else:
        snippet = _redact(str(getattr(response, "text", ""))[:_ERROR_BODY_CAP].strip(), key)
        if snippet:
            detail += f" — body: {snippet}"
    return detail


def _empty_context(page_status: object, page_error: object, warning: object, key: str) -> str:
    """Diagnostic suffix for an empty scrape: page status, error, and warning."""
    parts: list[str] = []
    if isinstance(page_status, int) and not isinstance(page_status, bool):
        parts.append(f"page status {page_status}")
    if page_error:
        parts.append(_redact(str(page_error), key))
    if warning:
        parts.append(f"warning: {_redact(str(warning), key)}")
    return f" ({'; '.join(parts)})" if parts else ""


def _markdown_from_payload(payload: object) -> str:
    """Extract the Markdown field from a scrape payload (v2 with flat tolerance)."""
    if not isinstance(payload, dict):
        return ""
    data = payload.get("data")
    if isinstance(data, dict):
        markdown = data.get("markdown")
        if isinstance(markdown, str):
            return markdown
    markdown = payload.get("markdown")
    return markdown if isinstance(markdown, str) else ""


def _scrape_payload(url: str, key: str, timeout: int) -> dict:
    """Run the v2 scrape request loop; return the validated JSON envelope.

    Shared by the bytes-only and with-payload variants so the retry, error,
    and page-status contract stays single-sourced. Raises
    :class:`FirecrawlFailed` on every failure path; success guarantees a dict
    carrying non-blank Markdown.
    """
    body = {
        "url": url,
        "formats": ["markdown"],
        "onlyMainContent": True,
        "maxAge": _CACHE_MAX_AGE_MS,
        "storeInCache": True,
        "proxy": "auto",
        "blockAds": True,
        "removeBase64Images": True,
        "timeout": _body_timeout_ms(timeout),
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    for attempt in range(FIRECRAWL_MAX_RETRIES + 1):
        try:
            response = httpx.post(
                FIRECRAWL_ENDPOINT,
                headers=headers,
                json=body,
                timeout=timeout,  # seconds; above the body budget so Firecrawl's 408 wins
                follow_redirects=True,
            )
        except httpx.TimeoutException:
            if attempt < FIRECRAWL_MAX_RETRIES:
                time.sleep(_sleep_seconds(None, attempt))
                continue
            raise FirecrawlFailed(
                _redact(f"firecrawl request failed for {url}: timed out after {timeout}s", key)
            ) from None
        except httpx.TransportError as exc:
            # `from None`: a transport exception's chain must never print the header.
            raise FirecrawlFailed(
                _redact(f"firecrawl request failed for {url}: transport error: {exc}", key)
            ) from None
        except Exception as exc:
            raise FirecrawlFailed(
                _redact(f"firecrawl request failed for {url}: {exc}", key)
            ) from None
        status = int(response.status_code)
        if status in RETRYABLE_STATUSES and attempt < FIRECRAWL_MAX_RETRIES:
            time.sleep(_sleep_seconds(response, attempt))
            continue
        if 300 <= status < 400:
            # Only reachable when redirect following gave up; never retryable.
            raise FirecrawlFailed(
                f"firecrawl scrape failed for {url}: HTTP Error {status}: "
                f"{_reason_for(status)} — redirect not followed"
            )
        if status >= 400:
            raise FirecrawlFailed(
                f"firecrawl scrape failed for {url}: {_http_error_detail(status, response, key)}"
            )
        try:
            payload = response.json()
        except Exception as exc:
            raise FirecrawlFailed(
                f"firecrawl scrape failed for {url}: unparseable JSON response "
                f"({_redact(str(exc), key)})"
            ) from None
        if not isinstance(payload, dict):
            raise FirecrawlFailed(
                f"firecrawl scrape failed for {url}: unparseable JSON response "
                f"(expected a JSON object, got {type(payload).__name__})"
            ) from None
        if payload.get("success") is False:
            raise FirecrawlFailed(
                f"firecrawl scrape failed for {url}: API error: "
                f"{_structured_error(payload, key) or 'unspecified error'}"
            )
        data = payload.get("data")
        if not isinstance(data, dict):
            data = {}
        metadata = data.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        page_status = metadata.get("statusCode")
        page_error = metadata.get("error")
        if isinstance(page_status, int) and not isinstance(page_status, bool):
            if page_status != 304 and not 200 <= page_status < 300:
                raise FirecrawlFailed(
                    f"firecrawl scrape failed for {url}: page returned statusCode "
                    f"{page_status}" + (f" ({_redact(str(page_error), key)})" if page_error else "")
                )
        markdown = _markdown_from_payload(payload)
        if not markdown.strip():
            raise FirecrawlFailed(
                f"firecrawl scrape failed for {url}: no markdown in response"
                + _empty_context(page_status, page_error, data.get("warning"), key)
            )
        return payload
    # Unreachable: every iteration above returns or raises. Kept for the type
    # checker, which cannot prove the range below is non-empty.
    raise FirecrawlFailed(
        _redact(f"firecrawl scrape failed for {url}: retries exhausted", key)
    ) from None


def fetch_via_firecrawl_with_payload(
    url: str, timeout: int = FIRECRAWL_TIMEOUT
) -> tuple[bytes, dict[str, object]]:
    """Scrape `url` through Firecrawl (v2); return (Markdown bytes, raw envelope).

    Same request/retry/parse contract as :func:`fetch_via_firecrawl`, except
    the accepted response also yields its raw JSON envelope so a caller can
    persist the billed provider payload beside the extracted Markdown. The
    envelope is redacted (``_redact`` over its JSON text with the call key)
    before return, so the Bearer key can never reach the side table even when
    the API echoes request metadata. Raises :class:`FirecrawlFailed` on every
    path the bytes-only variant raises.
    """
    key = _api_key()
    if not key:
        raise FirecrawlFailed("firecrawl skipped: missing FIRECRAWL_API_KEY")
    payload = _scrape_payload(url, key, timeout)
    markdown = _markdown_from_payload(payload)
    scrubbed = json.loads(_redact(json.dumps(payload, ensure_ascii=False), key))
    envelope: dict[str, object] = scrubbed if isinstance(scrubbed, dict) else {}
    return (markdown.encode("utf-8"), envelope)


def fetch_via_firecrawl(url: str, timeout: int = FIRECRAWL_TIMEOUT) -> bytes:
    """Scrape `url` through Firecrawl (v2); return Markdown bytes.

    Sends Bearer auth only (no cookies, no other credentials). Raises
    :class:`FirecrawlFailed` when ``FIRECRAWL_API_KEY`` is unset or blank
    (explicit skipped cause, zero request traffic), on transport failure, on an
    HTTP error status, when the API envelope reports ``success: false``, when
    the page itself failed (``data.metadata.statusCode``), or when the payload
    carries no Markdown. Retryable statuses and client timeouts are retried up
    to ``FIRECRAWL_MAX_RETRIES`` times. The API key never appears in any message
    or chained exception.
    """
    key = _api_key()
    if not key:
        raise FirecrawlFailed("firecrawl skipped: missing FIRECRAWL_API_KEY")
    payload = _scrape_payload(url, key, timeout)
    return _markdown_from_payload(payload).encode("utf-8")


#: The shipped fetch entry points, captured after definition. The
#: article-content chain consults these to tell a real leg from the
#: monkeypatched stand-in a test or embed installs (``fetch_via_firecrawl`` is
#: the historical patch target), so it can honor either patch without
#: duplicating the request loop or re-running a billed scrape.
SHIPPED_FETCH_WITH_PAYLOAD = fetch_via_firecrawl_with_payload
SHIPPED_FETCH_BYTES = fetch_via_firecrawl
