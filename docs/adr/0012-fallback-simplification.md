# ADR-0012 — Fallback simplification: right service for the right job

**Status:** accepted
**Date:** 2026-09-12

## Context

The retrieval stack had accumulated one wrong tool per job. Feed URLs were
pushed through Markdown readers and scrapers whose output the entry parser can
never consume; article content was fetched by three duplicated reader legs
(enrichment's gated fallback vs discovery's ungated walk) with two cause
formats; provider Markdown (reader/scrape legs, JSON-LD `articleBody`) was
re-run through the HTML cleaner, so links, images, and formatting were
rewritten on the way in; HTML extraction carried a regex converter beside
trafilatura; a total article-content failure left an unflagged thin RSS body
that ranking and agents could not tell from a clean one; and robots-Disallow
article paths were dropped from harvest plans before a single fetch.

The lane spec (`.scratch/fallback-simplification/spec.md`, tickets
`issues/01`–`06`) resolves all six with one rule: right service for the right
job. Per the repo convention (AGENTS.md), deviations from standing decisions
are flagged explicitly below instead of being silently overridden.

## Decision

- **01 — Feed retrieval is impersonated-only.** Ingesting an RSS source runs
  the impersonated feed fetch (plus its candidate-URL retry for a dead or
  emptied feed) and nothing else: no feed URL is ever sent to a reader or
  scraper, because the entry parser could not use Markdown output. An
  exhausted candidate list still raises the explicit empty-feed error.
- **02 — Total chain failure is terminally flagged.** When the whole
  article-content chain fails and only the thin RSS body survives, the row is
  persisted and then `flag_unrecoverable` files the `unrecoverable` Extraction
  Flag with the chained cause and `SYSTEM_REPORTER` (`"system"`). Ordering is
  load-bearing: the flag lane is an `UPDATE` keyed on the article URL, so it
  runs **after** `upsert_documents` in `ingest_source_flow`; a failure inside
  it is logged, never re-raised. A `force_on` source whose kept RSS body was
  already substantive is not unrecoverable and stays unflagged. Discovery-lane
  behavior is unchanged: a failed article is dropped with a run-level cause,
  never stubbed and never flagged.
- **03 — Provider Markdown is stored as-is.** Markdown delivered by a provider
  leg (Jina reader, Firecrawl scrape, JSON-LD `articleBody`) passes a
  thin-check only and is never run back through the HTML cleaner, so links,
  images, and formatting survive at every call site.
- **04 — HTML extraction is trafilatura-only.** The legacy regex converter and
  the HTML-level National Jeweler boilerplate scanner were deleted; the
  converter has no fallback, so HTML that trafilatura cannot extract yields
  empty Markdown and downstream thin-check / keep-RSS / `UnparseableBody`
  semantics are unchanged. The Markdown-level family cleanup is **kept** — the
  keep/delete verdict, the fixture measurements, and the `deduplicate=False`
  decision are recorded in `issues/04` and detailed in ADR-0004(d).
- **05 — One shared article-content chain.** `enrich.article_content_chain` is
  the single three-leg implementation (impersonated `fetch_impersonated` →
  Jina `fetch_reader` → lazy `fetch_via_firecrawl`) with one bounded,
  credential-scrubbed cause format that names each leg exactly once.
  Enrichment enters through `_fallback_after_primary_miss` (gated on
  primary-miss signals, `min_chars` thin-check per leg); discovery's
  `policy_get` walks the same chain ungated on fetch failure. Discovery's
  duplicated leg helpers are now thin adapters that re-raise
  `ArticleFetchError`, and `ProviderMarkdown` moved to `enrich.py` (discovery
  imports it) so the ticket-03 as-is contract holds through the shared path.
- **06 — robots Disallow is not an exclusion ground.** Discovery planning no
  longer drops article URLs because `robots.txt` disallows them; they are
  planned and fetched like any sibling. Crawl-delay (folded into the per-host
  pacing gap), bounded jitter, and newest-first `max_urls` budgeting still
  bound the fetch rhythm.

## Flagged deviations from standing decisions

- **Firecrawl narrows ADR-0004(b).** ADR-0004 recorded "No Firecrawl/
  Playwright provisioning, no universal scraper", and the CONTEXT V1 cuts said
  "No Firecrawl (revisit per-source when RSS/HTTP fails)". That stands for
  feeds (01: impersonation only) and against provisioning a universal scraper,
  but **article content now ends in a Firecrawl scrape leg** — the paid last
  resort after the impersonated primary and the Jina reader. This is the
  revisit-when-HTTP-fails (bot protection/paywall) clause being exercised, not
  a new general scraping posture: the chain is still per-article, curated
  sources only, and every leg failure stays explicit.
- **The Extraction Flag is no longer only agent-reported.** ADR-0005 and the
  CONTEXT glossary described an agent-filed marker; `unrecoverable` is
  system-filed by the ingestion pipeline (`SYSTEM_REPORTER`). The agent lane
  and the system lane share one storage contract and one reason set: agents
  can set and `clear` `unrecoverable` through the normal `flag_extraction`
  tool (unknown reasons still reject), and both lanes are capped at 0.3
  importance.

## Consequences

- **Payload shapes are unchanged.** Search results keep the 19-key shape, the
  ARTICLE/SEARCH/PERIOD key sets are untouched, and run results keep
  `{inserted, skipped}` augmented only by the optional, nonzero-only
  `enrich_skipped` / `enrich_causes` keys from ADR-0004(c).
- **Enrichment returns a 4-tuple.** `_enrich_docs` / `enrich_task` now return
  `(docs, skipped, causes, unrecoverable)`, where each `unrecoverable` entry is
  `(url, chained_detail)`; the per-document task returns a fourth element (the
  detail, else `None`). `ingest_source_flow` consumes that fourth element to
  file flags after the upsert.
- **Flags never block ingestion.** A raising `on_unrecoverable` sink or a
  failing post-upsert flag write is swallowed/logged; the kept RSS body and the
  run result are unaffected.
- **Test pins move with the contract.** `tests/test_retrieval.py`
  (`test_impersonated_lane_success_never_touches_reader_or_scraper`,
  `test_feed_failure_never_touches_reader_or_scraper`,
  `test_dead_primary_feed_recovers_via_next_candidate`,
  `test_exhausted_candidate_list_raises_explicit_empty_feed_error`),
  `tests/test_extraction_flag.py` slice 8 (total-chain-failure emission,
  `unrecoverable` accept/clear, upsert-then-flag ordering, `force_on` stays
  unflagged, sink failure never breaks the keep),
  `tests/test_enrichment.py` provider-Markdown-as-is section,
  `tests/test_fallback_policy.py` 4-tuple unpacks, and
  `tests/test_robots_disallow.py` pin the new behavior.
  `tests/test_nj_extraction_repair.py` pins the trafilatura-only family bodies.
- **No new surfaces.** No history table, no auto re-fetch, no scheduling, no
  analytics; flag overwrite/clear semantics and the importance cap are as
  ADR-0005 describes. Universality stays out: no Playwright provisioning, no
  crawl-everything scraper, curated sources only.
