"""Retrieval-lane tests: sitemap machinery, hub anchors, per-source routing.

Observable behavior (not privates):
- sitemap index → nested URL-set traversal, newest-first bounded backfill
- news sitemaps yield publication dates; robots crawl-delay honored
- articles carry full RSS-identical provenance (Source, canonical URL,
  tz-aware timestamps, language, content hash)
- canonical guard: declared canonical/og URL wins when same-host, else final
- rerun upsert is a no-op; one bad URL never aborts the harvest
- lane wiring: discovery-lane result shape + per-source isolation
- MarketingDirecto (ticket 29) rides the hub lane through the reader leg with
  no sitemap leg at all — the same reader-tolerant chain MarTech (ticket 27)
  proved; Jing Daily (ticket 12) keeps sitemap-first + hub-extend

All network is fixture-backed (FIXTURES); no live HTTP in tests.
"""

from __future__ import annotations

import gzip
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
from fake_transport import FakeTransport
from prefect_harness import no_engine

import marketing_intelligence.discovery as discovery
import marketing_intelligence.firecrawl as firecrawl
from marketing_intelligence.discovery import (
    DEFAULT_TIMEOUT,
    ArticleExtractError,
    ArticleFetchError,
    DiscoveryError,
    ProviderMarkdown,
    discover_urls,
    extract_article,
    fetch_sitemap_bytes,
    harvest_sitemap_source,
    parse_sitemap,
    plan_harvest,
    policy_get,
    robots_crawl_delay,
)
from marketing_intelligence.normalize import NormalizedDocument
from marketing_intelligence.sources import get_retrieval_config

FIXTURES = Path(__file__).parent / "fixtures"


MD = "MarketingDirecto"

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


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


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


def _forbid_fallbacks(monkeypatch: pytest.MonkeyPatch, seen: list[str]) -> None:
    """Stub Jina + Firecrawl to record and fail loudly if the chain reaches them."""

    def _jina(url: str, timeout: int = 30) -> tuple[str, bytes]:
        seen.append("jina")
        raise AssertionError("jina fallback must not fire")

    def _firecrawl(url: str, timeout: int = 30) -> bytes:
        seen.append("firecrawl")
        raise AssertionError("firecrawl fallback must not fire")

    monkeypatch.setattr(discovery, "_jina_reader_get", _jina)
    monkeypatch.setattr(firecrawl, "fetch_via_firecrawl", _firecrawl)


def test_policy_get_primary_success_never_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []
    _forbid_fallbacks(monkeypatch, seen)
    monkeypatch.setattr(
        discovery,
        "_impersonated_get",
        lambda url, timeout=30: (url + "?final=1", b"<html/>"),
    )
    final_url, body = policy_get("http://example.com/a", policy="impersonated-feed")
    assert final_url == "http://example.com/a?final=1"
    assert body == b"<html/>"
    assert seen == []


def test_policy_get_stdlib_only_alias_falls_back_to_jina(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``stdlib-only`` is a dead alias: a primary error still runs the chain."""
    seen: list[str] = []

    def fake_impersonated(url: str, timeout: int = 30) -> tuple[str, bytes]:
        raise ArticleFetchError(url, f"fetch failed for {url}: HTTP Error 404")

    def fake_jina(url: str, timeout: int = 30) -> tuple[str, bytes]:
        seen.append("jina")
        return (url, b"<html>via jina</html>")

    monkeypatch.setattr(discovery, "_impersonated_get", fake_impersonated)
    monkeypatch.setattr(discovery, "_jina_reader_get", fake_jina)
    monkeypatch.setattr(
        firecrawl,
        "fetch_via_firecrawl",
        lambda url, timeout=30: pytest.fail("firecrawl must not fire when jina succeeds"),
    )
    final_url, body = policy_get("http://example.com/a", policy="stdlib-only")
    assert final_url == "http://example.com/a"
    assert body == b"<html>via jina</html>"
    assert seen == ["jina"]


def test_policy_get_stdlib_only_alias_falls_back_to_firecrawl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When Jina also fails, ``stdlib-only`` reaches Firecrawl like any policy."""
    seen: list[str] = []

    def fake_impersonated(url: str, timeout: int = 30) -> tuple[str, bytes]:
        raise ArticleFetchError(url, f"fetch failed for {url}: HTTP Error 404")

    def fake_jina(url: str, timeout: int = 30) -> tuple[str, bytes]:
        seen.append("jina")
        raise ArticleFetchError(url, f"jina reader failed for {url}: HTTP Error 429")

    def fake_firecrawl(url: str, timeout: int = 30) -> bytes:
        seen.append("firecrawl")
        return b"<urlset/>"

    monkeypatch.setattr(discovery, "_impersonated_get", fake_impersonated)
    monkeypatch.setattr(discovery, "_jina_reader_get", fake_jina)
    monkeypatch.setattr(firecrawl, "fetch_via_firecrawl", fake_firecrawl)
    assert policy_get("http://example.com/a", policy="stdlib-only") == (
        "http://example.com/a",
        b"<urlset/>",
    )
    assert seen == ["jina", "firecrawl"]


def test_policy_get_primary_403_falls_back_to_jina(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []

    def fake_impersonated(url: str, timeout: int = 30) -> tuple[str, bytes]:
        raise ArticleFetchError(url, f"fetch failed for {url}: HTTP Error 403")

    def forbidden_firecrawl(url: str, timeout: int = 30) -> bytes:
        seen.append("firecrawl")
        raise AssertionError("firecrawl fallback must not fire")

    monkeypatch.setattr(discovery, "_impersonated_get", fake_impersonated)
    monkeypatch.setattr(
        discovery,
        "_jina_reader_get",
        lambda url, timeout=30: (url, b"<html>retry</html>"),
    )
    monkeypatch.setattr(firecrawl, "fetch_via_firecrawl", forbidden_firecrawl)
    final_url, body = policy_get("http://example.com/a", policy="impersonated-feed")
    assert body == b"<html>retry</html>"
    assert final_url == "http://example.com/a"
    assert seen == []


def test_policy_get_full_chain_falls_back_to_firecrawl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_impersonated(url: str, timeout: int = 30) -> tuple[str, bytes]:
        raise ArticleFetchError(url, f"fetch failed for {url}: HTTP Error 403")

    def fake_jina(url: str, timeout: int = 30) -> tuple[str, bytes]:
        raise ArticleFetchError(url, f"jina reader failed for {url}: HTTP Error 429")

    monkeypatch.setattr(discovery, "_impersonated_get", fake_impersonated)
    monkeypatch.setattr(discovery, "_jina_reader_get", fake_jina)
    monkeypatch.setattr(firecrawl, "fetch_via_firecrawl", lambda url, timeout=30: b"<urlset/>")
    assert policy_get("http://example.com/a", policy="impersonated-feed") == (
        "http://example.com/a",
        b"<urlset/>",
    )


def test_policy_get_chain_detail_lists_legs_once_and_redacts_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_impersonated(url: str, timeout: int = 30) -> tuple[str, bytes]:
        raise ArticleFetchError(url, f"fetch failed for {url}: 403 Bearer sk-live-abc123")

    def fake_jina(url: str, timeout: int = 30) -> tuple[str, bytes]:
        # The real shared reader leg raises bare causes: the chain names the leg.
        raise ArticleFetchError(url, "HTTP Error 403 fc-abcdefgh1234")

    def fake_firecrawl(url: str, timeout: int = 30) -> bytes:
        raise RuntimeError(f"firecrawl failed for {url}: rejected fc-deadbeef00")

    monkeypatch.setattr(discovery, "_impersonated_get", fake_impersonated)
    monkeypatch.setattr(discovery, "_jina_reader_get", fake_jina)
    monkeypatch.setattr(firecrawl, "fetch_via_firecrawl", fake_firecrawl)
    with pytest.raises(ArticleFetchError) as excinfo:
        policy_get("http://example.com/a", policy="impersonated-feed")
    detail = excinfo.value.detail
    # One bounded cause per leg, each leg label named exactly once (no doubled
    # leg prefixes), all credential-shaped tokens scrubbed.
    assert detail.startswith("http://example.com/a: ")
    assert detail.count("| jina reader:") == 1
    assert detail.count("| firecrawl:") == 1
    assert "Bearer" not in detail
    assert "fc-" not in detail
    assert detail.count("[redacted]") == 3


def test_policy_get_stdlib_only_without_curl_cffi_falls_back_to_jina(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A broken primary (no curl_cffi) still hands off to the Jina leg."""
    import marketing_intelligence.enrich as enrich

    seen: list[str] = []
    monkeypatch.setattr(enrich, "_curl_cffi_requests", None)

    def fake_jina(url: str, timeout: int = 30) -> tuple[str, bytes]:
        seen.append("jina")
        return (url, b"<html>via jina</html>")

    monkeypatch.setattr(discovery, "_jina_reader_get", fake_jina)
    final_url, body = policy_get("http://example.com/a", policy="stdlib-only")
    assert final_url == "http://example.com/a"
    assert body == b"<html>via jina</html>"
    assert seen == ["jina"]


# --- sitemap fetch: impersonated-only seam (ticket 25) -------------------------


SEAM_SITEMAP = "https://example.com/sitemap.xml"
SEAM_BLOCKED = "https://example.com/blocked-sitemap.xml"
SEAM_URL = "https://example.com/news/a-story"


def _seam_urlset(*locs: str) -> bytes:
    entries = "".join(
        f"<url><loc>{loc}</loc><lastmod>2026-09-04T18:30:00+02:00</lastmod></url>" for loc in locs
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{entries}</urlset>"
    ).encode()


def _sitemap_only_config(*sitemaps: str) -> dict[str, Any]:
    return {
        "type": "sitemap",
        "policy": "impersonated-feed",
        "extractor": "generic",
        "pacing_ms": 1000,
        "max_urls": 50,
        "sitemaps": list(sitemaps),
    }


def _explode_reader_legs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hard-fail every reader/scrape leg: a sitemap URL must never reach one."""

    def explode(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("reader/scrape leg must never see a sitemap URL")

    monkeypatch.setattr(discovery, "_jina_reader_get", explode)
    monkeypatch.setattr(discovery, "article_content_chain", explode)


def test_fetch_sitemap_bytes_uses_impersonated_leg_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sitemaps are fetched impersonated-only: no reader, no scrape, no chain."""
    calls: list[tuple[str, int]] = []

    def impersonated(url: str, timeout: int = 30) -> tuple[str, bytes]:
        calls.append((url, timeout))
        return (url, _seam_urlset(SEAM_URL))

    monkeypatch.setattr(discovery, "_impersonated_get", impersonated)
    _explode_reader_legs(monkeypatch)
    assert fetch_sitemap_bytes(SEAM_SITEMAP) == _seam_urlset(SEAM_URL)
    assert calls == [(SEAM_SITEMAP, DEFAULT_TIMEOUT)]


def test_reader_markdown_on_sitemap_url_is_a_challenge_cause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The live mangling (reader Markdown where XML was expected) is judged
    before the parser, so it can never surface as 'Unparseable sitemap'."""
    mangled = (
        b"Title: XML News Sitemap\n\nURL Source: "
        + SEAM_SITEMAP.encode()
        + b"\n\nMarkdown Content:\nhttps://example.com/news/a-story\n"
    )
    monkeypatch.setattr(discovery, "_impersonated_get", lambda url, timeout=30: (url, mangled))
    with pytest.raises(ArticleFetchError) as excinfo:
        fetch_sitemap_bytes(SEAM_SITEMAP)
    assert "challenge" in excinfo.value.detail
    assert "non-XML" in excinfo.value.detail


def test_html_block_page_on_sitemap_url_is_a_challenge_cause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    login_wall = (
        b"<!DOCTYPE html><html><head><title>Just a moment...</title></head><body>"
        b"Attention Required! | Cloudflare challenge \xe2\x80\x94 verify you are human</body></html>"
    )
    monkeypatch.setattr(discovery, "_impersonated_get", lambda url, timeout=30: (url, login_wall))
    with pytest.raises(ArticleFetchError) as excinfo:
        fetch_sitemap_bytes(SEAM_SITEMAP)
    detail = excinfo.value.detail
    assert "challenge" in detail and "marker" in detail


def test_gzipped_sitemap_passes_through_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gzipped = gzip.compress(_seam_urlset(SEAM_URL))
    monkeypatch.setattr(discovery, "_impersonated_get", lambda url, timeout=30: (url, gzipped))
    served = fetch_sitemap_bytes(SEAM_SITEMAP)
    assert served == gzipped  # the served bytes, not the inspection copy
    urls, _ = parse_sitemap(served)  # parse_sitemap still decompresses it
    assert [u.loc for u in urls] == [SEAM_URL]


def test_genuine_urlset_mentioning_a_marker_word_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real urlset is never misreported as a block page, even when an article
    slug contains a challenge keyword."""
    xml = _seam_urlset("https://example.com/challenge/cloudflare-captcha-story")
    monkeypatch.setattr(discovery, "_impersonated_get", lambda url, timeout=30: (url, xml))
    assert fetch_sitemap_bytes(SEAM_SITEMAP) == xml


def test_plan_harvest_parses_raw_xml_from_the_impersonated_leg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Plan-level lock: the XML parser receives raw sitemap bytes and the
    injected `fetch` (article/hub override) never sees a sitemap URL."""
    seen: list[str] = []

    def impersonated(url: str, timeout: int = 30) -> tuple[str, bytes]:
        seen.append(url)
        return (url, _seam_urlset(SEAM_URL))

    def injected_fetch(url: str) -> tuple[str, bytes]:
        assert url != SEAM_SITEMAP, f"injected fetch must not serve sitemaps: {url}"
        return (url, b"User-agent: *\nDisallow:\n")

    monkeypatch.setattr(discovery, "_impersonated_get", impersonated)
    plan = plan_harvest(
        _sitemap_only_config(SEAM_SITEMAP),
        "Example",
        "en",
        fetch=injected_fetch,
        sleep=lambda _: None,
    )
    assert [job.loc for job in plan.jobs] == [SEAM_URL]
    assert plan.causes == []
    assert seen == [SEAM_SITEMAP]


def test_reader_legs_never_see_sitemap_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    """With both reader legs hard-failed, sitemap discovery still works — no
    reader leg was ever offered the sitemap URL."""
    _explode_reader_legs(monkeypatch)
    monkeypatch.setattr(
        discovery, "_impersonated_get", lambda url, timeout=30: (url, _seam_urlset(SEAM_URL))
    )
    plan = plan_harvest(_sitemap_only_config(SEAM_SITEMAP), "Example", "en", sleep=lambda _: None)
    assert [job.loc for job in plan.jobs] == [SEAM_URL]
    assert plan.causes == []


def test_blocked_sitemap_surfaces_challenge_cause_never_unparseable_sitemap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A blocked sitemap is an explicit per-URL `challenge` cause; the sibling
    sitemap still flows and nothing reports 'Unparseable sitemap'."""
    login_wall = (
        b"<html><head><title>Just a moment...</title></head><body>"
        b"Verify you are human | cloudflare</body></html>"
    )

    def impersonated(url: str, timeout: int = 30) -> tuple[str, bytes]:
        return (url, login_wall if url == SEAM_BLOCKED else _seam_urlset(SEAM_URL))

    monkeypatch.setattr(discovery, "_impersonated_get", impersonated)
    plan = plan_harvest(
        _sitemap_only_config(SEAM_BLOCKED, SEAM_SITEMAP),
        "Example",
        "en",
        sleep=lambda _: None,
    )
    assert [job.loc for job in plan.jobs] == [SEAM_URL]
    assert any(SEAM_BLOCKED in cause and "challenge" in cause for cause in plan.causes)
    assert not any("Unparseable sitemap" in cause for cause in plan.causes)


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

    import marketing_intelligence.enrich as enrich

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


# --- hub lane: MarketingDirecto (ticket 29) -----------------------------------
# Live evidence (2026-09-12): every marketingdirecto.com sitemap URL and the
# homepage itself answer 403 to the impersonated leg, while the shared reader
# leg serves both. The curated stanza is therefore hub-only (`type: "hub"`, no
# sitemaps) and rides the same reader-tolerant chain ticket 27 proved for
# MarTech: the walled hub arrives as provider Markdown, whose image-wrapped
# listing links (`[![thumb](src) Kicker ## Headline](article)`) the shared
# extractor reads as the article target.
# `link_pattern` "-" keeps the hyphenated Spanish article slugs — but MD's
# section tokens are hyphenated too, so section fronts match as well; the
# anchored `sitemap_exclude` entries then drop exactly those listings
# (`^/digital-general/social-media-marketing$` drops the front while its
# `/…/<slug>` articles stay), which no substring pattern can express.

MD_HUB = "https://www.marketingdirecto.com/"
MD_ROBOTS = "https://www.marketingdirecto.com/robots.txt"
#: The live robots.txt declares `Crawl-delay: 10` (the stanza pacing floor)
#: beside the two sitemaps MD keeps 403-ing impersonated.
MD_ROBOTS_BODY = (
    b"User-agent: *\nCrawl-delay: 10\n\n"
    b"Sitemap: https://www.marketingdirecto.com/sitemap_index.xml\n"
    b"Sitemap: https://www.marketingdirecto.com/news-sitemap.xml\n"
)
MD_ARTICLES = (
    "https://www.marketingdirecto.com/especiales/reportajes-a-fondo/"
    "asi-creado-llyc-identidad-madring-nuevo-circuito-f1-madrid",
    "https://www.marketingdirecto.com/creacion/campanas-de-marketing/"
    "kfc-convierte-pepinillos-epitome-estilo-campana-acida-glamourosa",
    "https://www.marketingdirecto.com/anunciantes-general/"
    "ben-jerrys-convierte-helados-fundas-almohada-dormir-fresco",
    "https://www.marketingdirecto.com/digital-general/social-media-marketing/"
    "quien-brilla-mas-vasta-galaxia-redes-sociales",
)
MD_TITLES = {
    MD_ARTICLES[0]: "Así ha creado LLYC la identidad de MADRING, el nuevo circuito de F1 en Madrid",
    MD_ARTICLES[1]: (
        "KFC convierte los pepinillos en el epítome del estilo en una campaña tan ácida como glamourosa"
    ),
    MD_ARTICLES[2]: "Ben & Jerry's convierte sus helados en fundas de almohada para dormir fresco",
    MD_ARTICLES[3]: "¿Quién brilla más en la vasta galaxia de las redes sociales?",
}
#: Hub links the stanza pattern matches and that must never become jobs: the
#: section fronts (two of them have articles beneath them), the tema/asset
#: families, and the stand-alone corporate pages.
MD_NOISE_PATHS = (
    "/marketing-general",
    "/marketing-general/agencias",
    "/anunciantes-general",
    "/anunciantes-general/publicaciones",
    "/creacion/campanas-de-marketing",
    "/digital-general",
    "/digital-general/social-media-marketing",
    "/digital-general/medicion-sin-filtros-gfk",
    "/especiales/reportajes-a-fondo",
    "/especiales/cannes-lions",
    "/guias-especiales",
    "/imprescindibles/inteligencia-artificial",
    "/imprescindibles/historia-marcas",
    "/temas/burger-king",
    "/temas/campanas-navidenas",
    "/media-kit",
    "/quienes-somos",
    "/politica-cookies",
    "/punto-de-vista",
)


def _md_reader_article(url: str) -> bytes:
    """Jina-shaped provider Markdown for one walled MarketingDirecto article."""
    return (
        f"Title: {MD_TITLES[url]}\n\n"
        f"URL Source: {url}\n\n"
        "Published Time: 2026-09-11T13:00:00+00:00\n\n"
        "Author: Redacción\n\n"
        "Markdown Content:\n" + "Cobertura de marketing y publicidad en España. " * 20
    ).encode("utf-8")


def _md_fetch_map() -> dict[str, tuple[str, bytes]]:
    mapping: dict[str, tuple[str, bytes]] = {
        MD_ROBOTS: (MD_ROBOTS, MD_ROBOTS_BODY),
        MD_HUB: (MD_HUB, ProviderMarkdown(_fixture("md_hub_reader.md"))),
    }
    for url in MD_ARTICLES:
        mapping[url] = (url, ProviderMarkdown(_md_reader_article(url)))
    return mapping


def _harvest_config() -> dict[str, Any]:
    return dict(get_retrieval_config(MD))


def test_marketingdirecto_hub_links_recover_articles_and_match_site_noise() -> None:
    """The reader-Markdown hub yields the article URLs — image-wrapped listing
    links with kicker text contribute their target and relative links are
    absolutized — while bare images and off-host links are never links."""
    from urllib.parse import urlsplit

    from marketing_intelligence.discovery import extract_hub_links

    config = get_retrieval_config(MD)
    payload = _fixture("md_hub_reader.md").decode("utf-8")
    links = extract_hub_links(payload, MD_HUB, config["link_pattern"])
    assert [url for url in links if url in MD_ARTICLES] == list(MD_ARTICLES)
    assert MD_HUB not in links
    # Every noise path is matched by the pattern; only the stanza excludes
    # (asserted through the plan below) keep them from becoming jobs.
    assert set(MD_NOISE_PATHS) <= {urlsplit(url).path for url in links}
    assert not any(url.endswith((".webp", ".svg")) for url in links)  # bare images
    assert not any("eventos.marketingdirecto.com" in url for url in links)  # off-host


def test_marketingdirecto_plan_reads_the_reader_markdown_hub_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Planning goes robots → hub over the shared reader-tolerant fetch and
    never enters the impersonated-only sitemap leg (the stanza declares no
    sitemaps), so no blocked payload can ever reach the XML parser."""
    log: list[str] = []
    transport = FakeTransport.from_fetch_map(_md_fetch_map(), log=log)

    def _forbidden_sitemap_leg(url: str, timeout: int = DEFAULT_TIMEOUT) -> bytes:
        raise AssertionError(f"sitemap leg must not run for a hub-only source: {url}")

    monkeypatch.setattr(discovery, "fetch_sitemap_bytes", _forbidden_sitemap_leg)
    config = _harvest_config()
    assert config["sitemaps"] == []  # nothing rides the sitemap lane
    plan = plan_harvest(config, MD, "es", **transport.as_kwargs())
    assert [job.loc for job in plan.jobs] == list(MD_ARTICLES)
    assert plan.causes == []
    assert log == [MD_ROBOTS, MD_HUB]  # crawl-delay, then the hub listing
    assert not [url for url in log if url.endswith(".xml")]  # no sitemap traffic
    # Politeness floor: pacing 10s and robots Crawl-delay 10 (plus jitter).
    assert transport.sleep_recorder and all(s >= 10.0 for s in transport.sleep_recorder)


def test_marketingdirecto_harvest_one_bad_article_never_aborts() -> None:
    """A blocked article and a title-less page are explicit per-URL skips; the
    remaining hub-discovered articles still become documents."""
    mapping = _md_fetch_map()
    blocked = MD_ARTICLES[0]
    titleless = MD_ARTICLES[1]
    mapping[titleless] = (
        titleless,
        ProviderMarkdown(f"Title: \n\nURL Source: {titleless}\n\nMarkdown Content:\nbody".encode()),
    )
    transport = FakeTransport.from_fetch_map(
        mapping, failures={blocked: ArticleFetchError(blocked, "HTTP Error 403: Forbidden")}
    )
    report = harvest_sitemap_source(_harvest_config(), MD, "es", **transport.as_kwargs())
    assert [doc.url for doc in report.documents] == [MD_ARTICLES[2], MD_ARTICLES[3]]
    assert {doc.source for doc in report.documents} == {MD}
    assert {doc.language for doc in report.documents} == {"es"}
    assert report.skipped == 2
    assert any(blocked in cause and "403" in cause for cause in report.causes)
    assert any(titleless in cause and "missing title" in cause for cause in report.causes)
    for doc in report.documents:
        assert doc.published_at.tzinfo is not None
        assert doc.retrieved_at.tzinfo is not None
        assert doc.content_hash
        assert "Markdown Content:" not in doc.content
        assert "Title:" not in doc.content


def test_marketingdirecto_blocked_hub_is_an_explicit_cause_not_a_parse_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A blocked hub surfaces as an explicit `challenge` cause naming the URL
    and status (the keyword-less 403 lock page included), never as a bare
    status and never as `Unparseable sitemap`. No sitemap leg runs, so
    `Unparseable sitemap` cannot arise for this source at all."""

    def dead(url: str, timeout: int | None = None) -> tuple[str, bytes]:
        raise ArticleFetchError(
            url,
            f"fetch failed for {url}: HTTP Error 403: Forbidden — lock page (thin rendered text)",
        )

    def _forbidden_sitemap_leg(url: str, timeout: int = DEFAULT_TIMEOUT) -> bytes:
        raise AssertionError("no sitemap leg for a hub-only source")

    monkeypatch.setattr(discovery, "fetch_sitemap_bytes", _forbidden_sitemap_leg)
    with pytest.raises(DiscoveryError) as excinfo:
        harvest_sitemap_source(_harvest_config(), MD, "es", fetch=dead, sleep=lambda _: None)
    message = str(excinfo.value)
    assert "yielded no URLs" in message
    assert MD_HUB in message and "403" in message
    assert "challenge" in message
    assert "Unparseable sitemap" not in message


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
    transport = FakeTransport.from_fetch_map(_md_fetch_map())
    return harvest_sitemap_source(_harvest_config(), MD, "es", **transport.as_kwargs()).documents


def test_marketingdirecto_rerun_upsert_is_noop() -> None:
    """A repeat Ingestion Run inserts nothing new (rerun-safe hub lane)."""
    from marketing_intelligence.ingest import upsert_documents

    docs = _harvest_docs()
    conn = FakeConnection()
    assert upsert_documents(docs, conn=conn) == (4, 0)
    assert upsert_documents(docs, conn=conn) == (0, 4)
    assert len(conn.store) == 4


# --- flow wiring --------------------------------------------------------------


def _forbid_other_lanes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Routing proof: the RSS feed lane and the impersonated-only sitemap leg
    are never entered for MarketingDirecto."""
    import marketing_intelligence.flows as flows

    def _forbidden(lane: str) -> Any:
        def fake(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError(f"MarketingDirecto must not enter the {lane}")

        return fake

    monkeypatch.setattr(flows, "fetch_rss", _forbidden("RSS feed lane"))
    monkeypatch.setattr(
        discovery, "fetch_sitemap_bytes", _forbidden("impersonated-only sitemap lane")
    )


def test_flow_ingests_hub_source_with_exact_shape(
    monkeypatch: pytest.MonkeyPatch, no_engine: None
) -> None:
    """Lane routing: the Ingestion Run plans the hub, inserts every article,
    and keeps the clean {inserted, skipped} shape."""
    import marketing_intelligence.flows as flows

    fixture_fetch = FakeTransport.from_fetch_map(_md_fetch_map()).fetch
    # One seam for planning + article workers, exactly as the flow wires it.
    monkeypatch.setattr(flows, "discovery_fetch", lambda url, policy, gap_s: fixture_fetch(url))
    _forbid_other_lanes(monkeypatch)
    monkeypatch.setattr(flows, "enrich_document_or_keep", lambda doc, *a, **k: (doc, "rss", None))
    conn = FakeConnection()
    from marketing_intelligence.ingest import upsert_documents

    monkeypatch.setattr(flows, "upsert_documents", lambda docs: upsert_documents(docs, conn=conn))
    result = flows.ingest_source_flow(source_name=MD)
    assert result == {"inserted": 4, "skipped": 0}
    assert len(conn.store) == 4


def test_flow_clean_harvest_keeps_exact_shape(
    monkeypatch: pytest.MonkeyPatch, no_engine: None
) -> None:
    """One blocked article: the run keeps its shape, adds discovery_skipped
    and its explicit per-URL cause, and never reports an error."""
    import marketing_intelligence.flows as flows

    blocked = MD_ARTICLES[1]
    transport = FakeTransport.from_fetch_map(
        _md_fetch_map(), failures={blocked: ArticleFetchError(blocked, "HTTP Error 403: Forbidden")}
    )
    monkeypatch.setattr(flows, "discovery_fetch", lambda url, policy, gap_s: transport.fetch(url))
    _forbid_other_lanes(monkeypatch)
    monkeypatch.setattr(flows, "enrich_document_or_keep", lambda doc, *a, **k: (doc, "rss", None))
    conn = FakeConnection()
    from marketing_intelligence.ingest import upsert_documents

    monkeypatch.setattr(flows, "upsert_documents", lambda docs: upsert_documents(docs, conn=conn))
    result = flows.ingest_source_flow(source_name=MD)
    assert result["inserted"] == 3
    assert result["skipped"] == 0
    assert result["discovery_skipped"] == 1
    assert len(result["discovery_causes"]) == 1
    assert blocked in result["discovery_causes"][0]
    assert "error" not in result


def test_batch_isolates_hub_failure(monkeypatch: pytest.MonkeyPatch, no_engine: None) -> None:
    """Per-source isolation: a blocked hub fails only MarketingDirecto; the
    neighbouring RSS source still ingests under the same batch."""
    import marketing_intelligence.flows as flows
    from marketing_intelligence.sources import get_source

    transport = FakeTransport.from_fetch_map(_md_fetch_map())

    def failing_hub(url: str) -> tuple[str, bytes]:
        if url == MD_HUB:
            raise ArticleFetchError(url, "HTTP Error 403: Forbidden")
        return transport.fetch(url)

    monkeypatch.setattr(flows, "discovery_fetch", lambda url, policy, gap_s: failing_hub(url))
    monkeypatch.setattr(flows, "enrich_document_or_keep", lambda doc, *a, **k: (doc, "rss", None))
    shared: dict[str, FakeConnection] = {}

    def fake_upsert(docs: list[NormalizedDocument]) -> tuple[int, int]:
        from marketing_intelligence.ingest import upsert_documents

        conn = shared.setdefault(docs[0].source, FakeConnection())
        return upsert_documents(docs, conn=conn)

    monkeypatch.setattr(flows, "upsert_documents", fake_upsert)

    pj_url = get_source("Professional Jeweller")["rss_url"]
    pj_feed = (FIXTURES / "pj_sample.xml").read_bytes()

    def fake_fetch_rss(url: str, timeout: int = 30) -> bytes:
        if url == pj_url:
            return pj_feed
        raise AssertionError(f"unexpected RSS fetch: {url}")

    monkeypatch.setattr(flows, "fetch_rss", fake_fetch_rss)
    results = flows.ingest_sources_flow(source_names=[MD, "Professional Jeweller"])
    assert results[MD]["inserted"] == 0
    assert "error" in results[MD]
    assert "error" not in results["Professional Jeweller"]
    assert results["Professional Jeweller"]["inserted"] > 0


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
    from marketing_intelligence.discovery import extract_hub_links

    links = extract_hub_links(_fixture("jd_hub.html").decode("utf-8"), JD_HUB, "/posts/")
    assert links == [
        JD_YVMIN,
        JD_PAGANI,
        JD_K11,
        JD_VELVET,
        JD_ROULETTE,
    ]


def test_hub_links_dedupe_and_absolutize() -> None:
    from marketing_intelligence.discovery import extract_hub_links

    # Relative hrefs absolutize against the hub; the repeated Yvmin link
    # collapses; /intels/, /tags/, and off-host anchors are excluded.
    links = extract_hub_links(_fixture("jd_hub.html").decode("utf-8"), JD_HUB, "/posts/")
    assert len(links) == len(set(links)) == 5
    assert all(h.startswith("https://jingdaily.com/posts/") for h in links)


def test_discover_hub_urls_aggregates_pagination() -> None:
    from marketing_intelligence.discovery import discover_hub_urls

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
    from marketing_intelligence.discovery import discover_hub_urls

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
    from marketing_intelligence.discovery import extract_json_ld_body
    from marketing_intelligence.enrich import clean_to_markdown, is_thin

    html = _fixture("jd_article_full.html").decode("utf-8")
    generic = clean_to_markdown(html, JD_YVMIN)
    assert is_thin(generic)  # premise: generic extraction goes thin here
    body = extract_json_ld_body(html)
    assert body is not None
    assert len(body) > 1000
    assert "playful luxury" in body


def test_json_ld_ignores_stub_blocks_without_body() -> None:
    from marketing_intelligence.discovery import extract_json_ld_body

    html = _fixture("jd_article_full.html").decode("utf-8")
    body = extract_json_ld_body(html) or ""
    # The related-story stub ("gold prices") must not win over the article.
    assert "gold prices" not in body.lower()
    assert "Yvmin" in body


def test_json_ld_missing_falls_back_to_generic() -> None:
    from marketing_intelligence.discovery import extract_json_ld_body

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


def test_jing_harvest_sitemap_first_hub_extends(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = FakeTransport.from_fetch_map(
        _jing_fetch_map(),
        failures={JD_ROULETTE: ArticleFetchError(JD_ROULETTE, "HTTP Error 403")},
    )
    transport.install_sitemap_seam(monkeypatch)
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


def _jing_docs(monkeypatch: pytest.MonkeyPatch) -> list[NormalizedDocument]:
    transport = FakeTransport.from_fetch_map(
        _jing_fetch_map(),
        failures={JD_ROULETTE: ArticleFetchError(JD_ROULETTE, "HTTP Error 403")},
    )
    transport.install_sitemap_seam(monkeypatch)
    return harvest_sitemap_source(_jing_config(), JD, "en", **transport.as_kwargs()).documents


def test_jing_rerun_upsert_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    from marketing_intelligence.ingest import upsert_documents

    docs = _jing_docs(monkeypatch)
    conn = FakeConnection()
    assert upsert_documents(docs, conn=conn) == (7, 0)
    assert upsert_documents(docs, conn=conn) == (0, 7)
    assert len(conn.store) == 7


def test_jing_metered_body_kept_with_cause_then_flaggable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Thin threshold governs keep-vs-flag: metered doc kept, cause kept,
    and the Extraction Flag path accepts it (reason + detail + reporter)."""
    import marketing_intelligence.enrich as enrich
    import marketing_intelligence.flag as flag_lane

    docs = _jing_docs(monkeypatch)
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

    import marketing_intelligence.article as article_mod

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
    import marketing_intelligence.flows as flows

    mapping = _jing_fetch_map()
    failures = {JD_ROULETTE: ArticleFetchError(JD_ROULETTE, "HTTP Error 403")}
    fixture_fetch = _make_fetch(mapping, failures=failures)
    monkeypatch.setattr(flows, "discovery_fetch", lambda url, policy, gap_s: fixture_fetch(url))
    monkeypatch.setattr(discovery, "_impersonated_get", lambda url, timeout=30: fixture_fetch(url))
    monkeypatch.setattr(flows, "enrich_document_or_keep", lambda doc, *a, **k: (doc, "rss", None))
    conn = FakeConnection()
    from marketing_intelligence.ingest import upsert_documents

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


ARCHIVE_CHILD_SITEMAP = "https://dive.example/news/archive/2016/september.xml"
FRESH_CHILD_SITEMAP = "https://dive.example/news/sitemap-news.xml"
FRESH_CHILD_URL_1 = "https://dive.example/news/fresh-story-one/"
FRESH_CHILD_URL_2 = "https://dive.example/news/fresh-story-two/"

#: Pinned clock: the 2016 archive child is far outside the 60-day freshness
#: window the ticket-26 recency guard allows, so exclusion stays deterministic.
NOW = datetime(2026, 9, 12, tzinfo=UTC)


def test_discover_sitemap_exclude_skips_stale_child_sitemap_subtree() -> None:
    """Ticket 26: an excluded index child (a Dive ``/news/archive/`` tree) is
    never fetched; the bound refills from the fresh child's entries."""
    index = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        b"<sitemap><loc>" + ARCHIVE_CHILD_SITEMAP.encode() + b"</loc></sitemap>"
        b"<sitemap><loc>" + FRESH_CHILD_SITEMAP.encode() + b"</loc></sitemap>"
        b"</sitemapindex>"
    )
    fresh = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        b"<url><loc>" + FRESH_CHILD_URL_1.encode() + b"</loc>"
        b"<lastmod>2026-09-02T08:30:00+00:00</lastmod></url>"
        b"<url><loc>" + FRESH_CHILD_URL_2.encode() + b"</loc>"
        b"<lastmod>2026-09-01T08:30:00+00:00</lastmod></url></urlset>"
    )
    bodies = {"https://dive.example/sitemap.xml": index, FRESH_CHILD_SITEMAP: fresh}
    log: list[str] = []

    def fetch_body(url: str) -> bytes:
        log.append(url)
        return bodies[url]  # a stale-child fetch would KeyError

    urls, errors = discover_urls(
        ["https://dive.example/sitemap.xml"],
        fetch_body=fetch_body,
        max_urls=2,
        sitemap_exclude=["/archive/"],
        now=NOW,
    )
    assert errors == []
    assert [u.loc for u in urls] == [FRESH_CHILD_URL_1, FRESH_CHILD_URL_2]
    assert ARCHIVE_CHILD_SITEMAP not in log


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


def test_harvest_sitemap_exclude_webstories_never_fetched_bad_urls_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    monkeypatch.setattr(discovery, "_impersonated_get", lambda url, timeout=30: fetch(url))
    config: dict[str, Any] = {
        "type": "sitemap",
        "policy": "impersonated-feed",
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


def test_batch_isolates_jing_failure(monkeypatch: pytest.MonkeyPatch, no_engine: None) -> None:
    import marketing_intelligence.flows as flows

    # The whole discovery lane hard-fails: planning raises out of the
    # discover subflow, and the batch records the explicit per-source error.
    monkeypatch.setattr(
        flows,
        "plan_harvest",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("hub down")),
    )
    monkeypatch.setattr(flows, "enrich_document_or_keep", lambda doc, *a, **k: (doc, "rss", None))
    fixture = (FIXTURES / "infomoney_sample.xml").read_bytes()
    monkeypatch.setattr(flows, "fetch_rss", lambda url, timeout=30: fixture)

    def fake_upsert(docs: list[NormalizedDocument]) -> tuple[int, int]:
        return (len(docs), 0)

    monkeypatch.setattr(flows, "upsert_documents", fake_upsert)
    results = flows.ingest_sources_flow(source_names=["InfoMoney", JD])
    assert results["InfoMoney"] == {"inserted": 3, "skipped": 0}
    assert results[JD]["inserted"] == 0
    assert "error" in results[JD] and results[JD]["error"]


# --- ticket 27: MarTech hub lane through the reader-tolerant policy chain -----

MT = "MarTech"
MT_HUB = "https://martech.org/"
MT_ROBOTS = "https://martech.org/robots.txt"
MT_FEED = "https://martech.org/feed/"

#: Root-level article slugs recovered from the hub payload, in document order
#: (the fixture repeats one anchor and writes another relative, both of which
#: must collapse/absolutize to these three).
MT_ARTICLES = (
    "https://martech.org/marketing-without-signals-how-to-perform-when-the-data-disappears/",
    "https://martech.org/identity-resolution-vendors-compared-2026-buyers-guide/",
    "https://martech.org/ai-agents-move-from-pilots-to-marketing-workflows/",
)

MT_TITLES = dict(
    zip(
        MT_ARTICLES,
        (
            "Marketing Without Signals: How to Perform When the Data Disappears",
            "Identity Resolution Vendors Compared: 2026 Buyer's Guide",
            "AI Agents Move From Pilots to Marketing Workflows",
        ),
        strict=True,
    )
)


def _mt_article_html(url: str) -> bytes:
    title = MT_TITLES[url]
    return (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        f"<title>{title} | MarTech</title>"
        f'<link rel="canonical" href="{url}">'
        f'<meta property="og:title" content="{title}">'
        '<meta name="author" content="Kim Davis">'
        '<meta property="article:published_time" content="2026-09-11T13:00:00+00:00">'
        "</head><body><article><p>"
        + " ".join(["Marketing technology operations coverage."] * 20)
        + "</p></article></body></html>"
    ).encode("utf-8")


def _mt_fetch_map() -> dict[str, tuple[str, bytes]]:
    mapping: dict[str, tuple[str, bytes]] = {
        MT_ROBOTS: (MT_ROBOTS, b"User-agent: *\nDisallow:\n"),
        MT_HUB: (MT_HUB, _fixture("martech_hub.html")),
    }
    for url in MT_ARTICLES:
        mapping[url] = (url, _mt_article_html(url))
    return mapping


def test_martech_hub_anchors_recover_articles_and_drop_site_noise() -> None:
    """The hub payload's nav/taxonomy/corporate anchors never become articles:
    the stanza link pattern plus its excludes yield exactly the article slugs."""
    from urllib.parse import urlsplit

    from marketing_intelligence.discovery import extract_hub_links

    config = get_retrieval_config(MT)
    links = extract_hub_links(
        _fixture("martech_hub.html").decode("utf-8"), MT_HUB, config["link_pattern"]
    )
    assert MT_HUB not in links  # the site root carries no slug hyphen
    # Observable behavior at the planning seam (never a copy of the exclude
    # predicate): the produced job set is exactly the article slugs, so a
    # broken exclude rule fails here.
    plan = plan_harvest(config, MT, "en", fetch=_make_fetch(_mt_fetch_map()), sleep=lambda _: None)
    planned = [job.loc for job in plan.jobs]
    assert planned == list(MT_ARTICLES)
    # Taxonomy/author/corporate anchors were matched then excluded; the site
    # root and bare archive indices carry no hyphen and never match at all.
    dropped = [url for url in links if url not in set(planned)]
    assert any("/topic/" in urlsplit(url).path for url in dropped)
    assert any("/author/" in urlsplit(url).path for url in dropped)
    assert any("/about-martech-org/" in urlsplit(url).path for url in dropped)
    assert not any("/page/2/" in url or MT_FEED in url for url in links)


def test_martech_plan_recovers_articles_without_a_sitemap_or_feed_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Planning reads the hub through the injected policy fetch (the shared
    reader-tolerant chain) and never touches the impersonated-only sitemap leg,
    the dead feed, or any feed XML."""
    log: list[str] = []
    fetch = _make_fetch(_mt_fetch_map(), log=log)

    def _forbidden_sitemap_leg(url: str, timeout: int = DEFAULT_TIMEOUT) -> bytes:
        raise AssertionError(f"sitemap leg must not run for a hub-only source: {url}")

    monkeypatch.setattr(discovery, "fetch_sitemap_bytes", _forbidden_sitemap_leg)
    plan = plan_harvest(
        get_retrieval_config(MT),
        MT,
        "en",
        fetch=fetch,
        sleep=lambda _: None,
    )
    assert [job.loc for job in plan.jobs] == list(MT_ARTICLES)
    assert plan.causes == []
    assert log[0] == MT_ROBOTS  # robots crawl-delay first, then the hub listing
    assert log[1] == MT_HUB
    assert MT_FEED not in log  # the dead feed URL is never fetched in any lane
    assert not [url for url in log if url.endswith(".xml")]  # no sitemap traffic


def test_martech_harvest_one_bad_article_never_aborts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failing fetch and a title-less page are explicit per-URL skips; the
    remaining hub-discovered articles still become documents."""
    mapping = _mt_fetch_map()
    bad_fetch = MT_ARTICLES[1]
    bad_page = MT_ARTICLES[2]
    mapping[bad_page] = (bad_page, b"<html><body><p>no title here</p></body></html>")
    fetch = _make_fetch(
        mapping,
        failures={bad_fetch: ArticleFetchError(bad_fetch, "HTTP Error 403: Forbidden")},
    )
    report = harvest_sitemap_source(
        get_retrieval_config(MT), MT, "en", fetch=fetch, sleep=lambda _: None
    )
    assert [doc.url for doc in report.documents] == [MT_ARTICLES[0]]
    assert report.documents[0].source == MT
    assert report.documents[0].language == "en"
    assert report.skipped == 2
    assert any(bad_fetch in cause and "403" in cause for cause in report.causes)
    assert any(bad_page in cause and "missing title" in cause for cause in report.causes)


def test_martech_flow_takes_the_hub_lane_not_the_dead_feed(
    monkeypatch: pytest.MonkeyPatch, no_engine: None
) -> None:
    """Lane routing proof: the MarTech Ingestion Run plans the hub, so the RSS
    feed lane and the impersonated-only sitemap leg are never entered."""
    import marketing_intelligence.flows as flows

    fetch = _make_fetch(_mt_fetch_map())
    # One seam for planning + article workers, exactly as the flow wires it.
    monkeypatch.setattr(flows, "discovery_fetch", lambda url, policy, gap_s: fetch(url))

    def _forbidden(lane: str) -> Any:
        def fake(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError(f"MarTech must not enter the {lane}")

        return fake

    monkeypatch.setattr(flows, "fetch_rss", _forbidden("RSS feed lane"))
    monkeypatch.setattr(
        discovery, "fetch_sitemap_bytes", _forbidden("impersonated-only sitemap lane")
    )
    monkeypatch.setattr(flows, "enrich_document_or_keep", lambda doc, *a, **k: (doc, "rss", None))
    written: list[NormalizedDocument] = []

    def fake_upsert(docs: list[NormalizedDocument]) -> tuple[int, int]:
        written.extend(docs)
        return (len(docs), 0)

    monkeypatch.setattr(flows, "upsert_documents", fake_upsert)
    result = flows.ingest_source_flow(source_name=MT)
    assert result == {"inserted": 3, "skipped": 0}
    assert [doc.url for doc in written] == list(MT_ARTICLES)
    assert {doc.source for doc in written} == {MT}


# --- ticket 27: reader-leg payloads (Cloudflare walls every impersonated leg) ---


def _mt_reader_markdown(url: str, title: str) -> bytes:
    """Jina-shaped provider Markdown for one article (Title/URL/Published/Author)."""
    return (
        f"Title: {title}\n\n"
        f"URL Source: {url}\n\n"
        "Published Time: 2026-09-11T13:00:00+00:00\n\n"
        "Author: Kim Davis\n\n"
        "Markdown Content:\n" + "Marketing technology operations coverage. " * 20
    ).encode("utf-8")


def _mt_reader_fetch_map() -> dict[str, tuple[str, bytes]]:
    mapping: dict[str, tuple[str, bytes]] = {
        MT_ROBOTS: (MT_ROBOTS, b"User-agent: *\nDisallow:\n"),
        MT_HUB: (MT_HUB, ProviderMarkdown(_fixture("martech_hub_reader.md"))),
    }
    for url in MT_ARTICLES:
        mapping[url] = (url, ProviderMarkdown(_mt_reader_markdown(url, MT_TITLES[url])))
    return mapping


def test_hub_links_match_reader_markdown_payload() -> None:
    """The reader leg serves hub listings as provider Markdown (no anchors):
    Markdown links feed the same same-host/link-pattern filter, images do not."""
    config = get_retrieval_config(MT)
    links = discovery.extract_hub_links(
        _fixture("martech_hub_reader.md").decode("utf-8"),
        MT_HUB,
        config["link_pattern"],
    )
    assert links[0] == MT_ARTICLES[0]  # [![img](src)](target): the link target
    assert MT_ARTICLES[1] in links  # relative Markdown link, absolutized
    assert MT_ARTICLES[2] in links
    assert links.count(MT_ARTICLES[0]) == 1  # image link + text link dedupe
    assert not any(url.endswith(".png") for url in links)  # bare images are not links
    assert not any("example.com" in url for url in links)  # off-host dropped
    kept = [
        url for url in links if not any(x in urlsplit(url).path for x in config["sitemap_exclude"])
    ]
    assert kept == list(MT_ARTICLES)


def test_martech_plan_recovers_articles_from_reader_markdown_hub() -> None:
    """The walled hub's reader-leg Markdown still plans the article set."""
    fetch = _make_fetch(_mt_reader_fetch_map())
    plan = plan_harvest(get_retrieval_config(MT), MT, "en", fetch=fetch, sleep=lambda _: None)
    assert [job.loc for job in plan.jobs] == list(MT_ARTICLES)
    assert plan.causes == []


def test_reader_markdown_article_stores_header_block_as_provenance() -> None:
    """Reader Markdown supplies title/timestamp/author, the stored body drops
    the provider header block, and the article keeps its planned identity."""
    from marketing_intelligence.discovery import ArticleJob, fetch_extract_one

    title = MT_TITLES[MT_ARTICLES[0]]
    payload = _mt_reader_markdown(MT_ARTICLES[0], title)
    job = ArticleJob(
        loc=MT_ARTICLES[0],
        source_label=MT,
        retrieved_at_iso="2026-09-12T00:00:00+00:00",
        extractor="generic",
    )
    doc, cause = fetch_extract_one(job, fetch_one=lambda url: (url, ProviderMarkdown(payload)))
    assert cause is None
    assert doc is not None
    assert doc.title == title  # from the reader "Title:" line, not the URL
    assert doc.author == "Kim Davis"
    assert doc.published_at.isoformat() == "2026-09-11T13:00:00+00:00"
    assert doc.url == MT_ARTICLES[0]  # provider hop (r.jina.ai) is never the identity
    assert doc.canonical_url == MT_ARTICLES[0]
    assert "Markdown Content:" not in doc.content
    assert "Title:" not in doc.content
    assert doc.content.startswith("Marketing technology operations coverage.")


def test_reader_leg_keeps_the_requested_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """The reader base is a provider hop: the fetch identity stays the target."""
    from marketing_intelligence import enrich

    target = MT_ARTICLES[0]
    monkeypatch.setattr(
        enrich,
        "fetch_reader",
        lambda url, timeout=30: (
            "https://r.jina.ai/" + url,
            b"Title: T\n\nMarkdown Content:\nbody",
        ),
    )
    final_url, body = discovery._jina_reader_get(target)
    assert final_url == target
    assert b"Markdown Content:" in body


def test_martech_flow_ingests_when_every_fetch_rides_the_reader_leg(
    monkeypatch: pytest.MonkeyPatch, no_engine: None
) -> None:
    """Live wall (Cloudflare 403 on every impersonated leg): hub and article
    payloads arrive as reader Markdown and the Ingestion Run still inserts."""
    import marketing_intelligence.flows as flows

    fetch = _make_fetch(_mt_reader_fetch_map())
    monkeypatch.setattr(flows, "discovery_fetch", lambda url, policy, gap_s: fetch(url))
    monkeypatch.setattr(flows, "enrich_document_or_keep", lambda doc, *a, **k: (doc, "rss", None))
    written: list[NormalizedDocument] = []

    def fake_upsert(docs: list[NormalizedDocument]) -> tuple[int, int]:
        written.extend(docs)
        return (len(docs), 0)

    monkeypatch.setattr(flows, "upsert_documents", fake_upsert)
    result = flows.ingest_source_flow(source_name=MT)
    assert result == {"inserted": 3, "skipped": 0}
    assert [doc.url for doc in written] == list(MT_ARTICLES)
    assert [doc.title for doc in written] == [MT_TITLES[url] for url in MT_ARTICLES]
    assert all("Markdown Content:" not in doc.content for doc in written)
