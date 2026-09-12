# ADR-0002 — Shared service adapter with HTTP + MCP parity

**Status:** accepted
**Date:** 2026-09-05

## Context

Tickets 01–04 built DB-backed lanes (`search_articles`, `get_weekly_context`,
run health) with fake-conn unit tests and a live-DB verification (10 Social
Media Today rows, idempotent ingest, provenance reads). Ticket 05 must expose
them to callers twice — HTTP API and MCP tools — without letting the two
surfaces drift apart in validation or payload shape.

## Decision

- Single validated interface in `src/marketing_intelligence/service.py` (stdlib-only: no
  fastapi/mcp imports in `marketing_intelligence/`): `MAX_LIMIT=100`, `InvalidRequest`, and
  `search_articles(keyword, limit, conn)` + `get_period_context(from, to,
  sources, limit, conn)` with ISO-string coercion, unknown-source rejection,
  and a truthful period bundle (recency-ordered `recent_articles`, each item
  carrying a 1-based `rank`; no empty analytics placeholders).
- Thin adapters only: `src/api/app.py` (`GET /search`, `POST
  /period-context`, `GET /sources`, `InvalidRequest` → 422, `/docs` for demo) and
  `src/mcp/server.py` (6 tools calling the same service functions:
  `search_articles`, `get_period_context`, `get_article`,
  `list_sources_inventory`, `flag_extraction`, `mark_article_read`).
- `src/mcp/` intentionally has **no `__init__.py`**: the directory name
  collides with the installed `mcp` distribution, so a regular package here
  would shadow the dependency (or vice versa). The server module is run as a
  script (`uv run python src/mcp/server.py`) and loaded by file path in tests.
- `marketing_intelligence.search.search_articles` gains an optional injected `conn`
  (backward-compatible: `None` still opens/closes its own connection;
  injected conns are never closed here).

## Consequences

- Parity is structural (shared functions), pinned by
  `tests/test_mcp_parity.py` (API JSON == MCP tool result, both paths).
- Validation lives once in the service; adapters only translate
  `InvalidRequest` to 422 / tool errors.
- Pinned `mcp==1.29.1` (stays on the 1.x `FastMCP` API; 2.x renamed it to
  `MCPServer` — revisit deliberately, not by accident).
- Out of scope (unchanged): no digest schedule, no search filters, no
  embeddings/topics.
