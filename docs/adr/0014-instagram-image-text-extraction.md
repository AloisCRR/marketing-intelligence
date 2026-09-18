# ADR-0014 — Instagram image-text extraction (DeepSeek vision over Apify carousels)

**Status:** proposed
**Date:** 2026-09-16

## Context

Caption-only IG Documents underspecify image-heavy posts: the caption
teases ("McDonald's transforms a Monopoly promo into a collectible
accessory") while the carousel frames carry the specifics (product name
`MONIdero`, mechanic, partner). MCP consumers misread thin captions as
the whole story.

Standing decisions this touches: CONTEXT V1 cuts (deterministic-only
ingest: no LLM/embeddings; 21st source `ig:sabrikolod` caption-first,
`image_text: ignore`, `IMAGE_TEXT_MODES=("ignore",)`); ADR-0013
(fetch-full/store-raw/expose-caption, `basicData` billing, `document_payloads`
side table, enrichment bypass). Per AGENTS.md, deviations flagged below.

Smoke evidence (2026-09-16, live, ~$0.01 total, no repo changes):

- **MiMo-V2.5 rejected.** Image-URL mode timed out (CDN fetch fail);
  base64 default burned 999 reasoning tokens with `content: null`
  (`finish_reason: length`); `reasoning_effort: none` answered but
  degenerated into `oo`-repetition. 85–116 s/image. Dropped.
- **DeepSeek-V4.1-Flash accepted** (`deepseek-ai/DeepSeek-V4.1-Flash`,
  `reasoning_effort: none`, base64, `temperature: 0`,
  `max_tokens: 1000`): 4/4 local covers clean `stop` (2–5 s, ~936
  prompt tokens flat, ~$0.0002/image); pilot `Dc9PS-SkU6_`
  (`jordisanildefonso`, 4,688 likes) 17/17 frames in ~150 s,
  ~$0.005 total. Spanish accents/`’`/emoji preserved; photo-only
  frame correctly returns `NO_TEXT`.
- **Accuracy floor:** sponsor-logo lines and tiny glyphs hallucinate
  (`ZERBINO`, `caca`); tiled wallpaper patterns repeat until
  `max_tokens` cuts them; lifestyle-photo small text degrades to
  fragment soup. Fit for context-enrichment, never verbatim quotes.
- **`detailedData` is mandatory for carousels.** `basicData` returns
  `childPosts: None` — inner frames invisible. Single-post
  `detailedData` fetch (~5 s) fits the $0.035 `maxTotalChargeUsd`
  envelope but bills the extra `post-details` event ADR-0013 warned
  about. Cover == frame0 duplicate: dedupe by bytes before billing
  vision.

## Decision

- **Vision vendor #3: DeepInfra `deepseek-ai/DeepSeek-V4.1-Flash`.**
  Pinned model id, `reasoning_effort: none`, base64 upload (never pass
  CDN URLs — the provider cannot fetch them), `temperature: 0`,
  `max_tokens: 1000`, exact-text prompt with `NO_TEXT` sentinel.
  `DEEPINFRA_API_KEY` from env at call time, bearer header, never
  logged — same convention as `APIFY_API_TOKEN`/`FIRECRAWL_API_KEY`.
- **Per-frame side table, caption untouched.** New
  `document_image_texts(document_id FK, frame_index INT,
  image_text TEXT, model TEXT, extracted_at TIMESTAMPTZ)` — one row
  per frame, PRIMARY KEY `(document_id, frame_index)`, ON DELETE
  CASCADE. `documents.content` stays caption-only; image text is a
  separate read field so callers see both without confusion. Matches
  the `document_payloads`/importance/topics precedent; leaves
  `NormalizedDocument`/`INSERT_SQL` untouched.
- **Lane placement: post-upsert, post-payload, enrichment-bypassed.**
  The IG lane runs fetch (`detailedData` when the stanza opts into
  image text) → map → upsert → payload write → image-text stage:
  download frames (impersonated fetch, bytes-dedupe incl. cover) →
  vision per frame → side-table write. Best-effort like the payload
  write: failures record per-frame causes in the run result, never
  fail the Ingestion Run. `_enrich_docs` stays bypassed (`force_off`).
- **Per-account opt-in, pilot only.** `IMAGE_TEXT_MODES +=
  ("extract",)`; stanza key `image_text: extract` on
  `ig:jordisanildefonso` only (new 22nd source row, same per-account
  pattern). Every other source keeps `ignore`; `extract` on a
  non-Instagram stanza is dropped by `_validated_extras` like any
  invalid value. No backfill of existing rows until the pilot proves
  stable.
- **Retrieval exposure without payload-shape break.** `get_article`
  gains `image_texts[]` (ordered frames: `frame_index`,
  `image_text`, `model`, `extracted_at`); SEARCH/PERIOD items gain
  `has_image_text: bool` only (no per-frame text in list payloads).
  HTTP + MCP through the service adapter by construction; frozen key
  sets grow additively, never rename.

## Flagged deviations from standing decisions

- **Deterministic-only cut broken (second break after ADR-0013's
  paid-vendor exception).** A hosted VLM now runs in the ingest path.
  Contained: pinned model + temperature 0 + exact-transcription
  prompt (no summarization, no judgment), output stored as evidence
  text beside the caption, never merged into it; hallucination risk
  documented as the accuracy floor above.
- **Paid vendor #3 + billing vector.** DeepInfra per-token billing
  (~$0.0002/frame measured; ~$0.004/day at 20 frames, ~$0.02/day at
  100) plus Apify `detailedData` detail-event surcharge on opted-in
  accounts. Both capped per run (`maxTotalChargeUsd`,
  per-frame count cap); costs logged per Ingestion Run.
- **22nd source class instance.** `ig:jordisanildefonso` is the second
  premium per-account row; same posture as ADR-0013, not a widened
  scraper.

## Consequences

- **Cost envelope.** ~$0.005/carousel-post (17 frames) + Apify detail
  event inside $0.035/run cap. At 5 posts/day: ~$0.025/day vision.
  Repricing risk: DeepInfra promo pricing volatile — budget for it,
  pin model id in code so a silent swap is impossible.
- **New migration `013`** for `document_image_texts`; new
  `IMAGE_TEXT_MODES` entry; `image_text` joins the carried Instagram
  stanza keys (already carried — only the allowed set grows).
- **Test pins:** bytes-dedupe (cover==frame0 billed once),
  post-upsert write ordering, failure-never-blocks-run, `extract`
  dropped on non-Instagram stanzas, retrieval `image_texts[]` /
  `has_image_text` parity, `NO_TEXT` frames stored (not skipped),
  prompt/model pin determinism.
- **Deferred:** backfill of `ig:sabrikolod` rows, video-frame
  extraction (poster only in v1), OCR sidecar (Track B research
  stands by if vision pricing breaks), verbatim-quote guarantees.
