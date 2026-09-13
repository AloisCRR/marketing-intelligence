# 02 — ig:sabrikolod source row + registry stanza

**What to build:** `sources` row `ig:sabrikolod` (deterministic uuid5 id, language `es`), registry entry in both `data/curated-sources.json` and `.scratch` dev override, name in `V1_SOURCES`, seed in `db.SEED_SOURCES` + migration re-assert. Stanza: `{username: sabrikolod, hashtag_filter: ChismecitoMarketinero, content_mode: caption_first, image_text: ignore, cadence}`. New `RETRIEVAL_TYPES` entry + policy value (ADR-0013 exception); new stanza keys added to `_validated_extras` + `get_retrieval_config` (unknown keys drop silently otherwise).

**Blocked by:** 01

**Status:** ready-for-human

- [x] Source row exists with deterministic id; missing-row guards (`_ensure_source_row`, upsert) pass
- [x] Stanza loads in dev + baked-in paths; retrieval type no longer falls back to `rss`
- [x] Source appears in Source Inventory with `0`/`null` before first run
- [x] `APIFY_API_TOKEN` read from env at call time, never logged (Firecrawl precedent)

## Comments
