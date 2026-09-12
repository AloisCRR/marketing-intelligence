"""Firecrawl last-resort scrape: key hygiene and payload parsing.

Contract under test:
- missing/blank ``FIRECRAWL_API_KEY`` is an explicit skipped cause and sends
  no request at all
- a successful scrape parses both payload shapes (v1 ``data.markdown`` and
  flat ``markdown``) into Markdown bytes
- HTTP errors carry the status plus a truncated response snippet with the key
  redacted; transport errors suppress their context so the key never leaks
- the request sends Bearer auth only (no cookies, no other credentials), and
  the key value is compared by digest — never printed

All I/O is faked (monkeypatch, no network). The key used here is a
monkeypatched sentinel; the real ``FIRECRAWL_API_KEY`` is never read.
"""

from __future__ import annotations

import hashlib
from typing import Any

import pytest

from marketing_intelligence import firecrawl

URL = "https://example.com/articles/gated-story"

#: Fake key planted for these tests; never the real environment value.
SENTINEL_KEY = "fc-sentinel-key-not-a-real-credential"

MARKDOWN = "# Gated Story\n\nFull article body with real content."


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class _FakeResponse:
    def __init__(
        self,
        status_code: int = 200,
        payload: Any = None,
        text: str = "",
        json_error: Exception | None = None,
    ) -> None:
        self.status_code = status_code
        self.text = text
        self._payload = payload
        self._json_error = json_error

    def json(self) -> Any:
        if self._json_error is not None:
            raise self._json_error
        return self._payload


class _FakePost:
    """httpx.post double: records calls, replays one canned response or error."""

    def __init__(
        self, response: _FakeResponse | None = None, error: Exception | None = None
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self.response = response
        self.error = error

    def __call__(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append({"url": url, **kwargs})
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


def _wire(monkeypatch: pytest.MonkeyPatch, fake: _FakePost) -> None:
    monkeypatch.setattr(firecrawl.httpx, "post", fake)


# --- missing / blank key ------------------------------------------------------


def test_missing_key_is_explicit_skipped_cause_without_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    fake = _FakePost(response=_FakeResponse(200, {"markdown": MARKDOWN}))
    _wire(monkeypatch, fake)

    with pytest.raises(firecrawl.FirecrawlFailed) as info:
        firecrawl.fetch_via_firecrawl(URL)

    assert "firecrawl skipped: missing FIRECRAWL_API_KEY" in str(info.value)
    assert fake.calls == []  # skipped means no request, no key material


def test_blank_key_is_skipped_too(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FIRECRAWL_API_KEY", "   ")
    fake = _FakePost(response=_FakeResponse(200, {"markdown": MARKDOWN}))
    _wire(monkeypatch, fake)

    with pytest.raises(firecrawl.FirecrawlFailed) as info:
        firecrawl.fetch_via_firecrawl(URL)

    assert "firecrawl skipped: missing FIRECRAWL_API_KEY" in str(info.value)
    assert fake.calls == []


# --- success: both payload shapes ---------------------------------------------


def test_success_parses_v1_data_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FIRECRAWL_API_KEY", SENTINEL_KEY)
    fake = _FakePost(response=_FakeResponse(200, {"data": {"markdown": MARKDOWN}}))
    _wire(monkeypatch, fake)

    assert firecrawl.fetch_via_firecrawl(URL, timeout=7) == MARKDOWN.encode("utf-8")
    call = fake.calls[0]
    assert call["url"] == firecrawl.FIRECRAWL_ENDPOINT
    assert call["json"] == {"url": URL, "formats": ["markdown"]}
    assert call["timeout"] == 7


def test_success_parses_flat_markdown_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FIRECRAWL_API_KEY", SENTINEL_KEY)
    fake = _FakePost(response=_FakeResponse(200, {"markdown": MARKDOWN}))
    _wire(monkeypatch, fake)

    assert firecrawl.fetch_via_firecrawl(URL) == MARKDOWN.encode("utf-8")


def test_response_without_markdown_is_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FIRECRAWL_API_KEY", SENTINEL_KEY)
    fake = _FakePost(response=_FakeResponse(200, {"data": {"markdown": "   "}}))
    _wire(monkeypatch, fake)

    with pytest.raises(firecrawl.FirecrawlFailed, match="no markdown"):
        firecrawl.fetch_via_firecrawl(URL)


# --- auth: Bearer only, key never printed -------------------------------------


def test_request_sends_bearer_auth_and_no_other_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FIRECRAWL_API_KEY", SENTINEL_KEY)
    fake = _FakePost(response=_FakeResponse(200, {"data": {"markdown": MARKDOWN}}))
    _wire(monkeypatch, fake)

    firecrawl.fetch_via_firecrawl(URL)

    headers = fake.calls[0]["headers"]
    assert headers["Authorization"].startswith("Bearer ")
    # Compared by digest: the sentinel value is never rendered into output.
    assert _digest(headers["Authorization"]) == _digest(f"Bearer {SENTINEL_KEY}")
    assert set(headers) == {"Authorization", "Content-Type"}
    assert "Cookie" not in headers


# --- failure redaction --------------------------------------------------------


def test_http_error_carries_status_and_truncated_redacted_snippet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FIRECRAWL_API_KEY", SENTINEL_KEY)
    body = f"quota exceeded for {SENTINEL_KEY} " + "q" * 2000
    fake = _FakePost(response=_FakeResponse(402, text=body))
    _wire(monkeypatch, fake)

    with pytest.raises(firecrawl.FirecrawlFailed) as info:
        firecrawl.fetch_via_firecrawl(URL)

    message = str(info.value)
    assert "402" in message and "Payment Required" in message
    snippet = message.split("— body: ", 1)[1]
    assert snippet.startswith("quota exceeded for <redacted>")
    assert len(snippet) <= firecrawl._ERROR_BODY_CAP  # truncated, not whole body
    assert len(snippet) < len(body)
    assert SENTINEL_KEY not in message  # redacted before it leaves the module


def test_transport_error_suppresses_context_and_never_leaks_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FIRECRAWL_API_KEY", SENTINEL_KEY)
    boom = RuntimeError(f"connection reset sending Authorization: Bearer {SENTINEL_KEY}")
    fake = _FakePost(error=boom)
    _wire(monkeypatch, fake)

    with pytest.raises(firecrawl.FirecrawlFailed) as info:
        firecrawl.fetch_via_firecrawl(URL)

    raised = info.value
    assert SENTINEL_KEY not in str(raised)
    assert "<redacted>" in str(raised)
    assert raised.__cause__ is None  # `from None`: no chained original to print
    assert raised.__suppress_context__ is True


def test_unparseable_json_response_is_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FIRECRAWL_API_KEY", SENTINEL_KEY)
    fake = _FakePost(response=_FakeResponse(200, json_error=ValueError("bad json")))
    _wire(monkeypatch, fake)

    with pytest.raises(firecrawl.FirecrawlFailed) as info:
        firecrawl.fetch_via_firecrawl(URL)

    assert "unparseable JSON" in str(info.value)
    assert info.value.__suppress_context__ is True
