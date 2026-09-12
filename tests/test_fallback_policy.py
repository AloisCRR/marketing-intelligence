"""Gated fallback + per-source enrichment policy tests (ticket 03).

Contract under test:
- primary-miss pages (bot/challenge/JS signals) succeed via the gated fallback
  and store clean Markdown
- fallback sends data-minimizing headers (no Cookie/Authorization ever),
  backs off on 429 (honoring Retry-After, up to 2 retries), and any fallback
  failure keeps the RSS body with a recorded cause
- per-source policy (threshold override, force_on, force_off) is honored by
  the Ingestion Run; force_off performs zero fetch traffic

All I/O is faked (monkeypatch, no network, no Postgres).
"""

from __future__ import annotations

import urllib.error
from datetime import UTC, datetime
from typing import Any

import pytest
from prefect_harness import no_engine

from marketing_intelligence.normalize import NormalizedDocument, make_document

GATED_URL = "https://example.com/articles/gated-story"
FULL_URL = "https://example.com/articles/full-story"

NOW = datetime(2026, 9, 5, 12, 0, 0, tzinfo=UTC)

LONG_MARKDOWN = "# Gated Story\n\n" + " ".join(["Full article body with real content."] * 40)


def _thin_doc(url: str = GATED_URL, content: str = "Short summary.") -> NormalizedDocument:
    return make_document(
        source="Social Media Today",
        url=url,
        title="Gated Story",
        content=content,
        published_at=NOW,
        retrieved_at=NOW,
        language="en",
    )


def _full_doc() -> NormalizedDocument:
    return make_document(
        source="Social Media Today",
        url=FULL_URL,
        title="Full Story",
        content="x" * 600,
        published_at=NOW,
        retrieved_at=NOW,
        language="en",
    )


class _Resp:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def read(self) -> bytes:
        return self._body


class _CurlResponse:
    def __init__(self, status_code: int, body: bytes) -> None:
        self.status_code = status_code
        self.content = body


class _FakeCurlRequests:
    """curl_cffi.requests double for the impersonated-Chrome primary leg."""

    def __init__(self, status_code: int = 200, body: bytes = b"") -> None:
        self.calls: list[dict[str, Any]] = []
        self.status_code = status_code
        self.body = body

    def get(self, url: str, **kwargs: Any) -> _CurlResponse:
        self.calls.append({"url": url, **kwargs})
        return _CurlResponse(self.status_code, self.body)


def _failing(exc: Exception) -> Any:
    """Stub that always raises `exc` (fakes one leg of the fetch chain down)."""

    def _raise(*args: Any, **kwargs: Any) -> Any:
        raise exc

    return _raise


# --- fallback reader: privacy headers ----------------------------------------


def test_fallback_sends_data_minimizing_headers(monkeypatch: Any) -> None:
    from marketing_intelligence import enrich
    from marketing_intelligence.ingest import USER_AGENT

    captured: dict[str, Any] = {}

    def _fake_urlopen(request: Any, timeout: Any = None) -> _Resp:
        captured["request"] = request
        captured["timeout"] = timeout
        return _Resp(b"<article><p>" + b"x" * 600 + b"</p></article>")

    monkeypatch.setattr(enrich.urllib.request, "urlopen", _fake_urlopen)
    markdown = enrich.try_fallback_reader(GATED_URL, timeout=7)

    assert "x" * 100 in markdown
    assert "<" not in markdown and ">" not in markdown
    request = captured["request"]
    assert "r.jina.ai" in request.full_url  # Jina-reader-style endpoint
    assert GATED_URL in request.full_url  # original URL embedded, not dropped
    assert captured["timeout"] == 7
    sent = {k.lower(): v for k, v in request.headers.items()}
    sent.update({k.lower(): v for k, v in request.unredirected_hdrs.items()})
    assert "cookie" not in sent, "fallback must never send cookies"
    assert "authorization" not in sent, "fallback must never send credentials"
    assert sent.get("user-agent") == USER_AGENT  # explicit User-Agent
    assert sent.get("accept", "").startswith("text/")  # data-minimizing Accept


# --- fallback trigger: bot/challenge + JS-shell primary misses ----------------


def test_primary_bot_challenge_succeeds_via_fallback(monkeypatch: Any) -> None:
    from marketing_intelligence import enrich

    def _blocked(url: str, timeout: int = 30) -> str:
        raise enrich.FetchFailed(
            f"fetch failed for {url}: HTTP Error 403: Forbidden "
            "— body: just a moment ... cloudflare captcha challenge"
        )

    monkeypatch.setattr(enrich, "fetch_and_clean", _blocked)
    monkeypatch.setattr(enrich, "try_fallback_reader", lambda url, timeout=30: LONG_MARKDOWN)

    doc = _thin_doc()
    new_doc, method, cause = enrich.enrich_document_or_keep(doc)
    assert cause is None
    assert method == "enriched"
    assert new_doc.content == LONG_MARKDOWN
    assert new_doc.url == doc.url


def test_primary_js_shell_succeeds_via_fallback(monkeypatch: Any) -> None:
    from marketing_intelligence import enrich

    js_shell = (
        '<html><head><script id="__NEXT_DATA__" type="application/json">{}</script></head>'
        "<body><div>Please enable JavaScript to continue</div></body></html>"
    )
    monkeypatch.setattr(enrich, "fetch_and_clean", lambda url, timeout=30: js_shell)
    monkeypatch.setattr(enrich, "try_fallback_reader", lambda url, timeout=30: LONG_MARKDOWN)

    new_doc, method, cause = enrich.enrich_document_or_keep(_thin_doc())
    assert cause is None
    assert method == "enriched"
    assert new_doc.content == LONG_MARKDOWN


def test_challenge_body_behind_429_trips_gated_fallback(monkeypatch: Any) -> None:
    from marketing_intelligence import enrich

    fake_curl = _FakeCurlRequests(
        status_code=429, body=b"Attention Required! ... cloudflare captcha challenge"
    )
    monkeypatch.setattr(enrich, "_curl_cffi_requests", fake_curl)

    fallback_calls: list[str] = []

    def _fallback(url: str, timeout: int = 30) -> str:
        fallback_calls.append(url)
        return LONG_MARKDOWN

    monkeypatch.setattr(enrich, "try_fallback_reader", _fallback)

    new_doc, method, cause = enrich.enrich_document_or_keep(_thin_doc())
    assert [call["url"] for call in fake_curl.calls] == [GATED_URL]
    assert fake_curl.calls[0]["impersonate"] == "chrome"
    assert fallback_calls == [GATED_URL]  # body signal survived the 429: fallback ran
    assert cause is None
    assert method == "enriched"
    assert new_doc.content == LONG_MARKDOWN


def test_keywordless_403_lock_page_trips_gated_fallback(monkeypatch: Any) -> None:
    from marketing_intelligence import enrich

    # Styled bot lock page: non-empty body, no challenge keywords at all.
    lock_html = (
        b"<!DOCTYPE html><html><head><title></title><style>.lock{display:block}</style>"
        b"</head><body>"
        b'<div class="lock">Access temporarily restricted. Try again later.</div>'
        b"</body></html>"
    )
    assert not any(key in lock_html.decode().lower() for key in enrich._CHALLENGE_KEYWORDS), (
        "test premise: lock page carries no challenge keywords"
    )

    fake_curl = _FakeCurlRequests(status_code=403, body=lock_html)
    monkeypatch.setattr(enrich, "_curl_cffi_requests", fake_curl)

    fallback_calls: list[str] = []

    def _fallback(url: str, timeout: int = 30) -> str:
        fallback_calls.append(url)
        return LONG_MARKDOWN

    monkeypatch.setattr(enrich, "try_fallback_reader", _fallback)

    new_doc, method, cause = enrich.enrich_document_or_keep(_thin_doc())
    assert [call["url"] for call in fake_curl.calls] == [GATED_URL]
    assert fallback_calls == [GATED_URL]  # thin-rendered 403 counts as a miss
    assert cause is None
    assert method == "enriched"
    assert new_doc.content == LONG_MARKDOWN


def test_plain_paywall_without_challenge_signal_keeps_rss_no_fallback(
    monkeypatch: Any,
) -> None:
    from marketing_intelligence import enrich, firecrawl

    calls: list[str] = []

    def _paywalled(url: str, timeout: int = 30) -> str:
        raise enrich.FetchFailed(f"fetch failed for {url}: HTTP Error 403: Forbidden")

    def _must_not_run(url: str, timeout: int = 30) -> str:
        calls.append(url)
        raise AssertionError("fallback must stay gated on plain paywalls")

    monkeypatch.setattr(enrich, "fetch_and_clean", _paywalled)
    monkeypatch.setattr(enrich, "try_fallback_reader", _must_not_run)
    monkeypatch.setattr(firecrawl, "fetch_via_firecrawl", _must_not_run)

    kept, method, cause = enrich.enrich_document_or_keep(_thin_doc())
    assert calls == []  # neither fallback leg ran
    assert method == "rss"
    assert cause is not None and "403" in cause


# --- fallback failure semantics ------------------------------------------------


def test_fallback_429_backs_off_then_keeps_rss_with_rate_limited_cause(
    monkeypatch: Any,
) -> None:
    from marketing_intelligence import enrich, firecrawl

    attempts: list[str] = []
    sleeps: list[float] = []

    def _rate_limited(url: str, timeout: int = 30) -> str:
        raise enrich.FetchFailed(f"fetch failed for {url}: HTTP Error 429: Too Many Requests")

    def _always_429(request: Any, timeout: Any = None) -> Any:
        attempts.append(request.full_url)
        if len(attempts) == 1:
            hdrs: Any = {"Retry-After": "2"}
        else:
            hdrs = {"Retry-After": "0"}
        raise urllib.error.HTTPError(
            request.full_url,
            429,
            "Too Many Requests",
            hdrs,
            None,  # type: ignore[arg-type]
        )

    monkeypatch.setattr(enrich, "fetch_and_clean", _rate_limited)
    monkeypatch.setattr(enrich.urllib.request, "urlopen", _always_429)
    monkeypatch.setattr(enrich.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(
        firecrawl, "fetch_via_firecrawl", _failing(firecrawl.FirecrawlFailed("firecrawl down"))
    )

    doc = _thin_doc()
    kept, method, cause = enrich.enrich_document_or_keep(doc)
    assert kept is doc
    assert method == "rss"
    assert cause is not None and "rate-limited" in cause
    assert "firecrawl down" in cause  # last leg's cause chained, not swallowed
    assert len(attempts) == 3  # initial attempt + 2 retries, then give up
    assert sleeps == [2.0, 0.0]  # Retry-After honored per attempt


def test_fallback_failure_keeps_rss_and_chains_primary_cause(monkeypatch: Any) -> None:
    from marketing_intelligence import enrich, firecrawl

    def _empty_primary(url: str, timeout: int = 30) -> str:
        raise enrich.UnparseableBody(f"unparseable body for {url}: empty after cleaning")

    def _reader_down(url: str, timeout: int = 30) -> str:
        raise enrich.FetchFailed(f"fallback fetch failed for {url}: HTTP Error 403")

    def _firecrawl_down(url: str, timeout: int = 30) -> bytes:
        raise firecrawl.FirecrawlFailed(f"firecrawl scrape failed for {url}: HTTP Error 500")

    monkeypatch.setattr(enrich, "fetch_and_clean", _empty_primary)
    monkeypatch.setattr(enrich, "try_fallback_reader", _reader_down)
    monkeypatch.setattr(firecrawl, "fetch_via_firecrawl", _firecrawl_down)

    doc = _thin_doc()
    with pytest.raises(enrich.EnrichmentError) as info:
        enrich.enrich_document(doc)
    message = str(info.value)
    assert "unparseable body" in message  # primary cause preserved, not swallowed
    assert "reader: " in message and "403" in message  # Jina leg cause named
    assert "firecrawl: " in message and "500" in message  # paid leg cause named

    kept, method, cause = enrich.enrich_document_or_keep(doc)
    assert kept is doc
    assert method == "rss"
    assert cause is not None and "unparseable body" in cause


def test_thin_fallback_output_counts_as_miss_and_keeps_rss(monkeypatch: Any) -> None:
    from marketing_intelligence import enrich, firecrawl

    monkeypatch.setattr(
        enrich,
        "fetch_and_clean",
        lambda url, timeout=30: (_ for _ in ()).throw(
            enrich.FetchFailed(f"fetch failed for {url}: cloudflare challenge captcha")
        ),
    )
    # A reader stub / error page carries no substance: must not become canonical.
    monkeypatch.setattr(enrich, "try_fallback_reader", lambda url, timeout=30: "Not found.")
    monkeypatch.setattr(
        firecrawl, "fetch_via_firecrawl", _failing(firecrawl.FirecrawlFailed("firecrawl down"))
    )

    kept, method, cause = enrich.enrich_document_or_keep(_thin_doc())
    assert method == "rss"
    assert cause is not None
    assert "delivered no substantive text" in cause


def test_reader_miss_firecrawl_success_stores_enriched(monkeypatch: Any) -> None:
    from marketing_intelligence import enrich, firecrawl

    monkeypatch.setattr(
        enrich,
        "fetch_and_clean",
        lambda url, timeout=30: (_ for _ in ()).throw(
            enrich.FetchFailed(f"fetch failed for {url}: cloudflare captcha challenge")
        ),
    )
    reader_calls: list[str] = []
    monkeypatch.setattr(
        enrich,
        "try_fallback_reader",
        lambda url, timeout=30: (reader_calls.append(url), "Not found.")[1],
    )
    scrape_calls: list[str] = []
    monkeypatch.setattr(
        firecrawl,
        "fetch_via_firecrawl",
        lambda url, timeout=30: (scrape_calls.append(url), LONG_MARKDOWN.encode())[1],
    )

    doc = _thin_doc()
    new_doc, method, cause = enrich.enrich_document_or_keep(doc)
    assert cause is None
    assert method == "enriched"
    assert reader_calls == [GATED_URL]  # Jina reader tried first
    assert scrape_calls == [GATED_URL]  # Firecrawl only after the reader missed
    assert new_doc.content == LONG_MARKDOWN
    assert new_doc.url == doc.url


def test_missing_firecrawl_key_is_explicit_skipped_cause_never_leaks_key(
    monkeypatch: Any,
) -> None:
    from marketing_intelligence import enrich

    # Blank key (the real ambient key is masked): the chain must record the
    # explicit skipped cause, never key-derived material of any kind.
    monkeypatch.setenv("FIRECRAWL_API_KEY", "   ")
    monkeypatch.setattr(
        enrich,
        "fetch_and_clean",
        lambda url, timeout=30: (_ for _ in ()).throw(
            enrich.FetchFailed(f"fetch failed for {url}: cloudflare captcha challenge")
        ),
    )
    monkeypatch.setattr(enrich, "try_fallback_reader", _failing(enrich.FetchFailed("reader down")))

    doc = _thin_doc()
    kept, method, cause = enrich.enrich_document_or_keep(doc)
    assert kept is doc
    assert method == "rss"
    assert cause is not None
    assert "firecrawl skipped: missing FIRECRAWL_API_KEY" in cause
    assert "Bearer" not in cause and "<redacted>" not in cause


# --- per-source policy ----------------------------------------------------------


def test_unknown_source_returns_default_policy_never_raises() -> None:
    from marketing_intelligence.sources import get_enrichment_policy

    assert get_enrichment_policy("No Such Source") == {"threshold": 500, "mode": "auto"}


def test_policy_defaults_when_registry_entry_has_no_override(monkeypatch: Any) -> None:
    import marketing_intelligence.sources as sources

    monkeypatch.setattr(
        sources, "get_source", lambda name: {"name": name, "rss_url": "https://example.com/rss"}
    )
    assert sources.get_enrichment_policy("Anything") == {"threshold": 500, "mode": "auto"}


def test_policy_threshold_override_and_modes(monkeypatch: Any) -> None:
    import marketing_intelligence.sources as sources

    def _entry(overrides: Any) -> dict[str, Any]:
        return {"name": "X", "rss_url": "https://example.com/rss", "enrichment": overrides}

    monkeypatch.setattr(sources, "get_source", lambda name: _entry({"threshold": 1000}))
    assert sources.get_enrichment_policy("X") == {"threshold": 1000, "mode": "auto"}

    monkeypatch.setattr(sources, "get_source", lambda name: _entry({"mode": "force_on"}))
    assert sources.get_enrichment_policy("X") == {"threshold": 500, "mode": "force_on"}

    monkeypatch.setattr(sources, "get_source", lambda name: _entry({"mode": "force_off"}))
    assert sources.get_enrichment_policy("X") == {"threshold": 500, "mode": "force_off"}

    monkeypatch.setattr(
        sources, "get_source", lambda name: _entry({"threshold": -5, "mode": "sometimes"})
    )
    assert sources.get_enrichment_policy("X") == {"threshold": 500, "mode": "auto"}


def test_source_lookup_behavior_unchanged() -> None:
    import marketing_intelligence.sources as sources

    ora_before = sources.list_sources()
    assert isinstance(ora_before, list) and len(ora_before) >= 1
    try:
        sources.get_source("No Such Source")
    except KeyError:
        pass
    else:
        raise AssertionError("get_source must still raise KeyError for unknown sources")


# --- policy honored by the enrichment stage --------------------------------------


def test_threshold_override_honored_by_enrich_task(monkeypatch: Any, no_engine: None) -> None:
    import marketing_intelligence.flows as flows
    from marketing_intelligence import enrich as enrich_mod

    monkeypatch.setattr(
        flows,
        "get_enrichment_policy",
        lambda name: {"threshold": 1000, "mode": "auto"},
    )
    fetched: list[str] = []
    monkeypatch.setattr(
        enrich_mod,
        "fetch_and_clean",
        lambda url, timeout=30: (fetched.append(url), LONG_MARKDOWN)[1],
    )
    # 600 chars: sufficient under the 500 default, thin under the 1000 override.
    docs, skipped, causes = flows.enrich_task([_full_doc()], source_name="MarTech")
    assert fetched == [FULL_URL]
    assert docs[0].content == LONG_MARKDOWN
    assert skipped == 0
    assert causes == []


def test_force_on_enriches_sufficient_rss(monkeypatch: Any, no_engine: None) -> None:
    import marketing_intelligence.flows as flows
    from marketing_intelligence import enrich as enrich_mod

    monkeypatch.setattr(
        flows, "get_enrichment_policy", lambda name: {"threshold": 500, "mode": "force_on"}
    )
    fetched: list[str] = []
    monkeypatch.setattr(
        enrich_mod,
        "fetch_and_clean",
        lambda url, timeout=30: (fetched.append(url), LONG_MARKDOWN)[1],
    )
    docs, skipped, causes = flows.enrich_task([_full_doc()], source_name="MarTech")
    assert fetched == [FULL_URL]  # sufficient body still fetched under force_on
    assert docs[0].content == LONG_MARKDOWN
    assert skipped == 0
    assert causes == []


def test_force_off_performs_zero_fetch(monkeypatch: Any, no_engine: None) -> None:
    import marketing_intelligence.flows as flows
    from marketing_intelligence import enrich as enrich_mod

    monkeypatch.setattr(
        flows, "get_enrichment_policy", lambda name: {"threshold": 500, "mode": "force_off"}
    )

    def _must_not_fetch(url: str, timeout: int = 30) -> str:
        raise AssertionError(f"zero fetch under force_off, got {url}")

    def _must_not_open(request: Any, timeout: Any = None) -> Any:
        raise AssertionError("zero network traffic under force_off")

    monkeypatch.setattr(enrich_mod, "fetch_and_clean", _must_not_fetch)
    monkeypatch.setattr(enrich_mod, "try_fallback_reader", _must_not_fetch)
    monkeypatch.setattr(enrich_mod.urllib.request, "urlopen", _must_not_open)

    doc = _thin_doc()
    docs, skipped, causes = flows.enrich_task([doc], source_name="MarTech")
    assert docs == [doc] and docs[0] is doc
    assert skipped == 0
    assert causes == []


def test_flow_force_off_has_no_enrich_keys_and_completes(monkeypatch: Any, no_engine: None) -> None:
    import marketing_intelligence.flows as flows
    from marketing_intelligence import enrich as enrich_mod

    feed = (
        b'<?xml version="1.0" encoding="utf-8"?>'
        b'<rss version="2.0"><channel><title>SMT</title><link>https://example.com</link>'
        b"<item><title>Thin Story</title><link>https://example.com/articles/thin-story</link>"
        b"<description>Short.</description>"
        b"<pubDate>Thu, 03 Sep 2026 09:15:00 -0500</pubDate></item>"
        b"</channel></rss>"
    )
    monkeypatch.setattr(
        flows, "get_source", lambda name: {"name": name, "rss_url": "https://example.com/rss"}
    )
    monkeypatch.setattr(
        flows, "get_enrichment_policy", lambda name: {"threshold": 500, "mode": "force_off"}
    )
    monkeypatch.setattr(flows, "fetch_rss", lambda url, timeout=30: feed)
    monkeypatch.setattr(flows, "upsert_documents", lambda docs: (len(docs), 0))

    def _must_not_fetch(url: str, timeout: int = 30) -> str:
        raise AssertionError(f"zero fetch under force_off, got {url}")

    monkeypatch.setattr(enrich_mod, "fetch_and_clean", _must_not_fetch)
    monkeypatch.setattr(enrich_mod, "try_fallback_reader", _must_not_fetch)

    result = flows.ingest_source_flow(source_name="Social Media Today")
    assert result["inserted"] == 1
    assert "error" not in result
    assert "enrich_skipped" not in result
    assert "enrich_causes" not in result
