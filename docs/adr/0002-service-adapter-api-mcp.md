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
  sources, limit, conn, exclude_read, per_source_limit)` with ISO-string
  coercion, unknown-source rejection, and a truthful period bundle
  (recency-ordered `recent_articles`, each item carrying a 1-based `rank`;
  no empty analytics placeholders). `per_source_limit` (int in [1, 100] or
  None) caps how many bundle items any one source may contribute — slots a
  capped source cannot fill go to other sources in recency order — and
  composes with the `sources` allowlist; None (default) is uncapped. (Ticket
  21 later added optional `min_importance`/`topics` filters to both read
  functions — see ADR-0010.)
- Thin adapters only: `src/api/app.py` (`GET /health`, `GET /search`, `POST
  /period-context`, `GET /article`, `POST /flag-extraction`, `POST /mark-read`,
  `GET /sources`, `POST /importance`, `GET /importance`, `GET /vocabulary`,
  `POST /topics`, `POST /digest-picks`, `GET /digest-picks`, `DELETE
  /digest-picks`, `InvalidRequest` → 422, `/docs` for demo) and
  `src/mcp/server.py` (13 tools calling the same service functions:
  `search_articles`, `get_period_context`, `get_article`,
  `list_sources_inventory`, `flag_extraction`, `mark_article_read`,
  `set_importance`, `get_importance`, `list_vocabulary`,
  `set_document_topics`, `record_digest_picks`, `get_digest_picks`,
  `clear_digest_picks`).
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
- Out of scope (unchanged): no digest schedule, no embeddings, no automatic
  topic/entity extraction (Documents carry agent-written controlled Topics —
  see ADR-0009). The optional `min_importance`/`topics` search/period filters
  landed later in ADR-0010.
