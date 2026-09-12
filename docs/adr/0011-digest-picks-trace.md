# ADR-0011 — Digest picks trace (current-set table + diff as re-scoring signal)

**Status:** accepted
**Date:** 2026-09-11

## Context

Ticket 21 made the filtered week retrievable (`min_importance` + `topics` +
`per_source_limit`), so the digest funnel could select evidence in one call —
but nothing recorded *which* Documents a published digest actually used. Without
that trace there is no "what did we miss" review and no labeled dataset for
future ranking. Ticket 22 asks for a per-Document pick trace that round-trips
(set → read back → clear), for human edits to a published digest to be
capturable as re-scoring signal, and for a digest prompt rebuilt around the
score → tag → select → summarize funnel with the importance rubric versioned in
the prompt itself.

The existing annotation lanes (importance, topics) are append-only audits with
latest-wins reads; a pick set is a different shape — the digest for a date has a
pick set that is meant to be rewritten (a human edits it, an edition is
discarded), and the interesting artifact of a rewrite is the *difference*.

## Decision

- **`digest_picks` is a current set, not an audit trail.** Migration
  `011_digest_picks.sql` adds `digest_picks (id BIGSERIAL, digest_date DATE,
  document_id FK, reporter, created_at)` with `UNIQUE (digest_date,
  document_id)` (which also indexes the digest-date reads/clear). A row means
  "this Document is a pick for that digest date right now"; the lane
  (`marketing_intelligence/digests.py`) reconciles the stored set to the
  caller's URL list (insert added, delete removed) instead of appending
  tombstones. Re-recording an unchanged pick is idempotent per
  `(digest_date, document_id)` — no duplicate row, original `picked_at`
  preserved (the reporter is refreshed only when a later recording supplies
  one).
- **The diff is the re-scoring signal.** `record_digest_picks(digest_date,
  identifiers, reporter)` returns the read-back `picks` plus `added` /
  `removed` canonical identifiers. A human editing a published digest re-records
  the edited URL set; the diff names the Documents whose importance was under-
  or over-weighted, and the agent revisits them with `set_importance`.
  `clear_digest_picks(digest_date)` drops a discarded edition without a diff.
- **Reads stay digest-first; frozen retrieval payloads are untouched.**
  `get_digest_picks(digest_date)` returns each pick's Document identity
  (`url`, `canonical_url`, `title`, `source`, `published_at`) plus `reporter`
  and `picked_at`. Adding `digest_dates` to the single-article/search/period
  payloads would widen the frozen key sets (and each lane's SQL/fakes) for a
  convenience signal; the ticket's stated fallback is taken: Document-first
  discovery cross-references `get_digest_picks` picks with `get_article`, and
  the annotation-era key contracts stay as they are.
- **The rubric lives in the prompt.** `period_digest` now spells out the full
  funnel (recall the week → score with **IMPORTANCE RUBRIC v1** → tag via the
  canonical vocabulary → select source-balanced via `min_importance` /
  `topics` / `per_source_limit` → summarize with citations → record picks) and
  carries the rubric bands in its text. No server-side scoring logic is added;
  bumping the rubric means a new versioned string in the prompt, not a code
  change to the ranking lanes.
- **Parity via the adapter.** Both surfaces call the same three service
  functions (`record_digest_picks` / `get_digest_picks` / `clear_digest_picks`);
  unknown URLs, malformed dates and bad reporter values raise `InvalidRequest`
  → HTTP 422 / MCP tool error identically by construction.

## Consequences

- Pick history is not retained: re-recording loses the prior set (only the
  returned diff describes the edit). That is deliberate — the trace serves
  "what did the digest pick", and the append-only audit lanes already cover
  the annotations that outlive an edition.
- `record_digest_picks(..., identifiers=[])` reconciles a date's set to empty,
  which is functionally `clear_digest_picks`; the latter is the explicit
  "discard this edition" verb.
- Future ranking work can join `digest_picks` to `document_importance` /
  `document_topics` for a labeled dataset without changing these lanes.
- Scheduling, automatic digest generation, and analytics remain out of scope.
