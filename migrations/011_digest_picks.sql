-- 011_digest_picks.sql — digest selection trace (Ticket 22).
-- One row per (digest_date, document_id): the digest dated `digest_date`
-- selected this Document. Unlike document_importance / document_topics this
-- table is a *current set*, not an append-only audit trail: the lane
-- reconciles the stored set to the caller's (possibly human-edited) pick set
-- — inserts the added Documents, deletes the removed ones — so a row always
-- means "this Document is a pick for that digest date right now".
-- Idempotent per (digest_date, document_id) via the UNIQUE constraint:
-- re-recording an unchanged pick cannot duplicate it (the reporter is
-- refreshed when a later recording supplies one; created_at stays the first
-- record time, so the trace keeps its original timestamp). `clear_digest_picks`
-- deletes a date's rows outright — no tombstones, because the trace is a
-- per-date set that review reads as it stands.
--
-- document_id FK to documents (cascade on delete); reporter optional;
-- created_at defaults to now() (transaction time, matches 001/009/010
-- convention); id is the surrogate key. The UNIQUE (digest_date, document_id)
-- constraint doubles as the index for the digest-date read/clear lookups.
-- Idempotent DDL (IF NOT EXISTS everywhere); yoyo version tracking makes
-- rollback/re-apply safe (plain .sql: rollback unmarks, DDL stays).

CREATE TABLE IF NOT EXISTS digest_picks (
  id BIGSERIAL PRIMARY KEY,
  digest_date DATE NOT NULL,
  document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  reporter TEXT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT digest_picks_date_document_key UNIQUE (digest_date, document_id)
);
