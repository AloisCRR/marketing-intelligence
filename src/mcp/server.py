"""Thin MCP adapter over brain.service (Ticket 05).

Same validated payloads as the HTTP API by construction: both tools call the
shared service functions. Run directly (never `python -m mcp.server` — the
local `src/mcp/` dir intentionally has no `__init__.py` so the installed
`mcp` distribution keeps winning plain `import mcp`):

    uv run python src/mcp/server.py                 # stdio (local `make mcp`)
    uv run python src/mcp/server.py --http          # streamable HTTP (local only)

Deployment serves the streamable-HTTP app (`http_app`, or a fresh instance via
`create_http_app()`) as plain HTTP behind Traefik, which terminates TLS. When
`BRAIN_API_TOKEN` is set, HTTP requests must carry
`Authorization: Bearer <token>` (enforced via `StaticTokenVerifier` as
`FastMCP(token_verifier=...)`); stdio is unaffected. When unset, auth is
disabled (local-dev open mode).
"""

from __future__ import annotations

import sys
from typing import Any

from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from brain import service
from brain.auth import StaticTokenVerifier, is_auth_configured, mcp_auth_settings
from brain.healthcheck import health_payload  # noqa: F401  (installs access-log filter)
from brain.service import DEFAULT_SEARCH_LIMIT, DEFAULT_WEEKLY_LIMIT


def create_mcp() -> FastMCP:
    """Build the shared FastMCP server.

    Reads `BRAIN_API_TOKEN` at call time so tests/rotation get a fresh
    verdict; pass the result to `create_http_app` or serve `http_app`.
    FastMCP 1.29.1 API: `FastMCP(name, auth=AuthSettings(...),
    token_verifier=...)` — `auth` is required alongside `token_verifier`.
    """
    if is_auth_configured():
        return FastMCP(
            "trend-intelligence-brain",
            auth=mcp_auth_settings(),
            token_verifier=StaticTokenVerifier(),
        )
    return FastMCP("trend-intelligence-brain")


mcp = create_mcp()


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


async def health(request: Request) -> JSONResponse:
    """Unauthenticated liveness probe (plain route, bypasses token verifier)."""
    del request
    return JSONResponse(health_payload())


def _attach_health_route(app: Starlette) -> Starlette:
    """Append ``GET /health`` to a streamable-HTTP app (idempotent per app)."""
    for route in app.routes:
        if getattr(route, "path", None) == "/health":
            return app
    app.routes.append(Route("/health", endpoint=health, methods=["GET"]))
    return app


def create_http_app() -> Starlette:
    """Fresh streamable-HTTP Starlette app for this server (FastMCP 1.29.1 API).

    Uses `FastMCP.streamable_http_app(self) -> Starlette` (no args) on a
    freshly built server so the current `BRAIN_API_TOKEN` value applies.
    Serve plain HTTP behind Traefik (TLS terminated at the proxy), e.g.
    `uvicorn mcp.server:http_app --port 8124`.
    """
    return _attach_health_route(create_mcp().streamable_http_app())


# ASGI entrypoint reflecting the env at process start (containers set
# BRAIN_API_TOKEN before import, so this is the enforced app in prod).
http_app = _attach_health_route(mcp.streamable_http_app())


if __name__ == "__main__":
    if "--http" in sys.argv:
        mcp.run(transport="streamable-http")
    else:
        mcp.run()
