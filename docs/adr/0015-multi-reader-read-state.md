# ADR-0015 — Multi-reader Read State (`document_reads` set)

**Status:** accepted
**Date:** 2026-09-16

## Context

ADR-0008 put Read State in single-slot nullable columns on `documents`
(`read_at`/`read_by`, NULL = unread): one explicit mark per Document,
re-mark overwrites. With multiple MCP consumers, the slot destroys
evidence — agent B sees `read=true` but cannot tell whether it was
itself, agent A, or a human, so it cannot decide to skip vs re-read
(grill Q1–Q4: the confusion to prevent is self-vs-other consumption,
identity is caller-supplied free string ≤100 chars, query shape is
`readers[]` on the article payload, no new filter/list endpoints).

## Decision

- **New `document_reads(document_id FK, reader TEXT, read_at TIMESTAMPTZ)`,
  PK `(document_id, reader)`** (migration `014`). One row per
  `(document, reader)`; re-mark by the same reader updates its timestamp
  (upsert). Backfills one row per Document from the legacy
  `documents.read_at/read_by` where non-NULL. Set semantics follow the
  `digest_picks` precedent, not an array column (concurrent marks must
  not fight over one cell).
- **Legacy columns stay as latest-mark cache.** `documents.read_at/read_by`
  hold the max-`read_at` mark across all readers; `read` bool and
  `exclude_read` stay anyone-read. Zero read-path breakage; `readers[]`
  is additive (frozen ARTICLE/SEARCH/PERIOD key sets gain exactly one key).
- **Write semantics:** `read_by` becomes required on mark (blank → 422
  `InvalidRequest`; legacy anonymous rows survive only as `read=true` +
  `readers=[]` until re-marked). `clear` + `read_by` removes one reader;
  `clear` alone removes all (preserves today's reset path). Both caller
  surfaces go through the service adapter by construction.
- **Payload:** `readers: [{reader, read_at}]` sorted by `reader` asc,
  uncapped, on `get_article`/search/period items, alongside the
  `read/read_at/read_by` summary.

## Considered Options

- `readers JSONB/TEXT[]` array column on `documents`: rejected — concurrent
  per-reader marks serialize on one cell; no per-reader timestamp without
  structured elements; diverges from the side-table precedent
  (`document_payloads`, `document_image_texts`, `digest_picks`).
- Dropping `documents.read_at/read_by`: rejected — breaks every read path
  and caller for no gain; the cache keeps `exclude_read` and the `read`
  bool working unchanged.
- Anonymous marks in the set: rejected — a per-reader set cannot hold a
  readerless row; blank `read_by` is a caller error (422).
- Per-reader `exclude_read_by_me` filter / Document-first "what did reader
  Y read" listing: deferred — no demonstrated unread-queue use case;
  scope creep beyond the confusion-prevention goal.

## Consequences

- Migration `014` + backfill; lane upsert/delete + latest-mark cache
  refresh must be atomic per mark/clear (two writes, one transaction).
- Behaviour change: anonymous mark goes from accepted to 422 — the one
  breaking edge, confined to the write path.
- Retrieval annotates three paths (article/search/period); N+1 risk on
  list payloads must be handled lane-side (single join/batched fetch, not
  per-row queries).
- Deferred: per-reader exclude filter, reader listing endpoint, controlled
  reader vocabulary.
