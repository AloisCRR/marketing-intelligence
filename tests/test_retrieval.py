"""Per-source feed retrieval lane tests (oracle ora-1 fallback lane).

Observable behavior (not privates):
- get_retrieval_policy: allowlist validation, defaults, never raises
- curated JSON carries retrieval stanzas for all 20 sources (RSS + no-RSS)
- fetch_rss stdlib-only lane keeps current behavior byte-identical
- fetch_rss impersonated-feed lane retries once via curl_cffi on 403/challenge
  evidence, else raises an explicit fetch error (no Firecrawl/Playwright)
- fetch_task threads source_name -> retrieval policy; the leaf flow passes it
"""

from __future__ import annotations

import io
import json
import urllib.error
from email.message import Message
from pathlib import Path
from typing import Any

import pytest
from fake_transport import FakeTransport
from prefect_harness import no_engine

import brain.flows as flows
import brain.ingest as ingest
from brain.ingest import fetch_rss
from brain.normalize import NormalizedDocument
from brain.sources import get_retrieval_policy, get_source

FIXTURES = Path(__file__).parent / "fixtures"
CURATED = (
    Path(__file__).resolve().parents[1]
    / ".scratch"
    / "trend-intelligence-brain"
    / "curated-sources.json"
)


# --- policy validation -------------------------------------------------------


def test_v1_policies_match_oracle_design() -> None:
    assert get_retrieval_policy("Social Media Today") == {
        "type": "rss",
        "policy": "impersonated-feed",
    }
    assert get_retrieval_policy("InfoMoney") == {"type": "rss", "policy": "impersonated-feed"}
    assert get_retrieval_policy("MarTech") == {"type": "rss", "policy": "stdlib-only"}
    assert get_retrieval_policy("Professional Jeweller") == {
        "type": "rss",
        "policy": "impersonated-feed",
    }


def test_policy_never_raises_and_defaults() -> None:
    default = {"type": "rss", "policy": "stdlib-only"}
    assert get_retrieval_policy("No Such Source") == default
    assert get_retrieval_policy(None) == default
    # Registry RSS sources with explicit stdlib-only stanzas equal the default.
    assert get_retrieval_policy("JCK Online") == default
    assert get_retrieval_policy("Swarovski PR Newswire") == default


def test_policy_result_is_a_copy() -> None:
    first = get_retrieval_policy("Social Media Today")
    first["policy"] = "mutated"
    assert get_retrieval_policy("Social Media Today")["policy"] == "impersonated-feed"


# --- curated JSON stanzas ------------------------------------------------------


def test_curated_v1_stanzas() -> None:
    entries = {e["source_name"]: e for e in json.loads(CURATED.read_text(encoding="utf-8"))}
    assert entries["Social Media Today"]["retrieval"] == {
        "type": "rss",
        "policy": "impersonated-feed",
    }
    assert entries["InfoMoney"]["retrieval"] == {"type": "rss", "policy": "impersonated-feed"}
    assert entries["MarTech"]["retrieval"] == {"type": "rss", "policy": "stdlib-only"}
    assert entries["Professional Jeweller"]["retrieval"] == {
        "type": "rss",
        "policy": "impersonated-feed",
    }
    for name in (
        "Social Media Today",
        "InfoMoney",
        "MarTech",
        "Professional Jeweller",
    ):
        assert entries[name]["enrichment"] == {"threshold": 500, "mode": "auto"}
    # RSS extras carry explicit rss stanzas.
    for name in ("JCK Online", "Swarovski PR Newswire"):
        assert entries[name]["retrieval"] == {"type": "rss", "policy": "stdlib-only"}


def test_curated_no_rss_stanzas_declare_route_and_extractor() -> None:
    """Ticket 07: every no-RSS source declares a discovery route + extractor."""
    entries = {e["source_name"]: e for e in json.loads(CURATED.read_text(encoding="utf-8"))}
    assert len(entries) == 20
    expected_types = {
        "National Jeweler": "url-set+hub",
        "Exame": "sitemap",
        "Modaes": "sitemap",
        "Retail Dive": "sitemap+hub",
        "Jing Daily": "sitemap+hub",
        "Consumidor Moderno": "sitemap",
        "Meio & Mensagem": "sitemap",
        "Marketing Dive": "sitemap+hub",
        "MarketingDirecto": "sitemap",
        "Propmark": "sitemap",
        "Insider Latam": "sitemap",
        "LVMH Press Releases": "sitemap",
        "Richemont Media": "url-set",
        "Forbes México": "sitemap",
    }
    for name, rtype in expected_types.items():
        stanza = entries[name]["retrieval"]
        assert stanza["type"] == rtype, name
        assert stanza["extractor"] in ("generic", "json-ld-first"), name
    # Only Jing Daily needs JSON-LD-first extraction.
    assert entries["Jing Daily"]["retrieval"]["extractor"] == "json-ld-first"
    # Dive news sitemaps keep the proven impersonated retry lane.
    assert entries["Retail Dive"]["retrieval"]["policy"] == "impersonated-feed"
    assert entries["Marketing Dive"]["retrieval"]["policy"] == "impersonated-feed"
    # Hub-anchor routes declare their link patterns.
    assert entries["Jing Daily"]["retrieval"]["link_pattern"] == "/posts/"
    assert entries["Retail Dive"]["retrieval"]["link_pattern"] == "/news/"
    assert entries["National Jeweler"]["retrieval"]["link_pattern"] == "/articles/"


# --- fetch_rss fakes -----------------------------------------------------------


class _FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self, *args: Any) -> bytes:
        return self._payload

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *args: Any) -> bool:
        return False


def _http_error(url: str, code: int, body: bytes) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, code, "Forbidden", Message(), io.BytesIO(body))


class _FakeCurlResponse:
    def __init__(self, status_code: int, content: bytes) -> None:
        self.status_code = status_code
        self.content = content


class _FakeCurl:
    def __init__(self, status_code: int = 200, content: bytes = b"<rss/>") -> None:
        self.calls: list[dict[str, Any]] = []
        self._status_code = status_code
        self._content = content

    def get(self, url: str, **kwargs: Any) -> _FakeCurlResponse:
        self.calls.append({"url": url, **kwargs})
        return _FakeCurlResponse(self._status_code, self._content)


def _stub_urlopen(monkeypatch: pytest.MonkeyPatch, payload_or_exc: Any) -> None:
    def fake_open(request: Any, timeout: int = 30) -> _FakeResponse:
        if isinstance(payload_or_exc, Exception):
            raise payload_or_exc
        return _FakeResponse(bytes(payload_or_exc))

    monkeypatch.setattr("urllib.request.urlopen", fake_open)


# --- stdlib-only lane: current behavior ---------------------------------------


def test_stdlib_lane_passes_bytes_through(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_urlopen(monkeypatch, b"<rss>ok</rss>")
    assert fetch_rss("http://example.com/feed") == b"<rss>ok</rss>"
    assert fetch_rss("http://example.com/feed", policy="stdlib-only") == b"<rss>ok</rss>"


def test_stdlib_lane_for_unknown_policy_and_unregistered_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_urlopen(monkeypatch, b"<rss>ok</rss>")
    assert fetch_rss("http://example.com/feed", policy="bogus") == b"<rss>ok</rss>"


def test_stdlib_source_uses_plain_path_without_curl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeCurl()
    monkeypatch.setattr(ingest, "_curl_cffi_requests", fake)
    _stub_urlopen(monkeypatch, b"<rss>ok</rss>")
    assert fetch_rss(get_source("MarTech")["rss_url"]) == b"<rss>ok</rss>"
    assert fake.calls == []


# --- impersonated-feed lane ----------------------------------------------------


def test_impersonated_lane_first_try_success_needs_no_curl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeCurl()
    monkeypatch.setattr(ingest, "_curl_cffi_requests", fake)
    _stub_urlopen(monkeypatch, b"<rss>clean</rss>")
    assert fetch_rss("http://example.com/feed", policy="impersonated-feed") == b"<rss>clean</rss>"
    assert fake.calls == []


def test_impersonated_lane_retries_on_403(monkeypatch: pytest.MonkeyPatch) -> None:
    url = "http://example.com/feed"
    _stub_urlopen(monkeypatch, _http_error(url, 403, b""))
    fake = _FakeCurl(content=b"<rss>via-impersonation</rss>")
    monkeypatch.setattr(ingest, "_curl_cffi_requests", fake)
    assert fetch_rss(url, policy="impersonated-feed") == b"<rss>via-impersonation</rss>"
    assert len(fake.calls) == 1
    assert fake.calls[0]["impersonate"] == "chrome"
    assert fake.calls[0]["url"] == url


def test_impersonated_lane_retries_on_challenge_body(monkeypatch: pytest.MonkeyPatch) -> None:
    url = "http://example.com/feed"
    body = b"<html>Attention Required! | Cloudflare challenge</html>"
    _stub_urlopen(monkeypatch, _http_error(url, 500, body))
    fake = _FakeCurl(content=b"<rss>via-impersonation</rss>")
    monkeypatch.setattr(ingest, "_curl_cffi_requests", fake)
    assert fetch_rss(url, policy="impersonated-feed") == b"<rss>via-impersonation</rss>"
    assert len(fake.calls) == 1


def test_impersonated_lane_explicit_error_without_curl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "http://example.com/feed"
    _stub_urlopen(monkeypatch, _http_error(url, 403, b""))
    monkeypatch.setattr(ingest, "_curl_cffi_requests", None)
    with pytest.raises(RuntimeError, match="impersonated-feed"):
        fetch_rss(url, policy="impersonated-feed")


def test_impersonated_lane_plain_404_is_explicit_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "http://example.com/feed"
    _stub_urlopen(monkeypatch, _http_error(url, 404, b"not found"))
    fake = _FakeCurl()
    monkeypatch.setattr(ingest, "_curl_cffi_requests", fake)
    with pytest.raises(RuntimeError, match="impersonated-feed"):
        fetch_rss(url, policy="impersonated-feed")
    assert fake.calls == []


def test_impersonated_lane_transport_error_is_explicit_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_urlopen(monkeypatch, urllib.error.URLError("timed out"))
    fake = _FakeCurl()
    monkeypatch.setattr(ingest, "_curl_cffi_requests", fake)
    with pytest.raises(RuntimeError, match="impersonated-feed"):
        fetch_rss("http://example.com/feed", policy="impersonated-feed")
    assert fake.calls == []


def test_impersonated_lane_failed_retry_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    url = "http://example.com/feed"
    _stub_urlopen(monkeypatch, _http_error(url, 403, b""))
    fake = _FakeCurl(status_code=403, content=b"still blocked")
    monkeypatch.setattr(ingest, "_curl_cffi_requests", fake)
    with pytest.raises(RuntimeError, match="impersonated-feed"):
        fetch_rss(url, policy="impersonated-feed")


def test_impersonated_lane_resolves_registered_url_without_policy_arg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = get_source("Social Media Today")["rss_url"]
    _stub_urlopen(monkeypatch, _http_error(url, 403, b""))
    fake = _FakeCurl(content=b"<rss>via-impersonation</rss>")
    monkeypatch.setattr(ingest, "_curl_cffi_requests", fake)
    assert fetch_rss(url) == b"<rss>via-impersonation</rss>"
    assert len(fake.calls) == 1


# --- fetch_task threading ------------------------------------------------------


def test_fetch_task_threads_source_policy_without_changing_fetch_contract(
    monkeypatch: pytest.MonkeyPatch, no_engine: None
) -> None:
    seen: list[tuple] = []

    def fake_fetch(url: str, timeout: int = 30) -> bytes:
        seen.append((url, timeout))
        return b"<rss/>"

    monkeypatch.setattr(flows, "fetch_rss", fake_fetch)
    out = flows.fetch_task("http://example.com/feed", source_name="MarTech")
    assert out == b"<rss/>"
    assert seen == [("http://example.com/feed", 30)]  # single-arg compatible
    # Unknown sources never raise: default lane applies.
    assert flows.fetch_task("http://example.com/feed", source_name="No Such Source") == b"<rss/>"


def test_leaf_flow_threads_source_name_into_fetch_task(
    monkeypatch: pytest.MonkeyPatch, no_engine: None
) -> None:
    captured: dict[str, Any] = {}

    def fake_fetch_task(url: str, source_name: str | None = None) -> bytes:
        captured["url"] = url
        captured["source_name"] = source_name
        return (FIXTURES / "martech_sample.xml").read_bytes()

    monkeypatch.setattr(flows, "fetch_task", fake_fetch_task)
    monkeypatch.setattr(flows, "enrich_document_or_keep", lambda doc, *a, **k: (doc, "rss", None))
    monkeypatch.setattr(flows, "upsert_documents", lambda docs: (len(docs), 0))
    result = flows.ingest_source_flow(source_name="MarTech")
    assert result == {"inserted": 3, "skipped": 0}
    assert captured["source_name"] == "MarTech"
    assert captured["url"] == get_source("MarTech")["rss_url"]


def test_impersonated_source_flow_still_succeeds_with_stubbed_fetch(
    monkeypatch: pytest.MonkeyPatch, no_engine: None
) -> None:
    """Policy resolution must not break impersonated sources under the standard
    (url, timeout) fetch stub used across the suite."""
    fixture = (FIXTURES / "smt_sample.xml").read_bytes()
    transport = FakeTransport({get_source("Social Media Today")["rss_url"]: fixture})
    monkeypatch.setattr(flows, "fetch_rss", transport.fetch_rss)
    monkeypatch.setattr(flows, "enrich_document_or_keep", lambda doc, *a, **k: (doc, "rss", None))
    monkeypatch.setattr(flows, "upsert_documents", lambda docs: (len(docs), 0))
    result = flows.ingest_source_flow(source_name="Social Media Today")
    assert result["inserted"] == 3
    assert "error" not in result
    transport.assert_no_sleep()  # RSS lane never paces


def test_batch_keeps_shape_across_both_lanes(
    monkeypatch: pytest.MonkeyPatch, no_engine: None
) -> None:
    by_url = {get_source(n)["rss_url"]: n for n in ("MarTech", "InfoMoney")}
    fixtures = {
        "MarTech": (FIXTURES / "martech_sample.xml").read_bytes(),
        "InfoMoney": (FIXTURES / "infomoney_sample.xml").read_bytes(),
    }
    transport = FakeTransport({rss_url: fixtures[name] for rss_url, name in by_url.items()})
    monkeypatch.setattr(flows, "fetch_rss", transport.fetch_rss)
    monkeypatch.setattr(flows, "enrich_document_or_keep", lambda doc, *a, **k: (doc, "rss", None))

    def fake_upsert(docs: list[NormalizedDocument]) -> tuple[int, int]:
        return (len(docs), 0)

    monkeypatch.setattr(flows, "upsert_documents", fake_upsert)
    results = flows.ingest_sources_flow(source_names=["MarTech", "InfoMoney"])
    assert results["MarTech"] == {"inserted": 3, "skipped": 0}
    assert results["InfoMoney"] == {"inserted": 3, "skipped": 0}


# --- ticket 07: retrieval config seam (registry + fallbacks, no RSS change) ---


def test_registry_loads_all_20_sources() -> None:
    from brain.sources import list_sources

    names = [e["name"] for e in list_sources()]
    assert len(names) == 20
    assert len(set(names)) == 20


def test_no_rss_entries_keep_null_rss_url_and_iso_language() -> None:
    expected_language = {
        "National Jeweler": "en",
        "Exame": "pt",
        "Modaes": "es",
        "Retail Dive": "en",
        "Jing Daily": "en",
        "Consumidor Moderno": "pt",
        "Meio & Mensagem": "pt",
        "Marketing Dive": "en",
        "MarketingDirecto": "es",
        "Propmark": "pt",
        "Insider Latam": "es",
        "LVMH Press Releases": "en",
        "Richemont Media": "en",
        "Forbes México": "es",
    }
    for name, lang in expected_language.items():
        src = get_source(name)
        assert src["rss_url"] is None, name
        assert src["language"] == lang, name
        assert src["hub_url"], name


def test_no_rss_policies_validate_and_never_raise() -> None:
    assert get_retrieval_policy("Jing Daily")["type"] == "sitemap+hub"
    assert get_retrieval_policy("Jing Daily")["extractor"] == "json-ld-first"
    # Sitemap refs live in curated-sources.json (09 lane owns them): assert
    # shape here, exact URLs in the curated-stanza tests.
    modaes = get_retrieval_policy("Modaes")
    assert modaes["type"] == "sitemap"
    assert modaes["policy"] == "stdlib-only"
    assert modaes["extractor"] == "generic"
    assert len(modaes["sitemaps"]) >= 1
    assert get_retrieval_policy("National Jeweler")["type"] == "url-set+hub"
    assert get_retrieval_policy("No Such Source") == {"type": "rss", "policy": "stdlib-only"}
    assert get_retrieval_policy(None) == {"type": "rss", "policy": "stdlib-only"}


def test_retrieval_config_fills_defaults_for_ticket_08() -> None:
    from brain.sources import get_retrieval_config

    cfg = get_retrieval_config("MarketingDirecto")
    assert cfg == {
        "type": "sitemap",
        "policy": "stdlib-only",
        "extractor": "generic",
        # Verified live 2026-09-06: Yoast index + news sitemap (robots.txt
        # declares both); pacing honors robots Crawl-delay: 10.
        "sitemaps": [
            "https://www.marketingdirecto.com/news-sitemap.xml",
            "https://www.marketingdirecto.com/sitemap_index.xml",
        ],
        "hub": "https://www.marketingdirecto.com/",
        "hub_pages": [],
        "link_pattern": None,
        "sitemap_pattern": None,
        "id_guard": False,
        "pacing_ms": 10000,
        "max_urls": 50,
    }
    # Unknown sources fall back to safe RSS defaults without raising.
    unknown = get_retrieval_config("No Such Source")
    assert unknown["type"] == "rss"
    assert unknown["policy"] == "stdlib-only"
    assert unknown["extractor"] == "generic"
    assert unknown["sitemaps"] == []
    assert get_retrieval_config(None)["type"] == "rss"


def test_invalid_stanza_values_fall_back_without_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import brain.sources as sources

    real_get_source = sources.get_source

    def fake_get_source(name: str) -> dict[str, Any]:
        entry = real_get_source(name)
        if name == "Jing Daily":
            entry["retrieval"] = {
                "type": "teleport",
                "policy": "bogus",
                "extractor": "bogus",
                "sitemaps": "not-a-list",
                "pacing_ms": -1,
            }
        return entry

    monkeypatch.setattr(sources, "get_source", fake_get_source)
    assert sources.get_retrieval_policy("Jing Daily") == {"type": "rss", "policy": "stdlib-only"}


def test_rss_policy_shapes_unchanged_for_existing_lanes() -> None:
    # RSS stanzas keep the exact {"type", "policy"} shape: ingest.py and
    # flows.py read ["policy"] exactly as before.
    for name in (
        "Social Media Today",
        "MarTech",
        "Professional Jeweller",
        "InfoMoney",
        "JCK Online",
        "Swarovski PR Newswire",
    ):
        assert set(get_retrieval_policy(name)) == {"type", "policy"}, name
