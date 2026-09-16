-- 013_document_image_texts.sql — OCR/vision text per document frame (ADR-0014).
-- Idempotent: safe to apply repeatedly (IF NOT EXISTS; no seed rows here — the
-- pilot source is owned by its own ticket).
--
-- Why a side table: `documents` stays the evidence unit (NormalizedDocument
-- columns only) and keeps its single INSERT writer; the vision lane stores the
-- text it read out of a document's images separately, so a re-run with a newer
-- model re-derives image text without rewriting the Document. Matches the
-- document_importance / document_topics / document_payloads precedent.
--
-- Write ordering: the lane upserts the Document first (keyed by url /
-- canonical_url, ADR-0013), then resolves its id and inserts the frames'
-- texts, so the FK below is always satisfied. `(document_id, frame_index)` is
-- the PRIMARY KEY — one row per frame per document — so re-extracting a frame
-- upserts in place (`ON CONFLICT ... DO UPDATE`) and never duplicates. Rows are
-- appended, never reconciled: `extracted_at` defaults to now() (write time,
-- the now() convention of 001/009/010/011/012) records when that frame's text
-- was produced. ON DELETE CASCADE keeps texts aligned with the documents they
-- describe.
--
-- `frame_index` follows `enumerate_frame_urls` order (cover frame 0, then
-- childPosts in order); `model` records the extracting model id so a later
-- model's output is distinguishable without a schema change.

CREATE TABLE IF NOT EXISTS document_image_texts (
  document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  frame_index INT NOT NULL,
  image_text TEXT NOT NULL,
  model TEXT NOT NULL,
  extracted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (document_id, frame_index)
);
