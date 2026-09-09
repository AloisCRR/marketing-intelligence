"""Pilot sitemap-discovery slice for MarketingDirecto (Ticket 08).

Observable behavior (not privates):
- sitemap index → nested URL-set traversal, newest-first bounded backfill
- news sitemaps yield publication dates; robots crawl-delay honored
- articles carry full RSS-identical provenance (Source, canonical URL,
  tz-aware timestamps, es language, content hash)
- canonical guard: declared canonical/og URL wins when same-host, else final
- rerun upsert is a no-op; one bad URL never aborts the harvest
- flow wiring: sitemap lane result shape + per-source isolation; RSS unchanged

All network is fixture-backed (FIXTURES); no live HTTP in tests.
"""

from __future__ import annotations

import io
import urllib.error
from collections.abc import Mapping
from email.message import Message
from pathlib import Path
from typing import Any

import pytest
from fake_transport import FakeTransport
from prefect_harness import no_engine

import brain.discovery as discovery
from brain.discovery import (
    ArticleExtractError,
    ArticleFetchError,
    DiscoveryError,
    discover_urls,
    extract_article,
    harvest_sitemap_source,
    parse_sitemap,
    policy_get,
    robots_crawl_delay,
)
from brain.normalize import NormalizedDocument
from brain.sources import get_retrieval_config

FIXTURES = Path(__file__).parent / "fixtures"


MD = "MarketingDirecto"

NEWS_SITEMAP = "https://www.marketingdirecto.com/news-sitemap.xml"
INDEX_SITEMAP = "https://www.marketingdirecto.com/sitemap_index.xml"
CHILD_OLD = "https://www.marketingdirecto.com/post-sitemap.xml"
CHILD_NEW = "https://www.marketingdirecto.com/post-sitemap2.xml"

URL_CANVA = "https://www.marketingdirecto.com/creacion/campanas-de-marketing/canva-suma-suite-visual-novedades"
URL_IKEA = (
    "https://www.marketingdirecto.com/creacion/campanas-de-marketing/ikea-reto-pronunciacion-sueca"
)
URL_INFLUENCERS = "https://www.marketingdirecto.com/digital-general/social-media-marketing/marcas-creadas-por-influencers"
URL_BAD = "https://www.marketingdirecto.com/digital-general/marketing-de-contenidos/pagina-que-falla-siempre"
URL_OLD_SLUG = (
    "https://www.marketingdirecto.com/anunciantes-general/marcas/slug-antiguo-campana-verano"
)
URL_NEW_SLUG = (
    "https://www.marketingdirecto.com/anunciantes-general/marcas/slug-nuevo-campana-verano"
)
URL_AGEMD = (
    "https://www.marketingdirecto.com/marketing-general/agencias/dos-nuevos-proyectos-de-la-agemd"
)
URL_WCJ = (
    "https://www.marketingdirecto.com/marketing-general/agencias/nuevas-incorporaciones-en-wcj"
)


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _full_fetch_map() -> dict[str, tuple[str, bytes]]:
    return {
        "https://www.marketingdirecto.com/robots.txt": (
            "https://www.marketingdirecto.com/robots.txt",
            _fixture("md_robots.txt"),
        ),
        NEWS_SITEMAP: (NEWS_SITEMAP, _fixture("md_news_sitemap.xml")),
        INDEX_SITEMAP: (INDEX_SITEMAP, _fixture("md_sitemap_index.xml")),
        CHILD_OLD: (CHILD_OLD, _fixture("md_post_sitemap.xml")),
        CHILD_NEW: (CHILD_NEW, _fixture("md_post_sitemap2.xml")),
        URL_CANVA: (URL_CANVA, _fixture("md_article_canva.html")),
        URL_IKEA: (URL_IKEA, _fixture("md_article_ikea.html")),
        URL_INFLUENCERS: (URL_INFLUENCERS, _fixture("md_article_influencers.html")),
        URL_OLD_SLUG: (URL_OLD_SLUG, _fixture("md_article_slug.html")),
        URL_AGEMD: (URL_AGEMD, _fixture("md_article_agemd.html")),
        URL_WCJ: (URL_WCJ, _fixture("md_article_wcj.html")),
    }


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


# --- sitemap parsing ----------------------------------------------------------


def test_parse_sitemap_index_yields_children_newest_last() -> None:
    urls, children = parse_sitemap(_fixture("md_sitemap_index.xml"))
    assert urls == []
    assert [c.loc for c in children] == [CHILD_OLD, CHILD_NEW]
    assert children[0].lastmod is not None and children[1].lastmod is not None
    assert children[1].lastmod > children[0].lastmod


def test_parse_urlset_yields_lastmod() -> None:
    urls, children = parse_sitemap(_fixture("md_post_sitemap2.xml"))
    assert children == []
    assert [u.loc for u in urls] == [URL_CANVA, URL_BAD, URL_OLD_SLUG]
    assert urls[0].lastmod is not None
    assert urls[0].lastmod.isoformat() == "2026-09-04T18:30:00+02:00"


def test_parse_news_sitemap_yields_publication_dates() -> None:
    urls, children = parse_sitemap(_fixture("md_news_sitemap.xml"))
    assert children == []
    assert [u.loc for u in urls] == [URL_IKEA, URL_INFLUENCERS]
    assert urls[0].lastmod is not None
    assert urls[0].lastmod.isoformat() == "2026-09-06T10:00:00+00:00"


def test_parse_garbage_raises_value_error() -> None:
    with pytest.raises(ValueError):
        parse_sitemap(b"<not xml at all")
    with pytest.raises(ValueError):
        parse_sitemap(b"<feed><entry/></feed>")


# --- discovery traversal ------------------------------------------------------


def test_discover_newest_first_bounded_stops_early() -> None:
    log: list[str] = []
    fetch = _make_fetch(
        {
            INDEX_SITEMAP: (INDEX_SITEMAP, _fixture("md_sitemap_index.xml")),
            CHILD_OLD: (CHILD_OLD, _fixture("md_post_sitemap.xml")),
            CHILD_NEW: (CHILD_NEW, _fixture("md_post_sitemap2.xml")),
        },
        log=log,
    )
    urls, errors = discover_urls([INDEX_SITEMAP], fetch_body=lambda u: fetch(u)[1], max_urls=2)
    assert errors == []
    # Newest child traversed first; bound reached before touching the old child.
    assert [u.loc for u in urls] == [URL_CANVA, URL_BAD]
    assert CHILD_NEW in log
    assert CHILD_OLD not in log


def test_discover_records_bad_sitemap_without_aborting() -> None:
    fetch = _make_fetch(
        {
            INDEX_SITEMAP: (INDEX_SITEMAP, _fixture("md_sitemap_index.xml")),
            CHILD_NEW: (CHILD_NEW, _fixture("md_post_sitemap2.xml")),
        },
        failures={CHILD_OLD: ArticleFetchError(CHILD_OLD, "HTTP Error 404")},
    )
    urls, errors = discover_urls([INDEX_SITEMAP], fetch_body=lambda u: fetch(u)[1], max_urls=50)
    assert [u.loc for u in urls] == [URL_CANVA, URL_BAD, URL_OLD_SLUG]
    assert len(errors) == 1 and CHILD_OLD in errors[0]


def test_discover_dedupes_canonical_variants() -> None:
    dupe_index = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        b"<url><loc>" + URL_CANVA.encode() + b"</loc>"
        b"<lastmod>2026-09-04T18:30:00+02:00</lastmod></url>"
        b"<url><loc>" + URL_CANVA.encode() + b"?utm_source=x</loc>"
        b"<lastmod>2026-09-04T18:30:00+02:00</lastmod></url></urlset>"
    )
    fetch = _make_fetch({"https://example.com/s.xml": ("https://example.com/s.xml", dupe_index)})
    urls, _ = discover_urls(
        ["https://example.com/s.xml"], fetch_body=lambda u: fetch(u)[1], max_urls=50
    )
    assert [u.loc for u in urls] == [URL_CANVA]


# --- robots / policy fetch ----------------------------------------------------


def test_robots_crawl_delay_honored() -> None:
    assert robots_crawl_delay(_fixture("md_robots.txt").decode("utf-8")) == 10.0
    assert robots_crawl_delay("User-agent: *\nDisallow: /x/\n") == 0.0
    assert robots_crawl_delay("User-agent: Googlebot\nCrawl-delay: 5\n") == 0.0
    assert robots_crawl_delay("") == 0.0


def _http_error(url: str, code: int, body: bytes) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, code, "Forbidden", Message(), io.BytesIO(body))


def test_policy_get_stdlib_success_tracks_final_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        discovery, "_stdlib_get", lambda url, timeout=30: (url + "?final=1", b"<html/>")
    )
    final_url, body = policy_get("http://example.com/a", policy="stdlib-only")
    assert final_url == "http://example.com/a?final=1"
    assert body == b"<html/>"


def test_policy_get_plain_404_never_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []

    def fake_stdlib(url: str, timeout: int = 30) -> tuple[str, bytes]:
        raise _http_error(url, 404, b"not found")

    def fake_impersonated(url: str, timeout: int = 30) -> tuple[str, bytes]:
        seen.append(url)
        return (url, b"<html/>")

    monkeypatch.setattr(discovery, "_stdlib_get", fake_stdlib)
    monkeypatch.setattr(discovery, "_impersonated_get", fake_impersonated)
    with pytest.raises(ArticleFetchError, match="HTTP Error 404"):
        policy_get("http://example.com/a", policy="impersonated-feed")
    assert seen == []


def test_policy_get_403_retries_once_under_impersonated_feed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_stdlib(url: str, timeout: int = 30) -> tuple[str, bytes]:
        raise _http_error(url, 403, b"")

    monkeypatch.setattr(discovery, "_stdlib_get", fake_stdlib)
    monkeypatch.setattr(
        discovery,
        "_impersonated_get",
        lambda url, timeout=30: (url, b"<html>retry</html>"),
    )
    monkeypatch.setattr(discovery, "_curl_cffi_requests", object())
    final_url, body = policy_get("http://example.com/a", policy="impersonated-feed")
    assert body == b"<html>retry</html>"
    assert final_url == "http://example.com/a"


def test_policy_get_403_without_curl_is_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_stdlib(url: str, timeout: int = 30) -> tuple[str, bytes]:
        raise _http_error(url, 403, b"")

    monkeypatch.setattr(discovery, "_stdlib_get", fake_stdlib)
    monkeypatch.setattr(discovery, "_curl_cffi_requests", None)
    with pytest.raises(ArticleFetchError, match="impersonated retry"):
        policy_get("http://example.com/a", policy="impersonated-feed")


# --- article extraction -------------------------------------------------------


def test_extract_article_carries_full_provenance() -> None:
    from datetime import UTC, datetime

    html = _fixture("md_article_canva.html").decode("utf-8")
    doc = extract_article(
        url=URL_CANVA,
        final_url=URL_CANVA,
        html=html,
        source=MD,
        language="es",
        retrieved_at=datetime(2026, 9, 6, 12, 0, tzinfo=UTC),
    )
    assert isinstance(doc, NormalizedDocument)
    assert doc.source == MD
    assert doc.url == URL_CANVA
    assert doc.canonical_url == URL_CANVA
    assert doc.title == "Canva suma una suite visual con más de 100 novedades"
    assert doc.author == "Lucía Fernández"
    assert doc.language == "es"
    assert doc.published_at.isoformat() == "2026-09-04T18:30:00+02:00"
    assert doc.retrieved_at.tzinfo is not None
    assert len(doc.content) >= 500
    assert "suite visual" in doc.content
    assert "<" not in doc.content
    assert doc.content_hash


def test_extract_slug_change_uses_declared_canonical() -> None:
    from datetime import UTC, datetime

    html = _fixture("md_article_slug.html").decode("utf-8")
    doc = extract_article(
        url=URL_OLD_SLUG,
        final_url=URL_OLD_SLUG,
        html=html,
        source=MD,
        language="es",
        retrieved_at=datetime(2026, 9, 6, 12, 0, tzinfo=UTC),
        fallback_published=None,
    )
    # Old slug stays the fetch identity; the declared canonical wins storage.
    assert doc.url == URL_OLD_SLUG
    assert doc.canonical_url == URL_NEW_SLUG
    assert doc.title == "Campaña de verano que arrasa en las terrazas"


def test_extract_cross_host_canonical_ignored() -> None:
    from datetime import UTC, datetime

    html = (
        _fixture("md_article_canva.html")
        .decode("utf-8")
        .replace(
            "https://www.marketingdirecto.com/creacion/campanas-de-marketing/canva-suma-suite-visual-novedades",
            "https://evil.example/x",
        )
    )
    doc = extract_article(
        url=URL_CANVA,
        final_url=URL_CANVA,
        html=html,
        source=MD,
        language="es",
        retrieved_at=datetime(2026, 9, 6, 12, 0, tzinfo=UTC),
    )
    assert doc.canonical_url == URL_CANVA


def test_extract_missing_title_raises() -> None:
    from datetime import UTC, datetime

    with pytest.raises(ArticleExtractError, match="missing title"):
        extract_article(
            url="http://example.com/x",
            final_url="http://example.com/x",
            html="<html><body><p>no title here</p></body></html>",
            source=MD,
            language="es",
            retrieved_at=datetime(2026, 9, 6, 12, 0, tzinfo=UTC),
        )


def test_extract_empty_body_raises() -> None:
    from datetime import UTC, datetime

    with pytest.raises(ArticleExtractError, match="empty after cleaning"):
        extract_article(
            url="http://example.com/x",
            final_url="http://example.com/x",
            # Title via og: meta (strips clean) + body with no visible text.
            html="<html><head>"
            '<meta property="og:title" content="Solo titular">'
            '</head><body><div class="x"><span></span></div></body></html>',
            source=MD,
            language="es",
            retrieved_at=datetime(2026, 9, 6, 12, 0, tzinfo=UTC),
        )


def test_thin_body_is_kept_with_cause_not_bypassed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Thin threshold governs keep-vs-flag: a short body is kept, cause kept."""
    from datetime import UTC, datetime

    import brain.enrich as enrich

    html = (
        "<html><head><title>Breve</title>"
        '<meta property="article:published_time" content="2026-09-05T10:00:00+02:00">'
        "</head><body><article><h1>Breve</h1><p>Texto corto.</p></article></body></html>"
    )
    doc = extract_article(
        url="http://example.com/breve",
        final_url="http://example.com/breve",
        html=html,
        source=MD,
        language="es",
        retrieved_at=datetime(2026, 9, 6, 12, 0, tzinfo=UTC),
    )
    assert doc.content

    def boom(url: str, timeout: int = 30) -> str:
        raise enrich.FetchFailed(f"fetch failed for {url}: 403")

    monkeypatch.setattr(enrich, "fetch_and_clean", boom)
    monkeypatch.setattr(enrich, "try_fallback_reader", boom)
    kept, method, cause = enrich.enrich_document_or_keep(doc)
    assert kept.content == doc.content  # RSS-lane keep semantics: body kept
    assert method == "rss"
    assert cause is not None


# --- harvest ------------------------------------------------------------------


def _harvest_config() -> dict[str, Any]:
    return dict(get_retrieval_config(MD))


def test_harvest_end_to_end_newest_first() -> None:
    transport = FakeTransport.from_fetch_map(
        _full_fetch_map(),
        failures={URL_BAD: ArticleFetchError(URL_BAD, "HTTP Error 403: Forbidden")},
    )
    report = harvest_sitemap_source(_harvest_config(), MD, "es", **transport.as_kwargs())
    # News sitemap first (newest), then index child newest-first, bounded 50.
    assert [d.url for d in report.documents] == [
        URL_IKEA,
        URL_INFLUENCERS,
        URL_CANVA,
        URL_OLD_SLUG,
        URL_WCJ,
        URL_AGEMD,
    ]
    assert {d.language for d in report.documents} == {"es"}
    for doc in report.documents:
        assert doc.published_at.tzinfo is not None
        assert doc.retrieved_at.tzinfo is not None
        assert doc.content_hash
    # One bad article: explicit skip, harvest continues.
    assert report.skipped == 1
    assert len(report.causes) == 1 and URL_BAD in report.causes[0]
    # Politeness: robots Crawl-delay 10 honored (pacing 10s + jitter).
    assert transport.sleep_recorder and all(s >= 10.0 for s in transport.sleep_recorder)


def test_harvest_bounded_backfill() -> None:
    transport = FakeTransport.from_fetch_map(_full_fetch_map())
    config = _harvest_config()
    config["max_urls"] = 2
    report = harvest_sitemap_source(config, MD, "es", **transport.as_kwargs())
    assert [d.url for d in report.documents] == [URL_IKEA, URL_INFLUENCERS]
    assert report.skipped == 0


def test_harvest_raises_when_nothing_discovered() -> None:
    transport = FakeTransport()

    def dead(url: str) -> tuple[str, bytes]:
        raise ArticleFetchError(url, "HTTP Error 403: Forbidden")

    with pytest.raises(DiscoveryError, match="yielded no URLs"):
        harvest_sitemap_source(_harvest_config(), MD, "es", fetch=dead, sleep=transport.sleep)


# --- upsert / rerun / dedupe (FakeConnection mirrors the RSS lane) ------------


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


def _harvest_docs() -> list[NormalizedDocument]:
    transport = FakeTransport.from_fetch_map(
        _full_fetch_map(),
        failures={URL_BAD: ArticleFetchError(URL_BAD, "HTTP Error 403: Forbidden")},
    )
    return harvest_sitemap_source(_harvest_config(), MD, "es", **transport.as_kwargs()).documents


def test_rerun_upsert_is_noop() -> None:
    from brain.ingest import upsert_documents

    docs = _harvest_docs()
    conn = FakeConnection()
    assert upsert_documents(docs, conn=conn) == (6, 0)
    assert upsert_documents(docs, conn=conn) == (0, 6)
    assert len(conn.store) == 6


def test_slug_change_dedupes_on_hash() -> None:
    from brain.ingest import upsert_documents

    docs = _harvest_docs()
    conn = FakeConnection()
    upsert_documents(docs, conn=conn)
    # Same article refetched under its new slug: content hash collapses it.
    by_old_slug = next(d for d in docs if d.url == URL_OLD_SLUG)
    assert by_old_slug.canonical_url == URL_NEW_SLUG
    assert upsert_documents(docs, conn=conn) == (0, 6)


# --- flow wiring --------------------------------------------------------------


def test_flow_ingests_sitemap_source_with_exact_shape(
    monkeypatch: pytest.MonkeyPatch, no_engine: None
) -> None:
    import brain.flows as flows

    mapping = _full_fetch_map()
    failures = {URL_BAD: ArticleFetchError(URL_BAD, "HTTP Error 403: Forbidden")}
    fixture_fetch = _make_fetch(mapping, failures=failures)
    # One seam for the whole concurrent path: planning + article workers
    # share `discovery_fetch`, so fixture I/O flows through real logic.
    monkeypatch.setattr(flows, "discovery_fetch", lambda url, policy, gap_s: fixture_fetch(url))
    monkeypatch.setattr(flows, "enrich_document_or_keep", lambda doc, *a, **k: (doc, "rss", None))
    conn = FakeConnection()
    from brain.ingest import upsert_documents

    monkeypatch.setattr(flows, "upsert_documents", lambda docs: upsert_documents(docs, conn=conn))
    result = flows.ingest_source_flow(source_name=MD)
    assert result["inserted"] == 6
    assert result["skipped"] == 0
    assert result["discovery_skipped"] == 1
    assert len(result["discovery_causes"]) == 1
    assert "error" not in result


def test_flow_clean_harvest_keeps_exact_shape(
    monkeypatch: pytest.MonkeyPatch, no_engine: None
) -> None:
    import brain.flows as flows

    mapping = {k: v for k, v in _full_fetch_map().items() if k != URL_BAD}
    child_new = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        b"<url><loc>" + URL_CANVA.encode() + b"</loc>"
        b"<lastmod>2026-09-04T18:30:00+02:00</lastmod></url></urlset>"
    )
    mapping[CHILD_NEW] = (CHILD_NEW, child_new)
    news = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
        b'xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">'
        b"<url><loc>" + URL_IKEA.encode() + b"</loc><news:news><news:publication>"
        b"<news:name>marketingdirecto.com</news:name></news:publication>"
        b"<news:publication_date>2026-09-06T10:00:00+00:00</news:publication_date>"
        b"</news:news></url></urlset>"
    )
    mapping[NEWS_SITEMAP] = (NEWS_SITEMAP, news)
    mapping[CHILD_OLD] = (
        CHILD_OLD,
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"></urlset>',
    )
    fixture_fetch = _make_fetch(mapping)
    monkeypatch.setattr(flows, "discovery_fetch", lambda url, policy, gap_s: fixture_fetch(url))
    monkeypatch.setattr(flows, "enrich_document_or_keep", lambda doc, *a, **k: (doc, "rss", None))
    from brain.ingest import upsert_documents

    conn = FakeConnection()
    monkeypatch.setattr(flows, "upsert_documents", lambda docs: upsert_documents(docs, conn=conn))
    assert flows.ingest_source_flow(source_name=MD) == {"inserted": 2, "skipped": 0}


def test_batch_isolates_sitemap_failure(
    monkeypatch: pytest.MonkeyPatch, no_engine: None
) -> None:
    import brain.flows as flows
    from brain.sources import get_source

    mapping = _full_fetch_map()
    fixture_fetch = _make_fetch(mapping)
    monkeypatch.setattr(flows, "discovery_fetch", lambda url, policy, gap_s: fixture_fetch(url))
    monkeypatch.setattr(flows, "enrich_document_or_keep", lambda doc, *a, **k: (doc, "rss", None))
    shared: dict[str, FakeConnection] = {}

    def fake_upsert(docs: list[NormalizedDocument]) -> tuple[int, int]:
        from brain.ingest import upsert_documents

        conn = shared.setdefault(docs[0].source, FakeConnection())
        return upsert_documents(docs, conn=conn)

    monkeypatch.setattr(flows, "upsert_documents", fake_upsert)

    pj_url = get_source("Professional Jeweller")["rss_url"]

    def fake_fetch_rss(url: str, timeout: int = 30) -> bytes:
        if url == pj_url:
            raise RuntimeError("boom")
        raise AssertionError(f"unexpected RSS fetch: {url}")

    monkeypatch.setattr(flows, "fetch_rss", fake_fetch_rss)
    results = flows.ingest_sources_flow(source_names=[MD, "Professional Jeweller"])
    assert results[MD]["inserted"] == 6
    assert "error" not in results[MD]
    assert results["Professional Jeweller"]["inserted"] == 0
    assert "error" in results["Professional Jeweller"]


# --- ticket 12: Jing Daily hub anchors + JSON-LD-first extraction -------------

JD = "Jing Daily"
JD_HUB = "https://jingdaily.com/"
JD_PAGE2 = "https://jingdaily.com/page/2/"
JD_INDEX = "https://jingdaily.com/sitemap.xml"
JD_POSTS_SM = "https://jingdaily.com/posts/sitemap.xml"
JD_BRANDS_SM = "https://jingdaily.com/brands/sitemap.xml"

JD_YVMIN = "https://jingdaily.com/posts/how-yvmin-became-gen-z-favorite-jewelry-brand"
JD_PAGANI = "https://jingdaily.com/posts/why-pagani-is-made-for-billionaires"
JD_K11 = "https://jingdaily.com/posts/k11-musea-sales-rise-luxury-revamp"
JD_VELVET = "https://jingdaily.com/posts/velvet-rope-membership-clubs"
JD_ROULETTE = "https://jingdaily.com/posts/freemium-roulette-locked-story"
JD_LOTUS = "https://jingdaily.com/posts/white-lotus-luxury-hospitality-blind-spot"
JD_DOVE = "https://jingdaily.com/posts/dove-douyin-backlash-outsourcing-trial"
JD_ACME = "https://jingdaily.com/brands/acme-flagship-shanghai"


def _jd_variant(url: str, headline: str, extra: str) -> tuple[str, bytes]:
    """Per-article HTML from the shared shell: distinct headline/body/canonical
    so fixtures behave like distinct documents under hash dedupe."""
    html = _fixture("jd_article_full.html").decode("utf-8")
    html = (
        html.replace("How Yvmin became Gen Z's favorite jewelry brand", headline)
        .replace(
            "https://jingdaily.com/posts/how-yvmin-became-gen-z-favorite-jewelry-brand",
            url,
        )
        .replace(
            "rather than the jewel count itself.",
            f"rather than the jewel count itself. {extra}",
        )
    )
    return (url, html.encode("utf-8"))


def _jing_fetch_map() -> dict[str, tuple[str, bytes]]:
    mapping = {
        "https://jingdaily.com/robots.txt": (
            "https://jingdaily.com/robots.txt",
            _fixture("jd_robots.txt"),
        ),
        JD_HUB: (JD_HUB, _fixture("jd_hub.html")),
        JD_PAGE2: (JD_PAGE2, _fixture("jd_hub_page2.html")),
        JD_INDEX: (JD_INDEX, _fixture("jd_sitemap_index.xml")),
        JD_POSTS_SM: (JD_POSTS_SM, _fixture("jd_posts_sitemap.xml")),
        JD_BRANDS_SM: (JD_BRANDS_SM, _fixture("jd_brands_sitemap.xml")),
        JD_YVMIN: (JD_YVMIN, _fixture("jd_article_full.html")),
        JD_VELVET: (JD_VELVET, _fixture("jd_article_metered.html")),
    }
    for url, headline, extra in [
        (
            JD_PAGANI,
            "Why Pagani is made for billionaires",
            "Pagani's order books stay full in every cycle.",
        ),
        (
            JD_K11,
            "K11 Musea sales rise on luxury revamp",
            "Foot traffic beats the district average for the third quarter.",
        ),
        (
            JD_LOTUS,
            "The White Lotus exposes luxury hospitality's blind spot",
            "Hoteliers report a surge in suite inquiries after each episode.",
        ),
        (
            JD_DOVE,
            "Dove's Douyin backlash puts outsourcing on trial",
            "The brand pauses external content production pending review.",
        ),
        (
            JD_ACME,
            "Acme plants its flagship in Shanghai",
            "The two-floor store opens with an appointment-only salon.",
        ),
    ]:
        mapping[url] = _jd_variant(url, headline, extra)
    return mapping


def _jing_config() -> dict[str, Any]:
    config = dict(get_retrieval_config(JD))
    config["hub_pages"] = [JD_PAGE2]
    return config


# --- hub-anchor fallback (shared machinery for 10/11) -------------------------


def test_hub_links_match_pattern_filter_noise() -> None:
    from brain.discovery import extract_hub_links

    links = extract_hub_links(_fixture("jd_hub.html").decode("utf-8"), JD_HUB, "/posts/")
    assert links == [
        JD_YVMIN,
        JD_PAGANI,
        JD_K11,
        JD_VELVET,
        JD_ROULETTE,
    ]


def test_hub_links_dedupe_and_absolutize() -> None:
    from brain.discovery import extract_hub_links

    # Relative hrefs absolutize against the hub; the repeated Yvmin link
    # collapses; /intels/, /tags/, and off-host anchors are excluded.
    links = extract_hub_links(_fixture("jd_hub.html").decode("utf-8"), JD_HUB, "/posts/")
    assert len(links) == len(set(links)) == 5
    assert all(h.startswith("https://jingdaily.com/posts/") for h in links)


def test_discover_hub_urls_aggregates_pagination() -> None:
    from brain.discovery import discover_hub_urls

    fetch = _make_fetch(_jing_fetch_map())
    urls, errors = discover_hub_urls(
        [JD_HUB, JD_PAGE2],
        fetch_body=lambda u: fetch(u)[1],
        link_pattern="/posts/",
    )
    assert errors == []
    assert [u.loc for u in urls] == [
        JD_YVMIN,
        JD_PAGANI,
        JD_K11,
        JD_VELVET,
        JD_ROULETTE,
        JD_LOTUS,
        JD_DOVE,
    ]
    assert all(u.lastmod is None for u in urls)


def test_discover_hub_urls_records_bad_page_without_aborting() -> None:
    from brain.discovery import discover_hub_urls

    def failing(url: str) -> bytes:
        if url == JD_PAGE2:
            raise ArticleFetchError(JD_PAGE2, "HTTP Error 500")
        return _make_fetch(_jing_fetch_map())(url)[1]

    urls, errors = discover_hub_urls([JD_HUB, JD_PAGE2], fetch_body=failing, link_pattern="/posts/")
    assert [u.loc for u in urls][:2] == [JD_YVMIN, JD_PAGANI]
    assert len(errors) == 1 and JD_PAGE2 in errors[0]


def test_discover_prefer_pattern_visits_core_section_first() -> None:
    log: list[str] = []
    fetch = _make_fetch(_jing_fetch_map(), log=log)
    urls, errors = discover_urls(
        [JD_INDEX],
        fetch_body=lambda u: fetch(u)[1],
        max_urls=2,
        prefer_pattern="/posts/",
    )
    assert errors == []
    # Core section first: bound satisfied before the brands child is touched.
    assert [u.loc for u in urls] == [JD_DOVE, JD_YVMIN]
    assert JD_POSTS_SM in log
    assert JD_BRANDS_SM not in log


def test_discover_prefer_pattern_ranks_shallowest_first() -> None:
    nested_index = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        b"<sitemap><loc>https://jingdaily.com/wechat/sitemap.xml</loc></sitemap>"
        b"<sitemap><loc>https://jingdaily.com/intels/posts/sitemap.xml</loc></sitemap>"
        b"<sitemap><loc>https://jingdaily.com/posts/sitemap.xml</loc></sitemap>"
        b"</sitemapindex>"
    )
    deep = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        b"<url><loc>" + JD_K11.encode() + b"</loc>"
        b"<lastmod>2026-09-01T00:00:00+00:00</lastmod></url></urlset>"
    )
    mapping = dict(_jing_fetch_map())
    mapping["https://jingdaily.com/nested.xml"] = (
        "https://jingdaily.com/nested.xml",
        nested_index,
    )
    mapping["https://jingdaily.com/intels/posts/sitemap.xml"] = (
        "https://jingdaily.com/intels/posts/sitemap.xml",
        deep,
    )
    log: list[str] = []
    fetch = _make_fetch(mapping, log=log)
    urls, _ = discover_urls(
        ["https://jingdaily.com/nested.xml"],
        fetch_body=lambda u: fetch(u)[1],
        max_urls=50,
        prefer_pattern="/posts/",
    )
    children_order = [u for u in log if u.endswith("sitemap.xml")]
    # /posts/ (rank 0) before /intels/posts/ (nested) before /wechat/ (absent).
    assert children_order.index(JD_POSTS_SM) < children_order.index(
        "https://jingdaily.com/intels/posts/sitemap.xml"
    )


def test_discover_without_prefer_pattern_keeps_legacy_order() -> None:
    log: list[str] = []
    fetch = _make_fetch(_jing_fetch_map(), log=log)
    urls, _ = discover_urls([JD_INDEX], fetch_body=lambda u: fetch(u)[1], max_urls=50)
    # No preference: reverse document order (brands child before posts).
    assert log.index(JD_BRANDS_SM) < log.index(JD_POSTS_SM)
    assert {u.loc for u in urls} >= {JD_DOVE, JD_YVMIN, JD_ACME}


# --- JSON-LD-first extractor switch (shared machinery for 10/11) --------------


def test_json_ld_body_beats_thin_generic() -> None:
    from brain.discovery import extract_json_ld_body
    from brain.enrich import clean_to_markdown, is_thin

    html = _fixture("jd_article_full.html").decode("utf-8")
    generic = clean_to_markdown(html, JD_YVMIN)
    assert is_thin(generic)  # premise: generic extraction goes thin here
    body = extract_json_ld_body(html)
    assert body is not None
    assert len(body) > 1000
    assert "playful luxury" in body


def test_json_ld_ignores_stub_blocks_without_body() -> None:
    from brain.discovery import extract_json_ld_body

    html = _fixture("jd_article_full.html").decode("utf-8")
    body = extract_json_ld_body(html) or ""
    # The related-story stub ("gold prices") must not win over the article.
    assert "gold prices" not in body.lower()
    assert "Yvmin" in body


def test_json_ld_missing_falls_back_to_generic() -> None:
    from brain.discovery import extract_json_ld_body

    assert extract_json_ld_body("<html><body><p>no structured data</p></body></html>") is None
    assert extract_json_ld_body("not html at all {{{") is None


def test_json_ld_first_doc_uses_structured_metadata() -> None:
    from datetime import UTC, datetime

    html = _fixture("jd_article_full.html").decode("utf-8")
    doc = extract_article(
        url=JD_YVMIN,
        final_url=JD_YVMIN,
        html=html,
        source=JD,
        language="en",
        retrieved_at=datetime(2026, 9, 6, 12, 0, tzinfo=UTC),
        extractor="json-ld-first",
    )
    # Title falls back to the JSON-LD headline (no og:title in fixture).
    assert doc.title == "How Yvmin became Gen Z's favorite jewelry brand"
    assert doc.author == "Li Wei"
    assert doc.published_at.isoformat() == "2026-09-04T09:00:00+00:00"
    assert len(doc.content) > 1000
    assert doc.canonical_url == JD_YVMIN


def test_unknown_extractor_falls_back_to_generic() -> None:
    from datetime import UTC, datetime

    html = _fixture("md_article_canva.html").decode("utf-8")
    doc = extract_article(
        url="http://example.com/x",
        final_url="http://example.com/x",
        html=html,
        source=JD,
        language="en",
        retrieved_at=datetime(2026, 9, 6, 12, 0, tzinfo=UTC),
        extractor="teleport",
    )
    assert "suite visual" in doc.content


# --- Jing harvest end-to-end (sitemap-first, hub second) ----------------------


def test_jing_harvest_sitemap_first_hub_extends() -> None:
    transport = FakeTransport.from_fetch_map(
        _jing_fetch_map(),
        failures={JD_ROULETTE: ArticleFetchError(JD_ROULETTE, "HTTP Error 403")},
    )
    report = harvest_sitemap_source(_jing_config(), JD, "en", **transport.as_kwargs())
    # Sitemap coverage first (newest-first), then hub-only URLs in hub order.
    assert [d.url for d in report.documents] == [
        JD_DOVE,
        JD_YVMIN,
        JD_ACME,
        JD_PAGANI,
        JD_K11,
        JD_VELVET,
        JD_LOTUS,
    ]
    assert {d.language for d in report.documents} == {"en"}
    for doc in report.documents:
        assert doc.published_at.tzinfo is not None
        assert doc.retrieved_at.tzinfo is not None
        assert doc.content_hash
    # Full bodies come from structured data, not the thin shell.
    yvmin = next(d for d in report.documents if d.url == JD_YVMIN)
    assert len(yvmin.content) > 1000
    # Metered body is kept-aside with provenance, never bypassed.
    velvet = next(d for d in report.documents if d.url == JD_VELVET)
    assert velvet.title == "Inside the velvet-rope membership clubs"
    # One unloadable URL: explicit skip, harvest continues.
    assert report.skipped == 1
    assert len(report.causes) == 1 and JD_ROULETTE in report.causes[0]
    # No crawl-delay declared: stanza pacing applies.
    assert transport.sleep_recorder and all(s >= 1.0 for s in transport.sleep_recorder)


def _jing_docs() -> list[NormalizedDocument]:
    transport = FakeTransport.from_fetch_map(
        _jing_fetch_map(),
        failures={JD_ROULETTE: ArticleFetchError(JD_ROULETTE, "HTTP Error 403")},
    )
    return harvest_sitemap_source(_jing_config(), JD, "en", **transport.as_kwargs()).documents


def test_jing_rerun_upsert_is_noop() -> None:
    from brain.ingest import upsert_documents

    docs = _jing_docs()
    conn = FakeConnection()
    assert upsert_documents(docs, conn=conn) == (7, 0)
    assert upsert_documents(docs, conn=conn) == (0, 7)
    assert len(conn.store) == 7


def test_jing_metered_body_kept_with_cause_then_flaggable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Thin threshold governs keep-vs-flag: metered doc kept, cause kept,
    and the Extraction Flag path accepts it (reason + detail + reporter)."""
    import brain.enrich as enrich
    import brain.flag as flag_lane

    docs = _jing_docs()
    velvet = next(d for d in docs if d.url == JD_VELVET)
    assert enrich.is_thin(velvet.content)

    def boom(url: str, timeout: int = 30) -> str:
        raise enrich.FetchFailed(f"fetch failed for {url}: 403 metered")

    monkeypatch.setattr(enrich, "fetch_and_clean", boom)
    monkeypatch.setattr(enrich, "try_fallback_reader", boom)
    kept, method, cause = enrich.enrich_document_or_keep(velvet)
    assert kept.content == velvet.content  # kept-aside, never bypassed
    assert method == "rss"
    assert cause is not None

    updates: list[tuple] = []

    class _FlagCursor:
        rowcount = 1

        def execute(self, sql: str, params: tuple | None = None) -> _FlagCursor:
            updates.append((sql, params))
            return self

        def close(self) -> None:
            pass

    class _FlagConn:
        def cursor(self) -> _FlagCursor:
            return _FlagCursor()

    import brain.article as article_mod

    monkeypatch.setattr(
        article_mod,
        "get_article",
        lambda identifier, conn=None: {"url": velvet.url, "flag_reason": "thin"},
    )
    flagged = flag_lane.flag_extraction(
        velvet.url,
        reason="thin",
        detail="metered body under 500 chars; kept-aside by pipeline",
        flagged_by="ingest-jing-daily",
        conn=_FlagConn(),
    )
    assert flagged["flag_reason"] == "thin"
    assert updates and updates[0][1][0] == "thin"
    assert updates[0][1][3] == velvet.url


# --- Jing flow wiring ---------------------------------------------------------


def test_flow_ingests_jing_with_exact_shape(
    monkeypatch: pytest.MonkeyPatch, no_engine: None
) -> None:
    import brain.flows as flows

    mapping = _jing_fetch_map()
    failures = {JD_ROULETTE: ArticleFetchError(JD_ROULETTE, "HTTP Error 403")}
    fixture_fetch = _make_fetch(mapping, failures=failures)
    monkeypatch.setattr(flows, "discovery_fetch", lambda url, policy, gap_s: fixture_fetch(url))
    monkeypatch.setattr(flows, "enrich_document_or_keep", lambda doc, *a, **k: (doc, "rss", None))
    conn = FakeConnection()
    from brain.ingest import upsert_documents

    monkeypatch.setattr(flows, "upsert_documents", lambda docs: upsert_documents(docs, conn=conn))
    result = flows.ingest_source_flow(source_name=JD)
    # Declared stanza now ships hub_pages=[page/2/]: hub page 2 (lotus +
    # dove) joins the flow run; harvest-level tests prove pagination.
    assert result["inserted"] == 7
    assert result["skipped"] == 0
    assert result["discovery_skipped"] == 1
    assert len(result["discovery_causes"]) == 1
    assert "error" not in result


# --- ticket 14: sitemap_exclude (Exame /webstories/ pollution) ------------------

EX_SITEMAP = "https://exame.com/sitemap.xml"
EX_WS_1 = "https://exame.com/webstories/resumo-do-dia-1/"
EX_WS_2 = "https://exame.com/webstories/resumo-do-dia-2/"
EX_WS_3 = "https://exame.com/webstories/resumo-do-dia-3/"
EX_REAL_1 = "https://exame.com/negocios/varejo-de-luxo-acelera-expansao/"
EX_REAL_2 = "https://exame.com/casual/a-brilhante-disputa-entre-diamantes/"
EX_404 = "https://exame.com/negocios/pagina-que-falha-sempre/"
EX_NO_TITLE = "https://exame.com/negocios/pagina-sem-titulo/"


def _exame_polluted_sitemap() -> bytes:
    """Newest-first polluted urlset: webstories newest, then real articles,
    then a 404 and a title-less page (mirrors the 2026-09-06 Exame run)."""
    entries = [
        (EX_WS_1, "2026-09-06T12:00:00-03:00"),
        (EX_WS_2, "2026-09-06T11:00:00-03:00"),
        (EX_WS_3, "2026-09-06T10:00:00-03:00"),
        (EX_REAL_1, "2026-09-05T08:30:00-03:00"),
        (EX_REAL_2, "2026-09-04T08:30:00-03:00"),
        (EX_404, "2026-09-03T08:30:00-03:00"),
        (EX_NO_TITLE, "2026-09-02T08:30:00-03:00"),
    ]
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    ]
    for loc, lastmod in entries:
        parts.append(f"<url><loc>{loc}</loc><lastmod>{lastmod}</lastmod></url>")
    parts.append("</urlset>")
    return "".join(parts).encode("utf-8")


def test_discover_without_exclude_spends_budget_on_webstories() -> None:
    """Bug premise: with no exclusion, the newest webstories fill max_urls."""
    fetch = _make_fetch({EX_SITEMAP: (EX_SITEMAP, _exame_polluted_sitemap())})
    urls, errors = discover_urls([EX_SITEMAP], fetch_body=lambda u: fetch(u)[1], max_urls=2)
    assert errors == []
    assert [u.loc for u in urls] == [EX_WS_1, EX_WS_2]


def test_discover_sitemap_exclude_drops_webstories_before_budget() -> None:
    """Excluded URLs never consume the backfill budget: max_urls=2 still
    yields the two genuine articles even though webstories are newer."""
    fetch = _make_fetch({EX_SITEMAP: (EX_SITEMAP, _exame_polluted_sitemap())})
    urls, errors = discover_urls(
        [EX_SITEMAP],
        fetch_body=lambda u: fetch(u)[1],
        max_urls=2,
        sitemap_exclude=["/webstories/"],
    )
    assert errors == []
    assert [u.loc for u in urls] == [EX_REAL_1, EX_REAL_2]


def test_harvest_sitemap_exclude_webstories_never_fetched_bad_urls_explicit() -> None:
    """Webstories are excluded before fetch; the 404 and the title-less page
    surface as explicit per-URL discovery_causes skips; genuine articles
    still insert."""
    log: list[str] = []
    mapping = {
        "https://exame.com/robots.txt": (
            "https://exame.com/robots.txt",
            b"User-agent: *\nDisallow:\n",
        ),
        EX_SITEMAP: (EX_SITEMAP, _exame_polluted_sitemap()),
        EX_REAL_1: (EX_REAL_1, _fixture("md_article_canva.html")),
        EX_REAL_2: (EX_REAL_2, _fixture("md_article_canva.html")),
        EX_NO_TITLE: (EX_NO_TITLE, b"<html><body><p>no title here</p></body></html>"),
    }
    fetch = _make_fetch(
        mapping,
        log=log,
        failures={
            EX_404: ArticleFetchError(
                EX_404, "fetch failed for " + EX_404 + ": HTTP Error 404: Not Found"
            )
        },
    )
    config: dict[str, Any] = {
        "type": "sitemap",
        "policy": "stdlib-only",
        "extractor": "generic",
        "sitemaps": [EX_SITEMAP],
        "sitemap_exclude": ["/webstories/"],
        "pacing_ms": 1000,
        "max_urls": 50,
    }
    report = harvest_sitemap_source(config, "Exame", "pt", fetch=fetch, sleep=lambda _: None)
    assert [d.url for d in report.documents] == [EX_REAL_1, EX_REAL_2]
    assert report.skipped == 2
    assert any(EX_404 in c and "404" in c for c in report.causes)
    assert any(EX_NO_TITLE in c and "missing title" in c for c in report.causes)
    assert not any("/webstories/" in u for u in log)


def test_batch_isolates_jing_failure(
    monkeypatch: pytest.MonkeyPatch, no_engine: None
) -> None:
    import brain.flows as flows

    # The whole discovery lane hard-fails: planning raises out of the
    # discover subflow, and the batch records the explicit per-source error.
    monkeypatch.setattr(
        flows,
        "plan_harvest",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("hub down")),
    )
    monkeypatch.setattr(flows, "enrich_document_or_keep", lambda doc, *a, **k: (doc, "rss", None))
    fixture = (FIXTURES / "martech_sample.xml").read_bytes()
    monkeypatch.setattr(flows, "fetch_rss", lambda url, timeout=30: fixture)

    def fake_upsert(docs: list[NormalizedDocument]) -> tuple[int, int]:
        return (len(docs), 0)

    monkeypatch.setattr(flows, "upsert_documents", fake_upsert)
    results = flows.ingest_sources_flow(source_names=["MarTech", JD])
    assert results["MarTech"] == {"inserted": 3, "skipped": 0}
    assert results[JD]["inserted"] == 0
    assert "error" in results[JD] and results[JD]["error"]
