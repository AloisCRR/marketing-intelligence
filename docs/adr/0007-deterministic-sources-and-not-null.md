# ADR-0007 — Deterministic source seeds + NULL-source quarantine

**Status:** accepted
**Date:** 2026-09-06

## Context

Two schemas facts collided: `documents.source_id` is nullable (001) and the
ingest fallback writes NULL when a source name is unseeded (`ingest.py`
upsert path), so repeated ingestions accumulated unattributed rows. Identity is
`gen_random_uuid()` + `name UNIQUE`; dedupe is `sha256(title+content)`
(`marketing_intelligence/normalize.py`). `ingestion_runs` is keyed by `source_name TEXT`
(already `NOT NULL` per 003). `documents` carries **no** `source_name` column,
so a NULL-`source_id` row has no recoverable provenance.

## Decision

- Migration `007_deterministic_sources_and_not_null.sql` (yoyo, idempotent):
  re-asserts all 20 curated sources with `ON CONFLICT (name) DO NOTHING`.
  New rows take stdlib `uuid5(NAMESPACE_DNS,
  "trend-intelligence-brain:source:<name>")` ids (see `marketing_intelligence.db.source_uuid`; prefix kept for stable UUIDs);
  rows seeded by 001/006 keep their random ids — identity is the name UNIQUE
  column, so no backfill of old ids. **No new dependency**: deterministic ids
  need only the stdlib `uuid` module; adding a library for this would be
  duplication.
- Quarantine instead of backfill: NULL rows move to `quarantined_documents`
  (explicit table + reason + timestamp), then `documents` is cleaned. Guessing
  a source for them would fabricate evidence; deleting them would destroy it.
- `DELETE FROM ingestion_runs WHERE source_name IS NULL` removes only
  schema-violating rows (none possible under 003); valid run history is kept.
- `ALTER TABLE documents ALTER COLUMN source_id SET NOT NULL` runs **after**
  the quarantine in the same migration, so the guard holds on fresh and legacy
  DBs alike. Unknown-source upserts raise `ValueError` in `upsert_documents`
  (fail-fast guard — refuses the NULL `source_id` insert instead of writing
  unattributed rows); the exception propagates through the leaf flow and the
  batch flow records it as the explicit per-source
  `{inserted: 0, skipped: 0, error}` outcome. The DB-level `NOT NULL` is the
  backstop for any other writer.

## Consequences

- `marketing_intelligence.db` gains `source_uuid()`, `SEED_SOURCES`, `seed_sources()` and
  `quarantine_null_documents()` (stdlib-only, fake-connection friendly).
- `tests/test_db_seeds.py`: SQL-text contract (20 names, deterministic ids,
  quarantine-before-guard ordering) + helper idempotence + live scratch-DB
  end-to-end (NULL row quarantined, NOT NULL enforced, rerun no-op).
- Known follow-ups owned by other lanes (out of scope here): the exact
  file-list assertion in `tests/test_weekly_context.py` and the exact applied
   id lists in `tests/test_migrations_yoyo.py` (live tier) need the new `007`
   id appended. (`ingest.py` already fails loudly on unknown names — the
   ticket-13 `upsert_documents` guard raises `ValueError`, mapped to a
   per-source error by the batch flow.)
