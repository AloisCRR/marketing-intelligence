"""One-item article lookup tests (Ticket 01) — fake conns, no live Postgres.

TDD: written FIRST (red) against the new seam:
- `brain.article.get_article` (SELECT single row by url OR canonical_url)
- `brain.service.get_article` (validated; ValueError -> InvalidRequest)
- `GET /article?url=...` (thin HTTP adapter, InvalidRequest -> 422)
- `get_article` MCP tool (parity by construction)

Covers: known URL returns full content + provenance, canonical-URL match,
unknown/blank -> InvalidRequest + HTTP 422 shape + MCP tool error parity,
search/weekly list shapes unchanged.
"""

from __future__ import annotations

import datetime as _dt
import importlib.util
import os
import sys
from pathlib import Path
from typing import Any

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(os.path.dirname(_HERE), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from fastapi.testclient import TestClient  # noqa: E402

import brain.article as article_module  # noqa: E402
import brain.service as service  # noqa: E402
from api.app import app  # noqa: E402


def _utc(*args: int) -> _dt.datetime:
    return _dt.datetime(*args, tzinfo=_dt.UTC)


FULL_CONTENT = (
    "# TikTok Adds Voice Notes\n\nTikTok rolls out voice notes and image "
    "carousels for comments globally, with a longform body that must come back "
    "whole — never truncated to a snippet — so the agent has full context."
)

# (title, url, canonical_url, source, published_at, author, content,
#  flag_reason, flag_detail, flagged_at, flagged_by)
ARTICLE_ROWS = [
    (
        "TikTok Adds Voice Notes",
        "https://www.socialmediatoday.com/news/tiktok/1/?utm_source=rss#top",
        "https://www.socialmediatoday.com/news/tiktok/1/",
        "Social Media Today",
        _utc(2026, 9, 8, 14, 30),
        "Andrew Hutchinson",
        FULL_CONTENT,
        None,
        None,
        None,
        None,
    ),
]

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

SEARCH_RESULT_KEYS = {
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

WEEKLY_ARTICLE_KEYS = {
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


# --- fakes -------------------------------------------------------------------


class _ArticleCursor:
    """Cursor-style fake honouring exact-then-canonical query semantics."""

    def __init__(self, rows: list[tuple]) -> None:
        self._rows = rows
        self.last_sql: str | None = None
        self.last_params: tuple | None = None
        self._result: list[tuple] = []

    def execute(self, sql: str, params: tuple | None = None) -> None:
        self.last_sql = sql
        self.last_params = params
        ident = params[0] if params else None
        if "canonical_url" in sql:
            self._result = [r for r in self._rows if r[2] == ident]
        else:
            self._result = [r for r in self._rows if r[1] == ident]

    def fetchall(self) -> list[tuple]:
        return list(self._result)

    def close(self) -> None:
        pass


class _ArticleConnection:
    def __init__(self, rows: list[tuple] = ARTICLE_ROWS) -> None:
        self._rows = rows
        self.calls = 0
        self.closed = 0

    def cursor(self) -> _ArticleCursor:
        self.calls += 1
        return _ArticleCursor(self._rows)

    def close(self) -> None:
        self.closed += 1


def _patch_lane(monkeypatch: pytest.MonkeyPatch, rows: list[tuple]) -> _ArticleConnection:
    """Point brain.article.get_connection at a fake; return the connection."""
    conn = _ArticleConnection(rows)

    def fake_get_connection() -> _ArticleConnection:
        return conn

    monkeypatch.setattr(article_module, "get_connection", fake_get_connection)
    return conn


def _load_mcp_server() -> Any:
    path = Path(_SRC) / "mcp" / "server.py"
    spec = importlib.util.spec_from_file_location("trend_mcp_server_article", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MCP_SERVER = _load_mcp_server()

ARTICLE_PAYLOAD = {
    "title": "TikTok Adds Voice Notes",
    "url": "https://www.socialmediatoday.com/news/tiktok/1/?utm_source=rss#top",
    "canonical_url": "https://www.socialmediatoday.com/news/tiktok/1/",
    "source": "Social Media Today",
    "published_at": "2026-09-08T14:30:00+00:00",
    "author": "Andrew Hutchinson",
    "content": FULL_CONTENT,
}


# --- lookup ------------------------------------------------------------------


def test_known_url_returns_full_content_with_provenance() -> None:
    article = service.get_article(
        "https://www.socialmediatoday.com/news/tiktok/1/?utm_source=rss#top",
        conn=_ArticleConnection(),
    )
    assert set(article.keys()) == ARTICLE_KEYS
    assert article["title"] == "TikTok Adds Voice Notes"
    assert article["source"] == "Social Media Today"
    assert article["author"] == "Andrew Hutchinson"
    # Full stored body, not a snippet.
    assert article["content"] == FULL_CONTENT
    assert "snippet" not in article
    parsed = _dt.datetime.fromisoformat(str(article["published_at"]))
    assert parsed.tzinfo is not None


def test_canonical_url_match() -> None:
    conn = _ArticleConnection()
    # Canonical form (query/fragment stripped) resolves to the same row.
    by_canonical = service.get_article("https://www.socialmediatoday.com/news/tiktok/1/", conn=conn)
    assert by_canonical["content"] == FULL_CONTENT
    assert by_canonical["url"] == ARTICLE_ROWS[0][1]
    # Uppercase scheme/host also canonicalizes to the same row.
    by_case = service.get_article(
        "HTTPS://WWW.SOCIALMEDIATODAY.COM/news/tiktok/1/?x=1",
        conn=_ArticleConnection(),
    )
    assert by_case["canonical_url"] == "https://www.socialmediatoday.com/news/tiktok/1/"


def test_unknown_identifier_raises_invalid_request() -> None:
    conn = _ArticleConnection()
    with pytest.raises(service.InvalidRequest):
        service.get_article("https://unknown.example/nope/", conn=conn)
    # Lane-level contract: unknown -> ValueError (service maps it).
    with pytest.raises(ValueError):
        article_module.get_article("https://unknown.example/nope/", conn=_ArticleConnection())


def test_blank_and_non_string_rejected_without_query() -> None:
    conn = _ArticleConnection()
    for bad in ("", "   ", None, 123):
        with pytest.raises(service.InvalidRequest):
            service.get_article(bad, conn=conn)  # type: ignore[arg-type]
    assert conn.calls == 0


def test_injected_conn_is_not_closed() -> None:
    conn = _ArticleConnection()
    service.get_article("https://www.socialmediatoday.com/news/tiktok/1/", conn=conn)
    assert conn.calls >= 1
    assert conn.closed == 0


# --- HTTP surface ------------------------------------------------------------


def test_http_lookup_returns_service_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "get_article", lambda identifier, conn=None: ARTICLE_PAYLOAD)
    resp = TestClient(app).get("/article", params={"url": ARTICLE_ROWS[0][1]})
    assert resp.status_code == 200
    assert resp.json() == ARTICLE_PAYLOAD


def test_http_unknown_and_blank_map_to_422(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_lane(monkeypatch, [])  # empty store: every identifier is unknown
    live = TestClient(app)
    resp = live.get("/article", params={"url": "https://unknown.example/nope/"})
    assert resp.status_code == 422
    assert "detail" in resp.json()
    # Blank identifier never reaches the DB.
    assert live.get("/article", params={"url": "   "}).status_code == 422


# --- MCP parity --------------------------------------------------------------


def test_mcp_tool_registered_and_matches_http(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    tools = asyncio.run(MCP_SERVER.mcp.list_tools())
    assert "get_article" in [t.name for t in tools]
    monkeypatch.setattr(service, "get_article", lambda identifier, conn=None: ARTICLE_PAYLOAD)
    api_payload = TestClient(app).get("/article", params={"url": ARTICLE_ROWS[0][1]}).json()
    assert api_payload == ARTICLE_PAYLOAD
    assert MCP_SERVER.get_article(identifier=ARTICLE_ROWS[0][1]) == ARTICLE_PAYLOAD


def test_mcp_tool_error_parity(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_lane(monkeypatch, [])
    with pytest.raises(service.InvalidRequest):
        MCP_SERVER.get_article(identifier="https://unknown.example/nope/")
    with pytest.raises(service.InvalidRequest):
        MCP_SERVER.get_article(identifier="   ")


# --- list shapes unchanged ---------------------------------------------------


def test_search_shape_unchanged() -> None:
    results = service.search_articles("TikTok", conn=_SearchConnection())
    assert len(results) >= 1
    for row in results:
        assert set(row.keys()) == SEARCH_RESULT_KEYS
        assert "content" not in row


def test_weekly_shape_unchanged() -> None:
    from datetime import date

    ctx = service.get_weekly_context(date(2026, 9, 7), date(2026, 9, 13), conn=_WeeklyConn())
    assert set(ctx) == {
        "period",
        "important_articles",
        "top_stories",
        "emerging_topics",
        "topic_movements",
        "notable_entities",
        "source_convergence",
    }
    for key in (
        "top_stories",
        "emerging_topics",
        "topic_movements",
        "notable_entities",
        "source_convergence",
    ):
        assert ctx[key] == []
    for article in ctx["important_articles"]:
        assert set(article) == WEEKLY_ARTICLE_KEYS
        assert "content" not in article


# --- list-shape fakes (mirror tests/test_service_contract.py) ----------------


class _SearchCursor:
    def __init__(self, rows: list[tuple]) -> None:
        self._rows = rows
        self.last_params: tuple | None = None

    def execute(self, sql: str, params: tuple | None = None) -> None:
        self.last_params = params

    def fetchall(self) -> list[tuple]:
        if self.last_params:
            try:
                return list(self._rows[: int(self.last_params[-1])])
            except (ValueError, TypeError):
                pass
        return list(self._rows)

    def close(self) -> None:
        pass


class _SearchConnection:
    def __init__(self) -> None:
        self.calls = 0

    def cursor(self) -> _SearchCursor:
        self.calls += 1
        return _SearchCursor(
            [
                (
                    "TikTok Adds Voice Notes",
                    "https://www.socialmediatoday.com/news/tiktok/1/",
                    "https://www.socialmediatoday.com/news/tiktok/1/",
                    "Social Media Today",
                    _utc(2026, 9, 8, 14, 30),
                    "Andrew Hutchinson",
                    "TikTok rolls out voice notes globally.",
                    None,
                    None,
                    None,
                    None,
                )
            ]
        )

    def close(self) -> None:
        pass


class _WeeklyCursor:
    def __init__(self, rows: list[tuple]) -> None:
        self._rows = rows
        self._result: list[tuple] = []

    def execute(self, sql: str, params: tuple | None = None) -> _WeeklyCursor:
        assert params is not None
        start, end, names, limit = params
        kept = [r for r in self._rows if r[4] >= start and r[4] < end and r[3] in set(names)]
        kept.sort(key=lambda r: r[4], reverse=True)
        self._result = kept[: int(limit)]
        return self

    def fetchall(self) -> list[tuple]:
        return self._result


class _WeeklyConn:
    def __init__(self) -> None:
        self._rows = [
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
            )
        ]

    def execute(self, sql: str, params: tuple | None = None) -> _WeeklyCursor:
        return _WeeklyCursor(self._rows).execute(sql, params)

    def commit(self) -> None:
        pass

    def close(self) -> None:
        pass
