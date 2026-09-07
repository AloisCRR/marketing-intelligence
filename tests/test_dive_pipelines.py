"""Dive sites pipelines (Ticket 11): Retail Dive + Marketing Dive.

Observable behavior (not privates), reusing the proven machinery:
- hub-anchor fallback (12): hub listings yield /news/ URLs, noise filtered
- sitemap index + news sitemap with publication_date (08): extend coverage
- impersonated retry via policy_get: stdlib-first everywhere, retry only
  where the plain fetch is refused (live-verified 2026-09-06: plain 403 on
  every Dive route, impersonated 200 with full bodies)
- generic extraction, substantive bodies (500-char threshold governs
  keep-vs-flag; no structured-data dependency, no bypass)
- per-source rerun is a no-op; one bad URL / one dead Source never aborts

All network is fixture-backed; no live HTTP in tests.

Queued registry correction (lane 10 owns curated-sources.json — NOT edited
here): both Dive stanzas must append the robots-declared news sitemap
  Retail Dive:    sitemaps [..., "https://www.retaildive.com/google_news_sitemap.xml"]
  Marketing Dive: sitemaps [..., "https://www.marketingdive.com/google_news_sitemap.xml"]
Harvest tests apply that correction at the config level so coverage from the
news route is proven before lane 10 lands it.
"""

from __future__ import annotations

import io
import urllib.error
from collections.abc import Mapping
from email.message import Message
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest

import brain.discovery as discovery
from brain.discovery import (
    ArticleFetchError,
    discover_hub_urls,
    extract_hub_links,
    harvest_sitemap_source,
    parse_sitemap,
    policy_get,
)
from brain.normalize import NormalizedDocument
from brain.sources import get_retrieval_config, get_source

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


SOURCES: dict[str, dict[str, Any]] = {
    "Retail Dive": {
        "prefix": "rd",
        "host": "www.retaildive.com",
        "hub": "https://www.retaildive.com/topic/consumer-trends/",
        "articles": {
            "A": {
                "url": "https://www.retaildive.com/news/lululemon-pulls-back-store-plans-sales-plummet-q2/829660/",
                "title": "Lululemon pulls back store plans as sales plummet",
                "author": "Retail Dive Staff",
                "meta": '<meta property="article:published_time" content="2026-09-05T14:20:00+00:00">',
                "published": "2026-09-05T14:20:00+00:00",
                "lead": "Lululemon is pulling back on new store openings after a sharp drop in quarterly sales forced a rethink of its growth targets.",
                "extra": "The athletic brand now expects flat comparable sales for the full year.",
            },
            "C": {
                "url": "https://www.retaildive.com/news/ulta-flock-technology-backlash/829623/",
                "title": "Ulta's use of Flock technology causes upset online",
                "author": "Retail Dive Staff",
                "meta": '<meta property="article:published_time" content="2026-09-02T09:00:00+00:00">',
                "published": "2026-09-02T09:00:00+00:00",
                "lead": "Ulta Beauty faces online backlash over its reported use of Flock security cameras, reigniting debate over in-store surveillance.",
                "extra": "Privacy advocates urge retailers to disclose camera networks at the entrance.",
            },
            "N": {
                "url": "https://www.retaildive.com/news/reebok-hilary-duff-saks-fifth-avenue-neiman-marcus-campaigns-walmart-rotisserie-chicken-purse/829620/",
                "title": "The Weekly Closeout: Reebok partners with Hilary Duff and Saks launches fall campaign",
                "author": "Retail Dive Staff",
                "meta": "",
                "published": "2026-09-06T00:00:00+00:00",
                "lead": "Reebok teams with Hilary Duff while Saks Fifth Avenue rolls out its fall campaign in a busy week for retail marketing.",
                "extra": "The roundup also tracks Walmart's viral rotisserie-chicken purse moment.",
            },
            "H": {
                "url": "https://www.retaildive.com/news/target-adds-marketplace-sellers-holiday-assortment/829600/",
                "title": "Target adds marketplace sellers to bulk up holiday assortment",
                "author": "Retail Dive Staff",
                "meta": '<meta property="article:published_time" content="2026-09-03T16:45:00+00:00">',
                "published": "2026-09-03T16:45:00+00:00",
                "lead": "Target is recruiting third-party sellers to widen its holiday assortment without taking on extra inventory risk.",
                "extra": "The marketplace push focuses on toys, home goods and seasonal decor.",
            },
        },
    },
    "Marketing Dive": {
        "prefix": "mdiv",
        "host": "www.marketingdive.com",
        "hub": "https://www.marketingdive.com/",
        "articles": {
            "A": {
                "url": "https://www.marketingdive.com/news/pepsico-hands-global-media-to-publicis-amid-transformation-at-cpg-giant/829556/",
                "title": "PepsiCo hands global media to Publicis amid transformation at CPG giant",
                "author": "Marketing Dive Staff",
                "meta": '<meta property="article:published_time" content="2026-09-05T14:20:00+00:00">',
                "published": "2026-09-05T14:20:00+00:00",
                "lead": "PepsiCo consolidated its global media account with Publicis as the food and beverage giant sharpens its data-driven marketing.",
                "extra": "The shift covers planning and buying across more than 100 markets.",
            },
            "C": {
                "url": "https://www.marketingdive.com/news/coca-cola-holiday-campaign-creators-personalized-cans/829530/",
                "title": "Coca-Cola leans on creators and personalized cans for holiday campaign",
                "author": "Marketing Dive Staff",
                "meta": '<meta property="article:published_time" content="2026-09-02T09:00:00+00:00">',
                "published": "2026-09-02T09:00:00+00:00",
                "lead": "Coca-Cola pairs creator partnerships with personalized packaging for its festive season push in North America.",
                "extra": "Early social sentiment skews positive on nostalgia-led creative.",
            },
            "N": {
                "url": "https://www.marketingdive.com/news/nike-splits-global-creative-between-wieden-and-independent-shops/829521/",
                "title": "Nike splits global creative between Wieden and independent shops",
                "author": "Marketing Dive Staff",
                "meta": "",
                "published": "2026-09-06T00:00:00+00:00",
                "lead": "Nike is dividing global creative duties between its longtime agency partner and a roster of independent shops.",
                "extra": "The model aims for faster local work alongside global brand platforms.",
            },
            "H": {
                "url": "https://www.marketingdive.com/news/unilever-retail-media-upfront-beauty-brands-measurement/829500/",
                "title": "Unilever joins retail media upfront with measurement pledge for beauty brands",
                "author": "Marketing Dive Staff",
                "meta": '<meta property="article:published_time" content="2026-09-03T16:45:00+00:00">',
                "published": "2026-09-03T16:45:00+00:00",
                "lead": "Unilever committed upfront dollars to retail media networks, demanding clearer measurement for its beauty portfolio.",
                "extra": "The deal ties spend to incrementality tests across three retailers.",
            },
        },
    },
}

LABELS = list(SOURCES)
URL_BAD_SLUG = "pagina-que-falla-siempre/000000/"


def _routes(label: str) -> dict[str, str]:
    host = SOURCES[label]["host"]
    return {
        "index": f"https://{host}/sitemap.xml",
        "footer": f"https://{host}/sitemap-footer.xml",
        "topics": f"https://{host}/sitemap-topics.xml",
        "sept": f"https://{host}/news/archive/2026/september.xml",
        "aug": f"https://{host}/news/archive/2026/august.xml",
        "news": f"https://{host}/google_news_sitemap.xml",
        "bad": f"https://{host}/news/{URL_BAD_SLUG}",
    }


def _article_html(label: str, key: str) -> bytes:
    art = SOURCES[label]["articles"][key]
    html = _fixture("dive_article_shell.html").decode("utf-8")
    for token, value in [
        ("%%URL%%", art["url"]),
        ("%%TITLE%%", art["title"]),
        ("%%AUTHOR%%", art["author"]),
        ("%%DATE_META%%", art["meta"]),
        ("%%LEAD%%", art["lead"]),
        ("%%EXTRA%%", art["extra"]),
        ("%%LABEL%%", label),
    ]:
        html = html.replace(token, value)
    assert "%%" not in html
    return html.encode("utf-8")


def _fetch_map(label: str) -> dict[str, tuple[str, bytes]]:
    spec = SOURCES[label]
    prefix = spec["prefix"]
    host = spec["host"]
    routes = _routes(label)
    mapping: dict[str, tuple[str, bytes]] = {
        f"https://{host}/robots.txt": (
            f"https://{host}/robots.txt",
            _fixture(f"{prefix}_robots.txt"),
        ),
        routes["index"]: (routes["index"], _fixture(f"{prefix}_sitemap_index.xml")),
        routes["sept"]: (routes["sept"], _fixture(f"{prefix}_archive_sept.xml")),
        routes["aug"]: (routes["aug"], _fixture(f"{prefix}_archive_aug.xml")),
        routes["footer"]: (routes["footer"], _fixture("dive_empty_urlset.xml")),
        routes["topics"]: (routes["topics"], _fixture("dive_empty_urlset.xml")),
        routes["news"]: (routes["news"], _fixture(f"{prefix}_news_sitemap.xml")),
        spec["hub"]: (spec["hub"], _fixture(f"{prefix}_hub.html")),
    }
    for key in ("A", "C", "N", "H"):
        url = spec["articles"][key]["url"]
        mapping[url] = (url, _article_html(label, key))
    return mapping


def _make_fetch(
    mapping: dict[str, tuple[str, bytes]],
    log: list[str] | None = None,
    failures: Mapping[str, Exception] | None = None,
) -> Any:
    def fake_fetch(url: str) -> tuple[str, bytes]:
        if log is not None:
            log.append(url)
        if failures and url in failures:
            raise failures[url]
        return mapping[url]

    return fake_fetch


def _dive_config(label: str) -> dict[str, Any]:
    """Registry stanza plus the queued news-sitemap correction (lane 10)."""
    config = dict(get_retrieval_config(label))
    declared = [str(u) for u in (config.get("sitemaps") or [])]
    config["sitemaps"] = list(dict.fromkeys([*declared, _routes(label)["news"]]))
    return config


def _harvest(label: str, **overrides: Any) -> Any:
    routes = _routes(label)
    fetch = _make_fetch(
        _fetch_map(label),
        failures={routes["bad"]: ArticleFetchError(routes["bad"], "HTTP Error 403: Forbidden")},
    )
    config = _dive_config(label)
    config.update(overrides)
    return harvest_sitemap_source(
        config,
        label,
        str(get_source(label)["language"]),
        fetch=fetch,
        sleep=lambda _: None,
    )


# --- registry (read-only; lane 10 owns the JSON) -----------------------------


@pytest.mark.parametrize("label", LABELS)
def test_registry_dive_stanza_pins_verified_routes(label: str) -> None:
    spec = SOURCES[label]
    routes = _routes(label)
    assert get_source(label)["language"] == "en"
    config = get_retrieval_config(label)
    assert config["type"] == "sitemap+hub"
    assert config["policy"] == "impersonated-feed"
    assert config["extractor"] == "generic"
    assert config["sitemaps"][0] == routes["index"]
    assert config["hub"] == spec["hub"]
    assert config["link_pattern"] == "/news/"


# --- hub-anchor discovery ----------------------------------------------------


@pytest.mark.parametrize("label", LABELS)
def test_hub_listings_yield_news_urls_and_filter_noise(label: str) -> None:
    spec = SOURCES[label]
    links = extract_hub_links(
        _fixture(f"{spec['prefix']}_hub.html").decode("utf-8"),
        spec["hub"],
        "/news/",
    )
    arts = spec["articles"]
    assert links == [arts["A"]["url"], arts["N"]["url"], arts["H"]["url"]]


def test_discover_hub_urls_records_bad_page_without_aborting() -> None:
    fetch = _make_fetch(_fetch_map("Retail Dive"))
    hub = SOURCES["Retail Dive"]["hub"]
    dead = "https://www.retaildive.com/topic/does-not-exist/"

    def failing(url: str) -> bytes:
        if url == dead:
            raise ArticleFetchError(url, "HTTP Error 404")
        return fetch(url)[1]

    urls, errors = discover_hub_urls([hub, dead], fetch_body=failing, link_pattern="/news/")
    assert [u.loc for u in urls] == [
        SOURCES["Retail Dive"]["articles"][k]["url"] for k in ("A", "N", "H")
    ]
    assert len(errors) == 1 and dead in errors[0]


# --- sitemap + news-sitemap parsing ------------------------------------------


@pytest.mark.parametrize("label", LABELS)
def test_sitemap_index_lists_archive_children(label: str) -> None:
    spec = SOURCES[label]
    routes = _routes(label)
    urls, children = parse_sitemap(_fixture(f"{spec['prefix']}_sitemap_index.xml"))
    assert urls == []
    assert [c.loc for c in children] == [
        routes["footer"],
        routes["topics"],
        routes["sept"],
        routes["aug"],
    ]


@pytest.mark.parametrize("label", LABELS)
def test_news_sitemap_yields_publication_dates(label: str) -> None:
    spec = SOURCES[label]
    urls, children = parse_sitemap(_fixture(f"{spec['prefix']}_news_sitemap.xml"))
    assert children == []
    assert [u.loc for u in urls] == [spec["articles"]["A"]["url"], spec["articles"]["N"]["url"]]
    assert urls[0].lastmod is not None
    assert urls[0].lastmod.isoformat() == "2026-09-05T00:00:00+00:00"
    assert urls[1].lastmod is not None
    assert urls[1].lastmod.isoformat() == "2026-09-06T00:00:00+00:00"


# --- harvest -----------------------------------------------------------------


@pytest.mark.parametrize("label", LABELS)
def test_harvest_sitemap_first_news_and_hub_extend(label: str) -> None:
    spec = SOURCES[label]
    routes = _routes(label)
    arts = spec["articles"]
    report = _harvest(label)
    # News-only N first (newest), then archive A/C, then hub-only H (undated);
    # the archive BAD url is an explicit skip.
    assert [d.url for d in report.documents] == [
        arts["N"]["url"],
        arts["A"]["url"],
        arts["C"]["url"],
        arts["H"]["url"],
    ]
    assert {d.language for d in report.documents} == {"en"}
    by_url = {d.url: d for d in report.documents}
    for key in ("N", "A", "C", "H"):
        doc = by_url[arts[key]["url"]]
        assert doc.source == label
        assert doc.title == arts[key]["title"]
        assert doc.author == arts[key]["author"]
        assert doc.canonical_url == arts[key]["url"]
        assert doc.published_at.isoformat() == arts[key]["published"]
        assert doc.published_at.tzinfo is not None
        assert doc.retrieved_at.tzinfo is not None
        assert doc.content_hash
        assert len(doc.content) >= 500
        assert "<" not in doc.content
    # N carries no date in its HTML: the news publication_date wins.
    assert by_url[arts["N"]["url"]].published_at.isoformat() == "2026-09-06T00:00:00+00:00"
    assert report.skipped == 1
    assert len(report.causes) == 1 and routes["bad"] in report.causes[0]


@pytest.mark.parametrize("label", LABELS)
def test_harvest_bounded_backfill(label: str) -> None:
    spec = SOURCES[label]
    report = _harvest(label, max_urls=2)
    assert [d.url for d in report.documents] == [spec["articles"]["A"]["url"]]
    assert report.skipped == 1


def test_harvest_without_news_correction_still_flows_via_archives_and_hub() -> None:
    """An index-only stanza already flows; the declared news route
    extends it with N rather than rescuing it."""
    spec = SOURCES["Retail Dive"]
    routes = _routes("Retail Dive")
    fetch = _make_fetch(
        _fetch_map("Retail Dive"),
        failures={routes["bad"]: ArticleFetchError(routes["bad"], "HTTP Error 403")},
    )
    config = dict(get_retrieval_config("Retail Dive"))
    config["sitemaps"] = [routes["index"]]
    assert routes["news"] not in config["sitemaps"]
    report = harvest_sitemap_source(config, "Retail Dive", "en", fetch=fetch, sleep=lambda _: None)
    # N still flows via the hub, but undated (last) and dateless: the news
    # route promotes it to newest with a real publication date.
    assert [d.url for d in report.documents] == [
        spec["articles"][k]["url"] for k in ("A", "C", "N", "H")
    ]
    by_url = {d.url: d for d in report.documents}
    assert by_url[spec["articles"]["N"]["url"]].published_at.isoformat() == (
        by_url[spec["articles"]["N"]["url"]].retrieved_at.isoformat()
    )
    assert report.skipped == 1


def test_dive_robots_crawl_delay_honored() -> None:
    sleeps: list[float] = []
    routes = _routes("Retail Dive")
    fetch = _make_fetch(
        _fetch_map("Retail Dive"),
        failures={routes["bad"]: ArticleFetchError(routes["bad"], "HTTP Error 403")},
    )
    harvest_sitemap_source(
        _dive_config("Retail Dive"),
        "Retail Dive",
        "en",
        fetch=fetch,
        sleep=sleeps.append,
    )
    assert sleeps and all(s >= 5.0 for s in sleeps)


# --- impersonated retry: plain-first, retry only on refusal ------------------


def _http_error(url: str, code: int, body: bytes) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, code, "Forbidden", Message(), io.BytesIO(body))


def test_plain_first_no_retry_when_plain_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []

    def fake_impersonated(url: str, timeout: int = 30) -> tuple[str, bytes]:
        seen.append(url)
        raise AssertionError("impersonated retry must not fire")

    monkeypatch.setattr(discovery, "_stdlib_get", lambda url, timeout=30: (url, b"<urlset/>"))
    monkeypatch.setattr(discovery, "_impersonated_get", fake_impersonated)
    final_url, body = policy_get(_routes("Retail Dive")["aug"], policy="impersonated-feed")
    assert body == b"<urlset/>"
    assert seen == []


def test_news_sitemap_403_retries_once_impersonated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    news_url = _routes("Retail Dive")["news"]

    def fake_stdlib(url: str, timeout: int = 30) -> tuple[str, bytes]:
        raise _http_error(url, 403, b"")

    monkeypatch.setattr(discovery, "_stdlib_get", fake_stdlib)
    monkeypatch.setattr(
        discovery,
        "_impersonated_get",
        lambda url, timeout=30: (url, b"<urlset>news</urlset>"),
    )
    monkeypatch.setattr(discovery, "_curl_cffi_requests", object())
    final_url, body = policy_get(news_url, policy="impersonated-feed")
    assert final_url == news_url
    assert body == b"<urlset>news</urlset>"


def test_article_lane_under_stdlib_only_never_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []

    def fake_stdlib(url: str, timeout: int = 30) -> tuple[str, bytes]:
        raise _http_error(url, 403, b"")

    def fake_impersonated(url: str, timeout: int = 30) -> tuple[str, bytes]:
        seen.append(url)
        return (url, b"<html/>")

    monkeypatch.setattr(discovery, "_stdlib_get", fake_stdlib)
    monkeypatch.setattr(discovery, "_impersonated_get", fake_impersonated)
    with pytest.raises(ArticleFetchError, match="HTTP Error 403"):
        policy_get(
            SOURCES["Marketing Dive"]["articles"]["A"]["url"],
            policy="stdlib-only",
        )
    assert seen == []


# --- upsert / rerun (FakeConnection mirrors the RSS lane) ---------------------


class _FakeCursor:
    def __init__(self, store: dict[str, tuple]) -> None:
        self._store = store
        self.rowcount: int = 0
        self._row: tuple | None = None

    def execute(self, sql: str, params: tuple | None = None) -> _FakeCursor:
        assert params is not None
        if sql.lstrip().upper().startswith("SELECT"):
            self._row = (1,)
            self.rowcount = 1
            return self
        url, content_hash = params[1], params[-1]
        if url in self._store or content_hash in {p[-1] for p in self._store.values()}:
            self.rowcount = 0
        else:
            self._store[url] = params
            self.rowcount = 1
        return self

    def fetchone(self) -> tuple | None:
        return self._row


class FakeConnection:
    def __init__(self) -> None:
        self.store: dict[str, tuple] = {}
        self.commits = 0

    def execute(self, sql: str, params: tuple | None = None) -> _FakeCursor:
        return _FakeCursor(self.store).execute(sql, params)

    def commit(self) -> None:
        self.commits += 1


@pytest.mark.parametrize("label", LABELS)
def test_rerun_upsert_is_noop(label: str) -> None:
    from brain.ingest import upsert_documents

    docs = _harvest(label).documents
    assert len(docs) == 4
    conn = FakeConnection()
    assert upsert_documents(docs, conn=conn) == (4, 0)
    assert upsert_documents(docs, conn=conn) == (0, 4)
    assert len(conn.store) == 4


# --- flow wiring --------------------------------------------------------------


def test_flow_ingests_dive_source_with_exact_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import brain.flows as flows

    label = "Marketing Dive"
    routes = _routes(label)
    mapping = _fetch_map(label)
    failures = {routes["bad"]: ArticleFetchError(routes["bad"], "HTTP Error 403: Forbidden")}
    fixture_fetch = _make_fetch(mapping, failures=failures)
    # One seam for the whole concurrent path: planning + article workers
    # share `discovery_fetch`, so fixture I/O flows through real logic.
    monkeypatch.setattr(flows, "discovery_fetch", lambda url, policy, gap_s: fixture_fetch(url))
    monkeypatch.setattr(flows, "enrich_document_or_keep", lambda doc, *a, **k: (doc, "rss", None))
    conn = FakeConnection()
    from brain.ingest import upsert_documents

    monkeypatch.setattr(flows, "upsert_documents", lambda docs: upsert_documents(docs, conn=conn))
    result = flows.ingest_source_flow(source_name=label)
    assert result["inserted"] == 4
    assert result["skipped"] == 0
    assert result["discovery_skipped"] == 1
    assert len(result["discovery_causes"]) == 1
    assert "error" not in result


def test_batch_ingests_both_dives_and_isolates_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import brain.flows as flows

    maps = {label: _fetch_map(label) for label in LABELS}
    failing_host = SOURCES["Marketing Dive"]["host"]
    combined: dict[str, tuple[str, bytes]] = {}
    failures: dict[str, Exception] = {}
    for label in LABELS:
        if label == "Marketing Dive":
            continue
        routes = _routes(label)
        combined.update(maps[label])
        failures[routes["bad"]] = ArticleFetchError(routes["bad"], "HTTP Error 403")
    fixture_fetch = _make_fetch(combined, failures=failures)

    def fake_fetch(url: str, policy: str, gap_s: float) -> tuple[str, bytes]:
        # Every Marketing Dive fetch fails: planning finds zero URLs and
        # raises DiscoveryError, exactly the isolated per-source error.
        if urlsplit(url).netloc == failing_host:
            raise ArticleFetchError(url, "sitemap discovery yielded no URLs: boom")
        return fixture_fetch(url)

    monkeypatch.setattr(flows, "discovery_fetch", fake_fetch)
    monkeypatch.setattr(flows, "enrich_document_or_keep", lambda doc, *a, **k: (doc, "rss", None))
    shared: dict[str, FakeConnection] = {}

    def fake_upsert(docs: list[NormalizedDocument]) -> tuple[int, int]:
        from brain.ingest import upsert_documents

        conn = shared.setdefault(docs[0].source, FakeConnection())
        return upsert_documents(docs, conn=conn)

    monkeypatch.setattr(flows, "upsert_documents", fake_upsert)
    results = flows.ingest_sources_flow(source_names=LABELS)
    assert results["Retail Dive"]["inserted"] == 4
    assert "error" not in results["Retail Dive"]
    assert results["Marketing Dive"]["inserted"] == 0
    assert "error" in results["Marketing Dive"]
