-- 012_document_payloads.sql — raw retrieval payload side table (ADR-0013).
-- Idempotent: safe to apply repeatedly (IF NOT EXISTS everywhere; the source
-- re-assert is ON CONFLICT (name) DO NOTHING).
--
-- Why a side table: `documents` stays the evidence unit (NormalizedDocument
-- columns only) and keeps its single INSERT writer; the premium Instagram
-- lane additionally stores the full actor result as JSONB so later lanes can
-- re-derive fields (metrics, comments) without re-billing the scrape. Matches
-- the document_importance / document_topics / digest_picks precedent.
--
-- One payload per document: `document_id` is the PRIMARY KEY, so the lane's
-- write-after-upsert (keyed by url/canonical_url, ADR-0013) upserts in place
-- and never duplicates. ON DELETE CASCADE keeps payloads aligned with the
-- documents they describe. `retrieved_at` is the write time (transaction
-- time, the now() convention of 001/009/010/011).

CREATE TABLE IF NOT EXISTS document_payloads (
  document_id UUID PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
  payload JSONB NOT NULL,
  retrieved_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Re-assert the ADR-0013 premium source row (`db.source_uuid("ig:sabrikolod")`
-- — same stdlib uuid5 scheme as migration 007, literal embedded the same way).
-- ON CONFLICT (name) DO NOTHING: identity is the name UNIQUE column (ingest
-- resolves source_id by name), so a legacy gen_random_uuid() row for the same
-- name is preserved and never re-keyed.
INSERT INTO sources (id, name, rss_url, hub_url, language, enabled)
VALUES ('2554e4eb-8e39-5c76-8536-79cfc3d0cf08', 'ig:sabrikolod', NULL, 'https://www.instagram.com/sabrikolod/', 'es', true)
ON CONFLICT (name) DO NOTHING;
