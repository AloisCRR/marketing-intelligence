-- 008_read_state.sql — explicit read/unread markers (Read State lane).
-- Idempotent: safe to apply repeatedly (IF NOT EXISTS everywhere).
--
-- Nullable columns on documents (NULL = unread). Explicit mark/unmark only,
-- idempotent re-mark overwrites read_at/read_by; clear=True NULLs both.
-- Re-ingest uses ON CONFLICT DO NOTHING, so read state survives re-ingest
-- with no flow changes here.

ALTER TABLE documents ADD COLUMN IF NOT EXISTS read_at TIMESTAMPTZ;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS read_by TEXT;
