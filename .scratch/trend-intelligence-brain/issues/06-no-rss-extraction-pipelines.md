# 06 — No-RSS extraction pipelines (discovery + article extraction for 14 Sources)

Status: ready-for-agent

**What to build:** Every curated Source without an RSS feed becomes ingestible through the unchanged downstream contract: a sitemap-first URL discovery Ingestion Stage with hub-anchor fallback, plus a per-source-family article extractor, each Ingestion Run independently rerunnable with explicit partial failure.

**Blocked by:** 02 — Remaining sources on the same contract (RSS lane proven).

## Problem Statement

As the owner of the Trend Intelligence Brain, I can only ingest the 6 Sources that publish RSS today. The other 14 curated Sources (11 clean-HTML WordPress/corporate/PR sites, National Jeweler, Modaes, Jing Daily, the two Dive sites, LVMH) return nothing: the registry drops entries without an RSS URL, so there is no way to turn a hub page or sitemap into Documents. To ship V1 I need all 20 Sources flowing as durable evidence (Documents/Articles) with provenance, without resorting to a managed scraper or browser fleet for what is provably plain HTTP.

Live probing (browser + direct fetch, 2026-09-06) corrected the tracker data: National Jeweler shows no bot block (hub + article return 200 with the plain pipeline); Modaes serves the full 11k-char body despite paywall markers (cookie-consent wall, not enforced server-side); Jing Daily's sample article is open but needs a JSON-LD-first extractor (embedded `articleBody` ~7k chars where generic extraction yields ~200 thin chars). Two curated sample URLs are stale (National Jeweler numeric-ID reuse serves a different article; LVMH sample returns 404), so hardcoded sample URLs must not be trusted — discovery must come from sitemaps/hubs.

## Solution

Extend the Ingestion Layer with one retrieval-adapter seam behind the unchanged Document contract:

- A discovery Ingestion Stage per Source: sitemap-first (sitemap index → URL set, including per-locale and news sitemaps where declared), hub-anchor fallback (hub page → article-link pattern), both honoring robots declarations and per-host pacing.
- A per-family article extractor: default generic HTML-to-Markdown; JSON-LD `articleBody`-first for the Next.js/Sanity family (Jing Daily); impersonated retry only where proven necessary (Dive news sitemaps).
- Everything downstream (normalization, hash dedupe by URL + content hash, enrichment-keep semantics, per-source rerun isolation, explicit partial failure) stays exactly as the RSS lane behaves, so a Weekly Context consumer cannot tell which retrieval route a Document came from.

## User Stories

1. As a brain operator, I want each no-RSS Source to have a declared discovery route (sitemap and/or hub pattern), so that I can audit how its URLs enter the system.
2. As a brain operator, I want sitemap-index traversal (including nested and per-locale indexes), so that corporate/PR Sources with paginated hubs are fully covered.
3. As a brain operator, I want hub-anchor fallback using a per-source link pattern (e.g. `/posts/`, `/news/`, `/articles/<id>`), so that Sources whose sitemaps are incomplete or gated still yield URLs.
4. As a brain operator, I want discovery to honor robots declarations and per-host pacing with retries, so that ingestion stays a polite, deterministic crawler.
5. As a brain operator, I want each Ingestion Run to remain independently rerunnable per Source, so that re-running a Source is a no-op when nothing changed.
6. As a brain operator, I want partial failure to be explicit per Source and per Document, so that one bad hub page, sitemap entry, or article never aborts the rest.
7. As a digest consumer, I want Documents from no-RSS Sources to carry the same provenance (Source, canonical URL, timestamps, language, hash) as RSS Documents, so that evidence is uniform.
8. As a digest consumer, I want canonical-URL tracking (final URL after redirects + declared canonical), so that CMS numeric-ID reuse and slug changes do not silently swap article identity.
9. As a brain operator, I want WordPress-family Sources to ingest via sitemap + generic extraction, so that the nine standard sites need no bespoke code.
10. As a brain operator, I want Modaes to ingest via its sitemap index with generic extraction, so that its full body is captured without any paywall bypass.
11. As a brain operator, I want National Jeweler to ingest via its large URL set plus hub listing, so that its articles flow despite having no feed and no active bot block.
12. As a brain operator, I want Jing Daily discovery via hub anchors and sitemap, with JSON-LD-first extraction, so that its React-rendered articles yield full bodies.
13. As a brain operator, I want the Dive sites to ingest via hub anchors plus sitemap index and news sitemap (impersonated retry where the plain fetch is refused), so that their `/news/` corpus is covered.
14. As a brain operator, I want Richemont PR to ingest via its large URL set with generic extraction, so that low-cadence releases are captured.
15. As a brain operator, I want LVMH PR to ingest sitemap-only (per-locale index), so that the hub rendering failure in tooling does not block coverage.
16. As a brain operator, I want metered/freemium bodies that come back thin to be kept-aside or flagged rather than bypassed, so that access walls are never circumvented by the pipeline.
17. As a brain operator, I want an Extraction Flag path for improperly extracted Documents (reason + detail + reporter + timestamp), so that thin or wrong-article captures are visible to callers.
18. As a brain operator, I want per-source retrieval stanzas (discovery type, hub pattern, extractor family, pacing) stored alongside the curated Source record, so that route changes are config, not code forks.
19. As a brain operator, I want seeded Source rows for all 20 Sources, so that persistence matches the curated set.
20. As a brain operator, I want backfill bounded (e.g. newest N URLs per Source first), so that initial ingestion is observable and does not stampede hosts.
21. As a Service Adapter consumer, I want search and weekly-context results to include no-RSS Documents with identical payload shapes, so that no caller changes are needed.
22. As a Service Adapter consumer, I want language codes (en/pt/es) correctly assigned per Source, so that mixed-language corpora stay queryable.

## Implementation Decisions

- Single retrieval-adapter seam (confirmed with requester): one new discovery Ingestion Stage plus an extractor switch, both behind the unchanged normalized-Document and upsert contracts. No changes to dedupe semantics, enrichment-keep behavior, or caller surfaces.
- Discovery order per Source: declared sitemap(s) first; hub-anchor fallback second. Sitemap handling covers index → nested index → URL set, per-locale indexes, and news sitemaps. Hub handling covers a single listing URL plus a per-source article-link pattern with pagination where the host paginates.
- Retrieval policy stays deterministic plain-HTTP-first; the impersonated retry is enabled only for routes proven to refuse plain fetches (Dive news sitemaps). No browser automation and no managed extraction service in this slice.
- Extractor families: generic HTML-to-Markdown default for WordPress/corporate/PR/hub families; JSON-LD `articleBody`-first for the Next.js/Sanity family (fall back to generic extraction; a still-thin result is kept-aside/flagged, never bypassed). The existing thin threshold (500 chars) governs keep-vs-flag.
- Canonical identity: store the final URL after redirects and the declared canonical/og URL; hash identity remains title + normalized content. Stale curated sample URLs (numeric-ID reuse, 404s) are never trusted as discovery seeds.
- Per-source alignment (proven 2026-09-06 via browser + direct fetch):
  - WordPress sitemap family (9: MarketingDirecto, Propmark, Insider Latam, Forbes México, Consumidor Moderno, Meio & Mensagem, Exame, plus two latam/global siblings per curated set): sitemap index → generic extraction.
  - Modaes: sitemap index (`main.xml`) → generic extraction; tracker label corrected to cookie-consent wall, body open.
  - National Jeweler: large URL set + hub listing (`/industry`, 44 article links observed) → generic extraction; no bot-bypass work; guard against numeric-ID reuse.
  - Jing Daily: hub anchors (83 `/posts/` links observed, server-rendered) + sitemap → JSON-LD-first extraction.
  - Retail Dive / Marketing Dive: hub anchors (29/33 `/news/` links observed) + sitemap index + news sitemap (impersonated retry for the news sitemap only); no structured-data dependency.
  - Richemont PR: large URL set → generic extraction (sample verified full body).
  - LVMH PR: per-locale sitemap index only (hub page errored in browser tooling; sample URL stale 404) → generic extraction; verify extractor on a sitemap-discovered URL during implementation.
- Politeness: honor robots declarations (including crawl-delay where declared), per-host pacing with jitter, bounded concurrency, explicit timeouts; transient 403s retried under policy, persistent denials recorded as explicit Source errors.
- Config: each Source carries a retrieval stanza (discovery type, sitemap/hub references, link pattern, extractor family, pacing). Sources table seeded for all 20.
- Out-of-scope enforcement: login/metered bypass, browser rendering, and managed APIs are not part of this spec; they resolve to keep/flag, never to circumvention.

## Testing Decisions

- Test external behavior only (discovery yields expected URLs; extraction yields substantive bodies with provenance; reruns are no-ops; failures are explicit), not private helpers or parser internals.
- Coverage: discovery unit behavior per family (sitemap index, nested/per-locale/news sitemaps, hub-anchor patterns, robots/pacing); extractor behavior per family (generic body, JSON-LD-first with thin fallback); end-to-end per-Source fixture ingest (rerun no-op, language + tz-aware timestamps); isolation (one failing Source leaves others intact); stale-URL handling (ID reuse / 404 recorded, never stored as wrong article).
- Prior art: the existing per-source fixture-ingest pattern (rerun is a no-op; contract fields verified), per-source `{inserted, skipped[, error]}` isolation assertions, and the typecheck + test suite gate before claiming done.

## Out of Scope

- LLM enrichment, embeddings, topics/entities, velocity/emerging-topics (no history yet).
- Browser automation (rebuilt browsers), managed extraction APIs, residential proxies.
- Login flows, subscription bypass, or any auth-wall circumvention; metered bodies resolve to keep/flag.
- Scheduling changes and the Monday digest consumer; API/MCP payload changes (parity by construction holds).
- Backfill-all-history; initial ingest is newest-first bounded.

## Further Notes

- Evidence grounding: sitemap/robots probed for all 14 (12 sitemap-200; Dive sitemap index 200 on retry, news sitemaps impersonated-only); browser-verified hub anchors for Jing/Retail/Marketing Dive; direct-fetch verified bodies for Modaes (11k), National Jeweler (3.3k), Richemont (3.6k), Jing JSON-LD (7k vs 219 thin).
- Tracker corrections to apply alongside implementation: Modaes extractability label; National Jeweler bot-protected label (no block observed from this network on this date — keep impersonation as safety, monitor); LVMH + National Jeweler sample URLs stale.
- Browser-tooling note: LVMH hub errored with HTTP/2 protocol error in the inspection browser; implementation must confirm via pipeline fetch, but the spec route (sitemap-only) does not depend on hub rendering.
