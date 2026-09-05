-- 002_canonical_url_unique.sql — canonical-URL exact dedupe (Ticket 03).
-- Idempotent: safe to apply repeatedly (IF NOT EXISTS).
-- Closes the canonical-dedupe hole from 001 (UNIQUE on url only): two URLs
-- differing solely by query/fragment share a canonical_url and must collapse
-- to one row. Persistence uses bare `ON CONFLICT DO NOTHING`, so url,
-- canonical_url, and content_hash collisions all surface as skipped rows.

CREATE UNIQUE INDEX IF NOT EXISTS uq_documents_canonical_url
  ON documents (canonical_url);
