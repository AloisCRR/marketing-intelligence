"""Firecrawl last-resort scrape: key hygiene, v2 payload, and parse policy.

Contract under test:
- missing/blank ``FIRECRAWL_API_KEY`` is an explicit skipped cause and sends
  no request at all; quoted/``Bearer ``-prefixed env values are normalized
- a successful scrape parses both payload shapes (v2 ``data.markdown`` and
  flat ``markdown``) into Markdown bytes
- the request carries the deterministic v2 body (Markdown only, no LLM-backed
  options) and a millisecond server budget below the client timeout
- a page-level failure (HTTP 200 + ``success: true`` + ``metadata.statusCode``
  403/404) raises instead of yielding block-page bytes
- HTTP errors carry the status plus structured API error or a truncated
  redacted snippet; transport errors suppress their context
- only ``RETRYABLE_STATUSES`` and client timeouts are retried, with
  ``Retry-After``-aware sleeps recorded through ``firecrawl.time.sleep``
- the key never leaks: literal value or Bearer/``fc-`` token shape

All I/O is faked (monkeypatch, no network). The key used here is a
monkeypatched sentinel; the real ``FIRECRAWL_API_KEY`` is never read.
"""

from __future__ import annotations

import hashlib
from typing import Any

import httpx
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
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self.text = text
        self._payload = payload
        self._json_error = json_error
        self.headers = headers or {}

    def json(self) -> Any:
        if self._json_error is not None:
            raise self._json_error
        return self._payload


def _ok(markdown: str = MARKDOWN, metadata: dict[str, Any] | None = None) -> _FakeResponse:
    data: dict[str, Any] = {"markdown": markdown, "metadata": metadata or {"statusCode": 200}}
    return _FakeResponse(200, {"success": True, "data": data})


class _FakePost:
    """httpx.post double: records calls, replays queued responses or errors."""

    def __init__(
        self,
        response: _FakeResponse | None = None,
        error: Exception | None = None,
        responses: list[Any] | None = None,
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self.response = response
        self.error = error
        self._queue = list(responses) if responses is not None else None

    def __call__(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append({"url": url, **kwargs})
        if self._queue is not None:
            item = self._queue.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


def _wire(monkeypatch: pytest.MonkeyPatch, fake: _FakePost) -> None:
    monkeypatch.setattr(firecrawl.httpx, "post", fake)


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record retry sleeps instead of really sleeping."""
    recorded: list[float] = []
    monkeypatch.setattr(firecrawl.time, "sleep", recorded.append)
    return recorded


# --- missing / blank key ------------------------------------------------------


def test_missing_key_is_explicit_skipped_cause_without_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    fake = _FakePost(response=_ok())
    _wire(monkeypatch, fake)

    with pytest.raises(firecrawl.FirecrawlFailed) as info:
        firecrawl.fetch_via_firecrawl(URL)

    assert "firecrawl skipped: missing FIRECRAWL_API_KEY" in str(info.value)
    assert fake.calls == []  # skipped means no request, no key material


def test_blank_key_is_skipped_too(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FIRECRAWL_API_KEY", "   ")
    fake = _FakePost(response=_ok())
    _wire(monkeypatch, fake)

    with pytest.raises(firecrawl.FirecrawlFailed) as info:
        firecrawl.fetch_via_firecrawl(URL)

    assert "firecrawl skipped: missing FIRECRAWL_API_KEY" in str(info.value)
    assert fake.calls == []


# --- key hygiene --------------------------------------------------------------


def test_quoted_and_bearer_prefixed_env_key_is_normalized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FIRECRAWL_API_KEY", '"Bearer fc-quoted-sentinel-key"')
    fake = _FakePost(response=_ok())
    _wire(monkeypatch, fake)

    firecrawl.fetch_via_firecrawl(URL)

    # Compared by digest: the normalized value is never rendered into output.
    auth = fake.calls[0]["headers"]["Authorization"]
    assert _digest(auth) == _digest("Bearer fc-quoted-sentinel-key")


def test_redact_scrubs_token_shapes_without_the_literal_key() -> None:
    scrubbed = firecrawl._redact(
        "Authorization: Bearer fc-abcdefghijkl refused; BEARER fc-12345678", ""
    )
    assert scrubbed == "Authorization: <redacted> refused; <redacted>"


# --- success: payload shapes and v2 request body ------------------------------


def test_success_parses_v2_data_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FIRECRAWL_API_KEY", SENTINEL_KEY)
    fake = _FakePost(response=_ok())
    _wire(monkeypatch, fake)

    assert firecrawl.fetch_via_firecrawl(URL, timeout=7) == MARKDOWN.encode("utf-8")
    call = fake.calls[0]
    assert firecrawl.FIRECRAWL_ENDPOINT == "https://api.firecrawl.dev/v2/scrape"
    assert call["url"] == firecrawl.FIRECRAWL_ENDPOINT
    assert call["timeout"] == 7  # httpx stays in seconds
    assert call["follow_redirects"] is True
    body = call["json"]
    assert body["url"] == URL
    assert body["formats"] == ["markdown"]
    assert body["onlyMainContent"] is True
    assert body["maxAge"] == 172_800_000
    assert body["storeInCache"] is True
    assert body["proxy"] == "auto"
    assert body["blockAds"] is True
    assert body["removeBase64Images"] is True
    assert body["timeout"] == 2000  # ms, below the 7 s client budget
    for absent in (
        "onlyCleanContent",
        "actions",
        "headers",
        "minAge",
        "includeTags",
        "excludeTags",
        "zeroDataRetention",
        "json",
        "prompt",
    ):
        assert absent not in body


def test_success_parses_flat_markdown_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FIRECRAWL_API_KEY", SENTINEL_KEY)
    fake = _FakePost(response=_FakeResponse(200, {"success": True, "markdown": MARKDOWN}))
    _wire(monkeypatch, fake)

    assert firecrawl.fetch_via_firecrawl(URL) == MARKDOWN.encode("utf-8")


def test_warning_alone_does_not_fail_a_scrape_with_markdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FIRECRAWL_API_KEY", SENTINEL_KEY)
    response = _ok()
    response._payload["data"]["warning"] = "truncated at token limit"
    _wire(monkeypatch, _FakePost(response=response))

    assert firecrawl.fetch_via_firecrawl(URL) == MARKDOWN.encode("utf-8")


@pytest.mark.parametrize("page_status", [200, 304])
def test_clean_page_status_codes_succeed(monkeypatch: pytest.MonkeyPatch, page_status: int) -> None:
    monkeypatch.setenv("FIRECRAWL_API_KEY", SENTINEL_KEY)
    _wire(monkeypatch, _FakePost(response=_ok(metadata={"statusCode": page_status})))

    assert firecrawl.fetch_via_firecrawl(URL) == MARKDOWN.encode("utf-8")


def test_response_without_markdown_is_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FIRECRAWL_API_KEY", SENTINEL_KEY)
    _wire(monkeypatch, _FakePost(response=_FakeResponse(200, {"data": {"markdown": "   "}})))

    with pytest.raises(firecrawl.FirecrawlFailed, match="no markdown"):
        firecrawl.fetch_via_firecrawl(URL)


def test_missing_markdown_reports_page_context_and_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FIRECRAWL_API_KEY", SENTINEL_KEY)
    payload = {
        "success": True,
        "data": {
            "markdown": "",
            "metadata": {"statusCode": 200, "error": None},
            "warning": "no main content found",
        },
    }
    _wire(monkeypatch, _FakePost(response=_FakeResponse(200, payload)))

    with pytest.raises(firecrawl.FirecrawlFailed) as info:
        firecrawl.fetch_via_firecrawl(URL)

    message = str(info.value)
    assert "no markdown in response" in message
    assert "page status 200" in message
    assert "no main content found" in message


# --- page-level failures: HTTP 200 must not mean the page loaded --------------


def test_page_status_403_on_http_200_raises_never_returns_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FIRECRAWL_API_KEY", SENTINEL_KEY)
    response = _ok(metadata={"statusCode": 403, "error": "Blocked by bot wall"})
    _wire(monkeypatch, _FakePost(response=response))

    with pytest.raises(firecrawl.FirecrawlFailed) as info:
        firecrawl.fetch_via_firecrawl(URL)

    message = str(info.value)
    assert "statusCode 403" in message
    assert "Blocked by bot wall" in message


# --- API envelope failures ----------------------------------------------------


def test_success_false_on_http_200_raises_with_api_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FIRECRAWL_API_KEY", SENTINEL_KEY)
    payload = {
        "success": False,
        "error": "Unauthorized: Invalid token",
        "code": "UNAUTHORIZED",
        "details": "the key was revoked",
    }
    _wire(monkeypatch, _FakePost(response=_FakeResponse(200, payload)))

    with pytest.raises(firecrawl.FirecrawlFailed) as info:
        firecrawl.fetch_via_firecrawl(URL)

    message = str(info.value)
    assert "Unauthorized: Invalid token" in message
    assert "UNAUTHORIZED" in message


def test_non_object_json_payload_is_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FIRECRAWL_API_KEY", SENTINEL_KEY)
    _wire(monkeypatch, _FakePost(response=_FakeResponse(200, payload=["not", "an", "object"])))

    with pytest.raises(firecrawl.FirecrawlFailed) as info:
        firecrawl.fetch_via_firecrawl(URL)

    assert "unparseable JSON" in str(info.value)
    assert info.value.__suppress_context__ is True


# --- auth: Bearer only, key never printed -------------------------------------


def test_request_sends_bearer_auth_and_no_other_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FIRECRAWL_API_KEY", SENTINEL_KEY)
    fake = _FakePost(response=_ok())
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
    fake = _FakePost(response=_FakeResponse(400, text=body))
    _wire(monkeypatch, fake)

    with pytest.raises(firecrawl.FirecrawlFailed) as info:
        firecrawl.fetch_via_firecrawl(URL)

    message = str(info.value)
    assert "400" in message and "Bad Request" in message
    snippet = message.split("— body: ", 1)[1]
    assert snippet.startswith("quota exceeded for <redacted>")
    assert len(snippet) <= firecrawl._ERROR_BODY_CAP  # truncated, not whole body
    assert len(snippet) < len(body)
    assert SENTINEL_KEY not in message  # redacted before it leaves the module


def test_402_message_names_credits_and_never_leaks_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FIRECRAWL_API_KEY", SENTINEL_KEY)
    payload = {"success": False, "error": f"Payment Required: out of credits for {SENTINEL_KEY}"}
    _wire(monkeypatch, _FakePost(response=_FakeResponse(402, payload)))

    with pytest.raises(firecrawl.FirecrawlFailed) as info:
        firecrawl.fetch_via_firecrawl(URL)

    message = str(info.value)
    assert "402" in message
    assert "Payment Required" in message
    assert "credits" in message
    assert SENTINEL_KEY not in message
    assert "<redacted>" in message


def test_429_cause_carries_retry_after_and_sleeps_that_long(
    monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
) -> None:
    monkeypatch.setenv("FIRECRAWL_API_KEY", SENTINEL_KEY)
    response = _FakeResponse(
        429, {"success": False, "error": "Rate limit exceeded"}, headers={"Retry-After": "3"}
    )
    fake = _FakePost(response=response)
    _wire(monkeypatch, fake)

    with pytest.raises(firecrawl.FirecrawlFailed) as info:
        firecrawl.fetch_via_firecrawl(URL)

    assert "Retry-After: 3s" in str(info.value)
    assert len(fake.calls) == 1 + firecrawl.FIRECRAWL_MAX_RETRIES
    assert sleeps == [3.0, 3.0]  # one bounded sleep per retry, from the header


def test_429_http_date_retry_after_falls_back_to_backoff(
    monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
) -> None:
    monkeypatch.setenv("FIRECRAWL_API_KEY", SENTINEL_KEY)
    response = _FakeResponse(
        429,
        {"success": False, "error": "Rate limit exceeded"},
        headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"},
    )
    _wire(monkeypatch, _FakePost(response=response))

    with pytest.raises(firecrawl.FirecrawlFailed) as info:
        firecrawl.fetch_via_firecrawl(URL)

    message = str(info.value)
    assert "retryable" in message
    assert "Retry-After: " not in message  # an HTTP-date is not a usable delay
    assert sleeps == [1.0, 2.0]


def test_retry_after_sleep_is_capped(monkeypatch: pytest.MonkeyPatch, sleeps: list[float]) -> None:
    monkeypatch.setenv("FIRECRAWL_API_KEY", SENTINEL_KEY)
    response = _FakeResponse(
        429, {"success": False, "error": "Rate limit exceeded"}, headers={"Retry-After": "900"}
    )
    _wire(monkeypatch, _FakePost(response=response))

    with pytest.raises(firecrawl.FirecrawlFailed):
        firecrawl.fetch_via_firecrawl(URL)

    assert sleeps == [60.0, 60.0]  # never stall the fallback chain for 15 minutes


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
    _wire(monkeypatch, _FakePost(response=_FakeResponse(200, json_error=ValueError("bad json"))))

    with pytest.raises(firecrawl.FirecrawlFailed) as info:
        firecrawl.fetch_via_firecrawl(URL)

    assert "unparseable JSON" in str(info.value)
    assert info.value.__suppress_context__ is True


# --- retry policy -------------------------------------------------------------


def test_retryable_503_then_success_returns_and_backs_off(
    monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
) -> None:
    monkeypatch.setenv("FIRECRAWL_API_KEY", SENTINEL_KEY)
    unavailable = _FakeResponse(503, {"success": False, "error": "Service Unavailable"})
    fake = _FakePost(responses=[unavailable, unavailable, _ok()])
    _wire(monkeypatch, fake)

    assert firecrawl.fetch_via_firecrawl(URL) == MARKDOWN.encode("utf-8")
    assert len(fake.calls) == 3
    assert sleeps == [1.0, 2.0]  # attempt-based backoff, capped well below 60s


def test_client_timeout_is_retried_then_succeeds(
    monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
) -> None:
    monkeypatch.setenv("FIRECRAWL_API_KEY", SENTINEL_KEY)
    fake = _FakePost(responses=[httpx.TimeoutException("read timed out"), _ok()])
    _wire(monkeypatch, fake)

    assert firecrawl.fetch_via_firecrawl(URL) == MARKDOWN.encode("utf-8")
    assert sleeps == [1.0]


def test_exhausted_client_timeouts_name_the_client_budget(
    monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
) -> None:
    monkeypatch.setenv("FIRECRAWL_API_KEY", SENTINEL_KEY)
    timeouts = [httpx.TimeoutException("read timed out") for _ in range(3)]
    fake = _FakePost(responses=timeouts)
    _wire(monkeypatch, fake)

    with pytest.raises(firecrawl.FirecrawlFailed) as info:
        firecrawl.fetch_via_firecrawl(URL, timeout=7)

    message = str(info.value)
    assert "timed out after 7s" in message
    assert len(fake.calls) == 3
    assert sleeps == [1.0, 2.0]


def test_non_retryable_404_is_not_retried(
    monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
) -> None:
    monkeypatch.setenv("FIRECRAWL_API_KEY", SENTINEL_KEY)
    fake = _FakePost(response=_FakeResponse(404, {"success": False, "error": "Not Found"}))
    _wire(monkeypatch, fake)

    with pytest.raises(firecrawl.FirecrawlFailed) as info:
        firecrawl.fetch_via_firecrawl(URL)

    assert "404" in str(info.value)
    assert len(fake.calls) == 1
    assert sleeps == []


def test_redirect_status_that_was_not_followed_raises(
    monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
) -> None:
    monkeypatch.setenv("FIRECRAWL_API_KEY", SENTINEL_KEY)
    fake = _FakePost(response=_FakeResponse(302, text=""))
    _wire(monkeypatch, fake)

    with pytest.raises(firecrawl.FirecrawlFailed) as info:
        firecrawl.fetch_via_firecrawl(URL)

    assert "redirect not followed" in str(info.value)
    assert len(fake.calls) == 1
    assert sleeps == []


# --- body timeout derivation --------------------------------------------------


def test_body_timeout_is_derived_from_client_timeout() -> None:
    assert firecrawl._body_timeout_ms(30) == 25_000
    assert firecrawl._body_timeout_ms(7) == 2_000
    assert firecrawl._body_timeout_ms(1) == 1_000  # clamped to the documented minimum
    assert firecrawl._body_timeout_ms(1000) == 300_000  # clamped to the maximum
    # Firecrawl's budget must always expire before the client-side abort.
    for seconds in (5, 30, 120, 1000):
        assert firecrawl._body_timeout_ms(seconds) < seconds * 1000
