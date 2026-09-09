# 05 — Shared service adapter + API with MCP parity

**What to build:** One validated service interface (`marketing_intelligence.service`) with two thin, identical surfaces: HTTP (`GET /search`, `POST /weekly-context`) and MCP (2 tools). Same payloads by construction, `InvalidRequest` → 422.

**Blocked by:** 01–04 (verified live: sources=4, documents=10 SMT, idempotent ingest, provenance reads GO). Do NOT re-ingest; use existing rows + fake-conn tests.

**Status:** ready-for-human — implemented, needs human verify + accept

- [x] `src/marketing_intelligence/service.py` (stdlib-only): `MAX_LIMIT=100`, `InvalidRequest`, `search_articles(keyword,limit,conn)` + `get_weekly_context(from,to,sources,limit,conn)` with string coercion, unknown-source rejection, V1 trend keys `[]`, Panama tz handling
- [x] `src/marketing_intelligence/search.py`: optional `conn` pass-through (backward-compatible, no behavior change)
- [x] `src/api/app.py`: `GET /search`, `POST /weekly-context`, `InvalidRequest` → 422, `/docs` demoable
- [x] `src/mcp/server.py`: 2 tools calling the same service fns (no `__init__.py` — see ADR-0002 for the `mcp` name-collision reason)
- [x] `tests/test_service_contract.py` (fake conns), `tests/test_api_contract.py` (TestClient, monkeypatched service), `tests/test_mcp_parity.py` (API JSON == MCP tool result)
- [x] `pyproject.toml`: `fastapi>=0.115`, `uvicorn[standard]>=0.30`, `mcp==1.29.1` (pinned, 1.x FastMCP API), `httpx>=0.28` for TestClient
- [x] `docs/adr/0002-service-adapter-api-mcp.md`, CONTEXT.md glossary delta
- [x] `mypy src` clean; `pytest` green without live DB

## Verification (2026-09-05, agent)
- New: 22 passed (`test_service_contract` + `test_api_contract` + `test_mcp_parity`).
- Full: `pytest`: 100 passed, 1 skipped (live-DB guard). `mypy src`: no issues in 14 files.
- Live curl demo against Postgres 5433 (read-only, existing 10 SMT rows): `GET /search?q=TikTok` → 2 results with 7-key provenance; `POST /weekly-context` 2026-09-01→2026-09-05 → 10 `important_articles`; `limit=101` → 422.

## Human follow-ups
- Serve: `uv run uvicorn api.app:app --port 8000` (from `src/`, or set `PYTHONPATH=src`); MCP: `uv run python src/mcp/server.py`.
- Deliberately open: digest schedule, search filters, embeddings/topics (per V1 cuts).
