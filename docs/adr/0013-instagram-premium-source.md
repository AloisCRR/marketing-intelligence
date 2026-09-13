# ADR-0013 — Instagram premium source lane (Apify, pointer-based)

**Status:** accepted
**Date:** 2026-09-13

## Context

The project needs Instagram as an ingestion point for sentiment + trend analysis, starting with account `sabrikolod` (sample post `https://www.instagram.com/p/DdOz587EXSx/`). Instagram has no RSS/sitemap surface; the only deterministic path is a managed scraper. Research (`.scratch/research-instagram-apify-2026.md`) verified the Apify dedicated actors return captions, comment text, engagement counts, and ISO timestamps as plain JSON — no LLM/embeddings at ingest.

Standing decisions this touches: CONTEXT V1 cuts (20 curated RSS/sitemap sources, `impersonated-feed`-only policy, no universal scraper, Firecrawl as the only paid leg); `documents` has no payload column and one INSERT writer; `_enrich_docs` runs unconditionally on every lane; `RETRIEVAL_TYPES`/`RETRIEVAL_POLICIES` are closed tuples. Per AGENTS.md, deviations are flagged explicitly below.

Grill record: 13 questions, all agreed (session 2026-09-13). Pointer-based extraction (no full-history pull) was a user correction during the session.

## Decision

- **Premium source posture.** Instagram via Apify is accepted as a second paid external leg after Firecrawl: one `sources` row per IG account (`ig:sabrikolod`), per-account declarative stanza (username, hashtag filter, content mode, cadence) in `curated-sources.json`. The "no universal scraper" cut stands — this is a fixed-purpose managed actor with a declared schema, exercised per-account like the Firecrawl per-article leg. Standing rule (2026-09-14): never drop billed data — the lane ingests every returned post; `hashtag_filter` is a query-time hint only, never an ingest gate (caption holds `#tag` text, payload JSONB holds `hashtags[]`, so read-time filtering costs zero extra billing).
- **Actor selection: `apify/instagram-post-scraper`, not the main `instagram-scraper`.** Approved 2026-09-13. Same 1-result-per-post billing at a lower unit price (main: $2.70/1k Free → $1.50/1k Gold); cleaner contract (`onlyPostsNewerThan`/`skipPinnedPosts` in published schema; main has README-only inputs absent from its schema). Main only wins if mentions/places/search are later needed in one billing line.
- **Fetch-full / store-raw / expose-caption.** Apify charges per result, not per field: every run stores the full post JSON, v1 reads caption only. Comments v1 = free riders (`commentsCount`, `firstComment`, `latestComments` inside the post result); full `comment-scraper` corpus deferred to v2. Media v1 = URLs + `alt`, no OCR/vision (breaks the deterministic-only cut; separate decision later). Per-account config reserves an `image_text` slot.
- **Raw payload lives in a side table.** `document_payloads(document_id FK, payload JSONB)` — matches the `document_importance`/`topics`/`digest_picks` precedent, leaves `documents`/`NormalizedDocument`/`INSERT_SQL` untouched. The lane writes after upsert, keyed by URL like the flag lane.
- **Identity rule (fixed before backfill).** `url` = canonical `/p/<code>/` permalink (`/reel/` normalized); `title` = first non-empty caption line, fallback `@handle — <shortcode>`; hash over that. Two posts with byte-identical caption+title dedupe to one row (UNIQUE `content_hash`) — accepted, visible via `skipped`.
- **Pointer-based incremental, derived, capped.** No full-history pull. High-water mark = derived `MAX(documents.published_at)` per account row (no new state) — never "last returned", because the date filter bleeds extras (see smoke note). Run 0: `resultsLimit: 10`, no date filter. Run N: `onlyPostsNewerThan: <max_published_at − 60s>`, `resultsLimit: 15` hard cap. The 60s overlap re-emits one post per run (~$0.002) for zero-miss; dedupe makes it idempotent. Every run sets `resultsLimit` (no documented default) + a per-run max charge (`minimalMaxTotalChargeUsd` floor $0.0027).
- **Enrichment bypass.** The IG lane opts out of `_enrich_docs` (`force_off`-style): the caption is final. A short caption would otherwise read as "thin" and burn trafilatura/Jina/Firecrawl on data destruction.
- **Metrics stay in payload JSONB for v1.** Likes/comments/follower counts have no first-class columns; first-class metric columns only once trend queries prove which metrics matter.

## Flagged deviations from standing decisions

- **Paid vendor #2 + policy exception.** `RETRIEVAL_POLICIES` holds only `impersonated-feed` with docstrings asserting every fetch runs the impersonated chain. The Apify leg contradicts that invariant — needs a new policy value plus this ADR, not a quiet stanza addition.
- **21st source class.** V1 cuts say 20 curated RSS/sitemap sources. `ig:sabrikolod` is a non-feed source with new failure modes (login walls, `BLOCKED` items, free-tier caps) — a deliberate, capped exception, not a widened posture.
- **New secret.** `APIFY_API_TOKEN` from env at call time, never logged — same convention as `FIRECRAWL_API_KEY`.

## Consequences

- **Cost envelope.** ~$0.035/run cap on Starter ($19/mo, $17 annual); realistic tracking ≈ $4.90/mo usage inside the $19 credit. Free plan ($5, 15-comment cap) cannot serve sentiment — Starter is the floor. Price history is volatile; budget for repricing. Watch `dataDetailLevel`: smoke ran `detailedData` (the schema default); detailed rows bill a second `post-details` event on top of `post` — use `basicData` unless the extra fields are needed.
- **New migration `012`** for `document_payloads`; new `RETRIEVAL_TYPES` entry (unknown types silently fall back to `rss`); new stanza keys must be added to `_validated_extras` + `get_retrieval_config` or they drop silently.
- **Test pins:** pointer derivation, 15-cap, overlap dedupe, enrichment bypass, side-table write-after-upsert, title rule determinism.
- **Deferred to v2:** full comment corpus, first-class metric columns, image-text extraction, hashtag-discovery lane.

## Smoke test (2026-09-13, live, ~$0.04 on Free tier)

- **Run 1 (single `DdOz587EXSx`, `resultsLimit: 1`):** PASS. Caption 1,072 chars ES, `ownerUsername: sabrikolod`, ISO `timestamp`, `likesCount: 1651`, `commentsCount: 23`, `hashtags: [ChismecitoMarketinero]`, `firstComment` + 12 `latestComments` (`text/timestamp/likesCount/ownerUsername/replies`). Field keys pinned: `caption, hashtags, mentions, taggedUsers, alt, images, displayUrl, dimensions*, timestamp, owner*, type, productType, url, shortCode, id, likesCount, commentsCount, videoViewCount, firstComment, latestComments, childPosts, isCommentsDisabled, paidPartnership, inputUrl`.
- **Run 2 (bootstrap `sabrikolod`, `resultsLimit: 10`):** PASS with note. 10 posts returned but recency-ish, not strictly newest (mix of 2025-11 → 2026-09; 3 hidden-like `-1` rows). 4/10 carry `ChismecitoMarketinero` (query-time filtering signal — the lane ingests all 10 regardless; dropping billed posts ingest-side was rejected 2026-09-14). Bootstrap is a seed, not an ordering guarantee.
- **Run 3 (pointer `onlyPostsNewerThan = max − 60s`, cap 15):** FLAG → constraint. Returned 4 items, not 1: target post + 3 old posts (2025-11/12, 2026-02, pinned/related bleed). **The date filter is not a strict newer-than.** Steady runs must expect ~2–5 results billed per run and rely on dedupe (`ON CONFLICT DO NOTHING` → `skipped`); pointer stays derived `MAX(published_at)`.
- **Count drift:** actor reports `commentsCount: 23` where the logged-out page earlier showed 18 — engagement counts are point-in-time snapshots, never exact.
- **Dropped from smoke (accepted risk, not blockers):** full comment-corpus completeness and profile-scraper counts stay unverified until v2.
