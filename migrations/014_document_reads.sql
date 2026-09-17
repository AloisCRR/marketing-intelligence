-- 014_document_reads.sql — per-reader read marks on documents (ADR-0015:
-- set semantics side table).
-- Idempotent: safe to apply repeatedly (IF NOT EXISTS; the backfill uses
-- ON CONFLICT DO NOTHING, so re-applying neither duplicates rows nor rewrites
-- a read_at that a reader has since refreshed).
--
-- Why a side table: Read State used to be a single slot on `documents`
-- (`read_at, read_by`), so a second reader's mark silently overwrote the
-- first — who read what was not recoverable. `document_reads` makes the mark a
-- set: one row per (document, reader). Matches the document_importance /
-- document_topics / document_payloads / digest_picks precedent; `documents`
-- keeps its single INSERT writer and stays the evidence unit.
--
-- `documents.read_at / read_by` remain as a *cache* of the latest mark (the
-- newest row by read_at, NULL/NULL when the set is empty). Read-side queries
-- (article / search / period / `exclude_read`) keep reading that pair, so
-- anyone-read semantics and the `read` bool are unchanged; the read lane is
-- responsible for refreshing the cache in the same transaction as every
-- document_reads write (ADR-0013 write ordering: resolve the Document by
-- url / canonical_url, then write the side table).
--
-- PRIMARY KEY (document_id, reader) is what makes marking idempotent: re-marking
-- the same reader upserts in place (`ON CONFLICT ... DO UPDATE SET read_at =
-- now()`) and never duplicates. Rows are appended, never reconciled: read_at
-- defaults to now() (write time, the now() convention of 001/009/010/011/012/013)
-- and records when that reader re-read the document. ON DELETE CASCADE keeps
-- marks aligned with the documents they describe.
--
-- Backfill: every legacy single-slot mark with a real reader becomes one row
-- for that reader. Legacy reader-less marks (read_at set, read_by NULL/blank)
-- have no reader identity to attribute, so they are NOT copied — those
-- documents keep reading as read=true from the `documents` cache until they
-- are re-marked per reader.

CREATE TABLE IF NOT EXISTS document_reads (
  document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  reader TEXT NOT NULL,
  read_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (document_id, reader)
);

INSERT INTO document_reads (document_id, reader, read_at)
SELECT id, btrim(read_by), read_at
  FROM documents
 WHERE read_at IS NOT NULL
   AND read_by IS NOT NULL
   AND btrim(read_by) <> ''
ON CONFLICT (document_id, reader) DO NOTHING;
