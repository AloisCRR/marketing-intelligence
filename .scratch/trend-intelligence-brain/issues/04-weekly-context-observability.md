# 04 — Weekly context + observability

**What to build:** A caller passing an explicit week gets a grounded evidence bundle, and an operator can see ingestion health per source — no trend claims before history exists.

**Blocked by:** 02 — Remaining 3 sources; 03 — Dedupe + data-quality hardening.

**Status:** ready-for-human — implemented, needs human verify + accept

- [x] `get_weekly_context(from, to)` takes explicit dates interpreted in America/Panama and returns period + important articles with provenance
- [x] Ingestion-run and per-source health status are observable independently of article data
- [x] Contract tests cover the semantic interface; a seeded regression corpus exists for future extraction/ranking changes
- [x] No velocity or emerging-topic claims in V1 (documented as needing accumulated history)

## Verification (2026-09-05, agent)
- `get_weekly_context(from_date, to_date)`: naive inputs assumed Panama (TZ-independent), aware converted; date `to` covers full day; newest-first articles with full provenance; five trend keys present as explicit `[]` with documented no-history reason.
- `ingestion_runs` table (003) + `record_ingestion_run` / `get_source_health` (ok/error/unknown, latest-wins); flows record every outcome best-effort with result dicts unchanged.
- V1 scope declared: `V1_SOURCES` (SMT, MarTech, PJ, InfoMoney) defaults everywhere; extras by explicit name.
- Regression corpus: 5 fixtures with counts + pinned hashes in `tests/test_weekly_context.py`.
- `mypy src`: clean (10 files). `pytest`: 78 passed, 1 skipped (live-DB guard).
- @oracle review: ACCEPT, no blocking findings. Follow-ups applied: `[from, to)` + seeded-sources doc notes. Left open (review-documented): parse runs 3x on messy feeds (refactor if a 4th need appears); corpus pins first-doc hash only.

## Human follow-ups (no live Postgres in agent env)
- Run the 003 migration against a real DB (`BRAIN_RUN_DB_TESTS=1`); pre-002 canonical dupes need a human cleanup decision (probe in 003 header).
