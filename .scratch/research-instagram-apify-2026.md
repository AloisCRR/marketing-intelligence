# Research: Apify Instagram scrapers for sentiment + trend ingestion (2026-09-13)

Scope: can Apify cover an Instagram ingestion point (target user `sabrikolod`, sample post `https://www.instagram.com/p/DdOz587EXSx/`) for Spanish sentiment + trend analysis? Verified against primary sources only (Apify actor pages, actor API metadata, Apify platform docs, live fetch of the sample post/profile). Follows repo convention from `.scratch/research-content-extraction-2026.md`.

## Verdict

- **Yes, technically: Apify can do the job.** The dedicated actors return exactly the sentiment/trend surface (captions, comment text + timestamps + likes, engagement counts, follower counts, hashtag volumes) as deterministic JSON — no LLM/embeddings at ingest, compatible with the deterministic-ingest cut.
- **But it is a new paid, non-feed source class outside V1 cuts** (20 curated RSS/sitemap sources, no universal scraper, Firecrawl as the only paid leg). Needs a `CONTEXT.md`/ADR decision + `APIFY_API_TOKEN` secret + per-result spend caps + a comment-modeling decision before any pipeline work.
- **Pricing headline: practical minimum is Starter $19/mo** ($17/mo annual). Usage itself is tiny (~$5/mo for 1 profile + 100 posts + comments); the Free plan ($5/mo credit, 15-comment cap) cannot serve sentiment.
- **Minimal actor set: 3 dedicated actors** (post-scraper + comment-scraper + profile-scraper; + reel-scraper if video metrics matter). The main `instagram-scraper` is the advanced superset — use the dedicated variants for cleaner schemas.

## 1. Main actor: `apify/instagram-scraper`

- **Identity:** console ID `shu8hvrXbJbY3Eb9W`, build 0.0.781 (2026-09-11), source hidden, batch actor (no standby). Scale: ~392k users, ~193M total runs, 99.72% success/30d, 4.7/5 (593 reviews). [source: https://api.apify.com/v2/acts/apify~instagram-scraper] [source: https://apify.com/apify/instagram-scraper]
- **Invocation:** REST (`POST /v2/acts/apify~instagram-scraper/runs`, `/run-sync-get-dataset-items`), `apify-client` JS/Python, CLI, MCP, Apify Schedules (cron) + Tasks (saved inputs) + webhooks (`ACTOR.RUN.SUCCEEDED` → fetch dataset). Sync runs cap at 300 s → long scrapes use async + poll + dataset fetch. Results land in per-run default dataset (`GET /v2/datasets/{id}/items?format=json|jsonl|csv…`). [source: https://apify.com/apify/instagram-scraper] [source: https://docs.apify.com/api/v2.md] [source: https://docs.apify.com/storage/dataset.md]
- **Inputs (8 published, none required):** `resultsType` (posts|details|comments|reels|mentions|stories-default-posts), `directUrls` (IG URL regex), `resultsLimit` (int ≥1, per URL, prefill 100, no documented default), `onlyPostsNewerThan` (date/relative, UTC), `search` + `searchType` (hashtag|profile|place|user) + `searchLimit` (1–250), `addParentData` (bool). URLs beat search; one content type per run — posts + comments = two runs. [source: https://api.apify.com/v2/acts/shu8hvrXbJbY3Eb9W/builds/Yo3Xy6MICBnbclfWT] [source: https://apify.com/apify/instagram-scraper]
- **Outputs (dataset items):** posts carry `id/pk/shortCode/url/type/caption/hashtags/mentions/firstComment/latestComments[]/likesCount(-1 when hidden)/commentsCount/timestamp(ISO)/videoViewCount(deprecated)/videoPlayCount/childPosts/owner*`; profiles carry `followersCount/followsCount/postsCount/biography/latestPosts[12]/relatedProfiles`; hashtags carry `postsCount/postsPerDay/related buckets/topPosts`; comment mode carries `postUrl/commentUrl/text/owner/timestamp/likesCount/replies[]/repliesCount` (README samples; absent from published dataset schema). Per-item errors possible (`error/requestErrorMessages`, example `"Error: BLOCKED"`). [source: https://api.apify.com/v2/acts/shu8hvrXbJbY3Eb9W/builds/Yo3Xy6MICBnbclfWT] [source: https://apify.com/apify/instagram-scraper]
- **Auth:** no Instagram account/OAuth/cookies/proxy config — logged-out public surface only. Private profiles unscrapable (except public-collab posts). Apify token required to run. [source: https://apify.com/apify/instagram-scraper]
- **Discovery caveat:** hashtag search went behind login 2024-10-09; hashtag discovery now blends Google results (order/completeness not IG-native). Comment text is not IG-searchable → brand monitoring needs caption-search + hashtag-UGC + mentions, then dedupe. `stories` resultsType deprecated in favour of reels. [source: https://api.apify.com/v2/acts/shu8hvrXbJbY3Eb9W/builds/Yo3Xy6MICBnbclfWT] [source: https://apify.com/apify/instagram-scraper]
- **Incremental:** filter-based (`onlyPostsNewerThan`), no cursor/dedup guarantee — consumer dedupes. Unnamed datasets expire after 7 days; named persist. [source: https://apify.com/apify/instagram-scraper] [source: https://docs.apify.com/storage/dataset.md]
- **Target check (live 2026-09-13):** sample post is public logged-out: full Spanish caption (`#ChismecitoMarketinero` weekly roundup), author `sabrikolod` (verified), 1,544 likes / 18 comments, ~11 rendered comments in Spanish. Profile grid mixes `/p/` + `/reel/` items. URL matches actor `directUrls` regex. [source: https://www.instagram.com/p/DdOz587EXSx/] [source: https://www.instagram.com/sabrikolod/]
- **Doc drift (plan around):** `skipPinnedPosts/isNewestComments/includeNestedComments/addProfileStatistics` are README-only, absent from input schema (unverified on build 0.0.781); `addParentData` ships as `dataSource` vs `metaData` depending on doc; comment fields absent from dataset schema. [source: https://apify.com/apify/instagram-scraper] [source: https://api.apify.com/v2/acts/shu8hvrXbJbY3Eb9W/builds/Yo3Xy6MICBnbclfWT]

## 2. Pricing (very important — full detail)

- **Model:** pay-per-event, exactly one chargeable event `result` ("Each result written to the dataset"). No per-run/start fee, no proxy surcharge (platform usage bundled; input schema has no proxy field). Per-run max-charge cap supported (min $0.0027); run aborts gracefully at cap. [source: https://apify.com/apify/instagram-scraper/pricing] [source: https://api.apify.com/v2/acts/apify~instagram-scraper] [source: https://docs.apify.com/actors/running/actors-in-store.md]
- **Unit prices (USD/result):** FREE $0.0027 ($2.70/1k) · BRONZE $0.0023 ($2.30/1k) · SILVER $0.0019 ($1.90/1k) · GOLD $0.0015 ($1.50/1k) · PLATINUM $0.0009 / DIAMOND $0.0005 (enterprise-only). Plan mapping: Free=none, Starter=Bronze, Scale=Silver, Business=Gold. Store headline "from $1.50/1,000" = Gold rate. [source: https://api.apify.com/v2/acts/apify~instagram-scraper] [source: https://apify.com/apify/instagram-scraper/pricing] [source: https://docs.apify.com/actors/publishing/monetize/pricing-and-costs.md]
- **Plans:** Free $0 ($5/mo credit, blocks when consumed, no rollover) · Starter $19 ($19 credit, Bronze) · Scale $199 (Silver) · Business $999 (Gold); annual −10%. PPE charges draw down the plan credit; overage invoices at $20/$200 thresholds. No separate trial credit beyond Free $5. [source: https://apify.com/pricing] [source: https://docs.apify.com/account/billing.md] [source: https://docs.apify.com/account/subscriptions.md]
- **Free-plan caps kill sentiment:** only top ~15 comments/post, ~21 mentions, no `isNewestComments`, no nested replies. Starter lifts them. [source: https://apify.com/apify/instagram-scraper.md]
- **Post-run dataset reads/writes bill as normal platform usage** on top of event price (dataset reads $0.0004/1k, writes $0.005/1k, transfer external ~$0.20/GB). Negligible at this volume, but real. [source: https://docs.apify.com/actors/running/actors-in-store.md]
- **USD only; no EUR price list.** Illustrative FX (not quoted): $19 ≈ €17.3–17.6; $4.90 usage ≈ €4.5. VAT treatment unverified — check Console billing. [source: https://apify.com/pricing]

### Worked costs

- **Sample post (1 post result):** $0.0027 / $0.0023 / $0.0019 / $0.0015 (Free/Starter/Scale/Business). Post + N comments = 1+N results across two runs (single post result already includes `commentsCount` + first/latest comments, so read volume for 1 result before buying the corpus). 15 comments on Free ≈ $0.043; 50 comments on Starter ≈ $0.117; 200 ≈ $0.46; 1,000 ≈ $2.30. [source: https://apify.com/apify/instagram-scraper.md]
- **Monthly (1 profile daily + 100 posts + C comments/post, no replies):**

| C | results/mo | Free ($5) | Starter ($19) | Scale ($199) | Business ($999) |
|---|---|---|---|---|---|
| 15 | 1,630 | $4.40 ✓ | $3.75 | $3.10 | $2.45 |
| 20 | 2,130 | $5.75 → blocked | **$4.90 ⊂ $19 credit** | $4.05 | $3.20 |
| 50 | 5,130 | blocked | $11.80 | $9.75 | $7.70 |
| 100 | 10,130 | blocked | $23.30 (~$4.30 overage) | $19.25 | $15.20 |

  → **Effective $19/mo on Starter** for the realistic case. Scale/Business only win at ~86k+/mo results.
- **Watch-outs:** `resultsLimit` has no documented default — always set it + a per-run max charge. Nested replies each bill as a result. Price history is volatile ($0.0023→$0.0007→$0.0023→tiered PPE in ~2 yrs) — budget for repricing. Starter README "16,900 results/mo" does not reconcile with $19÷$0.0023 ≈ 8,261 — treat README figure as suspect. [source: https://api.apify.com/v2/acts/apify~instagram-scraper]

## 3. Variant comparison (7 actors)

| Actor | Purpose | Key inputs | Key outputs | When to use |
|---|---|---|---|---|
| `instagram-comment-scraper` ($1.90/1k; $2.30 Free) | Comments + replies en masse | `directUrls`, `resultsLimit`, `includeNestedComments` (paid only) | `id/text/ownerUsername/timestamp/likesCount/replies[]` | **Sentiment leg** — only variant whose unit is comment text |
| `instagram-profile-scraper` ($1.60/1k; $2.60 Free) | Profile + latest 12 posts | `usernames`, `includeAboutSection` (paid) | `followersCount/followsCount/postsCount/latestPosts[12]/relatedProfiles` | **Audience baseline** (weekly follower/post growth) |
| `instagram-post-scraper` ($1.00/1k; $2.60 Free) | Posts/carousels/reels by user or URL | `username`, `resultsLimit`, `skipPinnedPosts`, `onlyPostsNewerThan`, `dataDetailLevel` | `caption/hashtags/mentions/likesCount(-1 hidden)/commentsCount/timestamp/firstComment/latestComments[10]` | **Trend spine** (frequency + engagement); 1-result single-post record |
| `instagram-hashtag-scraper` ($1.90/1k; Free = first page only) | Recent posts/reels per hashtag/keyword | `hashtags`, `keywordSearch`, `resultsType` (posts\|reels) | per-item engagement + `locationName/musicInfo/isSponsored` | Topic discovery (e.g. `#ChismecitoMarketinero`), not user tracking |
| `instagram-api-scraper` ($1.40/1k; legacy) | Multi-purpose sweep (profiles/posts/comments/tags/search) | `directUrls`, `resultsType`, `resultsLimit` (max 50 comments/post) | older shape (`created_at` unix, `topComments`) | Only if one run must mix types; dedicated actors are better-typed |
| `instagram-reel-scraper` ($1.00/1k + paid add-ons) | Reel metrics + transcript | `username`, `resultsLimit`, `includeSharesCount`/`includeTranscript`/`includeDownloadedVideo` (paid) | views/shares/`transcript`/audio/`musicInfo`/latest 10 comments | Video-performance trend; sabrikolod feed is reel-heavy → optional 4th |
| `instagram-hashtag-analytics-scraper` ($1.40/1k) | Hashtag volume stats | `hashtags`, top/latest posts (paid) | `postsCount/postsPerDay/related/frequent/average/rare` | Hashtag reach/volume tracking; orthogonal to user tracking |

Sources per row: https://apify.com/apify/instagram-comment-scraper.md, https://apify.com/apify/instagram-profile-scraper.md, https://apify.com/apify/instagram-post-scraper.md, https://apify.com/apify/instagram-hashtag-scraper.md, https://apify.com/apify/instagram-api-scraper.md, https://apify.com/apify/instagram-reel-scraper.md, https://apify.com/apify/instagram-hashtag-analytics-scraper.md.

- **Pilot (sample post, Spanish sentiment): minimal = `comment-scraper` alone** (`directUrls: [post]`, ~18 results ≈ $0.05 Free). Add `post-scraper` (1 result ≈ $0.003) only to bundle caption/likes. No actor does language/sentiment — Spanish text arrives raw; sentiment must be a downstream agent annotation (like Topic/Importance). [source: https://apify.com/apify/instagram-comment-scraper.md] [source: CONTEXT.md]
- **Tracking (sabrikolod trend): minimal = `post-scraper` (spine, `onlyPostsNewerThan: 7 days`) + `comment-scraper` (per new post) + `profile-scraper` (weekly audience).** Add `reel-scraper` for views/shares/transcripts. Exclude hashtag pair + api-scraper. [source: https://apify.com/apify/instagram-post-scraper.md] [source: https://www.instagram.com/sabrikolod/]
- **Free alternative worth noting:** the caption + first ~11 comments are already in the public HTML, so a caption-only pilot needs no paid actor — but that is fragile markup scraping, and the repo's "no universal scraper" cut argues for the maintained actor on any permanent lane. [source: https://www.instagram.com/p/DdOz587EXSx/] [source: CONTEXT.md]

## 4. Fit vs repo (V1 cuts, ADRs)

- **Deterministic ingest:** ✓ structured JSON + ISO timestamps, no LLM/embeddings needed — sentiment stays on the agent annotation layer. [source: CONTEXT.md]
- **New source class:** ✗ Instagram is not RSS/sitemap; would be a 21st source with new failure modes (login walls, Google-mediated discovery, BLOCKED items, free-tier caps). "No universal scraper" cut needs an explicit exception (managed actor with declared schema, like the Firecrawl terminal leg). [source: CONTEXT.md]
- **New plumbing:** `APIFY_API_TOKEN` secret (env-at-call, never logged — per repo convention), per-run `resultsLimit` + max-charge caps, Prefect `@flow/@task` (async run + poll; run-sync caps at 300 s), staging→normalize→upsert keyed on `id/shortCode/url/timestamp`. [source: https://docs.apify.com/api/v2.md] [source: CONTEXT.md]
- **Modeling decision (unresolved):** comments are one-row-per-comment — does not map 1:1 onto Document; per-post Document with embedded comments vs per-comment entity needs a design call + probably an ADR.
- **Second paid vendor after Firecrawl:** needs budget/caps treatment of the same kind (per-result, comment-volume-scaled).

## 5. Recommendation

1. **Pilot (€0–$0.05):** single `comment-scraper` run on the sample post (+ optional `post-scraper` 1-result record). Confirms the open question that matters: how many of the 18 comments the logged-out surface actually returns, and whether caption + ~15 comments suffice for sentiment signal.
2. **If pilot passes:** Starter $19/mo, scheduled `post-scraper` + per-post `comment-scraper` + weekly `profile-scraper`, all spend-capped; sentiment as agent annotation (new ADR).
3. **Decide before pipeline work:** comment modeling (per-comment rows vs embedded), Instagram-as-Source-type, tracking cadence + monthly cap, VAT-inclusive cost confirmation in Console.

## Open questions (need a live run or owner decision)

1. Comment completeness: logged-out HTML showed ~11/18 — how many does the actor return? (unverifiable read-only)
2. FAQ-only inputs (`includeNestedComments` etc.) accepted on build 0.0.781?
3. Comment-mode exact field contract (README vs dataset schema disagree)?
4. IG-side rate limits before `BLOCKED` items?
5. Dedup/incrementality: do re-runs re-emit + re-charge?
6. Starter "16,900 results/mo" README figure vs $19÷$0.0023 math?
7. Comment SoR modeling + Instagram Source-type + sentiment-annotation ADR?

## Sources (primary only)

- https://apify.com/apify/instagram-scraper (+ .md, /pricing)
- https://apify.com/apify/instagram-comment-scraper (+ .md)
- https://apify.com/apify/instagram-profile-scraper (+ .md)
- https://apify.com/apify/instagram-post-scraper (+ .md)
- https://apify.com/apify/instagram-hashtag-scraper (+ .md)
- https://apify.com/apify/instagram-api-scraper (+ .md)
- https://apify.com/apify/instagram-reel-scraper (+ .md)
- https://apify.com/apify/instagram-hashtag-analytics-scraper (+ .md)
- https://api.apify.com/v2/acts/apify~instagram-scraper (pricing tiers, stats, history)
- https://api.apify.com/v2/acts/shu8hvrXbJbY3Eb9W/builds/Yo3Xy6MICBnbclfWT (input/dataset schema)
- https://api.apify.com/v2/acts/shu8hvrXbJbY3Eb9W/builds/Yo3Xy6MICBnbclfWT/openapi.json (endpoints, resultsLimit)
- https://apify.com/pricing, https://docs.apify.com/account/billing.md, https://docs.apify.com/account/subscriptions.md, https://docs.apify.com/actors/running/actors-in-store.md, https://docs.apify.com/actors/publishing/monetize/pricing-and-costs.md, https://docs.apify.com/actors/publishing/monetize/pay-per-event.md, https://docs.apify.com/storage/dataset.md, https://docs.apify.com/storage.md, https://docs.apify.com/api/v2.md, https://docs.apify.com/proxy.md, https://docs.apify.com/actors/running/schedules.md, https://docs.apify.com/actors/running/tasks.md, https://docs.apify.com/integrations/webhooks.md
- https://www.instagram.com/p/DdOz587EXSx/ (fetched 2026-09-13: caption, 1,544 likes, 18 comments)
- https://www.instagram.com/sabrikolod/ (fetched 2026-09-13: mixed /p/ + /reel/ grid)
- Local: CONTEXT.md (V1 cuts), .scratch/research-content-extraction-2026.md (format precedent)
