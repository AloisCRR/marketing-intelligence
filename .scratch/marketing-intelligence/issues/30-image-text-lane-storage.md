# 30: Image-text lane + side-table storage

**What to build:** Instagram image-text extraction runs as a post-upsert, post-payload stage of the IG lane, gated by the stanza `image_text` key — vision per carousel frame via pinned DeepInfra model, stored in a new `document_image_texts` side table, caption untouched, failures never blocking the Ingestion Run.

**Blocked by:** None (can start immediately; ADR-0014 is the spec).
**Status:** implemented (commit 614edf3; gates green: ruff check/format, mypy, 758 passed 1 skipped)

- [ ] Migration `013_document_image_texts.sql`: `document_image_texts(document_id UUID REFERENCES documents(id) ON DELETE CASCADE, frame_index INT, image_text TEXT NOT NULL, model TEXT NOT NULL, extracted_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY (document_id, frame_index))`, `IF NOT EXISTS` everywhere, idempotent re-runnable
- [ ] New module `src/marketing_intelligence/image_text.py`: pinned `MODEL_ID = "deepseek-ai/DeepSeek-V4.1-Flash"`, `reasoning_effort: none`, base64 upload (never CDN URLs), `temperature: 0`, `max_tokens: 1000`, exact-transcription prompt with `NO_TEXT` sentinel; `DEEPINFRA_API_KEY` read from env at call time, bearer header, never logged; per-frame timeout 120 s; failures return explicit cause strings, never raise
- [ ] Frame enumeration helper: cover `displayUrl` + `childPosts[].displayUrl` from the `detailedData` payload, SHA-256 bytes-dedupe before any vision call (cover == frame0 bills once); `NO_TEXT` frames are stored, not skipped
- [ ] Lane wiring in `instagram.py`: `image_text == "extract"` selects `dataDetailLevel: detailedData`, else `basicData` (unchanged default); stage runs after `write_document_payloads`, writes one row per frame keyed by `(document_id, frame_index)` resolved by URL like the payload lane; run result keeps `{inserted, skipped[, error]}` plus nonzero-only `image_text_frames` / `image_text_causes` (per-frame `<frame_index>: <detail>`); any stage failure degrades to causes, never fails the run
- [ ] `sources.py`: `IMAGE_TEXT_MODES += ("extract",)`; `_validated_extras` drops `extract` on non-Instagram stanzas (same silent-drop contract as other invalid values); `get_retrieval_config` carries the key unchanged
- [ ] `flows.py`: IG branch docstring/contract notes the new stage; enrichment bypass (`force_off`) unchanged; no payload-shape break for non-opted-in sources
- [ ] Regression tests (fake-conn, no live billing): bytes-dedupe bills cover once; write-after-upsert ordering; unknown-URL pairs never raise; stage failure yields causes with `{inserted, skipped}` intact; `extract` dropped on RSS stanzas; prompt/model/timeout pins
- [ ] Typecheck + full suite green (`ruff check`, `ruff format --check`, `mypy src`, `pytest -n auto`)
