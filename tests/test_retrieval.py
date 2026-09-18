"""Per-source feed retrieval lane tests (oracle ora-1 fallback lane).

Observable behavior (not privates):
- get_retrieval_policy: allowlist validation, defaults, never raises
- curated JSON carries retrieval stanzas for all curated sources (RSS + no-RSS +
  the two ADR-0013 Instagram accounts)
- fetch_rss runs one lane: curl_cffi Chrome impersonation under genuine
  browser headers; a failure raises and never touches a Markdown reader or
  scraper (feed URLs are raw XML, not extraction targets)
- every policy value (default, the dead "stdlib-only" alias, unknown) runs the
  same one lane, raising one explicit RuntimeError naming the policy
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

#: Ticket 27: MarTech's curated retrieval stanza. `link_pattern` is the
#: hyphen carried by martech.org's root-level article slugs; the site root
#: ("/") carries no hyphen, so it never matches, while the taxonomy/author/
#: conference families and the corporate pages are dropped by
#: `sitemap_exclude` (honored for hub anchors too). `hub_pages` carries the
#: second listing page: a post that fails and then scrolls off the homepage
#: stays discoverable from page 2 instead of never being retried.
MARTECH_HUB_STANZA: dict[str, Any] = {
    "type": "hub",
    "policy": "impersonated-feed",
    "extractor": "generic",
    "hub": "https://martech.org/",
    "link_pattern": "-",
    "hub_pages": ["https://martech.org/page/2/"],
    "sitemap_exclude": [
        "/topic/",
        "/author/",
        "/page/",
        "/conference/",
        "/about-martech-org/",
        "/intelligence-reports/",
        "/white-papers/",
        "/martech-topics/",
        "/martechbot",
        "/privacy-policy/",
        "/terms-of-service/",
        "/working-with-martech-content-team/",
        "/the-latest-ai-powered-martech-news-and-releases/",
    ],
    "pacing_ms": 1000,
    "max_urls": 50,
}

#: Ticket 29: MarketingDirecto's curated stanza. Live evidence (2026-09-12):
#: every marketingdirecto.com sitemap URL *and* the homepage itself answer 403
#: to the impersonated leg, while the shared reader leg serves both — so the
#: lane is hub-only discovery (no sitemaps) over the reader-tolerant fetch.
#: `link_pattern` "-" keeps the hyphenated Spanish article slugs. MD's section
#: tokens are hyphenated too, so section fronts match as well; the
#: ``^…$``-anchored excludes then drop exactly those listings
#: (`^/digital-general/social-media-marketing$` drops the front while its
#: `/…/<slug>` articles stay), which no substring pattern can express.
MARKETINGDIRECTO_HUB_STANZA: dict[str, Any] = {
    "type": "hub",
    "policy": "impersonated-feed",
    "extractor": "generic",
    "hub": "https://www.marketingdirecto.com/",
    "link_pattern": "-",
    "sitemap_exclude": [
        "/wp-content/",
        "/temas/",
        "^/media-kit$",
        "^/diccionario-marketing-publicidad-comunicacion-nuevas-tecnologias$",
        "^/quienes-somos$",
        "^/aviso-legal$",
        "^/politica-de-privacidad$",
        "^/politica-cookies$",
        "^/punto-de-vista$",
        "^/arena-media$",
        "^/next-level$",
        "^/digital-busines-innovation-sidn-digital-thinking$",
        "^/guias-especiales$",
        "^/marketing-general$",
        "^/marketing-general/agencias$",
        "^/marketing-general/entrevistas$",
        "^/marketing-general/gente$",
        "^/marketing-general/eventos-y-formacion$",
        "^/marketing-general/digital-innovation-trends-by-t2o$",
        "^/anunciantes-general$",
        "^/anunciantes-general/publicaciones$",
        "^/anunciantes-general/medios$",
        "^/creacion/campanas-de-marketing$",
        "^/digital-general$",
        "^/digital-general/digital$",
        "^/digital-general/e-commerce$",
        "^/digital-general/e-mail-marketing$",
        "^/digital-general/social-media-marketing$",
        "^/digital-general/medicion-sin-filtros-gfk$",
        "^/digital-general/mobile-marketing$",
        "^/digital-general/all-about-audio-by-audioemotion$",
        "^/especiales/reportajes-a-fondo$",
        "^/especiales/cannes-lions$",
        "^/especiales/ipg-mediabrands$",
        "^/especiales/the-future-of-advertising-especiales$",
        "^/especiales/enamorando-al-consumidor$",
        "^/imprescindibles/social-media$",
        "^/imprescindibles/historia-marcas$",
        "^/imprescindibles/inteligencia-artificial$",
    ],
    "pacing_ms": 10000,
    "max_urls": 50,
}

#: Ticket 28: Swarovski PR Newswire's curated stanza. The `/rss/swarovski`
#: feed is a dead 404; the route is hub discovery over the reachable news hub,
#: with `link_pattern` ".html" selecting the release slugs. The dead `rss_url`
#: stays in the registry as provenance only and never selects the lane.
SWAROVSKI_HUB_STANZA: dict[str, Any] = {
    "type": "hub",
    "policy": "impersonated-feed",
    "extractor": "generic",
    "hub": "https://www.prnewswire.com/news/swarovski/",
    "link_pattern": ".html",
    "pacing_ms": 1000,
    "max_urls": 50,
}


# --- policy validation -------------------------------------------------------


def test_curated_rss_policies_resolve_to_impersonated_feed() -> None:
    # MarTech is the curated exception (hub lane, ticket 27): its policy is
    # asserted with the hub stanza below.
    for name in ("Social Media Today", "InfoMoney", "Professional Jeweller"):
        assert get_retrieval_policy(name) == {"type": "rss", "policy": "impersonated-feed"}, name


def test_policy_never_raises_and_defaults() -> None:
    default = {"type": "rss", "policy": "impersonated-feed"}
    assert get_retrieval_policy("No Such Source") == default
    assert get_retrieval_policy(None) == default
    # Registry RSS sources resolve to the same single lane.
    assert get_retrieval_policy("JCK Online") == default
    # Ticket 28: Swarovski leaves the RSS default — its curated stanza is hub.
    assert get_retrieval_policy("Swarovski PR Newswire") == SWAROVSKI_HUB_STANZA


def test_policy_result_is_a_copy() -> None:
    first = get_retrieval_policy("Social Media Today")
    first["policy"] = "mutated"
    assert get_retrieval_policy("Social Media Today")["policy"] == "impersonated-feed"


# --- curated JSON stanzas ------------------------------------------------------


def test_curated_stanzas() -> None:
    entries = {e["source_name"]: e for e in json.loads(CURATED.read_text(encoding="utf-8"))}
    assert entries["Social Media Today"]["retrieval"] == {
        "type": "rss",
        "policy": "impersonated-feed",
    }
    assert entries["InfoMoney"]["retrieval"] == {"type": "rss", "policy": "impersonated-feed"}
    # Ticket 27: the curated MarTech lane is hub discovery on its live
    # homepage (the /feed/ URL is a dead 403); the dead rss_url stays in the
    # registry for provenance and no longer selects the lane.
    assert entries["MarTech"]["retrieval"] == MARTECH_HUB_STANZA
    assert entries["MarTech"]["rss_url"] == "https://martech.org/feed/"
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
    # RSS extras carry explicit rss stanzas on the single impersonated lane;
    # ticket 28 routed Swarovski onto its own curated hub stanza instead.
    assert entries["JCK Online"]["retrieval"] == {"type": "rss", "policy": "impersonated-feed"}
    assert entries["Swarovski PR Newswire"]["retrieval"] == SWAROVSKI_HUB_STANZA


def test_curated_no_rss_stanzas_declare_route_and_extractor() -> None:
    """Ticket 07: every no-RSS source declares a discovery route + extractor."""
    from marketing_intelligence.sources import catalog_names

    entries = {e["source_name"]: e for e in json.loads(CURATED.read_text(encoding="utf-8"))}
    # 20 feed sources + the two ADR-0013 Instagram accounts (their own stanzas
    # are covered by tests/test_instagram_registry.py).
    assert len(entries) == len(catalog_names())
    expected_types = {
        "National Jeweler": "url-set+hub",
        "Exame": "sitemap",
        "Modaes": "sitemap",
        "Retail Dive": "sitemap+hub",
        "Jing Daily": "sitemap+hub",
        "Consumidor Moderno": "sitemap",
        "Meio & Mensagem": "sitemap",
        "Marketing Dive": "sitemap+hub",
        "MarketingDirecto": "hub",
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
    # Ticket 29: MarketingDirecto left the sitemap lane for the hub lane (its
    # sitemaps and homepage are 403 impersonated); the curated stanza pins it.
    assert entries["MarketingDirecto"]["retrieval"] == MARKETINGDIRECTO_HUB_STANZA
    assert entries["MarketingDirecto"]["rss_url"] is None
    assert "sitemaps" not in entries["MarketingDirecto"]["retrieval"]
    # Only Jing Daily needs JSON-LD-first extraction.
    assert entries["Jing Daily"]["retrieval"]["extractor"] == "json-ld-first"
    # Dive news sitemaps keep the proven impersonated retry lane.
    assert entries["Retail Dive"]["retrieval"]["policy"] == "impersonated-feed"
    assert entries["Marketing Dive"]["retrieval"]["policy"] == "impersonated-feed"
    # Hub-anchor routes declare their link patterns.
    assert entries["Jing Daily"]["retrieval"]["link_pattern"] == "/posts/"
    assert entries["Retail Dive"]["retrieval"]["link_pattern"] == "/news/"
    assert entries["National Jeweler"]["retrieval"]["link_pattern"] == "/articles/"
    # MarketingDirecto (ticket 29) matches hyphenated Spanish article slugs.
    assert entries["MarketingDirecto"]["retrieval"]["link_pattern"] == "-"


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


def _forbid_reader_scraper(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail the test if a feed fetch reaches a Markdown reader or scraper.

    Feed URLs carry raw XML; the reader/scraper chain belongs to the article
    lane only. Anything here runs on failure paths too — proving the feed
    lane performs zero reader/scraper calls under any failure.
    """
    from marketing_intelligence import enrich, firecrawl

    def _forbidden(leg: str) -> Any:
        def fake(url: str, *args: Any, **kwargs: Any) -> Any:
            raise AssertionError(f"feed URL routed to {leg}: {url}")

        return fake

    monkeypatch.setattr(enrich, "try_fallback_reader", _forbidden("jina reader"))
    monkeypatch.setattr(enrich, "fetch_and_clean", _forbidden("primary article fetch"))
    monkeypatch.setattr(firecrawl, "fetch_via_firecrawl", _forbidden("firecrawl scrape"))
    # The reader/scraper feed legs are gone, not merely unused.
    assert not hasattr(ingest, "_fetch_feed_via_jina")
    assert not hasattr(ingest, "_fetch_feed_via_firecrawl")
    assert not hasattr(ingest, "FEED_FALLBACK_TIMEOUT")


# --- primary lane: curl_cffi Chrome impersonation ------------------------------


def test_primary_lane_uses_chrome_impersonation_and_browser_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "http://example.com/feed"
    fake = _FakeCurl(content=b"<rss>clean</rss>")
    monkeypatch.setattr(ingest, "_curl_cffi_requests", fake)
    # Unregistered URL: default lane, impersonated fetch succeeds immediately.
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


def test_primary_lane_registered_source_resolves_to_impersonated_lane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = get_source("Social Media Today")["rss_url"]
    fake = _FakeCurl(content=b"<rss>via-impersonation</rss>")
    monkeypatch.setattr(ingest, "_curl_cffi_requests", fake)
    _forbid_reader_scraper(monkeypatch)
    assert fetch_rss(url) == b"<rss>via-impersonation</rss>"
    assert fake.calls[0]["impersonate"] == "chrome"
    assert fake.calls[0]["headers"]["Accept"] == ingest._FEED_ACCEPT


def test_primary_http_error_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    url = "http://example.com/feed"
    fake = _FakeCurl(status_code=403, content=b"blocked")
    monkeypatch.setattr(ingest, "_curl_cffi_requests", fake)
    _forbid_reader_scraper(monkeypatch)
    # "stdlib-only" is a dead alias: the failure is the impersonated lane's,
    # raised immediately — nothing is routed to a reader or scraper.
    with pytest.raises(RuntimeError) as excinfo:
        fetch_rss(url, policy="stdlib-only")
    message = str(excinfo.value)
    assert "HTTP Error 403" in message
    assert "policy=impersonated-feed" in message
    assert len(fake.calls) == 1


def test_primary_transport_error_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    url = "http://example.com/feed"
    fake = _FakeCurl(error=RuntimeError("connection reset by peer"))
    monkeypatch.setattr(ingest, "_curl_cffi_requests", fake)
    _forbid_reader_scraper(monkeypatch)
    with pytest.raises(RuntimeError) as excinfo:
        fetch_rss(url, policy="stdlib-only")
    message = str(excinfo.value)
    assert "connection reset by peer" in message
    assert "policy=impersonated-feed" in message


def test_missing_curl_cffi_is_an_explicit_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "http://example.com/feed"
    monkeypatch.setattr(ingest, "_curl_cffi_requests", None)
    _forbid_reader_scraper(monkeypatch)
    # The hard dependency is an explicit failure, not a separate mode and not
    # a reason to hand the feed URL to a reader.
    with pytest.raises(RuntimeError) as excinfo:
        fetch_rss(url, policy="stdlib-only")
    message = str(excinfo.value)
    assert "curl_cffi unavailable" in message
    assert "policy=impersonated-feed" in message


# --- policy normalization: every value runs the one impersonated lane ----------


def test_every_policy_value_uses_the_one_impersonated_lane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "http://example.com/feed"
    fake = _FakeCurl(status_code=403, content=b"blocked")
    monkeypatch.setattr(ingest, "_curl_cffi_requests", fake)
    _forbid_reader_scraper(monkeypatch)
    # Default (unregistered URL), the dead stdlib-only alias, unknown, and the
    # explicit lane name all normalize to the single impersonated fetch, whose
    # failure names the policy.
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
    assert len(fake.calls) == 4


def test_stdlib_only_alias_matches_impersonated_feed_call_sequence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "http://example.com/feed"
    sequences: dict[str, int] = {}
    for policy in ("impersonated-feed", "stdlib-only"):
        fake = _FakeCurl(content=b"<rss>via-impersonation</rss>")
        monkeypatch.setattr(ingest, "_curl_cffi_requests", fake)
        _forbid_reader_scraper(monkeypatch)
        assert fetch_rss(url, policy=policy) == b"<rss>via-impersonation</rss>"
        sequences[policy] = len(fake.calls)
    # The dead alias is not a fetch identity: an identical single-lane call.
    assert sequences["stdlib-only"] == sequences["impersonated-feed"] == 1


# --- one lane: raw bytes only; candidate URLs cover dead feeds ---------------

_EMPTY_FEED = b'<?xml version="1.0"?><rss version="2.0"><channel><title>JCK</title></channel></rss>'


def test_impersonated_lane_success_never_touches_reader_or_scraper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "http://example.com/feed"
    monkeypatch.setattr(ingest, "_curl_cffi_requests", _FakeCurl(content=b"<rss>primary</rss>"))
    _forbid_reader_scraper(monkeypatch)
    assert fetch_rss(url, policy="impersonated-feed") == b"<rss>primary</rss>"


def test_feed_failure_never_touches_reader_or_scraper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every failure mode of the one lane raises — no reader/scraper traffic."""
    url = "http://example.com/feed"
    for fake in (
        _FakeCurl(status_code=403, content=b"blocked"),  # HTTP error
        _FakeCurl(error=RuntimeError("primary down")),  # transport error
        None,  # hard dependency absent
    ):
        monkeypatch.setattr(ingest, "_curl_cffi_requests", fake)
        _forbid_reader_scraper(monkeypatch)
        with pytest.raises(RuntimeError):
            fetch_rss(url, policy="impersonated-feed")


def test_dead_primary_feed_recovers_via_next_candidate(
    monkeypatch: pytest.MonkeyPatch, no_engine: None
) -> None:
    """An empty/dead primary feed falls through to the next candidate URL."""
    primary = get_source("JCK Online")["rss_url"]
    candidates = ingest.feed_candidate_urls(primary)
    assert len(candidates) > 1  # the code-level correction is exercised
    seen: list[str] = []

    def fake_fetch_task(url: str, source_name: str | None = None) -> bytes:
        seen.append(url)
        if url == primary:
            return _EMPTY_FEED  # 0 entries → EmptyFeedError → next candidate
        return (FIXTURES / "martech_sample.xml").read_bytes()

    monkeypatch.setattr(flows, "fetch_task", fake_fetch_task)
    monkeypatch.setattr(flows, "enrich_document_or_keep", lambda doc, *a, **k: (doc, "rss", None))
    monkeypatch.setattr(flows, "upsert_documents", lambda docs: (len(docs), 0))
    result = flows.ingest_source_flow(source_name="JCK Online")
    assert seen == list(candidates)  # primary first, then the correction
    assert result == {"inserted": 3, "skipped": 0}


def test_exhausted_candidate_list_raises_explicit_empty_feed_error(
    monkeypatch: pytest.MonkeyPatch, no_engine: None
) -> None:
    """No candidate yields entries: the last failure is the explicit error."""
    seen: list[str] = []

    def empty_fetch(url: str, source_name: str | None = None) -> bytes:
        seen.append(url)
        return _EMPTY_FEED

    monkeypatch.setattr(flows, "fetch_task", empty_fetch)
    with pytest.raises(ingest.EmptyFeedError) as excinfo:
        flows.ingest_source_flow(source_name="JCK Online")
    assert "0 entries" in str(excinfo.value)
    assert len(seen) == len(ingest.feed_candidate_urls(get_source("JCK Online")["rss_url"]))


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
        return (FIXTURES / "infomoney_sample.xml").read_bytes()

    monkeypatch.setattr(flows, "fetch_task", fake_fetch_task)
    monkeypatch.setattr(flows, "enrich_document_or_keep", lambda doc, *a, **k: (doc, "rss", None))
    monkeypatch.setattr(flows, "upsert_documents", lambda docs: (len(docs), 0))
    result = flows.ingest_source_flow(source_name="InfoMoney")
    assert result == {"inserted": 3, "skipped": 0}
    assert captured["source_name"] == "InfoMoney"
    assert captured["url"] == get_source("InfoMoney")["rss_url"]


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


def test_batch_keeps_shape_across_rss_sources(
    monkeypatch: pytest.MonkeyPatch, no_engine: None
) -> None:
    by_url = {get_source(n)["rss_url"]: n for n in ("Professional Jeweller", "InfoMoney")}
    fixtures = {
        "Professional Jeweller": (FIXTURES / "pj_sample.xml").read_bytes(),
        "InfoMoney": (FIXTURES / "infomoney_sample.xml").read_bytes(),
    }
    transport = FakeTransport({rss_url: fixtures[name] for rss_url, name in by_url.items()})
    monkeypatch.setattr(flows, "fetch_rss", transport.fetch_rss)
    monkeypatch.setattr(flows, "enrich_document_or_keep", lambda doc, *a, **k: (doc, "rss", None))

    def fake_upsert(docs: list[NormalizedDocument]) -> tuple[int, int]:
        return (len(docs), 0)

    monkeypatch.setattr(flows, "upsert_documents", fake_upsert)
    results = flows.ingest_sources_flow(source_names=["Professional Jeweller", "InfoMoney"])
    assert results["Professional Jeweller"] == {"inserted": 3, "skipped": 0}
    assert results["InfoMoney"] == {"inserted": 3, "skipped": 0}


# --- ticket 07: retrieval config seam (registry + fallbacks, no RSS change) ---


def test_registry_loads_all_curated_sources() -> None:
    from marketing_intelligence.sources import catalog_names, list_sources

    names = [e["name"] for e in list_sources()]
    assert len(names) == len(catalog_names())
    assert len(set(names)) == len(catalog_names())


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


def test_every_curated_source_reports_a_registered_policy() -> None:
    from marketing_intelligence.sources import RETRIEVAL_POLICIES, list_sources

    for entry in list_sources():
        policy = get_retrieval_policy(entry["name"])
        assert policy["policy"] in RETRIEVAL_POLICIES, entry["name"]
        if entry["name"] in ("ig:sabrikolod", "ig:jordisanildefonso"):
            # ADR-0013 exception: the Instagram accounts run the premium
            # Apify lane, never the impersonated feed chain.
            assert policy["type"] == "instagram", entry["name"]
            assert policy["policy"] == "apify-premium", entry["name"]
        else:
            assert policy["policy"] == "impersonated-feed", entry["name"]


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


def test_marketingdirecto_retrieval_config_is_the_hub_lane() -> None:
    """Ticket 29: MarketingDirecto's normalized stanza is hub discovery through
    the shared reader-tolerant fetch, with no sitemap leg (its sitemaps are 403
    impersonated, so the impersonated-only sitemap lane can never run)."""
    from marketing_intelligence.sources import get_retrieval_config

    cfg = get_retrieval_config("MarketingDirecto")
    assert cfg == {
        **MARKETINGDIRECTO_HUB_STANZA,
        "sitemaps": [],
        "hub_pages": [],
        "sitemap_pattern": None,
        "id_guard": False,
    }
    assert cfg["type"] == "hub"
    assert cfg["sitemaps"] == []  # nothing rides the impersonated-only sitemap leg
    assert cfg["hub"] == "https://www.marketingdirecto.com/"  # hub_url provenance
    assert cfg["policy"] == "impersonated-feed"
    assert get_retrieval_policy("MarketingDirecto")["policy"] == "impersonated-feed"
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
        "Professional Jeweller",
        "InfoMoney",
        "JCK Online",
    ):
        assert set(get_retrieval_policy(name)) == {"type", "policy"}, name
    # Ticket 28: Swarovski is no longer an RSS lane — its curated hub stanza
    # carries exactly the extras it declares, and nothing beyond them.
    swarovski = get_retrieval_policy("Swarovski PR Newswire")
    assert swarovski["type"] == "hub"
    assert swarovski["policy"] == "impersonated-feed"
    assert set(swarovski) == set(SWAROVSKI_HUB_STANZA)


def test_martech_retrieval_config_is_the_hub_lane() -> None:
    """Ticket 27: MarTech's normalized stanza is hub discovery through the
    shared policy chain, with no sitemap leg and no feed lane."""
    from marketing_intelligence.sources import get_retrieval_config

    cfg = get_retrieval_config("MarTech")
    assert cfg == {
        **MARTECH_HUB_STANZA,
        "sitemaps": [],
        "sitemap_pattern": None,
        "id_guard": False,
    }
    assert cfg["type"] == "hub"
    assert cfg["sitemaps"] == []  # nothing rides the impersonated-only sitemap leg
    # The widened discovery window: exactly one extra listing page, so a URL
    # that scrolls off the homepage is still planned on a later run.
    assert cfg["hub_pages"] == ["https://martech.org/page/2/"]
    assert cfg["policy"] == "impersonated-feed"
    assert get_retrieval_policy("MarTech")["policy"] == "impersonated-feed"


def test_martech_hub_lane_keeps_dead_feed_as_provenance_only() -> None:
    """The dead /feed/ URL stays registry provenance only: the effective
    stanza type selects the lane, and the hub config declares no sitemaps."""
    from marketing_intelligence.sources import get_retrieval_config

    assert get_source("MarTech")["rss_url"] == "https://martech.org/feed/"
    cfg = get_retrieval_config("MarTech")
    assert cfg["type"] == "hub"
    assert cfg["sitemaps"] == []
    assert cfg["hub"] == "https://martech.org/"
