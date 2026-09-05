# 03 — Dedupe + data-quality hardening

**What to build:** A brain that stays clean under repeats and messy feeds: exact duplicates collapse, bad items are visible rather than silently lost, and timestamps stay trustworthy.

**Blocked by:** 01 — Foundation + first source end-to-end (parallel with 02).

**Status:** ready-for-human — implemented, needs human verify + accept

- [x] Canonical-URL + content-hash exact dedupe: repeated ingestion is a no-op
- [x] Malformed items and missing optional fields are handled; incomplete records are identifiable for reprocessing/exclusion
- [x] Publication and retrieval timestamps stay distinct and timezone-aware
- [x] Story/event column is reserved but unused — independent coverage is preserved, not collapsed

## Verification (2026-09-05, agent)
- New `migrations/002_canonical_url_unique.sql` (UNIQUE on canonical_url; 001 never rewritten); INSERT uses bare `ON CONFLICT DO NOTHING` so url/canonical/hash collisions all land in `skipped`.
- Additive `ParseReport` + `parse_feed_with_report()` (parse_feed signature unchanged); flow surfaces `parse_skipped` only when nonzero.
- `mypy src`: clean (8 files). `pytest`: 59 passed, 1 skipped (live-DB guard).
- @oracle review: ACCEPT, no blocking findings. Follow-ups applied: dead `INIT_MIGRATION` removed, reasons-persistence tracking comment added.

## Notes for 04
- 002 backfill risk: pre-002 DBs with canonical dupes will fail the UNIQUE index until cleaned (probe: `SELECT canonical_url, count(*) ... HAVING count(*)>1`).
- Flow surfaces only the `parse_skipped` integer; per-item `skipped_reasons` should be persisted by 04.
- V1 scope (4 vs 6 registry sources) still open — 04 must declare it.
