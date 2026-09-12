# ADR-0009 — Controlled topic vocabulary (code registry + append-only tags)

**Status:** accepted
**Date:** 2026-09-11

## Context

Digest agents need to tag Documents so "Gen Z self-purchase" is one tag
everywhere instead of three spellings, and so any later emergence computation
can count the same theme across sources. Free-text tagging cannot deliver that:
spellings drift, unknown values silently enter the store, and removals would
erase the history a trend signal needs.

Ticket 20 asks for a discoverable vocabulary, server-side canonicalization,
tags visible wherever Documents are read, and additive vocabulary evolution.

## Decision

- **Vocabulary in code, read-only in the caller surface.** The registry lives in
  `marketing_intelligence.topics.TOPICS` (slug → `{kind, label, synonyms,
  retired_alias_of?}`) and is exposed by `list_vocabulary()` on both the HTTP
  (`GET /vocabulary`) and MCP (`list_vocabulary`) surfaces. Three controlled
  axes: `pillar`, `region`, `content-type` (`kind` is part of the listing so
  future filters can tell a theme from a geography from a format).
- **Canonicalization is server-side and total.** Matching ignores case,
  whitespace and separators (`-`/`_`/space); synonyms and retired aliases
  resolve to their canonical slug. Unknown or blank tags raise before any DB
  round-trip (`ValueError` → service `InvalidRequest` → HTTP 422 / MCP error)
  and are never stored as-is.
- **Storage is append-only with latest-wins.** Migration `010_topics.sql` adds
  `document_topics (id BIGSERIAL, document_id FK, topic_slug, assigned BOOLEAN,
  reporter, created_at)`. The effective set is the newest row per
  `(document_id, topic_slug)` ordered by `(created_at DESC, id DESC)`; a write
  appends assignments for new slugs and `assigned = FALSE` tombstones for
  removed ones. The effective set is exactly the last written list (`[]`
  clears), the history is never deleted, and a later write can restore a tag.
- **Visibility is on the Document.** Every search result, single read and
  period bundle item (plain and per-source-capped SELECTs) carries `topics` —
  the sorted effective canonical slugs, `[]` when unannotated — with no extra
  caller round-trip.
- **Evolution is additive.** Retire a slug by adding a `retired_alias_of`
  entry that points at its replacement; the old slug and its synonyms keep
  canonicalizing and stay listed forever. Slugs are never deleted or renumbered
  (same convention as migration filenames, ADR-0006).

Deviation from the delegating brief worth flagging: the brief said
`docs/adr/0002`, but 0002 is the existing Service Adapter ADR
(`0002-service-adapter-api-mcp.md`); this ADR takes the next free number.

## Consequences

- Vocabulary changes ship as code changes (deploy), not as DB rows; the
  registry is the single source of truth for canonical slugs.
- Ticket 21's importance/pillar filters build on the `topics` key and
  `canonicalize_topic()`; unannotated Documents must be handled explicitly by
  those filters (never silently dropped or silently top-ranked).
- Each read lane owns its effective-topic SQL (repo convention, as with the
  importance LATERAL): a future change to the effective-set rule must update
  `search.py`, `article.py` and `period.py` together, plus the lane in
  `topics.py`.
- Named-entity extraction, LLM classification and automatic tagging remain out
  of scope; topics stay agent-written.
