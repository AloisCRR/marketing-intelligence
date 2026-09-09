"""Thin MCP adapter over marketing_intelligence.service (Ticket 05).

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

import json
import sys
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from marketing_intelligence import service
from marketing_intelligence.auth import StaticTokenVerifier, is_auth_configured, mcp_auth_settings
from marketing_intelligence.healthcheck import (
    health_payload,  # noqa: F401  (installs access-log filter)
)
from marketing_intelligence.service import DEFAULT_PERIOD_LIMIT, DEFAULT_SEARCH_LIMIT

SERVER_INSTRUCTIONS = (
    "Marketing Intelligence is a persistent marketing/GenZ/culture/tech intelligence platform "
    "(not a newsletter generator): deterministic ingestion feeds a Postgres system of record, "
    "exposed here through a semantic layer for an AI digest consumer. "
    "You are a consumer of this layer — discover evidence with search_articles, read full text "
    "with get_article, and never own or re-run scraping/ingestion yourself. "
    "For any period synthesis, call get_period_context(from_date, to_date) with "
    "ISO dates (YYYY-MM-DD) and draft only from its important_articles evidence bundle, "
    "keeping provenance URLs attached. "
    "A digest is just one usage of a bundle: the caller picks any range and builds "
    "the digest from it — there is no built-in schedule. "
    "Use flag_extraction only to report improperly extracted content (thin body, JS shell, "
    "paywall challenge, truncated text, wrong body) — never for factual disagreements. "
    "Prefer read-only tools for exploration; flag_extraction and mark_article_read "
    "are the mutating tools."
)


def create_mcp() -> FastMCP:
    """Build the shared FastMCP server.

    Reads `BRAIN_API_TOKEN` at call time so tests/rotation get a fresh
    verdict; pass the result to `create_http_app` or serve `http_app`.
    FastMCP 1.29.1 API: `FastMCP(name, auth=AuthSettings(...),
    token_verifier=...)` — `auth` is required alongside `token_verifier`.
    """
    if is_auth_configured():
        return FastMCP(
            "Marketing Intelligence",
            instructions=SERVER_INSTRUCTIONS,
            auth=mcp_auth_settings(),
            token_verifier=StaticTokenVerifier(),
        )
    return FastMCP("Marketing Intelligence", instructions=SERVER_INSTRUCTIONS)


mcp = create_mcp()


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False))
def search_articles(
    keyword: Annotated[str, "Keyword to search in titles/bodies (non-blank)."],
    limit: Annotated[int, "Max results, 1-100."] = DEFAULT_SEARCH_LIMIT,
    exclude_read: Annotated[bool, "When true, hide read articles."] = False,
) -> list[dict[str, Any]]:
    """Search stored articles by keyword, newest first.

    Use for discovery: find candidate evidence before reading full text with
    get_article. This is the first step of every research workflow.

    Args:
        keyword: Non-blank search term matched against titles/bodies.
        limit: Max articles to return, 1-100 (default 20).
        exclude_read: When True, hide read articles (default False annotates only).

    Returns:
        List of article dicts (14 keys: 7 base + 4 extraction-flag keys + 3 read keys),
        ordered newest first; empty list when nothing matches.

    Raises:
        InvalidRequest: If keyword is blank or limit is outside 1-100.
    """
    return service.search_articles(keyword, limit=limit, exclude_read=exclude_read)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False))
def get_period_context(
    from_date: Annotated[str, "Range start, ISO date YYYY-MM-DD (inclusive)."],
    to_date: Annotated[str, "Range end, ISO date YYYY-MM-DD (inclusive)."],
    sources: Annotated[
        list[str] | None, "Optional source filter; unknown names are rejected."
    ] = None,
    limit: Annotated[int, "Max articles in bundle, 1-100."] = DEFAULT_PERIOD_LIMIT,
    exclude_read: Annotated[bool, "When true, hide read articles."] = False,
) -> dict[str, Any]:
    """Fetch the evidence bundle for a date range.

    Use for period synthesis: draft exclusively from the
    returned important_articles, keeping their URLs/provenance attached and
    separating observations from interpretations. A digest is just
    the case where the caller picks a range (often a week).

    Args:
        from_date: Range start as ISO date (YYYY-MM-DD, inclusive).
        to_date: Range end as ISO date (YYYY-MM-DD, inclusive).
        sources: Optional allowlist of source names; None means all sources.
        limit: Max articles in the bundle, 1-100 (default 50).
        exclude_read: When True, hide read articles (default False annotates only).

    Returns:
        Evidence-bundle dict with period and important_articles entries.

    Raises:
        InvalidRequest: If dates are malformed/unordered, sources unknown,
            or limit is outside 1-100.
    """
    return service.get_period_context(
        from_date, to_date, sources=sources, limit=limit, exclude_read=exclude_read
    )


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False))
def get_article(
    identifier: Annotated[str, "Article URL or canonical URL of the stored document."],
) -> dict[str, Any]:
    """Read one article's full stored text plus provenance.

    Use after search_articles (or a period bundle) when a snippet is not
    enough and the digest/synthesis needs the full body with citations.

    Args:
        identifier: Article URL or canonical URL.

    Returns:
        Full article dict including body text and provenance fields.

    Raises:
        InvalidRequest: If the identifier is blank, unknown, or ambiguous.
    """
    return service.get_article(identifier)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=False))
def flag_extraction(
    identifier: Annotated[str, "Article URL or canonical URL to flag."],
    reason: Annotated[
        str | None,
        "One of thin, js_shell, paywall_challenge, truncated, wrong_body, other.",
    ] = None,
    detail: Annotated[
        str | None, "Details (<=2000 chars; required when reason is 'other')."
    ] = None,
    flagged_by: Annotated[str | None, "Reporter label (<=100 chars)."] = None,
    clear: Annotated[bool, "When true, clear the flag instead of setting it."] = False,
) -> dict[str, Any]:
    """Flag (or clear) an extraction problem on one article.

    Use only for improperly extracted content (thin body, JS shell,
    paywall/bot challenge, truncated text, wrong body) — never for factual
    disputes about an otherwise well-extracted article. This is one of two
    mutating tools on this server (with mark_article_read).

    Args:
        identifier: Article URL or canonical URL.
        reason: One of FLAG_REASONS (thin, js_shell, paywall_challenge,
            truncated, wrong_body, other).
        detail: Free-text detail, max 2000 chars; required when
            reason is "other".
        flagged_by: Reporter label, max 100 chars.
        clear: When True, clear the flag (NULLs flag columns) instead of
            setting it.

    Returns:
        The updated article dict with flag columns applied or cleared.

    Raises:
        InvalidRequest: If the identifier is unknown or flag fields are
            invalid (bad reason, missing/oversize detail, oversize
            flagged_by).
    """
    return service.flag_extraction(  # type: ignore[attr-defined, no-any-return]
        identifier, reason=reason, detail=detail, flagged_by=flagged_by, clear=clear
    )


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=False))
def mark_article_read(
    identifier: Annotated[str, "Article URL or canonical URL to mark."],
    read_by: Annotated[str | None, "Reader label (<=100 chars)."] = None,
    clear: Annotated[bool, "When true, clear the read mark instead of setting it."] = False,
) -> dict[str, Any]:
    """Mark (or clear) an article as read.

    Use to record consuming a document; idempotent — re-marking updates
    read_at/read_by, clearing NULLs the read columns.

    Args:
        identifier: Article URL or canonical URL.
        read_by: Reader label, max 100 chars.
        clear: When True, clear the read mark instead of setting it.

    Returns:
        The updated article dict with read columns applied or cleared.

    Raises:
        InvalidRequest: If the identifier is unknown/blank or read_by is
            invalid (non-string, overlong).
    """
    return service.mark_article_read(identifier, read_by=read_by, clear=clear)


@mcp.resource("brain://about", mime_type="application/json")
def read_about() -> str:
    """Static overview: coverage and recommended workflow."""
    return json.dumps(
        {
            "server": "Marketing Intelligence",
            "what": "Persistent marketing/GenZ/culture/tech intelligence platform "
            "(not a newsletter generator): deterministic ingestion into a "
            "Postgres system of record, served to AI digest consumers.",
            "v1_sources": "20 curated V1 sources (RSS + sitemap/hub/url-set lanes).",
            "workflow": "search_articles for discovery -> get_article for full text -> "
            "get_period_context(from_date, to_date) for any period bundle "
            "(a digest is built from a caller-chosen range) -> flag_extraction for bad content.",
            "resources": ["brain://about", "article://{identifier}", "period://{from}/{to}"],
            "prompts": ["period_digest", "investigate_topic"],
        }
    )


@mcp.resource("article://{identifier}", mime_type="application/json")
def read_article(identifier: str) -> str:
    """Full stored article as JSON, by URL/canonical URL."""
    return json.dumps(service.get_article(identifier))


@mcp.resource("period://{from_date}/{to_date}", mime_type="application/json")
def read_period_bundle(from_date: str, to_date: str) -> str:
    """Evidence bundle as JSON for an ISO date range."""
    return json.dumps(service.get_period_context(from_date, to_date))


@mcp.prompt()
def period_digest(period: str, focus: str | None = None) -> str:
    """Scaffold for drafting a digest from a period evidence bundle.

    Args:
        period: Human label for the bundle range (e.g. "2026-08-31 to 2026-09-07").
        focus: Optional theme to emphasize (e.g. "GenZ culture").
    """
    scope = f" with focus on {focus}" if focus else ""
    return (
        f"Draft a digest for {period}{scope} "
        "from get_period_context important_articles only. "
        "Cite every claim with its article URL/provenance, "
        "separate observations from interpretations, "
        "and flag (do not silently fix) any badly extracted content via flag_extraction."
    )


@mcp.prompt()
def investigate_topic(keyword: str) -> str:
    """Scaffold for a deep dive on one topic from stored evidence.

    Args:
        keyword: Search term to start the investigation.
    """
    return (
        f"Investigate '{keyword}': call search_articles(keyword={keyword!r}), "
        "then get_article for the most relevant hits, then synthesize what the "
        "stored evidence supports with URL citations, "
        "clearly separating observations from interpretations."
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
