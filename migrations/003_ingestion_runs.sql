-- 003_ingestion_runs.sql — ingestion run history + per-source health (Ticket 04).
-- Idempotent: safe to apply repeatedly (IF NOT EXISTS everywhere).
--
-- 002 backfill probe (comment only — NO data cleanup statements here):
--   Canonical duplicates ingested before 002's UNIQUE index are historical
--   rows, not live bugs. To inspect them before any human-approved cleanup:
--     SELECT canonical_url, COUNT(*) AS dupes
--       FROM documents
--      GROUP BY canonical_url
--     HAVING COUNT(*) > 1
--     ORDER BY dupes DESC;
--   Any deduplication/backfill is a deliberate human decision in a later lane.

CREATE TABLE IF NOT EXISTS ingestion_runs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  source_name TEXT NOT NULL,
  started_at TIMESTAMPTZ NOT NULL,
  finished_at TIMESTAMPTZ NOT NULL,
  inserted INT NOT NULL DEFAULT 0,
  skipped INT NOT NULL DEFAULT 0,
  parse_skipped INT NOT NULL DEFAULT 0,
  error TEXT NULL,
  skipped_reasons TEXT[] NULL,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ingestion_runs_source_finished
  ON ingestion_runs (source_name, finished_at DESC);
