"""Firecrawl scrape fallback: the paid last resort in the fetch chain.

Called only after the impersonated-Chrome primary and the Jina-reader
fallback have both missed (see :mod:`marketing_intelligence.enrich`). The API
key is read from ``FIRECRAWL_API_KEY`` at call time — never at import — sent
only as a Bearer header, and never echoed into a return value, log line, or
exception message: a missing key is an explicit "skipped" cause, not an
import-time failure and not an exception raised while importing this module.
"""

from __future__ import annotations

import http
import os

import httpx

#: Native timeout (s) for one Firecrawl scrape request.
FIRECRAWL_TIMEOUT = 30

#: Firecrawl cloud scrape endpoint (v1).
FIRECRAWL_ENDPOINT = "https://api.firecrawl.dev/v1/scrape"

#: Error-body bytes carried into a failure cause (truncated, never the key).
_ERROR_BODY_CAP = 512

#: Stand-in for anything that could echo the API key.
_REDACTED = "<redacted>"


class FirecrawlFailed(Exception):
    """Firecrawl delivered no Markdown (missing key, transport, HTTP, payload)."""


def _api_key() -> str:
    """Read the Firecrawl API key at call time (never cached at import)."""
    return (os.environ.get("FIRECRAWL_API_KEY") or "").strip()


def _redact(message: str, key: str) -> str:
    """Scrub any occurrence of `key` from `message` before it leaves this module."""
    if not key or key not in message:
        return message
    return message.replace(key, _REDACTED)


def _reason_for(status: int) -> str:
    """Best-effort reason phrase for an HTTP status code."""
    try:
        return http.HTTPStatus(status).phrase
    except ValueError:
        return "Unknown"


def _markdown_from_payload(payload: object) -> str:
    """Extract the Markdown field from a scrape payload (v1 and v2 shapes)."""
    if not isinstance(payload, dict):
        return ""
    data = payload.get("data")
    if isinstance(data, dict):
        markdown = data.get("markdown")
        if isinstance(markdown, str):
            return markdown
    markdown = payload.get("markdown")
    return markdown if isinstance(markdown, str) else ""


def fetch_via_firecrawl(url: str, timeout: int = FIRECRAWL_TIMEOUT) -> bytes:
    """Scrape `url` through Firecrawl; return Markdown bytes.

    Sends Bearer auth only (no cookies, no other credentials). Raises
    :class:`FirecrawlFailed` when ``FIRECRAWL_API_KEY`` is unset or blank
    (explicit skipped cause), on transport failure, on an HTTP error status
    (status plus a truncated response snippet), or when the payload carries no
    Markdown. The API key never appears in any message or chained exception.
    """
    key = _api_key()
    if not key:
        raise FirecrawlFailed("firecrawl skipped: missing FIRECRAWL_API_KEY")
    try:
        response = httpx.post(
            FIRECRAWL_ENDPOINT,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"url": url, "formats": ["markdown"]},
            timeout=timeout,
        )
    except Exception as exc:
        # `from None`: a transport exception's chain must never print the header.
        raise FirecrawlFailed(
            f"firecrawl request failed for {url}: {_redact(str(exc), key)}"
        ) from None
    status = int(response.status_code)
    if status >= 400:
        detail = f"HTTP Error {status}: {_reason_for(status)}"
        snippet = _redact(response.text[:_ERROR_BODY_CAP].strip(), key)
        if snippet:
            detail += f" — body: {snippet}"
        raise FirecrawlFailed(f"firecrawl scrape failed for {url}: {detail}")
    try:
        payload = response.json()
    except Exception as exc:
        raise FirecrawlFailed(
            f"firecrawl scrape failed for {url}: unparseable JSON response "
            f"({_redact(str(exc), key)})"
        ) from None
    markdown = _markdown_from_payload(payload)
    if not markdown.strip():
        raise FirecrawlFailed(f"firecrawl scrape failed for {url}: no markdown in response")
    return markdown.encode("utf-8")
