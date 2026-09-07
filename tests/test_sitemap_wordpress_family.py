"""WordPress-family rollout + Modaes (Ticket 09).

Observable behavior (not privates), on the proven 08 path
(sitemap -> generic extraction, newest-first bounded):
- registry carries the live-verified sitemap route per Source with
  correct language codes (es/pt via existing normalize_language)
- Modaes ingests full bodies with no bypass; tracker label corrected
  to cookie-consent wall
- per-source fixture harvest: newest-first docs with full provenance,
  one bad URL is an explicit skip, rerun upsert is a no-op
- flow wiring: sitemap lane result shape + per-source isolation

All network is fixture-backed; no live HTTP in tests. Live routes were
verified 2026-09-06 during implementation (Yoast wp-sitemap.xml 301s to
sitemap_index.xml; Exame robots declares sitemap.xml; Modaes robots
declares sitemap_current.xml with Crawl-delay 3).
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest

from brain.discovery import (
    ArticleFetchError,
    DiscoveryError,
    discover_urls,
    harvest_sitemap_source,
)
from brain.normalize import NormalizedDocument
from brain.sources import get_retrieval_config, get_source

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


SOURCES: dict[str, dict[str, Any]] = {
    "Propmark": {
        "prefix": "pm",
        "language": "pt",
        "sitemaps": ["https://propmark.com.br/wp-sitemap.xml"],
        "child_old": "https://propmark.com.br/wp-sitemap-posts-post-1.xml",
        "child_new": "https://propmark.com.br/wp-sitemap-posts-post-2.xml",
        "url_a": "https://propmark.com.br/anunciantes/mary-kay-leva-as-ruas-brasileiras-acao-global-sobre-beleza-compartilhada/",
        "lastmod_a": "2026-09-05T10:20:00-03:00",
        "title_a": "Mary Kay leva às ruas brasileiras ação global sobre beleza compartilhada",
        "author_a": "Mariana Duarte",
        "url_c": "https://propmark.com.br/premiacao/festival-de-criatividade-anuncia-shortlist-recorde/",
        "url_bad": "https://propmark.com.br/anunciantes/pagina-que-falha-sempre/",
    },
    "Insider Latam": {
        "prefix": "il",
        "language": "es",
        "sitemaps": ["https://insiderlatam.com/sitemap_index.xml"],
        "child_old": "https://insiderlatam.com/post-sitemap.xml",
        "child_new": "https://insiderlatam.com/post-sitemap2.xml",
        "url_a": "https://insiderlatam.com/viviana-martinez-explica-como-campari-convierte-la-cocteleria-en-una-plataforma-de-marca/",
        "lastmod_a": "2026-09-05T11:00:00-05:00",
        "title_a": "Viviana Martínez explica cómo Campari convierte la coctelería en una plataforma de marca",
        "author_a": "Diego Salazar",
        "url_c": "https://insiderlatam.com/retail-media-se-consolida-como-tercer-gran-canal-publicitario-de-la-region/",
        "url_bad": "https://insiderlatam.com/pagina-que-falla-siempre/",
    },
    "Forbes México": {
        "prefix": "fm",
        "language": "es",
        "sitemaps": ["https://forbes.com.mx/sitemap_index.xml"],
        "child_old": "https://forbes.com.mx/post-sitemap.xml",
        "child_new": "https://forbes.com.mx/post-sitemap2.xml",
        "url_a": "https://forbes.com.mx/mientras-la-moda-atraviesa-dificultades-la-joyeria-contribuira-a-definir-a-los-ganadores-del-sector-del-lujo/",
        "lastmod_a": "2026-09-05T09:45:00-06:00",
        "title_a": "Mientras la moda atraviesa dificultades, la joyería contribuirá a definir a los ganadores del lujo",
        "author_a": "Fernanda Ruiz",
        "url_c": "https://forbes.com.mx/nearshoring-impulsa-la-inversion-en-centros-logisticos-del-norte-del-pais/",
        "url_bad": "https://forbes.com.mx/pagina-que-falla-siempre/",
    },
    "Consumidor Moderno": {
        "prefix": "cm",
        "language": "pt",
        "sitemaps": ["https://consumidormoderno.com.br/sitemap_index.xml"],
        "child_old": "https://consumidormoderno.com.br/post-sitemap.xml",
        "child_new": "https://consumidormoderno.com.br/post-sitemap2.xml",
        "url_a": "https://consumidormoderno.com.br/experiencia-sensorial-tendencia-marcas/",
        "lastmod_a": "2026-09-05T12:00:00-03:00",
        "title_a": "Experiência sensorial vira tendência entre marcas do varejo",
        "author_a": "Patrícia Lemos",
        "url_c": "https://consumidormoderno.com.br/programas-de-fidelidade-apostam-em-personalizacao-em-tempo-real/",
        "url_bad": "https://consumidormoderno.com.br/pagina-que-falha-sempre/",
    },
    "Meio & Mensagem": {
        "prefix": "mm",
        "language": "pt",
        "sitemaps": ["https://www.meioemensagem.com.br/sitemap_index.xml"],
        "child_old": "https://www.meioemensagem.com.br/post-sitemap.xml",
        "child_new": "https://www.meioemensagem.com.br/post-sitemap2.xml",
        "url_a": "https://www.meioemensagem.com.br/marketing/mercado-livre-que-fortalecer-moda-com-pandora",
        "lastmod_a": "2026-09-05T13:15:00-03:00",
        "title_a": "Mercado Livre quer fortalecer moda com Pandora",
        "author_a": "Camila Ferraz",
        "url_c": "https://www.meioemensagem.com.br/comunicacao/agencias-independentes-ganham-espaco-nas-contas-de-varejo",
        "url_bad": "https://www.meioemensagem.com.br/pagina-que-falha-sempre",
    },
    "Exame": {
        "prefix": "ex",
        "language": "pt",
        "sitemaps": ["https://exame.com/sitemap.xml"],
        "child_old": "https://exame.com/marketing/sitemap.xml",
        "child_new": "https://exame.com/negocios/sitemap.xml",
        "url_a": "https://exame.com/casual/a-brilhante-disputa-entre-diamantes-naturais-e-sinteticos/",
        "lastmod_a": "2026-09-05T08:30:00-03:00",
        "title_a": "A brilhante disputa entre diamantes naturais e sintéticos",
        "author_a": "Juliana Castro",
        "url_c": "https://exame.com/negocios/varejo-de-luxo-acelera-expansao-no-interior-de-sao-paulo/",
        "url_bad": "https://exame.com/negocios/pagina-que-falha-sempre/",
    },
    "Modaes": {
        "prefix": "mo",
        "language": "es",
        "sitemaps": ["https://www.modaes.com/sitemap_current.xml"],
        "child_old": None,
        "child_new": None,
        "url_a": "https://www.modaes.com/empresas/nike-pierde-su-lugar-en-el-sp-100-con-la-cotizacion-en-minimos-de-doce-anos",
        "lastmod_a": "2026-09-06T18:43:47+02:00",
        "title_a": "Nike pierde su lugar en el SP 100 con la cotización en mínimos de doce años",
        "author_a": "Redacción Modaes",
        "url_c": "https://www.modaes.com/entorno/el-lujo-vuelve-a-perder-impulso-en-china-por-las-tasas-a-los-altos-patrimonios",
        "url_bad": "https://www.modaes.com/empresas/pagina-que-falla-siempre",
    },
}

LABELS = list(SOURCES)


def _host(label: str) -> str:
    return urlsplit(SOURCES[label]["sitemaps"][0]).netloc


def _fetch_map(label: str) -> dict[str, tuple[str, bytes]]:
    spec = SOURCES[label]
    prefix = spec["prefix"]
    host = _host(label)
    mapping: dict[str, tuple[str, bytes]] = {
        f"https://{host}/robots.txt": (
            f"https://{host}/robots.txt",
            _fixture(f"{prefix}_robots.txt"),
        ),
        spec["url_a"]: (spec["url_a"], _fixture(f"{prefix}_article_a.html")),
        spec["url_c"]: (spec["url_c"], _fixture(f"{prefix}_article_c.html")),
    }
    if spec["child_old"] is None:  # Modaes: robots-declared flat urlset
        mapping[spec["sitemaps"][0]] = (
            spec["sitemaps"][0],
            _fixture(f"{prefix}_sitemap_current.xml"),
        )
    else:
        mapping[spec["sitemaps"][0]] = (
            spec["sitemaps"][0],
            _fixture(f"{prefix}_sitemap_index.xml"),
        )
        mapping[spec["child_old"]] = (
            spec["child_old"],
            _fixture(f"{prefix}_post_sitemap.xml"),
        )
        mapping[spec["child_new"]] = (
            spec["child_new"],
            _fixture(f"{prefix}_post_sitemap2.xml"),
        )
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


def _harvest(label: str, **overrides: Any) -> Any:
    spec = SOURCES[label]
    fetch = _make_fetch(
        _fetch_map(label),
        failures={spec["url_bad"]: ArticleFetchError(spec["url_bad"], "HTTP Error 403: Forbidden")},
    )
    config = dict(get_retrieval_config(label))
    config.update(overrides)
    return harvest_sitemap_source(
        config,
        label,
        str(get_source(label)["language"]),
        fetch=fetch,
        sleep=lambda _: None,
    )


# --- registry ---------------------------------------------------------------


@pytest.mark.parametrize("label", LABELS)
def test_registry_carries_correct_language_code(label: str) -> None:
    assert get_source(label)["language"] == SOURCES[label]["language"]


@pytest.mark.parametrize("label", LABELS)
def test_registry_sitemap_stanza_is_live_verified_route(label: str) -> None:
    config = get_retrieval_config(label)
    assert config["type"] == "sitemap"
    assert config["policy"] == "stdlib-only"
    assert config["extractor"] == "generic"
    assert config["sitemaps"] == SOURCES[label]["sitemaps"]


def test_modaes_tracker_label_corrected_to_cookie_consent_wall() -> None:
    raw = get_source("Modaes")["raw"]
    assert "cookie-consent" in raw["extractability"]
    assert "Paywall blocks body" not in raw["extractability"]
    assert get_retrieval_config("Modaes")["policy"] == "stdlib-only"


# --- harvest ----------------------------------------------------------------


@pytest.mark.parametrize("label", LABELS)
def test_harvest_end_to_end_newest_first_with_explicit_skip(label: str) -> None:
    spec = SOURCES[label]
    report = _harvest(label)
    assert [d.url for d in report.documents] == [spec["url_a"], spec["url_c"]]
    assert {d.language for d in report.documents} == {spec["language"]}
    newest = report.documents[0]
    assert newest.source == label
    assert newest.title == spec["title_a"]
    assert newest.author == spec["author_a"]
    assert newest.canonical_url == spec["url_a"]
    assert newest.published_at.isoformat() == spec["lastmod_a"]
    for doc in report.documents:
        assert doc.published_at.tzinfo is not None
        assert doc.retrieved_at.tzinfo is not None
        assert doc.content_hash
        assert "<" not in doc.content
    assert report.skipped == 1
    assert len(report.causes) == 1 and spec["url_bad"] in report.causes[0]


@pytest.mark.parametrize("label", LABELS)
def test_harvest_bounded_backfill(label: str) -> None:
    report = _harvest(label, max_urls=1)
    assert [d.url for d in report.documents] == [SOURCES[label]["url_a"]]
    assert report.skipped == 0


def test_discover_newest_child_first_stops_early() -> None:
    spec = SOURCES["Propmark"]
    log: list[str] = []
    mapping = _fetch_map("Propmark")
    fetch = _make_fetch(mapping, log=log)
    bodies = {url: body for url, body in mapping.values()}
    assert bodies  # fixture map is non-empty; traversal below goes through `fetch`
    urls, errors = discover_urls(spec["sitemaps"], fetch_body=lambda u: fetch(u)[1], max_urls=2)
    assert errors == []
    assert [u.loc for u in urls] == [spec["url_a"], spec["url_bad"]]
    assert spec["child_new"] in log
    assert spec["child_old"] not in log


def test_harvest_raises_when_nothing_discovered() -> None:
    def dead(url: str) -> tuple[str, bytes]:
        raise ArticleFetchError(url, "HTTP Error 403: Forbidden")

    with pytest.raises(DiscoveryError, match="yielded no URLs"):
        harvest_sitemap_source(
            dict(get_retrieval_config("Exame")),
            "Exame",
            "pt",
            fetch=dead,
            sleep=lambda _: None,
        )


def test_modaes_ingests_full_bodies_with_no_bypass() -> None:
    report = _harvest("Modaes")
    assert len(report.documents) == 2
    for doc in report.documents:
        assert len(doc.content) >= 500
        assert "cookie" not in doc.content.lower() or len(doc.content) >= 500


def test_modaes_robots_crawl_delay_honored() -> None:
    sleeps: list[float] = []
    spec = SOURCES["Modaes"]
    fetch = _make_fetch(
        _fetch_map("Modaes"),
        failures={spec["url_bad"]: ArticleFetchError(spec["url_bad"], "HTTP Error 403")},
    )
    harvest_sitemap_source(
        dict(get_retrieval_config("Modaes")),
        "Modaes",
        "es",
        fetch=fetch,
        sleep=sleeps.append,
    )
    assert sleeps and all(s >= 3.0 for s in sleeps)


def test_wordpress_default_pacing_honored() -> None:
    sleeps: list[float] = []
    spec = SOURCES["Propmark"]
    fetch = _make_fetch(
        _fetch_map("Propmark"),
        failures={spec["url_bad"]: ArticleFetchError(spec["url_bad"], "HTTP Error 403")},
    )
    harvest_sitemap_source(
        dict(get_retrieval_config("Propmark")),
        "Propmark",
        "pt",
        fetch=fetch,
        sleep=sleeps.append,
    )
    assert sleeps and all(s >= 1.0 for s in sleeps)


# --- upsert / rerun (FakeConnection mirrors the RSS lane) --------------------


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
    assert len(docs) == 2
    conn = FakeConnection()
    assert upsert_documents(docs, conn=conn) == (2, 0)
    assert upsert_documents(docs, conn=conn) == (0, 2)
    assert len(conn.store) == 2


# --- flow wiring ------------------------------------------------------------


def test_exame_registry_carries_webstories_exclude_with_full_budget() -> None:
    """Ticket 14: the Exame stanza excludes /webstories/ while keeping the
    full max_urls=50 backfill budget for genuine articles."""
    config = get_retrieval_config("Exame")
    assert config["sitemap_exclude"] == ["/webstories/"]
    assert config["max_urls"] == 50


def test_exame_harvest_excludes_webstories_before_budget_with_explicit_skips() -> None:
    """Ticket 14 end-to-end on the registry stanza: webstories (newest in the
    urlset) are excluded before the budget applies, the genuine article still
    inserts, and the 404 + title-less page are explicit per-URL skips."""
    from brain.discovery import ArticleFetchError as _FetchError
    from brain.discovery import harvest_sitemap_source as _harvest

    ws_new = "https://exame.com/webstories/resumo-do-dia-novo/"
    ws_old = "https://exame.com/webstories/resumo-do-dia-antigo/"
    real = SOURCES["Exame"]["url_a"]
    bad = SOURCES["Exame"]["url_bad"]
    no_title = "https://exame.com/negocios/pagina-sem-titulo/"
    sitemap_url = "https://exame.com/polluted-sitemap.xml"
    host = _host("Exame")
    sitemap_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"<url><loc>{ws_new}</loc><lastmod>2026-09-06T12:00:00-03:00</lastmod></url>"
        f"<url><loc>{ws_old}</loc><lastmod>2026-09-06T11:00:00-03:00</lastmod></url>"
        f"<url><loc>{real}</loc><lastmod>2026-09-05T08:30:00-03:00</lastmod></url>"
        f"<url><loc>{bad}</loc><lastmod>2026-09-04T09:00:00-03:00</lastmod></url>"
        f"<url><loc>{no_title}</loc><lastmod>2026-09-03T09:00:00-03:00</lastmod></url>"
        "</urlset>"
    ).encode()
    log: list[str] = []
    mapping = {
        f"https://{host}/robots.txt": (
            f"https://{host}/robots.txt",
            _fixture("ex_robots.txt"),
        ),
        sitemap_url: (sitemap_url, sitemap_xml),
        real: (real, _fixture("ex_article_a.html")),
        no_title: (no_title, b"<html><body><p>no title here</p></body></html>"),
    }
    fetch = _make_fetch(
        mapping,
        log=log,
        failures={bad: _FetchError(bad, f"fetch failed for {bad}: HTTP Error 404: Not Found")},
    )
    config = dict(get_retrieval_config("Exame"))
    config["sitemaps"] = [sitemap_url]
    config["max_urls"] = 3
    report = _harvest(config, "Exame", "pt", fetch=fetch, sleep=lambda _: None)
    # Budget of 3 covers the genuine article plus both bad URLs (webstories
    # excluded first — without exclusion the budget would hold ws_new,
    # ws_old and real); the 404 + title-less page are explicit skips,
    # never silent drops.
    assert [d.url for d in report.documents] == [real]
    assert report.documents[0].title == SOURCES["Exame"]["title_a"]
    assert report.skipped == 2
    assert any(bad in c and "404" in c for c in report.causes)
    assert any(no_title in c and "missing title" in c for c in report.causes)
    assert not any("/webstories/" in u for u in log)


def test_flow_ingests_sitemap_source_with_exact_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import brain.flows as flows

    label = "Propmark"
    spec = SOURCES[label]
    mapping = _fetch_map(label)
    failures = {spec["url_bad"]: ArticleFetchError(spec["url_bad"], "HTTP Error 403: Forbidden")}
    fixture_fetch = _make_fetch(mapping, failures=failures)
    # One seam for the whole concurrent path: planning + article workers
    # share `discovery_fetch`, so fixture I/O flows through real logic.
    monkeypatch.setattr(flows, "discovery_fetch", lambda url, policy, gap_s: fixture_fetch(url))
    monkeypatch.setattr(flows, "enrich_document_or_keep", lambda doc, *a, **k: (doc, "rss", None))
    conn = FakeConnection()
    from brain.ingest import upsert_documents

    monkeypatch.setattr(flows, "upsert_documents", lambda docs: upsert_documents(docs, conn=conn))
    result = flows.ingest_source_flow(source_name=label)
    assert result["inserted"] == 2
    assert result["skipped"] == 0
    assert result["discovery_skipped"] == 1
    assert len(result["discovery_causes"]) == 1
    assert "error" not in result


def test_batch_ingests_all_seven_and_isolates_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import brain.flows as flows

    maps = {label: _fetch_map(label) for label in LABELS}
    exame_host = _host("Exame")
    combined: dict[str, tuple[str, bytes]] = {}
    failures: dict[str, Exception] = {}
    for label in LABELS:
        if label == "Exame":
            continue
        spec = SOURCES[label]
        combined.update(maps[label])
        failures[spec["url_bad"]] = ArticleFetchError(spec["url_bad"], "HTTP Error 403")
    fixture_fetch = _make_fetch(combined, failures=failures)

    def fake_fetch(url: str, policy: str, gap_s: float) -> tuple[str, bytes]:
        # Every Exame fetch fails: planning finds zero URLs and raises
        # DiscoveryError, exactly the isolated per-source error.
        if urlsplit(url).netloc == exame_host:
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
    for label in LABELS:
        if label == "Exame":
            assert results[label]["inserted"] == 0
            assert "error" in results[label]
        else:
            assert results[label]["inserted"] == 2
            assert "error" not in results[label]
