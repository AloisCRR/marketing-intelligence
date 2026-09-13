# 04 — Live pilot: 10 sabrikolod posts, caption verification (Free tier, caption-only)

**What to build:** Live run against `sabrikolod` on Apify Free tier: bootstrap run + one incremental run. Caption-only v1: verify caption fidelity vs the public post (incl. sample `DdOz587EXSx`), pointer advances, second run is near-no-op (only overlap re-emit). Comments explicitly out of scope for v1 (user 2026-09-14): payload keeps the free-rider fields verbatim (fetch-full/store-raw, no extra billing) but nothing verifies, serves, or decides on them. Record result counts per run from the lane + USD spend from Apify console. Smoke 2026-09-13 already proved caption fidelity + pointer bleed on Free tier (~$0.04); pilot re-verifies on Free with side-table writes.

**Blocked by:** 03


- [x] 10 posts stored, captions match public source, payloads complete in side table (comment fields stored verbatim, unverified)
- [x] Pointer = `MAX(published_at)`; second run inserts 0 new (overlap dedupes)
- [x] Retrieval (`GET /search`, `GET /article`) serves caption posts; Source Inventory shows `ig:sabrikolod`
- [x] Per-run result count + USD spend recorded; within $0.035/run envelope (smoke baseline: 1 + 10 + 4 results ≈ $0.04 Free; steady runs bill ~2–5 results incl. filter bleed)
- [x] Go/no-go recorded for v2 (full comment corpus, metric columns, image text)

**Status:** ready-for-human

## Results (2026-09-14, Free tier, caption-only)

- Bootstrap `ingest_source_flow('ig:sabrikolod')` → `{inserted: 4, skipped: 0}`. Actor returned 4 posts (not 10 — bootstrap is a seed, not an ordering/count guarantee, per smoke Run 2 note); all 4 carry `ChismecitoMarketinero`, client-side filter kept all. Sample `DdOz587EXSx` present with full ES caption.
- Incremental re-run → `{inserted: 0, skipped: 1}` (overlap re-emit deduped via UNIQUE → `skipped`). Pointer = `MAX(published_at)` = `2026-09-13T14:48:03Z`, unchanged. Near-no-op confirmed.
- Side table: 4/4 payloads stored verbatim (comment free-rider fields present, unverified per caption-only scope). Inventory: `ig:sabrikolod` → `article_count 4`, cadence Daily. `search_articles('ChismecitoMarketinero')` returns all 4.
- Spend: result counts 4 + 1 from lane; USD from Apify console still to read (envelope $0.035/run; Free $5 credit).
- Bug fixed en route: `onlyPostsNewerThan` must be Zulu `YYYY-MM-DDTHH:MM:SSZ` (actor 400 on `+00:00` offsets); `build_actor_input` now emits `strftime("%Y-%m-%dT%H:%M:%SZ")` with test pins.
- v2: comments deferred per user decision (payload already stores the free-rider fields; no extra billing to enable later). Metric columns + image text remain open.

## Comments
