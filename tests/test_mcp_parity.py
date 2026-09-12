"""API<->MCP parity tests (Ticket 05) — identical payloads by construction.

Loads `src/mcp/server.py` by file path (NOT `import mcp.server`: the local
`src/mcp/` dir intentionally has no `__init__.py` so the installed `mcp`
distribution keeps winning plain `import mcp`). Stubs `marketing_intelligence.service` and
asserts the HTTP routes and the MCP tools return the same payloads.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
from pathlib import Path
from typing import Any

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(os.path.dirname(_HERE), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import marketing_intelligence.flag as flag_lane  # noqa: E402
import marketing_intelligence.read as read_lane  # noqa: E402
import marketing_intelligence.service as service  # noqa: E402
from api.app import app  # noqa: E402


def _load_mcp_server() -> Any:
    path = Path(_SRC) / "mcp" / "server.py"
    spec = importlib.util.spec_from_file_location("trend_mcp_server", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MCP_SERVER = _load_mcp_server()

SEARCH_PAYLOAD = [
    {
        "title": "TikTok Adds Voice Notes",
        "url": "https://www.socialmediatoday.com/news/tiktok/1/",
        "canonical_url": "https://www.socialmediatoday.com/news/tiktok/1/",
        "source": "Social Media Today",
        "published_at": "2026-09-08T14:30:00+00:00",
        "author": "Andrew Hutchinson",
        "snippet": "…TikTok rolls out voice notes…",
    }
]

PERIOD_PAYLOAD = {
    "period": {
        "from": "2026-09-07T00:00:00-05:00",
        "to": "2026-09-14T00:00:00-05:00",
        "timezone": "America/Panama",
    },
    "recent_articles": [],
}

FLAG_PAYLOAD = {
    "title": "TikTok Adds Voice Notes",
    "url": "https://www.socialmediatoday.com/news/tiktok/1/",
    "canonical_url": "https://www.socialmediatoday.com/news/tiktok/1/",
    "source": "Social Media Today",
    "published_at": "2026-09-08T14:30:00+00:00",
    "author": "Andrew Hutchinson",
    "content": "…full body…",
    "flag_reason": "truncated",
    "flag_detail": "body ends mid-sentence",
    "flagged_at": "2026-09-14T12:00:00+00:00",
    "flagged_by": "tester",
}

READ_PAYLOAD = {
    "title": "TikTok Adds Voice Notes",
    "url": "https://www.socialmediatoday.com/news/tiktok/1/",
    "canonical_url": "https://www.socialmediatoday.com/news/tiktok/1/",
    "source": "Social Media Today",
    "published_at": "2026-09-08T14:30:00+00:00",
    "author": "Andrew Hutchinson",
    "content": "…full body…",
    "flag_reason": None,
    "flag_detail": None,
    "flagged_at": None,
    "flagged_by": None,
    "read": True,
    "read_at": "2026-09-14T12:00:00+00:00",
    "read_by": "tester",
}


@pytest.fixture()
def stubbed_service(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        service,
        "search_articles",
        lambda keyword, limit=20, conn=None, exclude_read=False: SEARCH_PAYLOAD,
    )
    monkeypatch.setattr(
        service, "get_period_context", lambda from_date, to_date, **kw: PERIOD_PAYLOAD
    )
    # raising=False: parallel-lane compatible (green before/after Lane 1 lands).
    monkeypatch.setattr(
        service,
        "flag_extraction",
        lambda identifier, reason=None, detail=None, flagged_by=None, clear=False, conn=None: (
            FLAG_PAYLOAD
        ),
        raising=False,
    )
    # raising=False: read-state lane lands alongside this parity lane.
    monkeypatch.setattr(
        service,
        "mark_article_read",
        lambda identifier, read_by=None, clear=False, conn=None: READ_PAYLOAD,
        raising=False,
    )


def _unwrap_call_tool(out: Any) -> Any:
    """Normalise FastMCP.call_tool's (content, result-dict) return shape."""
    if isinstance(out, tuple) and len(out) == 2 and isinstance(out[1], dict):
        return out[1].get("result", out[1])
    return out


def test_mcp_registers_exactly_ten_tools() -> None:
    tools = asyncio.run(MCP_SERVER.mcp.list_tools())
    assert sorted(t.name for t in tools) == [
        "flag_extraction",
        "get_article",
        "get_importance",
        "get_period_context",
        "list_sources_inventory",
        "list_vocabulary",
        "mark_article_read",
        "search_articles",
        "set_document_topics",
        "set_importance",
    ]


def test_mutating_tools_are_not_read_only() -> None:
    tools = {t.name: t for t in asyncio.run(MCP_SERVER.mcp.list_tools())}
    assert tools["search_articles"].annotations.readOnlyHint is True
    assert tools["get_period_context"].annotations.readOnlyHint is True
    assert tools["get_article"].annotations.readOnlyHint is True
    assert tools["get_importance"].annotations.readOnlyHint is True
    assert tools["list_vocabulary"].annotations.readOnlyHint is True
    assert tools["flag_extraction"].annotations.readOnlyHint is False
    assert tools["mark_article_read"].annotations.readOnlyHint is False
    assert tools["set_importance"].annotations.readOnlyHint is False
    assert tools["set_document_topics"].annotations.readOnlyHint is False
    assert tools["set_importance"].annotations.idempotentHint is False
    assert tools["mark_article_read"].annotations.idempotentHint is False


def test_search_api_equals_mcp_tool(stubbed_service: None) -> None:
    api_payload = TestClient(app).get("/search", params={"q": "TikTok"}).json()
    assert api_payload == {"results": SEARCH_PAYLOAD}
    # Direct tool call (same service fn) ...
    assert MCP_SERVER.search_articles(keyword="TikTok") == SEARCH_PAYLOAD
    # ... and the registered-tool path agree.
    out = asyncio.run(
        MCP_SERVER.mcp.call_tool("search_articles", {"keyword": "TikTok", "limit": 20})
    )
    assert _unwrap_call_tool(out) == SEARCH_PAYLOAD
    assert api_payload["results"] == _unwrap_call_tool(out)


def test_period_api_equals_mcp_tool(stubbed_service: None) -> None:
    body = {"from_date": "2026-09-07", "to_date": "2026-09-13"}
    api_payload = TestClient(app).post("/period-context", json=body).json()
    assert api_payload == PERIOD_PAYLOAD
    assert (
        MCP_SERVER.get_period_context(from_date="2026-09-07", to_date="2026-09-13")
        == PERIOD_PAYLOAD
    )
    out = asyncio.run(MCP_SERVER.mcp.call_tool("get_period_context", dict(body, limit=50)))
    assert _unwrap_call_tool(out) == PERIOD_PAYLOAD
    assert api_payload == _unwrap_call_tool(out)


class _EmptyCursor:
    """Lane-seam fake for unknown URLs: UPDATE matches nothing, SELECT finds nothing."""

    rowcount = 0

    def execute(self, sql: str, params: object = None) -> None:
        pass

    def fetchall(self) -> list:
        return []

    def close(self) -> None:
        pass


class _EmptyConn:
    def cursor(self) -> _EmptyCursor:
        return _EmptyCursor()

    def commit(self) -> None:
        pass

    def close(self) -> None:
        pass


def test_mcp_tools_surface_service_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(service.InvalidRequest):
        MCP_SERVER.search_articles(keyword="   ")
    with pytest.raises(service.InvalidRequest):
        MCP_SERVER.get_period_context(from_date="nope", to_date="2026-09-13")
    with pytest.raises(service.InvalidRequest):
        MCP_SERVER.flag_extraction(identifier="   ")
    with pytest.raises(service.InvalidRequest):
        MCP_SERVER.flag_extraction(
            identifier="https://www.socialmediatoday.com/news/tiktok/1/",
            reason="not-a-reason",
        )
    url = "https://www.socialmediatoday.com/news/tiktok/1/"
    with pytest.raises(service.InvalidRequest):
        MCP_SERVER.flag_extraction(identifier=url, reason="thin", detail="x" * 2001)
    with pytest.raises(service.InvalidRequest):
        MCP_SERVER.flag_extraction(identifier=url, reason="other")
    with pytest.raises(service.InvalidRequest):
        MCP_SERVER.flag_extraction(identifier=url, reason="other", detail="   ")
    with pytest.raises(service.InvalidRequest):
        MCP_SERVER.flag_extraction(identifier=url, reason="thin", detail="d", flagged_by="y" * 101)
    # Unknown URL reaches the lane (empty store) and still surfaces InvalidRequest.
    monkeypatch.setattr(flag_lane, "get_connection", lambda: _EmptyConn())
    with pytest.raises(service.InvalidRequest):
        MCP_SERVER.flag_extraction(
            identifier="https://unknown.example/nope/", reason="thin", detail="d"
        )


def test_flag_api_equals_mcp_tool(stubbed_service: None) -> None:
    body = {"identifier": "https://www.socialmediatoday.com/news/tiktok/1/"}
    api_payload = TestClient(app).post("/flag-extraction", json=body).json()
    assert api_payload == FLAG_PAYLOAD
    # Direct tool call (same service fn) ...
    assert (
        MCP_SERVER.flag_extraction(identifier="https://www.socialmediatoday.com/news/tiktok/1/")
        == FLAG_PAYLOAD
    )
    # ... and the registered-tool path agree.
    out = asyncio.run(MCP_SERVER.mcp.call_tool("flag_extraction", dict(body)))
    assert _unwrap_call_tool(out) == FLAG_PAYLOAD
    assert api_payload == _unwrap_call_tool(out)


def test_search_exclude_read_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}

    def fake(
        keyword: str, limit: int = 20, conn: object = None, exclude_read: bool = False
    ) -> list:
        seen["keyword"] = keyword
        seen["exclude_read"] = exclude_read
        return SEARCH_PAYLOAD

    monkeypatch.setattr(service, "search_articles", fake)
    assert MCP_SERVER.search_articles(keyword="TikTok") == SEARCH_PAYLOAD
    assert seen == {"keyword": "TikTok", "exclude_read": False}
    assert MCP_SERVER.search_articles(keyword="TikTok", exclude_read=True) == SEARCH_PAYLOAD
    assert seen == {"keyword": "TikTok", "exclude_read": True}


def test_period_exclude_read_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}

    def fake(from_date: object, to_date: object, **kw: object) -> dict:
        seen.update(kw)
        return PERIOD_PAYLOAD

    monkeypatch.setattr(service, "get_period_context", fake)
    assert (
        MCP_SERVER.get_period_context(from_date="2026-09-07", to_date="2026-09-13")
        == PERIOD_PAYLOAD
    )
    assert seen["exclude_read"] is False
    assert (
        MCP_SERVER.get_period_context(
            from_date="2026-09-07", to_date="2026-09-13", exclude_read=True
        )
        == PERIOD_PAYLOAD
    )
    assert seen["exclude_read"] is True


def test_period_per_source_limit_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}

    def fake(from_date: object, to_date: object, **kw: object) -> dict:
        seen.update(kw)
        return PERIOD_PAYLOAD

    monkeypatch.setattr(service, "get_period_context", fake)
    body = {"from_date": "2026-09-07", "to_date": "2026-09-13"}
    assert MCP_SERVER.get_period_context(**body) == PERIOD_PAYLOAD
    assert seen["per_source_limit"] is None  # default preserves current behaviour
    out = asyncio.run(
        MCP_SERVER.mcp.call_tool("get_period_context", dict(body, per_source_limit=4))
    )
    assert _unwrap_call_tool(out) == PERIOD_PAYLOAD
    assert seen["per_source_limit"] == 4


def test_mark_read_api_equals_mcp_tool(stubbed_service: None) -> None:
    body = {"identifier": "https://www.socialmediatoday.com/news/tiktok/1/"}
    api_payload = TestClient(app).post("/mark-read", json=body).json()
    assert api_payload == READ_PAYLOAD
    # Direct tool call (same service fn) ...
    assert (
        MCP_SERVER.mark_article_read(identifier="https://www.socialmediatoday.com/news/tiktok/1/")
        == READ_PAYLOAD
    )
    # ... and the registered-tool path agree.
    out = asyncio.run(MCP_SERVER.mcp.call_tool("mark_article_read", dict(body)))
    assert _unwrap_call_tool(out) == READ_PAYLOAD
    assert api_payload == _unwrap_call_tool(out)


def test_mark_read_forwards_args(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}

    def fake(
        identifier: object,
        read_by: object = None,
        clear: object = False,
        conn: object = None,
    ) -> dict:
        seen["identifier"] = identifier
        seen["read_by"] = read_by
        seen["clear"] = clear
        return READ_PAYLOAD

    monkeypatch.setattr(service, "mark_article_read", fake, raising=False)
    assert (
        MCP_SERVER.mark_article_read(
            identifier="https://www.socialmediatoday.com/news/tiktok/1/", read_by="tester"
        )
        == READ_PAYLOAD
    )
    assert seen == {
        "identifier": "https://www.socialmediatoday.com/news/tiktok/1/",
        "read_by": "tester",
        "clear": False,
    }


def test_mcp_mark_read_surfaces_service_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(service.InvalidRequest):
        MCP_SERVER.mark_article_read(identifier="   ")
    url = "https://www.socialmediatoday.com/news/tiktok/1/"
    with pytest.raises(service.InvalidRequest):
        MCP_SERVER.mark_article_read(identifier=url, read_by="y" * 101)
    # Unknown URL reaches the lane (empty store) and still surfaces InvalidRequest.
    monkeypatch.setattr(read_lane, "get_connection", lambda: _EmptyConn())
    with pytest.raises(service.InvalidRequest):
        MCP_SERVER.mark_article_read(identifier="https://unknown.example/nope/")
