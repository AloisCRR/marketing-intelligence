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

import io
import urllib.error
from datetime import UTC, datetime
from typing import Any

import pytest
from prefect_harness import no_engine

from brain.normalize import NormalizedDocument, make_document

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


# --- fallback reader: privacy headers ----------------------------------------


def test_fallback_sends_data_minimizing_headers(monkeypatch: Any) -> None:
    from brain import enrich
    from brain.ingest import USER_AGENT

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
    from brain import enrich

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
    from brain import enrich

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
    from brain import enrich

    fallback_calls: list[str] = []

    def _challenge_429(request: Any, timeout: Any = None) -> Any:
        raise urllib.error.HTTPError(
            request.full_url,
            429,
            "Too Many Requests",
            {},  # type: ignore[arg-type]
            io.BytesIO(b"Attention Required! ... cloudflare captcha challenge"),
        )

    def _fallback(url: str, timeout: int = 30) -> str:
        fallback_calls.append(url)
        return LONG_MARKDOWN

    monkeypatch.setattr(enrich.urllib.request, "urlopen", _challenge_429)
    monkeypatch.setattr(enrich, "try_fallback_reader", _fallback)

    new_doc, method, cause = enrich.enrich_document_or_keep(_thin_doc())
    assert fallback_calls == [GATED_URL]  # body signal survived the 429: fallback ran
    assert cause is None
    assert method == "enriched"
    assert new_doc.content == LONG_MARKDOWN


def test_keywordless_403_lock_page_trips_gated_fallback(monkeypatch: Any) -> None:
    from brain import enrich

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

    class _FakeCurlRequests:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def get(self, url: str, **kwargs: Any) -> Any:
            self.calls.append(url)

            class _Resp:
                status_code = 403
                content = lock_html

            return _Resp()

    fake_curl = _FakeCurlRequests()
    monkeypatch.setattr(enrich, "_curl_cffi_requests", fake_curl)

    def _locked(request: Any, timeout: Any = None) -> Any:
        raise urllib.error.HTTPError(
            request.full_url,
            403,
            "Forbidden",
            {},  # type: ignore[arg-type]
            io.BytesIO(lock_html),
        )

    fallback_calls: list[str] = []

    def _fallback(url: str, timeout: int = 30) -> str:
        fallback_calls.append(url)
        return LONG_MARKDOWN

    monkeypatch.setattr(enrich.urllib.request, "urlopen", _locked)
    monkeypatch.setattr(enrich, "try_fallback_reader", _fallback)

    new_doc, method, cause = enrich.enrich_document_or_keep(_thin_doc())
    assert fake_curl.calls == [GATED_URL]
    assert fallback_calls == [GATED_URL]  # thin-rendered 403 counts as a miss
    assert cause is None
    assert method == "enriched"
    assert new_doc.content == LONG_MARKDOWN


def test_plain_paywall_without_challenge_signal_keeps_rss_no_fallback(
    monkeypatch: Any,
) -> None:
    from brain import enrich

    calls: list[str] = []

    def _paywalled(url: str, timeout: int = 30) -> str:
        raise enrich.FetchFailed(f"fetch failed for {url}: HTTP Error 403: Forbidden")

    def _must_not_run(url: str, timeout: int = 30) -> str:
        calls.append(url)
        raise AssertionError("fallback must stay gated on plain paywalls")

    monkeypatch.setattr(enrich, "fetch_and_clean", _paywalled)
    monkeypatch.setattr(enrich, "try_fallback_reader", _must_not_run)

    kept, method, cause = enrich.enrich_document_or_keep(_thin_doc())
    assert calls == []
    assert method == "rss"
    assert cause is not None and "403" in cause


# --- fallback failure semantics ------------------------------------------------


def test_fallback_429_backs_off_then_keeps_rss_with_rate_limited_cause(
    monkeypatch: Any,
) -> None:
    from brain import enrich

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

    doc = _thin_doc()
    kept, method, cause = enrich.enrich_document_or_keep(doc)
    assert kept is doc
    assert method == "rss"
    assert cause is not None and "rate-limited" in cause
    assert len(attempts) == 3  # initial attempt + 2 retries, then give up
    assert sleeps == [2.0, 0.0]  # Retry-After honored per attempt


def test_fallback_failure_keeps_rss_and_chains_primary_cause(monkeypatch: Any) -> None:
    from brain import enrich

    def _empty_primary(url: str, timeout: int = 30) -> str:
        raise enrich.UnparseableBody(f"unparseable body for {url}: empty after cleaning")

    def _fallback_down(url: str, timeout: int = 30) -> str:
        raise enrich.FetchFailed(f"fallback fetch failed for {url}: HTTP Error 403")

    monkeypatch.setattr(enrich, "fetch_and_clean", _empty_primary)
    monkeypatch.setattr(enrich, "try_fallback_reader", _fallback_down)

    doc = _thin_doc()
    with __import__("pytest").raises(enrich.EnrichmentError):
        enrich.enrich_document(doc)
    kept, method, cause = enrich.enrich_document_or_keep(doc)
    assert kept is doc
    assert method == "rss"
    assert cause is not None
    assert "unparseable body" in cause  # primary cause preserved, not swallowed


def test_thin_fallback_output_counts_as_miss_and_keeps_rss(monkeypatch: Any) -> None:
    from brain import enrich

    monkeypatch.setattr(
        enrich,
        "fetch_and_clean",
        lambda url, timeout=30: (_ for _ in ()).throw(
            enrich.FetchFailed(f"fetch failed for {url}: cloudflare challenge captcha")
        ),
    )
    # A reader stub / error page carries no substance: must not become canonical.
    monkeypatch.setattr(enrich, "try_fallback_reader", lambda url, timeout=30: "Not found.")

    kept, method, cause = enrich.enrich_document_or_keep(_thin_doc())
    assert method == "rss"
    assert cause is not None


# --- per-source policy ----------------------------------------------------------


def test_unknown_source_returns_default_policy_never_raises() -> None:
    from brain.sources import get_enrichment_policy

    assert get_enrichment_policy("No Such Source") == {"threshold": 500, "mode": "auto"}


def test_policy_defaults_when_registry_entry_has_no_override(monkeypatch: Any) -> None:
    import brain.sources as sources

    monkeypatch.setattr(
        sources, "get_source", lambda name: {"name": name, "rss_url": "https://example.com/rss"}
    )
    assert sources.get_enrichment_policy("Anything") == {"threshold": 500, "mode": "auto"}


def test_policy_threshold_override_and_modes(monkeypatch: Any) -> None:
    import brain.sources as sources

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
    import brain.sources as sources

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
    import brain.flows as flows
    from brain import enrich as enrich_mod

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
    import brain.flows as flows
    from brain import enrich as enrich_mod

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
    import brain.flows as flows
    from brain import enrich as enrich_mod

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
    import brain.flows as flows
    from brain import enrich as enrich_mod

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
