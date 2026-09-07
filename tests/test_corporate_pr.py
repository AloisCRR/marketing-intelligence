"""Corporate PR + National Jeweler pipelines (Ticket 10).

Observable behavior (not privates):
- url-set lane: stanza sitemap_pattern distills whole-site urlsets to the
  declared beat (NJ /articles/, Richemont /press-releases-news/,
  LVMH /news-lvmh/); sections skipped silently, never as errors
- NJ numeric-ID-reuse guard: recycled-ID canonicals are skipped, never
  stored; same-ID slug changes store with the declared canonical
- generic extraction provenance per family (NJ author anchors, canonical/
  og:title, sitemap-lastmod dates; RI/LVMH canonical + full bodies)
- rerun upsert is a no-op per source; batch isolates one failing source
- flow wiring: url-set/url-set+hub/sitemap(non-RSS) all reach discover_task;
  RSS lane untouched

All network is fixture-backed (FIXTURES); no live HTTP in tests.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest

from brain.discovery import (
    ArticleExtractError,
    discover_urls,
    extract_article,
    harvest_sitemap_source,
)
from brain.normalize import NormalizedDocument
from brain.sources import get_retrieval_config

FIXTURES = Path(__file__).parent / "fixtures"

NJ = "National Jeweler"
RI = "Richemont Media"
LVMH = "LVMH Press Releases"

NJ_SITEMAP = "https://nationaljeweler.com/sitemap.xml"
NJ_HUB = "https://nationaljeweler.com/industry"
NJ_15291 = "https://nationaljeweler.com/articles/15291-orloff-jewelers-will-soon-unveil-a-bigger-better-location"
NJ_15290 = "https://nationaljeweler.com/articles/15290-chelsea-gabrielle-transforms-pilates-springs-into-ring"
NJ_13197 = "https://nationaljeweler.com/articles/13197-here-are-the-first-recipients-of-nina-pugliese-memorial-scholarship"
NJ_15289_OLD = "https://nationaljeweler.com/articles/15289-old-slug-summer-trunk-show"
NJ_15289_NEW = "https://nationaljeweler.com/articles/15289-new-slug-summer-trunk-show"
NJ_15288 = "https://nationaljeweler.com/articles/15288-crime-ring-busted-in-antwerp"
NJ_15287 = "https://nationaljeweler.com/articles/15287-gold-price-rally-reshapes-buying"

RI_SITEMAP = "https://www.richemont.com/sitemap.xml"
RI_WATCHES = "https://www.richemont.com/news-media/press-releases-news/watches-and-wonders-shanghai-the-official-program-now-available/"
RI_MNET = "https://www.richemont.com/news-media/press-releases-news/richemont-signs-agreement-to-merge-its-media-interests/"
RI_SALES = "https://www.richemont.com/news-media/press-releases-news/quarterly-sales-update/"

LVMH_INDEX = "https://www.lvmh.com/sitemap.xml"
LVMH_EN = "https://www.lvmh.com/en/sitemap/en-us.xml"
LVMH_FR = "https://www.lvmh.com/fr/sitemap/fr-fr.xml"
LVMH_IT = "https://www.lvmh.com/it/sitemap/it-it.xml"
LVMH_JP = "https://www.lvmh.com/jp/sitemap/ja-jp.xml"
LVMH_GUERLAIN = "https://www.lvmh.com/en/news-lvmh/guerlain-revisits-an-icon-lheure-bleue"
LVMH_METIERS = (
    "https://www.lvmh.com/en/news-lvmh/lvmh-launches-metiers-dexcellence-video-series-in-china"
)
LVMH_VUITTON = "https://www.lvmh.com/en/news-lvmh/louis-vuitton-cultural-sponsor-frick-collection"
LVMH_FR_NEWS = "https://www.lvmh.com/fr/news-lvmh/guerlain-revisite-icone-lheure-bleue"
LVMH_IT_NEWS = "https://www.lvmh.com/it/news-lvmh/guerlain-rivede-icona-lheure-bleue"
LVMH_JP_NEWS = "https://www.lvmh.com/jp/news-lvmh/guerlain-icon-lheure-bleue"


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _make_fetch(
    mapping: dict[str, tuple[str, bytes]],
    log: list[str] | None = None,
    failures: dict[str, Exception] | None = None,
) -> Any:
    def fake_fetch(url: str) -> tuple[str, bytes]:
        if log is not None:
            log.append(url)
        if failures and url in failures:
            raise failures[url]
        return mapping[url]

    return fake_fetch


def _nj_variant(
    url: str, headline: str, extra: str, author: str = "Lenore Fedow"
) -> tuple[str, bytes]:
    html = _fixture("nj_article_full.html").decode("utf-8")
    html = html.replace("Orloff Jewelers Will Soon Unveil a Bigger, Better Location", headline)
    html = html.replace(
        "https://nationaljeweler.com/articles/15291-orloff-jewelers-will-soon-unveil-a-bigger-better-location",
        url,
    )
    html = html.replace("Lenore+Fedow", author.replace(" ", "+")).replace("Lenore Fedow", author)
    html = html.replace(
        "every square foot of the new layout.",
        f"every square foot of the new layout. {extra}",
    )
    return (url, html.encode("utf-8"))


def _nj_fetch_map() -> dict[str, tuple[str, bytes]]:
    mapping: dict[str, tuple[str, bytes]] = {
        "https://nationaljeweler.com/robots.txt": (
            "https://nationaljeweler.com/robots.txt",
            _fixture("nj_robots.txt"),
        ),
        NJ_SITEMAP: (NJ_SITEMAP, _fixture("nj_sitemap.xml")),
        NJ_HUB: (NJ_HUB, _fixture("nj_hub.html")),
        NJ_15291: (NJ_15291, _fixture("nj_article_full.html")),
        NJ_13197: (NJ_13197, _fixture("nj_article_reused.html")),
        NJ_15289_OLD: (NJ_15289_OLD, _fixture("nj_article_reslug.html")),
    }
    for url, headline, extra, author in [
        (
            NJ_15290,
            "Chelsea Gabrielle Turns Pilates Springs Into a Ring",
            "The one-of-a-kind ring headlines the designer's spring capsule.",
            "Natalie Francisco",
        ),
        (
            NJ_15288,
            "Crime Ring Busted in Antwerp",
            "Investigators recovered stones valued in the low seven figures.",
            "Lenore Fedow",
        ),
        (
            NJ_15287,
            "Gold Price Rally Reshapes Autumn Buying",
            "Retailers say bridal customers are sizing down center stones.",
            "Sam Reyes",
        ),
    ]:
        mapping[url] = _nj_variant(url, headline, extra, author)
    return mapping


def _ri_variant(url: str, headline: str, extra: str) -> tuple[str, bytes]:
    html = _fixture("ri_article_full.html").decode("utf-8")
    html = html.replace(
        "Watches and Wonders Shanghai, the official program now available", headline
    )
    html = html.replace(
        "https://www.richemont.com/news-media/press-releases-news/watches-and-wonders-shanghai-the-official-program-now-available/",
        url,
    )
    html = html.replace(
        "dedicated days for press, collectors, and clients of the Maisons.",
        f"dedicated days for press, collectors, and clients of the Maisons. {extra}",
    )
    return (url, html.encode("utf-8"))


def _ri_fetch_map() -> dict[str, tuple[str, bytes]]:
    mapping: dict[str, tuple[str, bytes]] = {
        "https://www.richemont.com/robots.txt": (
            "https://www.richemont.com/robots.txt",
            _fixture("ri_robots.txt"),
        ),
        RI_SITEMAP: (RI_SITEMAP, _fixture("ri_sitemap.xml")),
        RI_WATCHES: (RI_WATCHES, _fixture("ri_article_full.html")),
    }
    for url, headline, extra in [
        (
            RI_MNET,
            "Richemont signs agreement to merge its media interests",
            "The transaction remains subject to customary closing conditions.",
        ),
        (
            RI_SALES,
            "Quarterly sales update",
            "Growth was broad-based across regions and distribution channels.",
        ),
    ]:
        mapping[url] = _ri_variant(url, headline, extra)
    return mapping


def _lvmh_variant(url: str, headline: str, extra: str) -> tuple[str, bytes]:
    html = _fixture("lvmh_article_full.html").decode("utf-8")
    html = html.replace("Guerlain revisits an icon: L'Heure Bleue", headline)
    html = html.replace(
        "https://www.lvmh.com/en/news-lvmh/guerlain-revisits-an-icon-lheure-bleue",
        url,
    )
    html = html.replace(
        "tracing a century of the scent through advertising posters, flacons, and client correspondence.",
        "tracing a century of the scent through advertising posters, flacons, and client "
        f"correspondence. {extra}",
    )
    return (url, html.encode("utf-8"))


def _lvmh_fetch_map() -> dict[str, tuple[str, bytes]]:
    mapping: dict[str, tuple[str, bytes]] = {
        "https://www.lvmh.com/robots.txt": (
            "https://www.lvmh.com/robots.txt",
            _fixture("lvmh_robots.txt"),
        ),
        LVMH_INDEX: (LVMH_INDEX, _fixture("lvmh_index.xml")),
        LVMH_EN: (LVMH_EN, _fixture("lvmh_en.xml")),
        LVMH_FR: (LVMH_FR, _fixture("lvmh_fr.xml")),
        LVMH_IT: (LVMH_IT, _fixture("lvmh_it.xml")),
        LVMH_JP: (LVMH_JP, _fixture("lvmh_jp.xml")),
        LVMH_GUERLAIN: (LVMH_GUERLAIN, _fixture("lvmh_article_full.html")),
    }
    for url, headline, extra in [
        (
            LVMH_METIERS,
            "LVMH launches excellence video series in China",
            "New episodes follow apprentices in workshops across three cities.",
        ),
        (
            LVMH_VUITTON,
            "Louis Vuitton sponsors the Frick Collection",
            "The three-year program funds conservation and public access.",
        ),
        (
            LVMH_FR_NEWS,
            "Guerlain revisite une icone : L'Heure Bleue",
            "La maison celebre plus d'un siecle de parfumerie.",
        ),
        (
            LVMH_IT_NEWS,
            "Guerlain rivede un'icona: L'Heure Bleue",
            "La maison celebra oltre un secolo di profumeria.",
        ),
        (
            LVMH_JP_NEWS,
            "Guerlain icon L'Heure Bleue",
            "The maison marks a century of the scent in Asia.",
        ),
    ]:
        mapping[url] = _lvmh_variant(url, headline, extra)
    return mapping


# --- url-set lane: sitemap_pattern distills the beat --------------------------


def test_url_filter_distills_beat_silently() -> None:
    fetch = _make_fetch(_ri_fetch_map())
    urls, errors = discover_urls(
        [RI_SITEMAP],
        fetch_body=lambda u: fetch(u)[1],
        max_urls=50,
        url_filter="/press-releases-news/",
    )
    assert errors == []
    # Listing + releases match the pattern; homepage/history do not.
    # (Harvest drops the stanza hub listing itself before fetching.)
    assert [u.loc for u in urls] == [
        "https://www.richemont.com/news-media/press-releases-news/",
        RI_WATCHES,
        RI_MNET,
        RI_SALES,
    ]


def test_url_filter_none_keeps_legacy_unfiltered() -> None:
    fetch = _make_fetch(_ri_fetch_map())
    urls, _ = discover_urls([RI_SITEMAP], fetch_body=lambda u: fetch(u)[1], max_urls=50)
    assert len(urls) == 6  # homepage + listing + 3 releases + history


def test_stanza_configs_carry_url_set_routes() -> None:
    nj = get_retrieval_config(NJ)
    assert nj["type"] == "url-set+hub"
    assert nj["sitemaps"] == ["https://nationaljeweler.com/sitemap.xml"]
    assert nj["sitemap_pattern"] == "/articles/"
    assert nj["id_guard"] is True
    ri = get_retrieval_config(RI)
    assert ri["type"] == "url-set"
    assert ri["sitemaps"] == ["https://www.richemont.com/sitemap.xml"]
    assert ri["sitemap_pattern"] == "/press-releases-news/"
    assert ri["id_guard"] is False
    lvmh = get_retrieval_config(LVMH)
    assert lvmh["type"] == "sitemap"
    assert lvmh["sitemaps"] == ["https://www.lvmh.com/sitemap.xml"]
    assert lvmh["sitemap_pattern"] == "/news-lvmh/"


def test_curated_url_set_entries_verified_live() -> None:
    import json

    entries = {
        e["source_name"]: e
        for e in json.loads(
            (
                FIXTURES.parent.parent
                / ".scratch"
                / "trend-intelligence-brain"
                / "curated-sources.json"
            ).read_text(encoding="utf-8")
        )
    }
    assert entries[NJ]["retrieval"]["sitemaps"] == ["https://nationaljeweler.com/sitemap.xml"]
    assert entries[NJ]["retrieval"]["id_guard"] is True
    assert entries[NJ]["extractability"] == "clean HTML"
    assert entries[RI]["retrieval"]["sitemap_pattern"] == "/press-releases-news/"
    assert entries[LVMH]["retrieval"]["sitemap_pattern"] == "/news-lvmh/"


# --- generic metadata heuristics ----------------------------------------------


def test_entities_unescaped_in_title_and_canonical() -> None:
    from datetime import UTC, datetime

    from brain.discovery import extract_declared_canonical

    assert (
        extract_declared_canonical(
            '<link rel="canonical" href="/articles/1?a=1&amp;b=2">',
            "https://nationaljeweler.com/articles/1",
        )
        == "https://nationaljeweler.com/articles/1?a=1&b=2"
    )
    doc = extract_article(
        url="http://example.com/x",
        final_url="http://example.com/x",
        html="<html><head>"
        '<meta property="og:title" content="Van&#xA0;Cleef &amp; Arpels">'
        "</head><body><article><h1>X</h1><p>"
        + ("Body sentence. " * 40)
        + "</p></article></body></html>",
        source="S",
        language="en",
        retrieved_at=datetime(2026, 9, 6, 12, 0, tzinfo=UTC),
    )
    assert doc.title == "Van Cleef & Arpels"


def test_author_anchor_fallback_reads_byline() -> None:
    from brain.discovery import extract_author

    html = _fixture("nj_article_full.html").decode("utf-8")
    assert extract_author(html) == "Lenore Fedow"


def test_author_anchor_rejects_contact_links() -> None:
    from brain.discovery import extract_author

    html = (
        "<html><body><p>Text.</p>"
        '<a class="author-contact" href="mailto:tips@example.com">tips@example.com</a>'
        "</body></html>"
    )
    assert extract_author(html) is None


def test_time_datetime_published() -> None:
    from datetime import UTC, datetime

    from brain.discovery import extract_published_raw

    html = (
        "<html><head><title>T</title></head><body>"
        '<time datetime="2026-08-07T10:00:00+02:00">7 August 2026</time>'
        "</body></html>"
    )
    assert extract_published_raw(html) == "2026-08-07T10:00:00+02:00"
    doc = extract_article(
        url="http://example.com/x",
        final_url="http://example.com/x",
        html=html + "<article><h1>T</h1><p>" + ("Body sentence. " * 40) + "</p></article>",
        source="S",
        language="en",
        retrieved_at=datetime(2026, 9, 6, 12, 0, tzinfo=UTC),
    )
    assert doc.published_at.isoformat() == "2026-08-07T10:00:00+02:00"


# --- numeric-ID-reuse guard ----------------------------------------------------


def test_id_reuse_mismatch_skipped_never_stored() -> None:
    from datetime import UTC, datetime

    html = _fixture("nj_article_reused.html").decode("utf-8")
    with pytest.raises(ArticleExtractError, match="canonical id mismatch"):
        extract_article(
            url=NJ_13197,
            final_url=NJ_13197,
            html=html,
            source=NJ,
            language="en",
            retrieved_at=datetime(2026, 9, 6, 12, 0, tzinfo=UTC),
            id_guard=True,
        )


def test_id_reuse_stored_without_guard_base_behavior() -> None:
    from datetime import UTC, datetime

    html = _fixture("nj_article_reused.html").decode("utf-8")
    doc = extract_article(
        url=NJ_13197,
        final_url=NJ_13197,
        html=html,
        source=NJ,
        language="en",
        retrieved_at=datetime(2026, 9, 6, 12, 0, tzinfo=UTC),
    )
    assert doc.url == NJ_13197
    assert doc.canonical_url == "https://nationaljeweler.com/articles/10422-another-story-entirely"


def test_reslug_same_id_stored_with_declared_canonical() -> None:
    from datetime import UTC, datetime

    html = _fixture("nj_article_reslug.html").decode("utf-8")
    doc = extract_article(
        url=NJ_15289_OLD,
        final_url=NJ_15289_OLD,
        html=html,
        source=NJ,
        language="en",
        retrieved_at=datetime(2026, 9, 6, 12, 0, tzinfo=UTC),
        id_guard=True,
    )
    assert doc.url == NJ_15289_OLD
    assert doc.canonical_url == NJ_15289_NEW


def test_guard_ignores_id_free_slug_changes() -> None:
    from datetime import UTC, datetime

    html = _fixture("md_article_slug.html").decode("utf-8")
    doc = extract_article(
        url="https://www.marketingdirecto.com/anunciantes-general/marcas/slug-antiguo-campana-verano",
        final_url="https://www.marketingdirecto.com/anunciantes-general/marcas/slug-antiguo-campana-verano",
        html=html,
        source="MarketingDirecto",
        language="es",
        retrieved_at=datetime(2026, 9, 6, 12, 0, tzinfo=UTC),
        id_guard=True,
    )
    assert (
        doc.canonical_url
        == "https://www.marketingdirecto.com/anunciantes-general/marcas/slug-nuevo-campana-verano"
    )


# --- National Jeweler harvest (url-set + hub, generic, id guard) ---------------


def test_nj_harvest_url_set_first_hub_extends() -> None:
    sleeps: list[float] = []
    fetch = _make_fetch(_nj_fetch_map())
    report = harvest_sitemap_source(
        dict(get_retrieval_config(NJ)), NJ, "en", fetch=fetch, sleep=sleeps.append
    )
    # Sitemap newest-first, then hub-only URLs; the ID-reused sample is gone.
    assert [d.url for d in report.documents] == [
        NJ_15291,
        NJ_15290,
        NJ_15289_OLD,
        NJ_15288,
        NJ_15287,
    ]
    assert {d.language for d in report.documents} == {"en"}
    first = report.documents[0]
    assert first.title == "Orloff Jewelers Will Soon Unveil a Bigger, Better Location"
    assert first.author == "Lenore Fedow"
    assert first.published_at.isoformat() == "2026-09-06T00:00:00+00:00"
    assert first.canonical_url == NJ_15291
    assert len(first.content) >= 500
    for doc in report.documents:
        assert doc.published_at.tzinfo is not None
        assert doc.retrieved_at.tzinfo is not None
        assert doc.content_hash
    # Reslug stores under the declared canonical, same ID.
    reslug = next(d for d in report.documents if d.url == NJ_15289_OLD)
    assert reslug.canonical_url == NJ_15289_NEW
    # Stale-sample ID reuse: explicit skip, harvest continues.
    assert report.skipped == 1
    assert len(report.causes) == 1
    assert NJ_13197 in report.causes[0] and "canonical id mismatch" in report.causes[0]
    # No crawl-delay declared: stanza pacing applies.
    assert sleeps and all(s >= 1.0 for s in sleeps)


def _nj_docs() -> list[NormalizedDocument]:
    return harvest_sitemap_source(
        dict(get_retrieval_config(NJ)),
        NJ,
        "en",
        fetch=_make_fetch(_nj_fetch_map()),
        sleep=lambda _: None,
    ).documents


def test_nj_rerun_upsert_is_noop() -> None:
    from brain.ingest import upsert_documents

    docs = _nj_docs()
    conn = FakeConnection()
    assert upsert_documents(docs, conn=conn) == (5, 0)
    assert upsert_documents(docs, conn=conn) == (0, 5)
    assert len(conn.store) == 5


# --- Richemont harvest (url-set, generic) --------------------------------------


def test_ri_harvest_releases_with_full_provenance() -> None:
    log: list[str] = []
    fetch = _make_fetch(_ri_fetch_map(), log=log)
    report = harvest_sitemap_source(
        dict(get_retrieval_config(RI)), RI, "en", fetch=fetch, sleep=lambda _: None
    )
    assert [d.url for d in report.documents] == [RI_WATCHES, RI_MNET, RI_SALES]
    assert report.skipped == 0
    assert report.causes == []
    first = report.documents[0]
    assert first.title == "Watches and Wonders Shanghai, the official program now available | Media"
    assert first.canonical_url == RI_WATCHES
    assert first.published_at.isoformat() == "2026-08-07T15:00:00+00:00"
    assert len(first.content) >= 500
    assert {d.language for d in report.documents} == {"en"}
    # url-set without link_pattern: hub listing never fetched.
    assert "https://www.richemont.com/news-media/press-releases-news/" not in log


def test_ri_rerun_upsert_is_noop() -> None:
    from brain.ingest import upsert_documents

    docs = harvest_sitemap_source(
        dict(get_retrieval_config(RI)),
        RI,
        "en",
        fetch=_make_fetch(_ri_fetch_map()),
        sleep=lambda _: None,
    ).documents
    conn = FakeConnection()
    assert upsert_documents(docs, conn=conn) == (3, 0)
    assert upsert_documents(docs, conn=conn) == (0, 3)


# --- LVMH harvest (per-locale sitemap index, sitemap-only) ----------------------


def test_lvmh_harvest_covers_locales_sitemap_only() -> None:
    log: list[str] = []
    fetch = _make_fetch(_lvmh_fetch_map(), log=log)
    report = harvest_sitemap_source(
        dict(get_retrieval_config(LVMH)), LVMH, "en", fetch=fetch, sleep=lambda _: None
    )
    # Reverse index order (jp/it/fr/en), undated throughout; corporate filtered.
    assert [d.url for d in report.documents] == [
        LVMH_JP_NEWS,
        LVMH_IT_NEWS,
        LVMH_FR_NEWS,
        LVMH_GUERLAIN,
        LVMH_METIERS,
        LVMH_VUITTON,
    ]
    assert report.skipped == 0
    # Extractor verified on sitemap-discovered URLs: full generic bodies.
    for doc in report.documents:
        assert len(doc.content) >= 500
        assert doc.published_at.tzinfo is not None
    guerlain = report.documents[3]
    assert guerlain.title == "Guerlain revisits an icon: L'Heure Bleue | LVMH"
    assert guerlain.canonical_url == LVMH_GUERLAIN
    # No lastmod anywhere: published falls back to retrieval (honest, tz-aware).
    assert guerlain.published_at == guerlain.retrieved_at
    # Sitemap-only route: hub rendering never required.
    assert "https://www.lvmh.com/news-documents/press-releases/" not in log


def test_lvmh_bounded_backfill() -> None:
    fetch = _make_fetch(_lvmh_fetch_map())
    config = dict(get_retrieval_config(LVMH))
    config["max_urls"] = 2
    report = harvest_sitemap_source(config, LVMH, "en", fetch=fetch, sleep=lambda _: None)
    assert [d.url for d in report.documents] == [LVMH_JP_NEWS, LVMH_IT_NEWS]


def test_lvmh_rerun_upsert_is_noop() -> None:
    from brain.ingest import upsert_documents

    docs = harvest_sitemap_source(
        dict(get_retrieval_config(LVMH)),
        LVMH,
        "en",
        fetch=_make_fetch(_lvmh_fetch_map()),
        sleep=lambda _: None,
    ).documents
    conn = FakeConnection()
    assert upsert_documents(docs, conn=conn) == (6, 0)
    assert upsert_documents(docs, conn=conn) == (0, 6)


# --- upsert fake (mirrors the RSS lane) -----------------------------------------


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


# --- flow wiring -----------------------------------------------------------------


def _flow_fetch(monkeypatch: pytest.MonkeyPatch, mapping: dict[str, tuple[str, bytes]]) -> None:
    import brain.flows as flows

    # One seam for the whole concurrent path: planning + article workers
    # share `discovery_fetch`, so fixture I/O flows through real logic.
    fetch = _make_fetch(mapping)
    monkeypatch.setattr(flows, "discovery_fetch", lambda url, policy, gap_s: fetch(url))
    monkeypatch.setattr(flows, "enrich_document_or_keep", lambda doc, *a, **k: (doc, "rss", None))


def _flow_upsert(monkeypatch: pytest.MonkeyPatch, conn: FakeConnection) -> None:
    import brain.flows as flows
    from brain.ingest import upsert_documents

    monkeypatch.setattr(flows, "upsert_documents", lambda docs: upsert_documents(docs, conn=conn))


def test_flow_ingests_nj_with_exact_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    import brain.flows as flows

    _flow_fetch(monkeypatch, _nj_fetch_map())
    conn = FakeConnection()
    _flow_upsert(monkeypatch, conn)
    result = flows.ingest_source_flow(source_name=NJ)
    assert result["inserted"] == 5
    assert result["skipped"] == 0
    assert result["discovery_skipped"] == 1
    assert len(result["discovery_causes"]) == 1
    assert "error" not in result


def test_flow_ingests_ri_clean_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    import brain.flows as flows

    _flow_fetch(monkeypatch, _ri_fetch_map())
    conn = FakeConnection()
    _flow_upsert(monkeypatch, conn)
    assert flows.ingest_source_flow(source_name=RI) == {"inserted": 3, "skipped": 0}


def test_flow_ingests_lvmh_clean_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    import brain.flows as flows

    _flow_fetch(monkeypatch, _lvmh_fetch_map())
    conn = FakeConnection()
    _flow_upsert(monkeypatch, conn)
    assert flows.ingest_source_flow(source_name=LVMH) == {"inserted": 6, "skipped": 0}


def test_batch_ingests_all_three_and_isolates_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import brain.flows as flows
    from brain.discovery import ArticleFetchError

    maps = {NJ: _nj_fetch_map(), RI: _ri_fetch_map(), LVMH: _lvmh_fetch_map()}
    combined: dict[str, tuple[str, bytes]] = {}
    for label, mapping in maps.items():
        if label != RI:
            combined.update(mapping)
    fixture_fetch = _make_fetch(combined)

    def fake_fetch(url: str, policy: str, gap_s: float) -> tuple[str, bytes]:
        # Every Richemont fetch fails: planning finds zero URLs and raises
        # DiscoveryError, exactly the isolated per-source error.
        if urlsplit(url).netloc == "www.richemont.com":
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
    results = flows.ingest_sources_flow(source_names=[NJ, RI, LVMH])
    assert results[NJ]["inserted"] == 5
    assert "error" not in results[NJ]
    assert results[RI]["inserted"] == 0
    assert "error" in results[RI] and results[RI]["error"]
    assert results[LVMH]["inserted"] == 6
    assert "error" not in results[LVMH]
