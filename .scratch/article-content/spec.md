# Article Content for MCP — Specification

Status: ready-for-agent

## Problem Statement

The autonomous AI agent consuming the semantic layer gets Weekly Context bundles and search hits that carry provenance (title, URLs, Source, timestamps, author, snippet) but no full Article text. The Weekly Context deliberately stays light so list responses do not bloat, which leaves the agent with titles and links only and forces it to re-fetch the open web per item — exactly the scraping/ETL work the platform exists to keep off the agent. RSS-only ingestion stores the feed summary as the Document body, so even the database has no clean full text to consult. The agent needs full post content as clean Markdown, consultable on demand, without making list endpoints heavy.

## Solution

Keep Weekly Context and search lists light (provenance only, as today). Store clean Markdown full text as the canonical Document body in Postgres (the existing system of record, same text column, hash over stored text), enriched at ingest time with a thin-only trigger. Expose one dedicated one-item lookup (`get_article` by URL/canonical URL/identifier) returning full Markdown plus full provenance, on both caller surfaces (HTTP and MCP) through the single Service Adapter so payloads stay identical by construction. Enrichment uses Crawl4AI as primary with Jina Reader as fallback (service alternative: Forage; paid last resort: Firecrawl cloud), per-item failure isolated: any extraction failure keeps the RSS text and records the cause on the Ingestion Run without blocking it.

## User Stories

1. As an autonomous AI agent, I want weekly evidence bundles to stay light (provenance only), so that large periods do not exhaust my context window.
2. As an autonomous AI agent, I want to fetch the full clean-Markdown text of one Article on demand, so that I have full context only for the items I actually use.
3. As an autonomous AI agent, I want the one-item lookup to work identically over HTTP and MCP, so that I learn one contract for both surfaces.
4. As an autonomous AI agent, I want invalid lookup input (unknown URL, blank identifier, out-of-range limit) to fail with the same validation shape as today, so that error handling stays uniform.
5. As a digest consumer, I want Weekly Context list payloads unchanged in shape, so that existing synthesis prompts keep working.
6. As a digest consumer, I want search hits unchanged in shape, so that keyword workflows keep working.
7. As a platform operator, I want full Markdown stored in Postgres as the system of record, so that there is one store, one transaction, and no pointer lifecycle.
8. As a platform operator, I want enrichment to run only when the RSS text is thin or missing (under a character threshold, per-Source overridable), so that fetch cost is paid only where the payoff exists.
9. As a platform operator, I want each article-URL fetch failure (paywall, bot protection, timeout, unparseable body) to keep the RSS text and record the cause per item, so that one bad page never aborts an Ingestion Run.
10. As a platform operator, I want each Source's Ingestion Run to stay independently rerunnable with explicit partial failure, so that retries and observability behave as today.
11. As a platform operator, I want deterministic extraction with no LLM at ingest, so that V1 stays deterministic and observable.
12. As a platform operator, I want the extraction stack to be local-first (no per-page fees, no data sharing by default), with a zero-ops fallback for JS/bot pages, so that cost and privacy stay controlled.
13. As a future embeddings lane, I want exactly one canonical stored text per Document, so that vectorization later is a single migration plus adapter change with no text-source ambiguity.
14. As a Source curator, I want per-Source enrichment opt-ins/overrides (threshold, force-on, force-off), so that paywalled or bot-protected Sources get deliberate policy instead of global behavior.

## Implementation Decisions

- Weekly Context and search list contracts are unchanged: provenance-only lists, same keys, same limits. Full text is never embedded into list responses.
- New capability is a one-item `get_article` lookup (by URL, canonical URL, or identifier — one canonical key decided at build time) returning the full stored Markdown plus full provenance, bounded to a single row so result-limit fan-out cannot apply.
- Both caller surfaces expose the lookup through the Service Adapter: the HTTP surface adds the lookup endpoint and the MCP surface adds the third tool, both calling the same validated service function with identical payloads by construction; validation failures map to the existing 422/tool-error shape.
- Canonical stored text is clean Markdown held in the existing Document text column; the dedupe hash is computed over the stored text. No schema migration for storage; optional provenance columns (format, method, extracted-at) are deferred.
- Enrichment is an Ingestion Stage inside the per-Source Ingestion Run: RSS-first parse as today, then a thin-only trigger (short/empty RSS text after stripping, or fetch signals such as paywall/bot/thin static output) decides whether to fetch the article URL and clean it to Markdown.
- Tool order: Crawl4AI primary (local-only, deterministic cleaned-HTML to Markdown with raw/fit variants, browser-backed for JS pages); Jina Reader gated fallback for pages the primary misses (privacy headers set, no credentials ever sent, backoff on rate limits). If the team prefers an HTTP service over an embedded crawler library, the approved swap is the single-container Forage service; Firecrawl cloud is reserved as a paid per-Source last resort and is not built now.
- Failure semantics: enrichment exceptions keep the RSS summary as the stored body, record method plus cause on the Ingestion Run's visible accounting, and continue the run. No item-level hard failure.
- Resolved contradictions: the standing RSS-only / no-Firecrawl cut is narrowed per-Source by this spec (enrichment fallback allowed under the thin-trigger); the Postgres-plus-pgvector and service-adapter-parity decisions stand unchanged; object storage stays deferred (raw HTML archives are a later concern, not this lane).
- License/ops notes carried into build: Crawl4AI attribution clause honored; Jina fallback uses data-minimizing headers; Forage options are GPL-3.0 and young (pin and smoke-test if chosen); self-hosted Firecrawl is out (loses its bot engine and costs six services).

## Testing Decisions

- Test external behavior and data contracts only, never private helpers: stored-body Markdown assertions via the service lookup, list-shape stability, validation/422 paths, rerunnable Ingestion Runs with explicit partial failure, HTTP↔MCP parity by construction.
- Seam 1 (query): the Service Adapter lookup plus unchanged list contracts — covers both surfaces at once; prior art is the existing search/weekly payload and 422-path tests.
- Seam 2 (ingestion): the enrichment Ingestion Stage — thin-trigger fires/skips, Markdown stored on success, RSS fallback plus recorded cause on failure, rerun inserts nothing new; prior art is the existing parse-report/skip-accounting and hash-dedupe rerun tests.
- Run the full pytest suite before claiming done; keep the deterministic, no-LLM-at-ingest property covered by asserting the enrichment path makes no model calls.

## Out of Scope

- Creating embeddings, topics, entities, velocity, or emerging-topic claims (still deferred; this lane only banks the canonical text that enables them later).
- Object-storage raw archives (HTML/PDF retention) and any Backblaze B2 provisioning.
- Internet exposure of MCP (Streamable HTTP transport, OAuth 2.1 resource-server hardening, proxy/rate-limit) — separate researched lane, separate spec.
- Firecrawl cloud provisioning or spend; mass backfill policy for already-stored Documents (follow-up decision once enrichment proves out per Source).
- Changing Weekly Context or search list payloads, limits, or ranking.

## Further Notes

- Grill record: Q1 lists stay light plus one-item lookup; Q2 Postgres text, B2 deferred; Q3 clean Markdown via Crawl4AI plus Jina; Q4 thin-only trigger; Q5 same-column storage; Q6 dedicated lookup with parity; Q7 fallback never blocks; Q8 tool pick confirmed. Seams confirmed before drafting.
- Evidence: content-extraction research (primary-sources only) with license/ops comparison; MCP transport and pgvector research stand as separate lanes with their own files.
- End state feeds `/to-tickets`: tracer-bullet tickets with blocking edges under `issues/`, worked blockers-first.
