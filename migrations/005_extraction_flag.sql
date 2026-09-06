-- 005_extraction_flag.sql — agent-reported extraction quality markers (ADR-0005).
-- Idempotent: safe to apply repeatedly (IF NOT EXISTS everywhere).
--
-- Nullable columns on documents (overwrite on re-flag — no history table V1).
-- Only clear=true NULLs the columns; re-ingest uses ON CONFLICT DO NOTHING,
-- so flags survive re-ingest with no flow changes here.

ALTER TABLE documents ADD COLUMN IF NOT EXISTS flag_reason TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS flag_detail TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS flagged_at TIMESTAMPTZ;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS flagged_by TEXT;
