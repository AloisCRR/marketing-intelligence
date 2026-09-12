# ADR-0004 — Article-content lane: deliberate narrowings of standing cuts

**Status:** accepted
**Date:** 2026-09-05

## Context

The article-content lane (`.scratch/article-content/spec.md`, "Resolved
contradictions") could not be built without narrowing three standing V1 cuts,
and it swaps the spec's named primary extractor for a dependency-free one
(since replaced by trafilatura-only; see (d)). Per repo convention these
deviations are flagged explicitly instead of being silently overridden: the
Postgres-plus-pgvector (ADR-0001), service-adapter parity (ADR-0002), and
ephemeral-Prefect run-shape (ADR-0003) decisions stand unchanged except where
noted below.

## Decision

- (a) Lookup surface added beyond the "search + weekly only / 2 tools" V1 cut:
  `GET /article` plus a third MCP tool `get_article` (full stored body plus
  provenance, by URL/canonical URL). Both stay thin adapters over the shared
  `marketing_intelligence.service` adapter, so HTTP↔MCP parity holds by construction and the
  ADR-0002 consequence (validation once in the service) is extended, not
  broken. Weekly/search list payloads, limits, and ranking are unchanged.
- (b) Jina-reader gated fallback under the thin-trigger, inside the
  no-Firecrawl / no-universal-scraper guardrail: the fallback fires only on a
  primary-miss signal (bot/challenge/rate-limit evidence, empty primary
  output, or JS-shell output), sends data-minimizing headers (explicit
  User-Agent, `Accept: text/*`, never Cookie/Authorization), honors 429
  Retry-After up to 2 retries, and never blocks an Ingestion Run (RSS body
  kept with a recorded cause). No Firecrawl/Playwright provisioning, no
  universal scraper.
- (c) Optional `enrich_skipped` / `enrich_causes` keys extend the ADR-0003
  run-result shape: present only when nonzero, so clean-feed results keep
  their exact `{inserted, skipped}` shape; each cause entry carries method
  plus cause (`"rss: <cause>"` whenever the stored body stayed RSS), and the
  same entries flow into `record_ingestion_run`'s `skipped_reasons`.
- (d) **Superseded 2026-09-12 — trafilatura-only HTML extraction.** Accepted
  deviation (original): the spec names Crawl4AI as the primary extractor; the
  lane built a stdlib urllib+regex Markdown cleaner instead (no new
  dependencies, deterministic, hermetic tests). The regex-primary era is
  superseded: HTML-to-Markdown extraction runs a single converter,
  trafilatura (hard dependency), and the legacy regex converter plus the
  HTML-level National Jeweler boilerplate scanner are deleted. An HTML body
  trafilatura cannot extract yields empty Markdown — no tag-stripped fallback
  dump — so callers keep their thin-check / keep-RSS / `UnparseableBody`
  semantics. The Markdown-level National Jeweler post-processing (inline
  editorial anchors flattened to plain words, boilerplate trailer cut) is kept
  because the family fixtures fail without it: dropping it leaves
  `nj_article_genz_polluted.html` at 883 chars with `](`/URL links instead of
  the pinned 756-char `nj_article_genz_clean.md` (closure page: 726 vs the
  pinned 646). trafilatura's `deduplicate` flag stays off for every URL: its
  process-global LRU segment cache discards a re-extracted article, which the
  deleted regex fallback used to mask. Crawl4AI/Forage remains an approved
  future swap with no contract change (same stored-body and cause shapes).

## Consequences

- HTML bodies extract through trafilatura only: unextractable input (stubs,
  lock pages, non-HTML) produces empty Markdown rather than a degraded
  tag-stripped dump, so the thin-check / keep-RSS / `UnparseableBody`
  behavior downstream is unchanged and no regex path exists to regress into.
- `tests/test_mcp_parity.py` pins 3 tools, not 2; `tests/test_feature_parity.py`
  pins list-shape stability and HTTP↔MCP lookup identity.
- Pre-enrichment shape tests stay hermetic via an identity-enrich stub so
  they keep covering parse/dedupe, not enrichment.
- Object-storage raw archives, mass backfill, embeddings/topics, and MCP
  internet exposure remain out of scope per the lane spec.
