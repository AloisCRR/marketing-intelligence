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
    """Search articles by keyword, newest first (11 keys: 7 base + 4 flag keys)."""
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


@mcp.tool()
def flag_extraction(
    identifier: str,
    reason: str | None = None,
    detail: str | None = None,
    flagged_by: str | None = None,
    clear: bool = False,
) -> dict[str, Any]:
    """Flag (or clear) an extraction issue on one article; returns updated Article."""
    return service.flag_extraction(  # type: ignore[attr-defined, no-any-return]
        identifier, reason=reason, detail=detail, flagged_by=flagged_by, clear=clear
    )


if __name__ == "__main__":
    mcp.run()
