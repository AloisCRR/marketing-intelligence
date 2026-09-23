"""Ticket 26: stale / non-article sitemap excludes (Dive family, Meio & Mensagem,
Insider Latam).

Observable behavior (not privates), on the proven 08/09/14 path
(sitemap -> exclude -> bounded backfill):

- Retail Dive, Marketing Dive (``/archive/`` plus the ``/topic/`` section
  fronts) and Meio & Mensagem (``/podcasts/``, ``/patrocinado/``) declare their
  excludes in the curated stanza, and they survive normalization into
  :func:`get_retrieval_config` without disturbing any other stanza key or the
  pre-existing Exame exclude
- Insider Latam declares its taxonomy/archive routes (``/tag/``,
  ``/category/``, ``/archivos/``, ``/archivo/``, ``/author/``, ``/page/``),
  which reach the retrieval config with the sitemap list, pacing and bound
  untouched
- the exclude drops matching URLs *before* the ``max_urls`` backfill
  bound: without it the stale route consumes the whole budget and the
  fresh route is never reached; with it the budget refills with genuine
  articles
- excluded URLs are never fetched as articles and never surface as
  per-URL fetch failures (no ``causes``, no ``skipped``)

The stale children here are 2012/2014 (and the Exame webstories urls live
under a non-dated path): explicit ``NOW`` pins the ticket-26 recency guard
so the assertions never ride the wall clock — these children stay outside
the 60-day freshness window forever.

All network is stub-backed (in-memory sitemaps); no live HTTP in tests.
The excluded slugs (``/archive/``, ``/topic/``, ``/podcasts/``,
``/patrocinado/``, and the Insider Latam taxonomy routes) are the ones the
live routes expose (checked 2026-09-12).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest

import marketing_intelligence.discovery as discovery
from marketing_intelligence.discovery import discover_urls, harvest_sitemap_source
from marketing_intelligence.sources import get_retrieval_config

FIXTURES = Path(__file__).parent / "fixtures"

#: Declared ``sitemap_exclude`` per source: a bare string stands for the
#: one-entry list ``get_retrieval_config`` normalizes it into.
STANZA_EXCLUDE: dict[str, str | list[str]] = {
    "Retail Dive": ["/archive/", "/topic/"],
    "Marketing Dive": ["/archive/", "/topic/"],
    "Meio & Mensagem": ["/podcasts/", "/patrocinado/"],
    "Insider Latam": [
        "/tag/",
        "/category/",
        "/archivos/",
        "/archivo/",
        "/author/",
        "/page/",
    ],
}

#: Per-source stub routes. ``index`` is the stanza's first declared sitemap;
#: ``declared`` is the sitemap list discovery walks; ``index_children`` is the
#: index's document order (real indexes list newest first, and discovery walks
#: children reversed, so the stale child is reached first); ``fresh`` is the
#: genuine-articles route reached only once the stale one is excluded.
CASES: dict[str, dict[str, Any]] = {
    "Retail Dive": {
        "language": "en",
        "index": "https://www.retaildive.com/sitemap.xml",
        "declared": (
            "https://www.retaildive.com/sitemap.xml",
            "https://www.retaildive.com/google_news_sitemap.xml",
        ),
        "index_children": ("https://www.retaildive.com/news/archive/2012/december.xml",),
        "stale_child": "https://www.retaildive.com/news/archive/2012/december.xml",
        "stale": (
            "https://www.retaildive.com/news/archive/2012/12/29/j-crew-ceo-defends-online-shopping/85387/",
            "https://www.retaildive.com/news/archive/2012/12/29/walmart-pledges-safeguards/85384/",
            "https://www.retaildive.com/news/archive/2012/12/28/office-depot-retains-ranking/85280/",
        ),
        "fresh": "https://www.retaildive.com/google_news_sitemap.xml",
        "genuine": (
            "https://www.retaildive.com/news/reformation-first-earnings-double-store-fleet/830162/",
            "https://www.retaildive.com/news/nordstrom-alum-takes-the-ceo-reins/830155/",
            "https://www.retaildive.com/news/destination-xl-turnaround-plan-q2/830143/",
        ),
    },
    "Marketing Dive": {
        "language": "en",
        "index": "https://www.marketingdive.com/sitemap.xml",
        "declared": (
            "https://www.marketingdive.com/sitemap.xml",
            "https://www.marketingdive.com/google_news_sitemap.xml",
        ),
        "index_children": ("https://www.marketingdive.com/news/archive/2014/september.xml",),
        "stale_child": "https://www.marketingdive.com/news/archive/2014/september.xml",
        "stale": (
            "https://www.marketingdive.com/news/archive/2014/09/30/brand-recap-september/90501/",
            "https://www.marketingdive.com/news/archive/2014/09/29/campaign-of-the-week/90488/",
            "https://www.marketingdive.com/news/archive/2014/09/25/media-buying-briefing/90452/",
        ),
        "fresh": "https://www.marketingdive.com/google_news_sitemap.xml",
        "genuine": (
            "https://www.marketingdive.com/news/pepsico-hands-global-media-to-publicis/829556/",
            "https://www.marketingdive.com/news/nike-splits-global-creative-between-shops/829521/",
            "https://www.marketingdive.com/news/coca-cola-holiday-campaign-creators/829530/",
        ),
    },
    "Meio & Mensagem": {
        "language": "pt",
        "index": "https://www.meioemensagem.com.br/sitemap_index.xml",
        "declared": ("https://www.meioemensagem.com.br/sitemap_index.xml",),
        # Newest-first document order; reversed traversal reaches the
        # podcast child first.
        "index_children": (
            "https://www.meioemensagem.com.br/post-news.xml",
            "https://www.meioemensagem.com.br/podcast-sitemap.xml",
        ),
        "stale_child": "https://www.meioemensagem.com.br/podcast-sitemap.xml",
        "stale": (
            "https://www.meioemensagem.com.br/podcasts/women-to-watch/lideranca-e-gestao/",
            "https://www.meioemensagem.com.br/podcasts/women-to-watch/vacinas-e-gestao/",
            "https://www.meioemensagem.com.br/podcasts/podcasts/serie-criatividade-6a-temporada/",
        ),
        "fresh": "https://www.meioemensagem.com.br/post-news.xml",
        "genuine": (
            "https://www.meioemensagem.com.br/midia/sbt-confirma-fim-de-contrato-com-rodrigo-bocardi",
            "https://www.meioemensagem.com.br/marketing/msp-estudios-colecao-de-halloween",
            "https://www.meioemensagem.com.br/comunicacao/campanhas-da-semana-conexao-humana",
        ),
    },
}

LABELS = list(CASES)
MAX_URLS = 2

#: Pinned reference clock for the discovery recency guard: the 2012/2014
#: archive children and the undated podcast child all fall outside the
#: 60-day freshness window, so exclusion is asserted time-independently.
NOW = datetime(2026, 9, 12, tzinfo=UTC)


def _urlset(entries: tuple[str, ...]) -> bytes:
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    ]
    for loc in entries:
        parts.append(f"<url><loc>{loc}</loc></url>")
    parts.append("</urlset>")
    return "".join(parts).encode("utf-8")


def _index(children: tuple[str, ...]) -> bytes:
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    ]
    for loc in children:
        parts.append(f"<sitemap><loc>{loc}</loc></sitemap>")
    parts.append("</sitemapindex>")
    return "".join(parts).encode("utf-8")


def _case(label: str) -> dict[str, Any]:
    return {**CASES[label], "label": label}


def _bodies(case: dict[str, Any]) -> dict[str, bytes]:
    return {
        case["index"]: _index(tuple(case["index_children"])),
        case["stale_child"]: _urlset(tuple(case["stale"])),
        case["fresh"]: _urlset(tuple(case["genuine"])),
    }


def _declared_exclude(label: str) -> list[str]:
    """The stanza's declared exclude as ``get_retrieval_config`` returns it."""
    declared = STANZA_EXCLUDE[label]
    return [declared] if isinstance(declared, str) else list(declared)


# --- registry: the stanza excludes reach the retrieval config -----------------


@pytest.mark.parametrize("label", LABELS)
def test_stanza_declares_exclude_through_retrieval_config(label: str) -> None:
    config = get_retrieval_config(label)
    assert config["sitemap_exclude"] == _declared_exclude(label)
    assert config["max_urls"] == 50  # backfill bound unchanged by the exclude


#: A Meio & Mensagem sponsored-content landing path: a section front, not an
#: article, so the stanza excludes it alongside the podcast routes.
MEIO_SPONSORED_URL = "https://www.meioemensagem.com.br/patrocinado/sebrae-conteudo-especial/"


def test_meio_sponsored_section_is_excluded_before_fetch() -> None:
    """``/patrocinado/`` is declared and applied like the podcast routes: the
    exclude helper discovery runs over every candidate URL drops a sponsored
    URL, so it consumes no backfill budget and is never fetched."""
    exclude = get_retrieval_config("Meio & Mensagem")["sitemap_exclude"]
    assert "/patrocinado/" in exclude
    # The helper discovery applies to urlset entries and traversed children.
    assert discovery._path_excluded(discovery._path_of(MEIO_SPONSORED_URL), exclude)

    declared = CASES["Meio & Mensagem"]["declared"][0]
    article = CASES["Meio & Mensagem"]["genuine"][0]
    # Sponsored URL first: without the exclude it takes the single slot.
    bodies = {declared: _urlset((MEIO_SPONSORED_URL, article))}
    fetched: list[str] = []

    def fetch_body(url: str) -> bytes:
        fetched.append(url)
        return bodies[url]

    urls, errors = discover_urls(
        [declared],
        fetch_body=fetch_body,
        max_urls=1,
        sitemap_exclude=exclude,
        now=NOW,
    )
    assert errors == []
    assert [u.loc for u in urls] == [article]
    assert MEIO_SPONSORED_URL not in fetched


#: A Dive ``/topic/`` section front: a hub listing, not an article, so both
#: Dive stanzas exclude it alongside the ``/archive/`` trees.
DIVE_TOPIC_URLS = {
    "Retail Dive": "https://www.retaildive.com/topic/consumer-trends/",
    "Marketing Dive": "https://www.marketingdive.com/topic/social-media/",
}


@pytest.mark.parametrize("label", ["Retail Dive", "Marketing Dive"])
def test_dive_topic_listing_is_excluded_before_fetch(label: str) -> None:
    """The ``/topic/`` front is declared and applied like ``/archive/``: it
    loses the only backfill slot to the article behind it and is never
    fetched (the hub/tag ingestion reported by the production feedback)."""
    exclude = get_retrieval_config(label)["sitemap_exclude"]
    assert "/topic/" in exclude
    # The declared order keeps the pre-existing archive entry first.
    assert exclude == _declared_exclude(label)

    listing = DIVE_TOPIC_URLS[label]
    article = CASES[label]["genuine"][0]
    sitemap = f"https://{urlsplit(article).netloc}/news_sitemap.xml"
    bodies = {sitemap: _urlset((listing, article))}

    # Bug premise: without the exclude the listing takes the only slot.
    stale, errors = discover_urls([sitemap], fetch_body=bodies.__getitem__, max_urls=1)
    assert errors == []
    assert [u.loc for u in stale] == [listing]

    fetched: list[str] = []

    def fetch_body(url: str) -> bytes:
        fetched.append(url)
        return bodies[url]

    urls, errors = discover_urls(
        [sitemap],
        fetch_body=fetch_body,
        max_urls=1,
        sitemap_exclude=exclude,
        now=NOW,
    )
    assert errors == []
    assert [u.loc for u in urls] == [article]
    assert listing not in fetched


#: Insider Latam taxonomy/archive routes: hub, tag and date listings, not
#: articles.
IL_LISTING_URLS = (
    "https://insiderlatam.com/tag/retail-media/",
    "https://insiderlatam.com/category/marketing/",
    "https://insiderlatam.com/archivos/2024/",
    "https://insiderlatam.com/archivo/2023/",
    "https://insiderlatam.com/author/diego-salazar/",
    "https://insiderlatam.com/page/2/",
)
IL_ARTICLE_URL = (
    "https://insiderlatam.com/retail-media-se-consolida-como-tercer-gran-canal-"
    "publicitario-de-la-region/"
)


def test_insider_latam_listing_routes_are_excluded_before_fetch() -> None:
    """Every declared taxonomy/archive route is dropped before the bound, so
    the budget refills with the article behind them, none of the listings is
    fetched, and the rest of the stanza keeps its established shape."""
    config = get_retrieval_config("Insider Latam")
    assert config["sitemap_exclude"] == _declared_exclude("Insider Latam")
    # Merge, never overwrite: sitemap list, pacing and bound are unchanged.
    assert config["sitemaps"] == ["https://insiderlatam.com/sitemap_index.xml"]
    assert config["pacing_ms"] == 1000
    assert config["max_urls"] == 50

    sitemap = "https://insiderlatam.com/post-sitemap.xml"
    bodies = {sitemap: _urlset((*IL_LISTING_URLS, IL_ARTICLE_URL))}

    # Bug premise: without the exclude the listings fill the whole budget.
    stale, errors = discover_urls(
        [sitemap], fetch_body=bodies.__getitem__, max_urls=len(IL_LISTING_URLS)
    )
    assert errors == []
    assert [u.loc for u in stale] == list(IL_LISTING_URLS)

    fetched: list[str] = []

    def fetch_body(url: str) -> bytes:
        fetched.append(url)
        return bodies[url]

    urls, errors = discover_urls(
        [sitemap],
        fetch_body=fetch_body,
        max_urls=len(IL_LISTING_URLS),
        sitemap_exclude=config["sitemap_exclude"],
        now=NOW,
    )
    assert errors == []
    assert [u.loc for u in urls] == [IL_ARTICLE_URL]
    assert not set(fetched) & set(IL_LISTING_URLS)


def test_stanza_excludes_leave_other_stanzas_untouched() -> None:
    """Merge, never overwrite: Exame keeps its webstories exclude, and
    stanzas without an exclude keep their exact established shape."""
    assert get_retrieval_config("Exame")["sitemap_exclude"] == ["/webstories/"]
    assert "sitemap_exclude" not in get_retrieval_config("Consumidor Moderno")


def test_curated_copies_stay_byte_identical() -> None:
    """The dev override (.scratch) and the shipped copy must not drift."""
    root = FIXTURES.parent.parent
    scratch = root / ".scratch" / "marketing-intelligence" / "curated-sources.json"
    shipped = root / "src" / "marketing_intelligence" / "data" / "curated-sources.json"
    assert scratch.read_bytes() == shipped.read_bytes()


# --- discovery seam: excludes drop URLs before the max_urls bound -------------


@pytest.mark.parametrize("label", LABELS)
def test_discover_without_stanza_exclude_spends_budget_on_stale(label: str) -> None:
    """Bug premise: without the exclude, the stale route is walked first
    and consumes the whole backfill bound."""
    case = _case(label)
    bodies = _bodies(case)
    urls, errors = discover_urls(
        list(case["declared"]),
        fetch_body=bodies.__getitem__,
        max_urls=MAX_URLS,
    )
    assert errors == []
    assert [u.loc for u in urls] == list(case["stale"][:MAX_URLS])


@pytest.mark.parametrize("label", LABELS)
def test_discover_stanza_exclude_refills_budget_with_articles(label: str) -> None:
    """The registry exclude drops the stale URLs before the bound applies,
    so the budget refills with genuine articles and the fresh route is
    reached; excluded URLs are never fetched."""
    case = _case(label)
    bodies = _bodies(case)
    log: list[str] = []
    config = get_retrieval_config(label)

    def fetch_body(url: str) -> bytes:
        log.append(url)
        return bodies[url]

    urls, errors = discover_urls(
        list(case["declared"]),
        fetch_body=fetch_body,
        max_urls=MAX_URLS,
        sitemap_exclude=config["sitemap_exclude"],
        now=NOW,
    )
    assert errors == []
    assert [u.loc for u in urls] == list(case["genuine"][:MAX_URLS])
    assert not set(log) & set(case["stale"])


# --- harvest: excluded URLs are never fetched as articles --------------------


@pytest.mark.parametrize("label", LABELS)
def test_harvest_never_fetches_excluded_articles(
    label: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end with the registry stanza: the excluded URLs never reach the
    article fetch, and they are silent drops, not per-URL fetch failures."""
    case = _case(label)
    host = urlsplit(case["index"]).netloc
    article = (FIXTURES / "md_article_canva.html").read_bytes()
    sitemaps = _bodies(case)
    bodies: dict[str, bytes] = {f"https://{host}/robots.txt": b"User-agent: *\nDisallow:\n"}
    for url in case["genuine"]:
        bodies[url] = article
    if label in ("Retail Dive", "Marketing Dive"):
        hub = get_retrieval_config(label)["hub"]
        bodies[hub] = b"<html><body><p>listing without matching anchors</p></body></html>"

    # Sitemap traversal is impersonated-only (never the injected seam);
    # `fetch` stays the robots/hub/article override.
    monkeypatch.setattr(
        discovery, "_impersonated_get", lambda url, timeout=30: (url, sitemaps[url])
    )

    log: list[str] = []

    def fetch(url: str) -> tuple[str, bytes]:
        log.append(url)
        if url in case["stale"]:
            raise AssertionError(f"excluded URL was fetched as an article: {url}")
        return (url, bodies[url])

    config = dict(get_retrieval_config(label))
    config["max_urls"] = MAX_URLS
    report = harvest_sitemap_source(
        config, label, case["language"], fetch=fetch, sleep=lambda _: None, now=NOW
    )

    assert [d.url for d in report.documents] == list(case["genuine"][:MAX_URLS])
    assert report.skipped == 0
    assert report.causes == []
    assert not set(log) & set(case["stale"])
