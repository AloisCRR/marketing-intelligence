"""Thin MCP adapter over brain.service (Ticket 05).

Same validated payloads as the HTTP API by construction: both tools call the
shared service functions. Run directly (never `python -m mcp.server` — the
local `src/mcp/` dir intentionally has no `__init__.py` so the installed
`mcp` distribution keeps winning plain `import mcp`):

    uv run python src/mcp/server.py
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from brain import service
from brain.service import DEFAULT_SEARCH_LIMIT, DEFAULT_WEEKLY_LIMIT

mcp = FastMCP("trend-intelligence-brain")


@mcp.tool()
def search_articles(keyword: str, limit: int = DEFAULT_SEARCH_LIMIT) -> list[dict[str, Any]]:
    """Search ingested articles by keyword, newest first (7-key provenance dicts)."""
    return service.search_articles(keyword, limit=limit)


@mcp.tool()
def get_weekly_context(
    from_date: str,
    to_date: str,
    sources: list[str] | None = None,
    limit: int = DEFAULT_WEEKLY_LIMIT,
) -> dict[str, Any]:
    """Weekly evidence bundle for [from_date, to_date] (ISO dates)."""
    return service.get_weekly_context(from_date, to_date, sources=sources, limit=limit)


@mcp.tool()
def get_article(identifier: str) -> dict[str, Any]:
    """One article's full stored text plus provenance, by URL/canonical URL."""
    return service.get_article(identifier)


if __name__ == "__main__":
    mcp.run()
