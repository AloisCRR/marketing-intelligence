# ADR-0010 — Annotation-aware retrieval filters (importance floor + Topic filter)

**Status:** accepted
**Date:** 2026-09-11

## Context

Ticket 19 scores Documents 0–1 and Ticket 20 tags them with canonical Topics.
Those annotations are stored and visible on every read, but until now the
digest consumer still had to over-fetch and juggle sources by hand to build a
"what mattered this week" view. Ticket 21 asks for one call that combines an
importance floor, a Topic (pillar) filter and the existing per-source cap, and
for keyword search to reuse the same filters. It also demands an explicit,
documented answer for Documents nobody has annotated yet, and parity + 422
across HTTP and MCP.

## Decision

- **Two new read filters, both optional, both composable.** `min_importance`
  (float in `[0, 1]` or `null`) and `topics` (`list[str]` or `null`) are added
  to `service.get_period_context` / `service.search_articles` and to both thin
  surfaces (`GET /search` query params, `POST /period-context` body fields;
  MCP tool args). They compose with `sources`, `exclude_read` and
  `per_source_limit` in a single call.
- **Importance floor predicate is `imp.score >= floor`.** Because the latest
  score is read through the existing `LATERAL` join, PostgreSQL NULL semantics
  give the documented unannotated rule for free: a Document with no importance
  row has a NULL score, so it is **excluded only when a floor is set**. No
  coalescing to 0, no default score — the absence stays visible.
- **Topic filter is canonical with array-overlap semantics.** Caller tags are
  canonicalized by the vocabulary lane (`topics.canonicalize_topic`):
  synonyms, case/whitespace variants and retired aliases resolve to their slug;
  an unknown tag raises `InvalidRequest` before any query (HTTP 422, MCP tool
  error) and is never queried as-is. A Document matches when it carries **at
  least one** requested slug (`tps.topics && %s::text[]`), mirroring the
  "any of these sources" shape of the existing `sources` allowlist. An empty
  list adds no constraint; a Document with no topics is excluded only when a
  non-empty filter is set.
- **Ordering only changes when the caller asks.** Without a floor the bundle
  and search results stay purely recency-ordered (annotations never reorder an
  unfiltered query, so an unannotated Document is never silently top-ranked).
  With a floor the order becomes importance-first, ties by recency, with an
  explicit `NULLS LAST` as belt-and-braces (a NULL cannot satisfy the floor
  anyway).
- **One SQL composition point per lane.** `period._annotation_filters` /
  `_bundle_sql` and `search._search_sql` build the optional predicates and the
  matching parameter order; the per-source window ranks rows using the same
  ordering, so a capped source's freed slots go to the next source under
  importance (floored) or recency (unfloored) order.
- **Validation belongs to the adapter.** Range/type checks and Topic
  canonicalization live in `service` (`_validate_min_importance`,
  `_validate_topic_filter`), keeping HTTP and MCP identical by construction;
  the lanes re-validate defensively for direct callers.

## Consequences

- The success criterion is one call: `min_importance=0.7`, `topics=["fashion"]`
  (or any caller synonym) and `per_source_limit=2` over a week returns an
  on-pillar, importance-ranked, source-spread bundle with no manual juggling.
- Unannotated Documents are a first-class, documented case: they are never
  silently dropped from an unfiltered query and never silently top-ranked; a
  floor or a topic filter is a deliberate, caller-visible exclusion.
- Ordering semantics are now conditional in both retrieval lanes; any future
  "rank by importance" claim must go through the floored path (or a new
  explicit parameter) rather than changing the default.
- Each read lane still owns its effective-topic SQL (ADR-0009), so a change to
  the effective-set rule must update `search.py` and `period.py` together.
- Digests picks/feedback (Ticket 22) and any automatic annotation remain out of
  scope; filters read what agents have already written.
