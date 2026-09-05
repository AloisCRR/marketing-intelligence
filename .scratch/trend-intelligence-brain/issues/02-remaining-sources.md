# 02 — Remaining 3 sources on the same contract

**What to build:** All four V1 sources flowing through the unchanged adapter contract, each independently repairable, with one source's failure leaving the others intact.

**Blocked by:** 01 — Foundation + first source end-to-end.

**Status:** ready-for-human — implemented, needs human verify + accept

- [x] MarTech, Professional Jeweller, and InfoMoney ingest via the 01 adapter contract with no contract changes
- [x] Each source can be rerun independently of the others
- [x] A failing source is recorded explicitly and does not block successful sources

## Verification (2026-09-05, agent)
- Fixture ingest per source: rerun is a no-op; contract fields incl. language en/en/pt + tz-aware timestamps verified.
- `ingest_sources_flow` / `ingest_all_sources_flow` return per-source {inserted, skipped[, error]}; one failing fetch leaves the others intact.
- `NormalizedDocument` untouched (additive optional `language` params only).
- `mypy src`: clean (8 files). `pytest`: 42 passed, 1 skipped (live-DB guard).
- @oracle review: ACCEPT, no blocking findings.

## Pre-04 decision (from review, not blocking)
- V1 scope ambiguity: `ingest_all` defaults to all 6 RSS-capable registry sources (incl. JCK Online + Swarovski PR Newswire) while the migration seeds the V1 four. Declare 4 vs 6 sources before 04 and align `ingest_all` default, seeds, and the `..._covers_all_four` test name.
