-- 010_topics.sql — controlled Topic tags on Documents (Ticket 20).
-- Append-only history: every set_document_topics call appends one row per
-- assignment; the newest row per (document_id, topic_slug) — by created_at,
-- then id — is the effective one. A later row with assigned = FALSE is a
-- retirement tombstone (the tag is cleared) and nothing is ever deleted, so
-- the full tagging history is retained. Idempotent DDL (IF NOT EXISTS
-- everywhere); yoyo version tracking makes rollback/re-apply safe (plain
-- .sql: rollback unmarks, DDL stays).
--
-- topic_slug holds a canonical slug from the TOPICS registry
-- (marketing_intelligence.topics); the lane canonicalizes synonyms and
-- rejects unknown tags before writing, so non-canonical values never land
-- here. document_id FK to documents (cascade on delete); reporter optional;
-- created_at defaults to now() (transaction time, matches 001/009
-- convention); id is the deterministic tiebreak within one transaction.

CREATE TABLE IF NOT EXISTS document_topics (
  id BIGSERIAL PRIMARY KEY,
  document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  topic_slug TEXT NOT NULL,
  assigned BOOLEAN NOT NULL DEFAULT TRUE,
  reporter TEXT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_document_topics_document_created
  ON document_topics (document_id, created_at DESC, id DESC);
