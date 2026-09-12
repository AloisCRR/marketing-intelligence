-- 009_importance.sql — agent-writable importance annotations (Ticket 19).
-- Append-only history: every write inserts a new row; the newest row (by
-- created_at, then id) is the effective annotation. Latest wins, full history
-- retained. Idempotent DDL (IF NOT EXISTS everywhere); yoyo version tracking
-- makes rollback/re-apply safe (plain .sql: rollback unmarks, DDL stays).
--
-- document_id FK to documents (cascade on delete); score REAL in [0, 1] is
-- enforced by the lane/service adapter (flagged/paywalled bodies are capped
-- at 0.3 server-side before the row is written); rationale/reporter optional.
-- created_at defaults to now() (transaction time, matches 001 convention);
-- id is the deterministic tiebreak for writes in one transaction.

CREATE TABLE IF NOT EXISTS document_importance (
  id BIGSERIAL PRIMARY KEY,
  document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  score REAL NOT NULL,
  rationale TEXT NULL,
  reporter TEXT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_document_importance_document_created
  ON document_importance (document_id, created_at DESC, id DESC);
