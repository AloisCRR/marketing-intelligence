"""Ticket 28: Swarovski PR Newswire dead RSS feed → hub-lane routing.

The curated registry keeps Swarovski's (dead, HTTP 404) RSS stanza untouched;
the code-level lane override in :mod:`marketing_intelligence.ingest` routes
its Ingestion Run into the *existing* ``plan_harvest`` hub path on the
reachable PR Newswire hub — no second hub implementation, no wasted fetch of
the dead feed, no reader leg for feed XML.

Covered here:
- the routing seam (dead RSS stanza resolves to a hub-lane config),
- the discovery seam (stub PR Newswire hub HTML → real article URLs),
- the Ingestion Run (inserts real articles, never touches the dead URL),
- partial failure and per-source isolation (unchanged flow behavior),
- the regression pin (every other source's stanza resolves identically).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from prefect_harness import no_engine

import marketing_intelligence.flows as flows
from marketing_intelligence.discovery import ArticleFetchError, plan_harvest
from marketing_intelligence.ingest import apply_retrieval_override, feed_candidate_urls
from marketing_intelligence.sources import get_retrieval_config, get_source, list_sources

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures"
SHIPPED_REGISTRY = ROOT / "src" / "marketing_intelligence" / "data" / "curated-sources.json"

SWAROVSKI = "Swarovski PR Newswire"
DEAD_RSS = "https://www.prnewswire.com/rss/swarovski"
HUB = "https://www.prnewswire.com/news/swarovski/"
LINK_PATTERN = ".html"
ROBOTS = "https://www.prnewswire.com/robots.txt"
ARTICLES = (
    "https://www.prnewswire.com/news-releases/swarovski-unveils-aurora-collection-302100001.html",
    "https://www.prnewswire.com/news-releases/swarovski-opens-flagship-in-paris-302100002.html",
    "https://www.prnewswire.com/news-releases/swarovski-annual-results-302100003.html",
)


def _effective_config() -> dict[str, Any]:
    return apply_retrieval_override(SWAROVSKI, get_retrieval_config(SWAROVSKI))


def _article_html(url: str, title: str) -> bytes:
    return (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        f"<title>{title}</title>"
        f'<link rel="canonical" href="{url}">'
        '<meta property="article:published_time" content="2026-09-10T09:15:00+00:00">'
        "</head><body><article>"
        f"<h1>{title}</h1>"
        f"<p>{title}: the jeweller reported quarterly growth across every region, driven "
        "by high-jewellery demand, a wider store footprint, and new lab-grown lines.</p>"
        "<p>Management reiterated its full-year outlook and pointed to double-digit "
        "growth in the Americas and Asia-Pacific.</p>"
        "</article></body></html>"
    ).encode()


def _fetch_map() -> dict[str, tuple[str, bytes]]:
    hub = (FIXTURES / "prn_hub.html").read_bytes()
    mapping: dict[str, tuple[str, bytes]] = {
        ROBOTS: (ROBOTS, b"User-agent: *\nDisallow:\n"),
        HUB: (HUB, hub),
    }
    for index, url in enumerate(ARTICLES):
        mapping[url] = (url, _article_html(url, f"Swarovski story number {index}"))
    return mapping


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


def _patch_flow_seams(
    monkeypatch: Any,
    mapping: dict[str, tuple[str, bytes]],
    log: list[str] | None = None,
    failures: dict[str, Exception] | None = None,
) -> None:
    """Route planning + article workers through the stub PR Newswire payloads."""
    fetch = _make_fetch(mapping, log=log, failures=failures)
    monkeypatch.setattr(flows, "discovery_fetch", lambda url, policy, gap_s: fetch(url))
    monkeypatch.setattr(flows, "enrich_document_or_keep", lambda doc, *a, **k: (doc, "rss", None))
    monkeypatch.setattr(flows, "upsert_documents", lambda docs: (len(docs), 0))


def _forbid_feed_lane(monkeypatch: Any) -> None:
    """Any RSS-lane fetch for Swarovski is a routing bug: fail loudly, offline."""

    def _forbidden(url: str, timeout: int = 30) -> bytes:
        raise AssertionError(f"dead RSS lane must not be used: {url}")

    monkeypatch.setattr(flows, "fetch_rss", _forbidden)


# --- routing seam ---------------------------------------------------------------


def test_dead_swarovski_feed_resolves_to_hub_lane_config() -> None:
    """The unit seam: the override swaps the dead RSS stanza for a hub stanza."""
    effective = _effective_config()
    assert effective["type"] == "hub"
    assert effective["hub"] == HUB
    assert effective["link_pattern"] == LINK_PATTERN
    assert effective["policy"] == "impersonated-feed"
    # The full discovery stanza shape survives the overlay.
    assert effective["extractor"] == "generic"
    assert effective["sitemaps"] == []
    assert effective["hub_pages"] == []
    assert effective["max_urls"] == 50


def test_registry_stanza_still_reads_rss_while_runtime_routes_to_hub() -> None:
    """Curated registry untouched: the shipped JSON still declares rss."""
    src = get_source(SWAROVSKI)
    assert src["rss_url"] == DEAD_RSS
    assert get_retrieval_config(SWAROVSKI)["type"] == "rss"
    entries = json.loads(SHIPPED_REGISTRY.read_text(encoding="utf-8"))
    entry = next(e for e in entries if e["source_name"] == SWAROVSKI)
    assert entry["rss_url"] == DEAD_RSS
    assert entry["hub_url"] == HUB
    # The stanza is the plain dead-RSS declaration — no routing correction.
    assert entry["retrieval"] == {"type": "rss", "policy": "impersonated-feed"}
    # And the runtime lane (code-level) is hub, from the same registry input.
    assert _effective_config()["type"] == "hub"


def test_other_sources_resolve_identically() -> None:
    """Regression: only Swarovski is overridden; the object stays identical."""
    for entry in list_sources():
        name = str(entry["name"])
        if name == SWAROVSKI:
            continue
        config = get_retrieval_config(name)
        assert apply_retrieval_override(name, config) is config, name
    # Healthy RSS sources keep the exact single-candidate contract.
    healthy = get_source("InfoMoney")["rss_url"]
    assert feed_candidate_urls(healthy) == (healthy,)
    unknown = get_retrieval_config("No Such Source")
    assert unknown["type"] == "rss"
    assert apply_retrieval_override("No Such Source", unknown) is unknown
    assert apply_retrieval_override(None, unknown) is unknown


# --- discovery seam -------------------------------------------------------------


def test_hub_seam_recovers_prnewswire_article_urls() -> None:
    """Stub hub HTML through the real plan_harvest hub path → article URLs."""
    seen: list[str] = []
    fetch = _make_fetch(_fetch_map(), log=seen)
    plan = plan_harvest(_effective_config(), SWAROVSKI, "en", fetch=fetch, sleep=lambda _: None)
    assert [job.loc for job in plan.jobs] == list(ARTICLES)
    assert plan.causes == []
    assert plan.policy == "impersonated-feed"
    # The hub listing is not an article, and the dead feed is never fetched.
    assert HUB in seen
    assert DEAD_RSS not in seen


def test_hub_seam_reports_one_bad_article_without_aborting(
    monkeypatch: Any, no_engine: None
) -> None:
    """One unreachable article is an explicit partial failure, never an abort."""
    bad = ARTICLES[1]
    seen: list[str] = []
    _patch_flow_seams(
        monkeypatch,
        _fetch_map(),
        log=seen,
        failures={bad: ArticleFetchError(bad, "HTTP Error 500")},
    )
    _forbid_feed_lane(monkeypatch)
    result = flows.ingest_source_flow(source_name=SWAROVSKI)
    assert result["inserted"] == 2
    assert result["skipped"] == 0
    assert result["discovery_skipped"] == 1
    assert any(bad in cause for cause in result["discovery_causes"])
    assert "error" not in result
    assert DEAD_RSS not in seen


# --- Ingestion Run --------------------------------------------------------------


def test_flow_ingests_swarovski_via_hub_never_dead_feed(monkeypatch: Any, no_engine: None) -> None:
    seen: list[str] = []
    _patch_flow_seams(monkeypatch, _fetch_map(), log=seen)
    _forbid_feed_lane(monkeypatch)
    result = flows.ingest_source_flow(source_name=SWAROVSKI)
    assert result == {"inserted": 3, "skipped": 0}
    assert set(ARTICLES) <= set(seen)
    assert HUB in seen
    assert DEAD_RSS not in seen


def test_batch_isolates_swarovski_from_failing_neighbour(monkeypatch: Any, no_engine: None) -> None:
    _patch_flow_seams(monkeypatch, _fetch_map())

    def failing_feed(url: str, timeout: int = 30) -> bytes:
        raise RuntimeError(f"fetch failed for {url}: HTTP Error 404")

    monkeypatch.setattr(flows, "fetch_rss", failing_feed)
    results = flows.ingest_sources_flow(source_names=[SWAROVSKI, "InfoMoney"])
    assert results[SWAROVSKI] == {"inserted": 3, "skipped": 0}
    assert results["InfoMoney"]["inserted"] == 0
    assert results["InfoMoney"]["error"]


class _FakeCursor:
    """Dedupe-by-url/content_hash cursor mirroring the ON CONFLICT DO NOTHING."""

    def __init__(self, store: dict[str, tuple]) -> None:
        self._store = store
        self.rowcount = 0
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


def test_rerun_of_swarovski_upsert_is_idempotent(monkeypatch: Any, no_engine: None) -> None:
    """A repeat Ingestion Run inserts nothing new (rerun-safe hub lane)."""
    from marketing_intelligence.ingest import upsert_documents

    _patch_flow_seams(monkeypatch, _fetch_map())
    _forbid_feed_lane(monkeypatch)
    conn = FakeConnection()
    monkeypatch.setattr(flows, "upsert_documents", lambda docs: upsert_documents(docs, conn=conn))
    assert flows.ingest_source_flow(source_name=SWAROVSKI) == {"inserted": 3, "skipped": 0}
    assert flows.ingest_source_flow(source_name=SWAROVSKI) == {"inserted": 0, "skipped": 3}
    assert len(conn.store) == 3
