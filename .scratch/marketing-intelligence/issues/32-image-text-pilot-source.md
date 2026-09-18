# 32: Pilot source `ig:jordisanildefonso` + live proving

**What to build:** The pilot account exists as a 22nd source row with `image_text: extract`, proves the lane end-to-end on live data, and records the cost/latency envelope that gates any wider rollout.

**Blocked by:** 30 (lane must exist), 31 (reads must expose what the pilot writes).

**Status:** ready-for-agent

- [ ] Curated stanzas (both JSON copies, byte-identical): `ig:jordisanildefonso` with `retrieval: {type: instagram, policy: apify-premium, username: jordisanildefonso, content_mode: caption_first, image_text: extract}`, `enrichment: {threshold: 500, mode: force_off}`, `cadence: Daily`, `access: premium`, `language: Spanish`, `sample_article_url: https://www.instagram.com/p/Dc9PS-SkU6_/`; `ig:sabrikolod` stanza unchanged (`image_text: ignore`)
- [ ] Seed: `db.SEED_SOURCES` + migration-013 re-assert (same `ON CONFLICT (name) DO NOTHING` pattern as 012) for the new row; `test_db_seeds.py` pin
- [ ] Live proving run (one `ingest_source_flow("ig:jordisanildefonso")`): post `Dc9PS-SkU6_` ingested with caption Document + payload row + 17 `document_image_texts` rows; run result shows `{inserted: 1, skipped: 0, image_text_frames: 17}` (dedupe may vary cover/frame0); per-frame DeepInfra cost + Apify charge recorded in the ticket comments
- [ ] Retrieval proof: `get_article` on the pilot Document returns ordered `image_texts[]`; `search_articles`/`get_period_context` show `has_image_text: true`; caption `content` unchanged
- [ ] Rollout gate note in ticket comments: measured $/post, s/frame, Apify detail-event surcharge, and the explicit go/no-go for `ig:sabrikolod` backfill (deferred by default — no backfill in this ticket)
- [ ] CONTEXT.md source counts restated (21 → 22 with the pilot exception); typecheck + full suite green
