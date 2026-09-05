"""API<->MCP parity tests (Ticket 05) — identical payloads by construction.

Loads `src/mcp/server.py` by file path (NOT `import mcp.server`: the local
`src/mcp/` dir intentionally has no `__init__.py` so the installed `mcp`
distribution keeps winning plain `import mcp`). Stubs `brain.service` and
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

import brain.service as service  # noqa: E402
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

WEEKLY_PAYLOAD = {
    "period": {
        "from": "2026-09-07T00:00:00-05:00",
        "to": "2026-09-14T00:00:00-05:00",
        "timezone": "America/Panama",
    },
    "important_articles": [],
    "top_stories": [],
    "emerging_topics": [],
    "topic_movements": [],
    "notable_entities": [],
    "source_convergence": [],
}


@pytest.fixture()
def stubbed_service(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        service, "search_articles", lambda keyword, limit=20, conn=None: SEARCH_PAYLOAD
    )
    monkeypatch.setattr(
        service, "get_weekly_context", lambda from_date, to_date, **kw: WEEKLY_PAYLOAD
    )


def _unwrap_call_tool(out: Any) -> Any:
    """Normalise FastMCP.call_tool's (content, result-dict) return shape."""
    if isinstance(out, tuple) and len(out) == 2 and isinstance(out[1], dict):
        return out[1].get("result", out[1])
    return out


def test_mcp_registers_exactly_two_tools() -> None:
    tools = asyncio.run(MCP_SERVER.mcp.list_tools())
    assert sorted(t.name for t in tools) == ["get_weekly_context", "search_articles"]


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


def test_weekly_api_equals_mcp_tool(stubbed_service: None) -> None:
    body = {"from_date": "2026-09-07", "to_date": "2026-09-13"}
    api_payload = TestClient(app).post("/weekly-context", json=body).json()
    assert api_payload == WEEKLY_PAYLOAD
    assert (
        MCP_SERVER.get_weekly_context(from_date="2026-09-07", to_date="2026-09-13")
        == WEEKLY_PAYLOAD
    )
    out = asyncio.run(MCP_SERVER.mcp.call_tool("get_weekly_context", dict(body, limit=50)))
    assert _unwrap_call_tool(out) == WEEKLY_PAYLOAD
    assert api_payload == _unwrap_call_tool(out)


def test_mcp_tools_surface_service_validation() -> None:
    with pytest.raises(service.InvalidRequest):
        MCP_SERVER.search_articles(keyword="   ")
    with pytest.raises(service.InvalidRequest):
        MCP_SERVER.get_weekly_context(from_date="nope", to_date="2026-09-13")
