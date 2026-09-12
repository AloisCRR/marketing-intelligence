"""Per-source feed retrieval lane tests (oracle ora-1 fallback lane).

Observable behavior (not privates):
- get_retrieval_policy: allowlist validation, defaults, never raises
- curated JSON carries retrieval stanzas for all 20 sources (RSS + no-RSS)
- fetch_rss runs one chain: curl_cffi Chrome impersonation under genuine
  browser headers (primary) → Jina reader → Firecrawl
- every policy value (default, the dead "stdlib-only" alias, unknown) runs the
  same chain: primary curl_cffi impersonation → Jina → Firecrawl, one chained
  RuntimeError naming every failed leg
- fetch_task threads source_name -> retrieval policy; the leaf flow passes it
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fake_transport import FakeTransport
from prefect_harness import no_engine

import marketing_intelligence.flows as flows
import marketing_intelligence.ingest as ingest
from marketing_intelligence.enrich import BROWSER_HEADERS
from marketing_intelligence.ingest import fetch_rss
from marketing_intelligence.normalize import NormalizedDocument
from marketing_intelligence.sources import get_retrieval_policy, get_source

FIXTURES = Path(__file__).parent / "fixtures"
CURATED = (
    Path(__file__).resolve().parents[1]
    / ".scratch"
    / "marketing-intelligence"
    / "curated-sources.json"
)


# --- policy validation -------------------------------------------------------


def test_v1_policies_all_resolve_to_impersonated_feed() -> None:
    for name in ("Social Media Today", "InfoMoney", "MarTech", "Professional Jeweller"):
        assert get_retrieval_policy(name) == {"type": "rss", "policy": "impersonated-feed"}, name


def test_policy_never_raises_and_defaults() -> None:
    default = {"type": "rss", "policy": "impersonated-feed"}
    assert get_retrieval_policy("No Such Source") == default
    assert get_retrieval_policy(None) == default
    # Registry RSS sources resolve to the same single lane.
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
    assert entries["MarTech"]["retrieval"] == {"type": "rss", "policy": "impersonated-feed"}
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
    # RSS extras carry explicit rss stanzas on the single impersonated lane.
    for name in ("JCK Online", "Swarovski PR Newswire"):
        assert entries[name]["retrieval"] == {"type": "rss", "policy": "impersonated-feed"}


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


class _FakeCurlResponse:
    def __init__(self, status_code: int, content: bytes) -> None:
        self.status_code = status_code
        self.content = content


class _FakeCurl:
    """Recording curl_cffi stand-in: one canned response or one canned raise."""

    def __init__(
        self,
        status_code: int = 200,
        content: bytes = b"<rss/>",
        error: Exception | None = None,
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self._status_code = status_code
        self._content = content
        self._error = error

    def get(self, url: str, **kwargs: Any) -> _FakeCurlResponse:
        self.calls.append({"url": url, **kwargs})
        if self._error is not None:
            raise self._error
        return _FakeCurlResponse(self._status_code, self._content)


def _stub_fallbacks(
    monkeypatch: pytest.MonkeyPatch,
    log: list[str],
    *,
    jina: bytes | Exception,
    firecrawl: bytes | Exception,
) -> None:
    """Replace both fallback legs with recording stubs (never any network)."""

    def make(leg: str, outcome: bytes | Exception) -> Any:
        def fake(url: str, *args: Any, **kwargs: Any) -> bytes:
            log.append(leg)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        return fake

    monkeypatch.setattr(ingest, "_fetch_feed_via_jina", make("jina", jina))
    monkeypatch.setattr(ingest, "_fetch_feed_via_firecrawl", make("firecrawl", firecrawl))


# --- primary lane: curl_cffi Chrome impersonation ------------------------------


def test_primary_lane_uses_chrome_impersonation_and_browser_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "http://example.com/feed"
    fake = _FakeCurl(content=b"<rss>clean</rss>")
    monkeypatch.setattr(ingest, "_curl_cffi_requests", fake)
    # Unregistered URL: default chain, primary succeeds on the first try.
    assert fetch_rss(url) == b"<rss>clean</rss>"
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["url"] == url
    assert call["impersonate"] == "chrome"
    headers = call["headers"]
    assert headers["User-Agent"] == BROWSER_HEADERS["User-Agent"]
    assert headers["User-Agent"].startswith("Mozilla/5.0")
    assert headers["Accept"] == ingest._FEED_ACCEPT
    assert headers["Accept-Language"] == BROWSER_HEADERS["Accept-Language"]
    assert call["timeout"] >= ingest.IMPERSONATED_TIMEOUT


def test_primary_lane_registered_source_resolves_to_impersonated_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = get_source("Social Media Today")["rss_url"]
    fake = _FakeCurl(content=b"<rss>via-impersonation</rss>")
    monkeypatch.setattr(ingest, "_curl_cffi_requests", fake)
    assert fetch_rss(url) == b"<rss>via-impersonation</rss>"
    assert fake.calls[0]["impersonate"] == "chrome"
    assert fake.calls[0]["headers"]["Accept"] == ingest._FEED_ACCEPT


def test_primary_http_error_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    url = "http://example.com/feed"
    traffic: list[str] = []
    fake = _FakeCurl(status_code=403, content=b"blocked")
    monkeypatch.setattr(ingest, "_curl_cffi_requests", fake)
    _stub_fallbacks(
        monkeypatch,
        traffic,
        jina=RuntimeError("jina boom"),
        firecrawl=RuntimeError("firecrawl boom"),
    )
    # "stdlib-only" is a dead alias: the primary failure still chains through
    # both fallback legs.
    with pytest.raises(RuntimeError) as excinfo:
        fetch_rss(url, policy="stdlib-only")
    message = str(excinfo.value)
    assert "HTTP Error 403" in message
    assert "jina: jina boom" in message
    assert "firecrawl: firecrawl boom" in message
    assert traffic == ["jina", "firecrawl"]


def test_primary_transport_error_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    url = "http://example.com/feed"
    traffic: list[str] = []
    fake = _FakeCurl(error=RuntimeError("connection reset by peer"))
    monkeypatch.setattr(ingest, "_curl_cffi_requests", fake)
    _stub_fallbacks(
        monkeypatch,
        traffic,
        jina=RuntimeError("jina boom"),
        firecrawl=RuntimeError("firecrawl boom"),
    )
    with pytest.raises(RuntimeError) as excinfo:
        fetch_rss(url, policy="stdlib-only")
    message = str(excinfo.value)
    assert "connection reset by peer" in message
    assert "jina: jina boom" in message
    assert traffic == ["jina", "firecrawl"]


def test_missing_curl_cffi_is_an_explicit_chained_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "http://example.com/feed"
    traffic: list[str] = []
    monkeypatch.setattr(ingest, "_curl_cffi_requests", None)
    _stub_fallbacks(
        monkeypatch,
        traffic,
        jina=RuntimeError("jina boom"),
        firecrawl=RuntimeError("firecrawl boom"),
    )
    # The hard dependency is an explicit primary failure, not a separate mode:
    # the chain still runs Jina then Firecrawl and surfaces every cause.
    with pytest.raises(RuntimeError) as excinfo:
        fetch_rss(url, policy="stdlib-only")
    message = str(excinfo.value)
    assert "curl_cffi unavailable" in message
    assert "jina: jina boom" in message
    assert "firecrawl: firecrawl boom" in message
    assert traffic == ["jina", "firecrawl"]


# --- policy normalization: every value runs the one full chain ----------------


def test_every_policy_value_runs_the_full_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "http://example.com/feed"
    traffic: list[str] = []
    fake = _FakeCurl(status_code=403, content=b"blocked")
    monkeypatch.setattr(ingest, "_curl_cffi_requests", fake)
    _stub_fallbacks(
        monkeypatch,
        traffic,
        jina=RuntimeError("jina boom"),
        firecrawl=RuntimeError("firecrawl boom"),
    )
    # Default (unregistered URL), the dead stdlib-only alias, unknown, and the
    # explicit lane name all normalize to one full chain: primary → Jina →
    # Firecrawl, with the chained message naming every leg.
    for kwargs in (
        {},
        {"policy": "stdlib-only"},
        {"policy": "bogus"},
        {"policy": "impersonated-feed"},
    ):
        with pytest.raises(RuntimeError) as excinfo:
            fetch_rss(url, **kwargs)
        message = str(excinfo.value)
        assert "policy=impersonated-feed" in message
        assert "HTTP Error 403" in message
        assert "jina: jina boom" in message
        assert "firecrawl: firecrawl boom" in message
    assert traffic == ["jina", "firecrawl"] * 4
    assert len(fake.calls) == 4


def test_stdlib_only_alias_matches_impersonated_feed_call_sequence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "http://example.com/feed"
    sequences: dict[str, tuple[int, list[str]]] = {}
    for policy in ("impersonated-feed", "stdlib-only"):
        traffic: list[str] = []
        fake = _FakeCurl(status_code=403, content=b"blocked")
        monkeypatch.setattr(ingest, "_curl_cffi_requests", fake)
        _stub_fallbacks(
            monkeypatch,
            traffic,
            jina=b"<rss>via-jina</rss>",
            firecrawl=b"<rss>via-firecrawl</rss>",
        )
        assert fetch_rss(url, policy=policy) == b"<rss>via-jina</rss>"
        sequences[policy] = (len(fake.calls), list(traffic))
    # The dead alias is not a fetch identity: an identical leg sequence.
    assert sequences["stdlib-only"] == sequences["impersonated-feed"] == (1, ["jina"])


# --- impersonated-feed chain: primary → Jina → Firecrawl -----------------------


def test_chain_primary_success_skips_fallbacks(monkeypatch: pytest.MonkeyPatch) -> None:
    url = "http://example.com/feed"
    traffic: list[str] = []
    monkeypatch.setattr(ingest, "_curl_cffi_requests", _FakeCurl(content=b"<rss>primary</rss>"))
    _stub_fallbacks(monkeypatch, traffic, jina=b"<rss>jina</rss>", firecrawl=b"<rss>fc</rss>")
    assert fetch_rss(url, policy="impersonated-feed") == b"<rss>primary</rss>"
    assert traffic == []


def test_chain_falls_back_to_jina_when_primary_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "http://example.com/feed"
    traffic: list[str] = []
    fake = _FakeCurl(status_code=403, content=b"blocked")
    monkeypatch.setattr(ingest, "_curl_cffi_requests", fake)
    _stub_fallbacks(monkeypatch, traffic, jina=b"<rss>via-jina</rss>", firecrawl=b"<rss>fc</rss>")
    assert fetch_rss(url, policy="impersonated-feed") == b"<rss>via-jina</rss>"
    assert traffic == ["jina"]  # Firecrawl never reached


def test_chain_falls_through_jina_to_firecrawl(monkeypatch: pytest.MonkeyPatch) -> None:
    url = "http://example.com/feed"
    traffic: list[str] = []
    fake = _FakeCurl(error=RuntimeError("primary down"))
    monkeypatch.setattr(ingest, "_curl_cffi_requests", fake)
    _stub_fallbacks(
        monkeypatch,
        traffic,
        jina=RuntimeError("jina boom"),
        firecrawl=b"<rss>via-firecrawl</rss>",
    )
    assert fetch_rss(url, policy="impersonated-feed") == b"<rss>via-firecrawl</rss>"
    assert traffic == ["jina", "firecrawl"]


def test_chain_all_legs_fail_message_chains_every_cause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "http://example.com/feed"
    traffic: list[str] = []
    fake = _FakeCurl(error=RuntimeError("primary down"))
    monkeypatch.setattr(ingest, "_curl_cffi_requests", fake)
    _stub_fallbacks(
        monkeypatch,
        traffic,
        jina=RuntimeError("jina boom"),
        firecrawl=RuntimeError("firecrawl boom"),
    )
    with pytest.raises(RuntimeError) as excinfo:
        fetch_rss(url, policy="impersonated-feed")
    message = str(excinfo.value)
    assert "policy=impersonated-feed" in message
    assert "primary down" in message
    assert "jina: jina boom" in message
    assert "firecrawl: firecrawl boom" in message
    assert traffic == ["jina", "firecrawl"]


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
    from marketing_intelligence.sources import list_sources

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


def test_every_curated_source_reports_impersonated_feed() -> None:
    from marketing_intelligence.sources import list_sources

    for entry in list_sources():
        assert get_retrieval_policy(entry["name"])["policy"] == "impersonated-feed", entry["name"]


def test_no_rss_policies_validate_and_never_raise() -> None:
    assert get_retrieval_policy("Jing Daily")["type"] == "sitemap+hub"
    assert get_retrieval_policy("Jing Daily")["extractor"] == "json-ld-first"
    # Sitemap refs live in curated-sources.json (09 lane owns them): assert
    # shape here, exact URLs in the curated-stanza tests.
    modaes = get_retrieval_policy("Modaes")
    assert modaes["type"] == "sitemap"
    assert modaes["policy"] == "impersonated-feed"
    assert modaes["extractor"] == "generic"
    assert len(modaes["sitemaps"]) >= 1
    assert get_retrieval_policy("National Jeweler")["type"] == "url-set+hub"
    assert get_retrieval_policy("No Such Source") == {"type": "rss", "policy": "impersonated-feed"}
    assert get_retrieval_policy(None) == {"type": "rss", "policy": "impersonated-feed"}


def test_legacy_stdlib_only_alias_normalizes_to_impersonated_feed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import marketing_intelligence.sources as sources

    real_get_source = sources.get_source

    def fake_get_source(name: str) -> dict[str, Any]:
        entry = real_get_source(name)
        if name == "MarTech":
            entry["retrieval"] = {"type": "rss", "policy": "stdlib-only"}
        return entry

    monkeypatch.setattr(sources, "get_source", fake_get_source)
    # Back-compat accepted, never a fetch identity: the alias resolves to the
    # single impersonated lane.
    assert sources.get_retrieval_policy("MarTech") == {
        "type": "rss",
        "policy": "impersonated-feed",
    }


def test_retrieval_config_fills_defaults_for_ticket_08() -> None:
    from marketing_intelligence.sources import get_retrieval_config

    cfg = get_retrieval_config("MarketingDirecto")
    assert cfg == {
        "type": "sitemap",
        "policy": "impersonated-feed",
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
    assert unknown["policy"] == "impersonated-feed"
    assert unknown["extractor"] == "generic"
    assert unknown["sitemaps"] == []
    assert get_retrieval_config(None)["type"] == "rss"


def test_invalid_stanza_values_fall_back_without_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import marketing_intelligence.sources as sources

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
    assert sources.get_retrieval_policy("Jing Daily") == {
        "type": "rss",
        "policy": "impersonated-feed",
    }


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
