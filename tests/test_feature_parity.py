"""Feature parity + stability proof (Ticket 04) — the whole feature holds together.

End-to-end verification over lanes 01/02/03, hermetic (fake conns, monkeypatched
fetch — no live Postgres, no network, no model calls):

- Period Context and search list payloads match their pre-feature shapes
  key-for-key at the same limits (11-key search dicts, 10-key period dicts +
  5 explicit-empty V1 trend keys).
- One-item lookup returns identical payloads over HTTP (TestClient) and MCP
  (direct tool call + registered-tool path), including identical validation
  failures (unknown/blank -> 422 detail shape == MCP InvalidRequest message).
- An Ingestion Run over mixed content (sufficient RSS, thin RSS, bot-blocked
  page, paywalled page) stores the expected bodies and reports per-item causes
  with the run completing (no error key).
- No model/LLM calls on any covered path (import guard).
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import importlib.util
import os
import sys
from datetime import UTC, date
from pathlib import Path
from typing import Any

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(os.path.dirname(_HERE), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from prefect_harness import no_engine  # noqa: E402

import marketing_intelligence.article as article_lane  # noqa: E402
import marketing_intelligence.enrich as enrich_lane  # noqa: E402
import marketing_intelligence.period as period_lane  # noqa: E402
import marketing_intelligence.search as search_lane  # noqa: E402
import marketing_intelligence.service as service  # noqa: E402
from api.app import app  # noqa: E402


def _load_mcp_server() -> Any:
    path = Path(_SRC) / "mcp" / "server.py"
    spec = importlib.util.spec_from_file_location("trend_mcp_server_feat", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MCP_SERVER = _load_mcp_server()


SEARCH_KEYS = {
    "title",
    "url",
    "canonical_url",
    "source",
    "published_at",
    "author",
    "snippet",
    "flag_reason",
    "flag_detail",
    "flagged_at",
    "flagged_by",
}

PERIOD_ARTICLE_KEYS = {
    "title",
    "url",
    "canonical_url",
    "source",
    "published_at",
    "author",
    "flag_reason",
    "flag_detail",
    "flagged_at",
    "flagged_by",
}

PERIOD_TOP_KEYS = {
    "period",
    "important_articles",
    "top_stories",
    "emerging_topics",
    "topic_movements",
    "notable_entities",
    "source_convergence",
}

TREND_KEYS = (
    "top_stories",
    "emerging_topics",
    "topic_movements",
    "notable_entities",
    "source_convergence",
)

ARTICLE_KEYS = {
    "title",
    "url",
    "canonical_url",
    "source",
    "published_at",
    "author",
    "content",
    "flag_reason",
    "flag_detail",
    "flagged_at",
    "flagged_by",
}


def _utc(*args: int) -> _dt.datetime:
    return _dt.datetime(*args, tzinfo=UTC)


# --- fakes -------------------------------------------------------------------


class _CursorFake:
    """DB-API cursor fake: preset rows, honors a LIMIT last-param."""

    def __init__(self, rows: list[tuple]) -> None:
        self._rows = rows
        self.last_params: tuple | None = None

    def execute(self, sql: str, params: tuple | None = None) -> _CursorFake:
        self.last_params = params
        return self

    def fetchall(self) -> list[tuple]:
        if self.last_params:
            limit = self.last_params[-1]
            if isinstance(limit, int) and not isinstance(limit, bool):
                return list(self._rows[:limit])
        return list(self._rows)

    def close(self) -> None:
        pass


class _ConnFake:
    """Connection fake exposing .cursor() (search/article lane style)."""

    def __init__(self, rows: list[tuple]) -> None:
        self._rows = rows
        self.cursor_obj = _CursorFake(rows)

    def cursor(self) -> _CursorFake:
        return self.cursor_obj

    def commit(self) -> None:
        pass

    def close(self) -> None:
        pass


class _PeriodConnFake:
    """Connection fake exposing .execute() directly (period lane style)."""

    def __init__(self, rows: list[tuple]) -> None:
        self.cursor_obj = _CursorFake(rows)

    def execute(self, sql: str, params: tuple | None = None) -> _CursorFake:
        return self.cursor_obj.execute(sql, params)

    def close(self) -> None:
        pass


class _ArticleConnFake(_ConnFake):
    """Single-row lookup fake: rows only for the known URL/canonical pair."""

    def __init__(self, row: tuple, url: str, canonical: str) -> None:
        super().__init__([row])
        self._url = url
        self._canonical = canonical

    def cursor(self) -> _CursorFake:
        inner = self

        class _Lookup(_CursorFake):
            def fetchall(inner_self: _CursorFake) -> list[tuple]:
                key = (inner_self.last_params or (None,))[0]
                if key in (inner._url, inner._canonical):
                    return list(inner._rows)
                return []

        cursor = _Lookup(inner._rows)
        self.cursor_obj = cursor
        return cursor


FULL_CONTENT = (
    "# TikTok Adds Voice Notes\n\nTikTok rolls out voice notes and image "
    "carousels for comments globally, with a longform body that must come back "
    "whole — never truncated to a snippet — so the agent has full context."
)

ARTICLE_URL = "https://www.socialmediatoday.com/news/tiktok/1/?utm_source=rss#top"
ARTICLE_CANONICAL = "https://www.socialmediatoday.com/news/tiktok/1/"

ARTICLE_ROW = (
    "TikTok Adds Voice Notes",
    ARTICLE_URL,
    ARTICLE_CANONICAL,
    "Social Media Today",
    _utc(2026, 9, 8, 14, 30),
    "Andrew Hutchinson",
    FULL_CONTENT,
    None,
    None,
    None,
    None,
)

SEARCH_ROWS = [
    (
        "TikTok Adds Voice Notes",
        "https://www.socialmediatoday.com/news/tiktok/1/",
        "https://www.socialmediatoday.com/news/tiktok/1/",
        "Social Media Today",
        _utc(2026, 9, 8, 14, 30),
        "Andrew Hutchinson",
        "TikTok rolls out voice notes for comments globally.",
        None,
        None,
        None,
        None,
    ),
    (
        "TikTok Shop Expands",
        "https://www.socialmediatoday.com/news/tiktok-shop/2/",
        "https://www.socialmediatoday.com/news/tiktok-shop/2/",
        "Social Media Today",
        _utc(2026, 9, 7, 10, 0),
        None,
        "TikTok Shop expands to new markets with live selling.",
        None,
        None,
        None,
        None,
    ),
]

PERIOD_ROWS = [
    (
        "TikTok Adds Voice Notes",
        "https://www.socialmediatoday.com/news/tiktok/1/",
        "https://www.socialmediatoday.com/news/tiktok/1/",
        "Social Media Today",
        _utc(2026, 9, 8, 14, 30),
        "Andrew Hutchinson",
        None,
        None,
        None,
        None,
    ),
    (
        "Signal Loss Rebuild",
        "https://martech.org/signal-loss/2/",
        "https://martech.org/signal-loss/2/",
        "MarTech",
        _utc(2026, 9, 9, 13, 0),
        None,
        None,
        None,
        None,
        None,
    ),
]


def _unwrap_call_tool(out: Any) -> Any:
    if isinstance(out, tuple) and len(out) == 2 and isinstance(out[1], dict):
        return out[1].get("result", out[1])
    return out


# --- list-payload shape stability ---------------------------------------------


def test_search_list_payload_is_eleven_keys_at_same_limits() -> None:
    results = service.search_articles("TikTok", limit=20, conn=_ConnFake(SEARCH_ROWS))
    assert len(results) == 2
    for item in results:
        assert set(item) == SEARCH_KEYS
    # Same limits as the pre-feature contract: limit bounds, MAX_LIMIT enforced.
    bounded = service.search_articles("TikTok", limit=1, conn=_ConnFake(SEARCH_ROWS))
    assert len(bounded) == 1 and set(bounded[0]) == SEARCH_KEYS
    with pytest.raises(service.InvalidRequest):
        service.search_articles("TikTok", limit=101, conn=_ConnFake(SEARCH_ROWS))


def test_period_list_payload_matches_pre_feature_shape() -> None:
    ctx = period_lane.get_period_context(
        date(2026, 9, 7), date(2026, 9, 13), conn=_PeriodConnFake(PERIOD_ROWS)
    )
    assert set(ctx) == PERIOD_TOP_KEYS
    assert set(ctx["period"]) == {"from", "to", "timezone"}
    assert len(ctx["important_articles"]) == 2
    for item in ctx["important_articles"]:
        assert set(item) == PERIOD_ARTICLE_KEYS
    # V1: no history yet — trend keys present as explicit empties.
    for key in TREND_KEYS:
        assert ctx[key] == []
    # Same limits: limit=1 bounds the article list.
    bounded = period_lane.get_period_context(
        date(2026, 9, 7), date(2026, 9, 13), limit=1, conn=_PeriodConnFake(PERIOD_ROWS)
    )
    assert len(bounded["important_articles"]) == 1


def test_http_search_and_period_match_service_shapes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(search_lane, "get_connection", lambda: _ConnFake(SEARCH_ROWS))
    monkeypatch.setattr(period_lane, "get_connection", lambda: _PeriodConnFake(PERIOD_ROWS))
    client = TestClient(app)
    search_payload = client.get("/search", params={"q": "TikTok"}).json()
    assert set(search_payload) == {"results"}
    assert all(set(item) == SEARCH_KEYS for item in search_payload["results"])
    period_payload = client.post(
        "/period-context", json={"from_date": "2026-09-07", "to_date": "2026-09-13"}
    ).json()
    assert set(period_payload) == PERIOD_TOP_KEYS
    assert all(set(item) == PERIOD_ARTICLE_KEYS for item in period_payload["important_articles"])


# --- one-item lookup parity: HTTP == MCP ---------------------------------------


@pytest.fixture()
def article_conn(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _ArticleConnFake(ARTICLE_ROW, ARTICLE_URL, ARTICLE_CANONICAL)
    monkeypatch.setattr(article_lane, "get_connection", lambda: fake)


def test_get_article_identical_over_http_and_mcp(article_conn: None) -> None:
    expected = service.get_article(ARTICLE_URL)
    assert set(expected) == ARTICLE_KEYS
    assert expected["content"] == FULL_CONTENT  # full body, never a snippet

    http_payload = TestClient(app).get("/article", params={"url": ARTICLE_URL}).json()
    assert http_payload == expected

    # Canonical-URL form resolves to the same stored document on both surfaces.
    assert TestClient(app).get("/article", params={"url": ARTICLE_CANONICAL}).json() == (expected)
    assert MCP_SERVER.get_article(identifier=ARTICLE_CANONICAL) == expected

    # Direct tool call and the registered-tool path agree with HTTP.
    assert MCP_SERVER.get_article(identifier=ARTICLE_URL) == expected
    out = asyncio.run(MCP_SERVER.mcp.call_tool("get_article", {"identifier": ARTICLE_URL}))
    assert _unwrap_call_tool(out) == expected
    assert http_payload == _unwrap_call_tool(out)


def test_get_article_validation_failures_identical(article_conn: None) -> None:
    client = TestClient(app)
    for bad in ("   ", "https://www.socialmediatoday.com/news/unknown/0/"):
        http_resp = client.get("/article", params={"url": bad})
        assert http_resp.status_code == 422
        assert set(http_resp.json()) == {"detail"}
        with pytest.raises(service.InvalidRequest) as excinfo:
            MCP_SERVER.get_article(identifier=bad)
        # Identical failure message on both surfaces.
        assert http_resp.json() == {"detail": str(excinfo.value)}


# --- mixed-content Ingestion Run -----------------------------------------------


MIX_SUFFICIENT_BODY = ("Sufficient RSS analysis. " * 40).strip()  # over the 500-char threshold
MIX_THIN_BODY = "Thin teaser."
MIX_BOT_BODY = "Bot teaser."
MIX_PAYWALLED_BODY = "Paywalled teaser."

MIX_RSS = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<rss version="2.0"><channel>\n'
    "<title>Social Media Today</title>\n"
    "<link>https://www.socialmediatoday.com/</link>\n"
    "<description>mix</description>\n"
    "<item><title>Sufficient Analysis Piece</title>"
    "<link>https://www.socialmediatoday.com/news/sufficient/1/</link>"
    "<pubDate>Thu, 04 Sep 2026 14:30:00 +0000</pubDate>"
    f"<description><![CDATA[<p>{MIX_SUFFICIENT_BODY}</p>]]></description>"
    "</item>\n"
    "<item><title>Thin Teaser Piece</title>"
    "<link>https://www.socialmediatoday.com/news/thin/2/</link>"
    "<pubDate>Thu, 04 Sep 2026 15:30:00 +0000</pubDate>"
    f"<description><![CDATA[<p>{MIX_THIN_BODY}</p>]]></description>"
    "</item>\n"
    "<item><title>Bot-Blocked Piece</title>"
    "<link>https://www.socialmediatoday.com/news/botblocked/3/</link>"
    "<pubDate>Thu, 04 Sep 2026 16:30:00 +0000</pubDate>"
    f"<description><![CDATA[<p>{MIX_BOT_BODY}</p>]]></description>"
    "</item>\n"
    "<item><title>Paywalled Piece</title>"
    "<link>https://www.socialmediatoday.com/news/paywalled/4/</link>"
    "<pubDate>Thu, 04 Sep 2026 17:30:00 +0000</pubDate>"
    f"<description><![CDATA[<p>{MIX_PAYWALLED_BODY}</p>]]></description>"
    "</item>\n"
    "</channel></rss>\n"
).encode()

MIX_THIN_MARKDOWN = "# Thin Teaser Piece\n\nThe full analyst note, fetched as Markdown."
# Fallback output must itself clear the thin threshold: reader stubs and error
# pages never become the canonical stored body (enrich lane contract).
MIX_FALLBACK_MARKDOWN = (
    "# Bot-Blocked Piece\n\nReader-fallback Markdown after the challenge. " * 20
).strip()


def test_mixed_content_run_stores_expected_bodies_and_causes(
    monkeypatch: pytest.MonkeyPatch, no_engine: None
) -> None:
    import marketing_intelligence.flows as flows

    monkeypatch.setattr(flows, "fetch_rss", lambda url, timeout=30: MIX_RSS)
    monkeypatch.setattr(
        flows, "get_enrichment_policy", lambda name: {"threshold": 500, "mode": "auto"}
    )
    fetched: list[str] = []
    fallback_calls: list[str] = []

    def fake_fetch_and_clean(url: str, timeout: int = 30) -> str:
        fetched.append(url)
        if url.endswith("/thin/2/"):
            return MIX_THIN_MARKDOWN
        if url.endswith("/botblocked/3/"):
            raise enrich_lane.FetchFailed(
                f"fetch failed for {url}: HTTP Error 403: Forbidden — body: "
                "Attention Required! | Cloudflare challenge captcha"
            )
        if url.endswith("/paywalled/4/"):
            raise enrich_lane.FetchFailed(
                f"fetch failed for {url}: HTTP Error 402: Payment Required"
            )
        raise AssertionError(f"sufficient bodies must pass through untouched: {url}")

    def fake_fallback(url: str, timeout: int = 30) -> str:
        fallback_calls.append(url)
        assert url.endswith("/botblocked/3/"), f"fallback must stay gated: {url}"
        return MIX_FALLBACK_MARKDOWN

    monkeypatch.setattr(enrich_lane, "fetch_and_clean", fake_fetch_and_clean)
    monkeypatch.setattr(enrich_lane, "try_fallback_reader", fake_fallback)

    stored: dict[str, str] = {}

    def fake_upsert(docs: list[Any]) -> tuple[int, int]:
        for doc in docs:
            stored[doc.url] = doc.content
        return (len(docs), 0)

    monkeypatch.setattr(flows, "upsert_documents", fake_upsert)

    result = flows.ingest_source_flow(source_name="Social Media Today")

    # The run completes: no error key, all four documents persisted.
    assert "error" not in result
    assert result["inserted"] == 4
    assert result["skipped"] == 0
    assert len(stored) == 4  # exactly one canonical stored text per Document

    # Sufficient RSS untouched (zero fetch); thin -> markdown; bot-blocked ->
    # fallback markdown (gated, one call); paywalled -> RSS body kept + cause.
    assert stored["https://www.socialmediatoday.com/news/sufficient/1/"] == (MIX_SUFFICIENT_BODY)
    assert stored["https://www.socialmediatoday.com/news/thin/2/"] == MIX_THIN_MARKDOWN
    assert stored["https://www.socialmediatoday.com/news/botblocked/3/"] == MIX_FALLBACK_MARKDOWN
    assert stored["https://www.socialmediatoday.com/news/paywalled/4/"] == MIX_PAYWALLED_BODY

    assert result["enrich_skipped"] == 1
    assert len(result["enrich_causes"]) == 1
    assert result["enrich_causes"][0].startswith("rss:")  # method recorded with cause
    assert "https://www.socialmediatoday.com/news/paywalled/4/" in result["enrich_causes"][0]
    assert fallback_calls == ["https://www.socialmediatoday.com/news/botblocked/3/"]
    assert "https://www.socialmediatoday.com/news/sufficient/1/" not in fetched


# --- no model calls --------------------------------------------------------------


def test_no_model_calls_on_covered_paths() -> None:
    banned = (
        "torch",
        "transformers",
        "sentence_transformers",
        "sklearn",
        "openai",
        "anthropic",
        "langchain",
        "llama_index",
    )
    loaded = {name.split(".")[0] for name in sys.modules}
    assert not (set(banned) & loaded), f"model libs imported: {set(banned) & loaded}"
    roots = [Path(_SRC) / "marketing_intelligence", Path(_SRC) / "api", Path(_SRC) / "mcp"]
    hits = []
    for root in roots:
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for lib in banned:
                if f"import {lib}" in text or f"from {lib}" in text:
                    hits.append(f"{path}:{lib}")
    assert hits == [], f"model imports in covered source: {hits}"
