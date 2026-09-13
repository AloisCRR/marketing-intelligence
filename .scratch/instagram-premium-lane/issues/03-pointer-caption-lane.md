# 03 — Pointer-based caption lane (10-bootstrap, 15-cap, 60s overlap)

**What to build:** Instagram lane module emitting `NormalizedDocument` per post: run 0 = `resultsLimit: 10`, no date filter; run N = `onlyPostsNewerThan: <MAX(published_at) − 60s>`, `resultsLimit: 15` hard cap + per-run max charge. Identity: canonical `/p/<code>/` URL (`/reel/` normalized), title = first caption line else `@handle — <shortcode>`. Mapping: caption→`content`, handle→`author`, IG `timestamp`→`published_at`, `es` language. Hashtag filter client-side post-fetch. Enrichment bypassed (`force_off`-style). Branch in `ingest_source_flow` lane switch converging on upsert → payload write.

**Blocked by:** 02

**Status:** ready-for-human

- [x] Bootstrap inserts ≤10 posts (seed only — actor returns recency-ish, not strictly newest); steady run inserts only posts newer than pointer
- [x] 15-cap bounds every run (burst-safe); max charge set on every run
- [x] 60s overlap re-emit + date-filter bleed (smoke: 4 items for a 1-post window — filter is NOT strict newer-than) dedupe via UNIQUE (`ON CONFLICT DO NOTHING`, visible as `skipped`); pointer stays derived `MAX(published_at)`, never "last returned"
- [x] Title rule deterministic across re-runs (same post re-hashes identically)
- [x] Caption never passes trafilatura/Jina/Firecrawl
- [x] Every run returns per-source `{inserted, skipped[, error]}`; failure explicit, rerunnable
- [x] `dataDetailLevel` pinned to `basicData` unless detailed fields are needed (detailed rows bill a second `post-details` event; smoke ran the `detailedData` default)
- [x] Pinned field key set from smoke Run 1: `caption, hashtags, mentions, taggedUsers, alt, images, displayUrl, dimensions*, timestamp, owner*, type, productType, url, shortCode, id, likesCount, commentsCount, videoViewCount, firstComment, latestComments, childPosts, isCommentsDisabled, paidPartnership, inputUrl`
## Comments
