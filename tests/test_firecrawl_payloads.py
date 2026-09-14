"""Firecrawl billed-envelope capture: redaction, chain tagging, propagation.

Contract under test:
- ``fetch_via_firecrawl_with_payload`` returns ``(Markdown bytes, envelope)``
  and redacts Bearer/``fc-`` token shapes out of the released v2 envelope
- the bytes-only ``fetch_via_firecrawl`` keeps returning bare bytes under the
  unchanged v2 request/parse contract (its own patch target survives)
- :func:`marketing_intelligence.enrich.article_content_chain` tags a winning
  Firecrawl :class:`ProviderMarkdown` with ``{provider, url, response}`` and
  :func:`firecrawl_payload_of` reads it back
- a patched bytes-only ``fetch_via_firecrawl`` still wins as a chain leg with
  no envelope (``firecrawl_payload_of`` -> None): the old patch target is
  honored
- ``enrich_document`` and ``discovery.fetch_extract_one`` both propagate the
  carried envelope onto the document they return

All I/O is faked (monkeypatch, no network, no Postgres). The key used here is
a monkeypatched sentinel; the real ``FIRECRAWL_API_KEY`` is never read.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from marketing_intelligence import enrich, firecrawl
from marketing_intelligence.discovery import (
    ArticleJob,
    ProviderMarkdown,
    fetch_extract_one,
)
from marketing_intelligence.normalize import NormalizedDocument, make_document

URL = "https://example.com/articles/gated-story"

#: Fake key planted for these tests; never the real environment value.
SENTINEL_KEY = "fc-sentinel-key-not-a-real-credential"

#: Firecrawl Markdown long enough to clear enrich's thin threshold (500 chars).
BODY_MARKDOWN = "# Gated Story\n\n" + " ".join(["Sustained reporting sentence."] * 30)

#: Clean v2 envelope: what a successful scrape looks like with no secrets.
ENVELOPE: dict[str, Any] = {
    "success": True,
    "data": {"markdown": BODY_MARKDOWN, "metadata": {"statusCode": 200}},
}

NOW = datetime(2026, 9, 13, 12, 0, 0, tzinfo=UTC)


def _thin_doc() -> NormalizedDocument:
    return make_document(
        source="Social Media Today",
        url=URL,
        title="Gated Story",
        content="Short teaser.",
        published_at=NOW,
        retrieved_at=NOW,
        language="en",
    )


class _FakeResponse:
    def __init__(
        self,
        status_code: int = 200,
        payload: Any = None,
        text: str = "",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self.text = text
        self._payload = payload
        self.headers = headers or {}

    def json(self) -> Any:
        return self._payload


class _FakePost:
    """httpx.post double: records calls, replays the configured response."""

    def __init__(self, response: _FakeResponse) -> None:
        self.calls: list[dict[str, Any]] = []
        self.response = response

    def __call__(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append({"url": url, **kwargs})
        return self.response


def _ok(payload: dict[str, Any] | None = None) -> _FakeResponse:
    return _FakeResponse(200, payload if payload is not None else ENVELOPE)


def _wire(monkeypatch: pytest.MonkeyPatch, fake: _FakePost) -> None:
    monkeypatch.setattr(firecrawl.httpx, "post", fake)


def _reader_miss(url: str, timeout: int = 30) -> tuple[str, bytes]:
    raise enrich.FetchFailed("reader down")


# --- fetch_via_firecrawl_with_payload: bytes + envelope -----------------------


def test_with_payload_returns_markdown_bytes_and_validated_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FIRECRAWL_API_KEY", SENTINEL_KEY)
    fake = _FakePost(_ok())
    _wire(monkeypatch, fake)

    result = firecrawl.fetch_via_firecrawl_with_payload(URL)

    assert isinstance(result, tuple) and len(result) == 2
    body, envelope = result
    assert isinstance(body, bytes)
    assert body == BODY_MARKDOWN.encode("utf-8")
    # Clean envelope round-trips unchanged: no secrets to scrub.
    assert envelope == ENVELOPE
    assert fake.calls[0]["url"] == firecrawl.FIRECRAWL_ENDPOINT


def test_with_payload_redacts_fc_token_shapes_from_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FIRECRAWL_API_KEY", SENTINEL_KEY)
    echoing = {
        "success": True,
        "data": {
            "markdown": BODY_MARKDOWN,
            "metadata": {
                "statusCode": 200,
                "echoedToken": "fc-echoed-token-1234567890",
            },
        },
    }
    _wire(monkeypatch, _FakePost(_ok(echoing)))

    _, envelope = firecrawl.fetch_via_firecrawl_with_payload(URL)

    text = json.dumps(envelope, ensure_ascii=False)
    assert "fc-echoed-token-1234567890" not in text  # key-shaped token scrubbed
    assert "<redacted>" in text
    # Only credentials are scrubbed: the billed Markdown survives intact.
    assert envelope["data"]["markdown"] == BODY_MARKDOWN


def test_with_payload_redacts_echoed_bearer_header_from_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FIRECRAWL_API_KEY", SENTINEL_KEY)
    echoing = {
        "success": True,
        "data": {
            "markdown": BODY_MARKDOWN,
            "metadata": {
                "statusCode": 200,
                "requestHeaders": {"Authorization": f"Bearer {SENTINEL_KEY}"},
            },
        },
    }
    _wire(monkeypatch, _FakePost(_ok(echoing)))

    # Redaction must keep the envelope JSON parseable (a quote-eating scrub
    # would raise JSONDecodeError here, not just leak).
    _, envelope = firecrawl.fetch_via_firecrawl_with_payload(URL)

    text = json.dumps(envelope, ensure_ascii=False)
    assert SENTINEL_KEY not in text  # literal key never reaches the side table
    assert "Bearer" not in text  # nor the Bearer credential shape
    assert "<redacted>" in text
    assert envelope["data"]["markdown"] == BODY_MARKDOWN


# --- bytes-only variant: patch target and v2 contract unchanged -------------


def test_bytes_only_fetch_still_returns_bytes_under_v2_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FIRECRAWL_API_KEY", SENTINEL_KEY)
    fake = _FakePost(_ok())
    _wire(monkeypatch, fake)

    body = firecrawl.fetch_via_firecrawl(URL)

    assert isinstance(body, bytes)  # never a tuple: the old shape is intact
    assert body == BODY_MARKDOWN.encode("utf-8")
    sent = fake.calls[0]["json"]
    assert sent["url"] == URL
    assert sent["formats"] == ["markdown"]
    assert sent["onlyMainContent"] is True


# --- article_content_chain: tagging the winning Firecrawl leg ----------------


def test_chain_tags_winning_firecrawl_payload_with_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fake_with_payload(url: str, timeout: int = 30) -> tuple[bytes, dict[str, Any]]:
        return (BODY_MARKDOWN.encode("utf-8"), ENVELOPE)

    monkeypatch.setattr(firecrawl, "fetch_via_firecrawl_with_payload", _fake_with_payload)

    final_url, payload = enrich.article_content_chain(
        URL, reader=_reader_miss, primary_cause="primary miss"
    )

    assert final_url == URL
    assert isinstance(payload, ProviderMarkdown)
    assert payload == BODY_MARKDOWN.encode("utf-8")
    assert enrich.firecrawl_payload_of(payload) == {
        "provider": "firecrawl",
        "url": URL,
        "response": ENVELOPE,
    }


def test_chain_honors_bytes_only_patched_fetch_with_no_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The pre-payload patch target: a monkeypatched bytes-only scrape must
    # still win the chain, just without a billed envelope to carry.
    def _fake_bytes(url: str, timeout: int = 30) -> bytes:
        return BODY_MARKDOWN.encode("utf-8")

    monkeypatch.setattr(firecrawl, "fetch_via_firecrawl", _fake_bytes)

    final_url, payload = enrich.article_content_chain(
        URL, reader=_reader_miss, primary_cause="primary miss"
    )

    assert final_url == URL
    assert isinstance(payload, ProviderMarkdown)
    assert payload == BODY_MARKDOWN.encode("utf-8")
    assert enrich.firecrawl_payload_of(payload) is None


# --- propagation onto the returned document -----------------------------------


def test_enrich_document_propagates_envelope_onto_enriched_doc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _blocked(url: str, timeout: int = 30) -> str:
        raise enrich.FetchFailed(f"fetch failed for {url}: 403 cloudflare captcha challenge")

    monkeypatch.setattr(enrich, "fetch_and_clean", _blocked)
    monkeypatch.setattr(enrich, "try_fallback_reader", _reader_miss)
    monkeypatch.setattr(
        firecrawl,
        "fetch_via_firecrawl_with_payload",
        lambda url, timeout=30: (BODY_MARKDOWN.encode("utf-8"), ENVELOPE),
    )

    new_doc, method, cause = enrich.enrich_document(_thin_doc())

    assert cause is None
    assert method == enrich.METHOD_ENRICHED
    assert new_doc.content == BODY_MARKDOWN
    assert enrich.firecrawl_payload_of(new_doc) == {
        "provider": "firecrawl",
        "url": URL,
        "response": ENVELOPE,
    }


def test_fetch_extract_one_propagates_envelope_onto_doc() -> None:
    tagged = ProviderMarkdown(("<title>Gated Story</title>\n\n" + BODY_MARKDOWN).encode("utf-8"))
    tagged.__dict__[enrich.FIRECRAWL_PAYLOAD_ATTR] = {
        "provider": "firecrawl",
        "url": URL,
        "response": ENVELOPE,
    }
    job = ArticleJob(
        loc=URL,
        source_label="Social Media Today",
        retrieved_at_iso=NOW.isoformat(),
        extractor="generic",
    )

    doc, cause = fetch_extract_one(job, fetch_one=lambda url: (url, tagged))

    assert cause is None
    assert doc is not None
    assert enrich.firecrawl_payload_of(doc) == {
        "provider": "firecrawl",
        "url": URL,
        "response": ENVELOPE,
    }
