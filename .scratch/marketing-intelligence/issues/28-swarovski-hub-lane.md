# 28: Swarovski RSS to hub conversion

**What to build:** Swarovski ingests via its reachable PR Newswire hub page instead of the dead 404 RSS URL — the Source switches lanes following the established dead-feed routing precedent, producing real inserts instead of `fetch failed ... HTTP Error 404`.

**Blocked by:** 25 (Sitemap fetch impersonated-only).

**Status:** done

- [x] Swarovski Ingestion Run completes without error and inserts real articles (`inserted > 0` on a live run)
- [x] Dead RSS URL handled via the code-level fallback-routing precedent (curated registry untouched by the routing correction)
- [x] Per-source rerun stays isolated with explicit partial failure
- [x] Regression test pins the fallback routing at the feed-candidate seam
- [x] Typecheck + test suite gate green before claiming done

_Resolution (2026-09-12):_ routing is pinned at the lane/config seam (`_RETRIEVAL_OVERRIDES` + `apply_retrieval_override`, asserted through the effective retrieval config), *not* at `feed_candidate_urls` as the checkbox's wording suggests — a hub page is a listing, not a feed payload, so the correction switches the stanza type rather than adding a feed fallback URL.

_2026-09-18: lane-seam refactor — the code-level override is deleted; Swarovski's curated stanza now declares the hub lane directly (dead rss_url kept as provenance)._
