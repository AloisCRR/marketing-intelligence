# 01 — Migration 012 + document_payloads side table

**What to build:** `migrations/012_document_payloads.sql` creating `document_payloads(document_id UUID PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE, payload JSONB NOT NULL, retrieved_at TIMESTAMPTZ NOT NULL DEFAULT now())`, idempotent (`IF NOT EXISTS`), following the 005/008 precedent. Lane write-after-upsert helper keyed by URL, same pattern as the flag lane.

**Status:** ready-for-human

- [x] Migration applies cleanly on fresh + existing DB (`IF NOT EXISTS`, FK cascade)
- [x] Write helper inserts payload row after `upsert_documents`, keyed on url/canonical_url
- [x] Re-run is idempotent (second write for same document upserts, never duplicates)
- [x] `documents`, `NormalizedDocument`, `INSERT_SQL` untouched

## Comments
