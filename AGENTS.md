# AGENTS.md — Trend Intelligence Brain

Persistent data platform (not a newsletter generator): curated sources → deterministic ingestion → PG-backed knowledge → semantic API/MCP → AI digest consumer.
Full system spec: `.scratch/trend-intelligence-brain/spec.md`. Glossary/single source of truth: `CONTEXT.md`.

## Runnable commands

Grounded in `pyproject.toml` (`src/` layout, Python >=3.12) and `compose.yml` (`db` = Postgres 18 + pgvector).

```sh
pip install -e ".[dev]"   # or: pip install -e . && pip install pytest ruff mypy
pytest                     # testpaths = tests/
ruff check src tests && ruff format --check src tests
mypy src
docker compose up -d db        # Postgres 18 + pgvector (DATABASE_URL=postgresql://brain:brain@localhost:5433/brain)
```

## What this is

Five layers (spec §Solution): Source (curated sites/feeds) → Ingestion (Prefect `@flow/@task`, RSS+HTTP V1) → Data/Knowledge (Postgres 18 + pgvector, system of record) → Semantic/Query (`brain.service` adapter → thin HTTP + MCP parity surfaces) → AI (digest consumer).
V1 cuts (`CONTEXT.md`): RSS-only (4 sources), deterministic only (no LLM/embeddings/topics), search + `get_weekly_context(from, to)` only.

## How to work here

- Issues: `.scratch/<slug>/spec.md` + `.scratch/<slug>/issues/NN-<slug>.md` — one file per ticket, `Status:` line near top. Why: local markdown is the tracker, no GitHub Issues. See `docs/agents/issue-tracker.md`.
- Triage: five canonical labels (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). Why: skills speak roles; this repo maps them 1:1. See `docs/agents/triage-labels.md`. Only pick up `ready-for-agent` work.
- Domain: use exact glossary terms (`CONTEXT.md`); avoid listed synonyms (e.g. Ingestion Run, not Flow Run). Why: single-context repo, glossary prevents drift. See `docs/agents/domain.md`.
- ADRs: `docs/adr/` (0001 local PG18+pgvector, 0002 service-adapter parity, 0003 ephemeral Prefect). Flag contradictions explicitly instead of silently overriding.

## Domain context

- Source of truth: `CONTEXT.md` (glossary + V1 cuts) and `docs/adr/`. Curated sources: `.scratch/trend-intelligence-brain/curated-sources.json`.
- Boundaries: agent is a **consumer** of the semantic layer, not a scraper; callers use `brain.service` (`MAX_LIMIT=100`, `InvalidRequest` → HTTP 422), never raw SQL as primary access; ingestion stays deterministic and observable (rerunnable `ingest_source_flow` per source, explicit partial failure).

## Testing

Test observable behavior and data contracts, not private helpers: upsert/dedupe by hash, rerunnable ingestion runs, `GET /search` + `POST /weekly-context` payloads and 422 paths, HTTP↔MCP parity by construction. Run `pytest` before claiming done.

## Out-of-scope guardrails

- No custom ML / forecasting / anomaly detection (Phase 4, needs history first).
- No warehouse / Spark / Kafka — single self-hosted Postgres is the system of record.
- No dedicated vector DB — pgvector-native only.
- No universal scraper — curated sources only; Firecrawl/Playwright only per-source when RSS/HTTP fails (bot protection/paywall).
