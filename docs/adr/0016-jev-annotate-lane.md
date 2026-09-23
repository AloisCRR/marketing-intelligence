# ADR-0016 — Jev annotate lane (TypeSafe classification over all Documents)

**Status:** accepted
**Date:** 2026-09-21

## Context

`period-context` for ~8 days returns ~100k tokens: the bundle is unfiltered by
default (`topics=None, min_importance=None`), so the digest agent pays full
content price for off-theme evidence (e.g. finance) it must then discard.
The existing annotation stores (`document_importance`, `document_topics`) are
empty for machine writers — only agents write them by hand, one MCP call at a
time, which is exactly the token waste we want to kill.

Standing decisions this touches: CONTEXT "Deterministic by default" (no
LLM/embeddings/automatic extraction in ingest; ADR-0013 paid-vendor and
ADR-0014 vision exceptions already exist); glossary `Topic` (closed 40-slug
vocab, unknown rejected, `Avoid: free-text tag, category`); `Importance`
(agent-written 0–1, 0.3 flag/paywall cap); service-adapter parity (frozen
SEARCH 21 / ARTICLE 21 / PERIOD 13 key sets).

Smoke evidence (2026-09-21, live `jev-1.13.0`, 8 stratified local docs,
7 questions each in one call per doc, throwaway `/tmp/jev_probe.py`):

- Judgments sane: JCK sales → `jewelry` 0.98; 99/Márcia Sensitiva →
  `marketing` 1.00 + `brazil` 1.00 + `campaign`; finance control (InfoMoney
  Rubio trade-deals) → `none_of_above` 1.00, digest 0.94/3 — excluded from
  the priority default, still searchable. Thin state ("SBT" title-only)
  still resolved to `marketing`/`brazil`.
- Cost: 13,763 input tokens → $0.00058 for 8 docs (~$0.00007/doc).
  Full 630-doc backfill ≈ ~$0.05; daily increment ≈ fractions of a cent.
- Latency 206–275ms/call (first 805ms cold). Outputs free.
- Finding 1: single-Choice `.choice` under-tags (Tiffany-exec move forced
  `luxury` 0.81, hiding `jewelry` while the Noul read 0.75). Fix: take all
  options with probability ≥ 0.2 as tags, not just the winner — same call,
  no extra cost.
- Finding 2: `content_type` confidence runs low (0.47–0.65 on 4/8). Fix:
  store the ctype top pick regardless — it only feeds an OR filter, so a
  wrong ctype never hides a doc its pillar already surfaces.

## Decision

- **Decision vendor #4: TypeSafe Jev.** Pinned model id (never a moving
  alias), one `system_one` call per Document, state = `{title, source,
  text}` (first ~3000 chars of content). `TYPESAFE_API_KEY` from env at call
  time, never logged — same convention as
  `APIFY_API_TOKEN`/`FIRECRAWL_API_KEY`/`DEEPINFRA_API_KEY`.
  `typesafe-sdk` becomes a real dependency (probed 0.7.1).
- **One call, four judgments.** `pillar` Choice (18 pillars +
  `none_of_above`), `region` Choice (10 regions + `unclear`),
  `content_type` Choice (10 content-types), `digest_relevance` Score
  (0–3 rubric: irrelevant / background / relevant / must-read). Batching
  adds no noise per the vendor's parallel-questions cookbook: one call per
  doc, all questions together.
- **Jev is `reporter='jev'`, same latest-wins.** Writes go through the
  existing `set_importance` / `set_document_topics` — no migration, no new
  table, no key-set change. Agent overwrite wins if later. The 0.3
  flag/paywall cap is untouched (it is a cap, not a default).
- **Multi-label read-off, confidence-gated writes.** Pillar/region tags =
  every option with probability ≥ 0.2 (`none_of_above`/`unclear` ignored,
  canonicalized server-side). Pillar/region tags written only when that
  question's confidence ≥ 0.5; ctype top pick always; importance =
  `digest_relevance / 3` written only when its confidence ≥ 0.5, rationale
  records score + confidence. Zero writes → `low-confidence` cause, retry
  next run. Vendor error → cause, never a guess (spec §Knowledge #40).
- **Lane placement: `annotate_source_flow`, chained post-ingest.**
  Separate flow (never blocks retrieval on vendor latency), same `_BATCH_CHUNK` pattern, idempotency = "no `document_importance` row at
  all and no human (`reporter IS DISTINCT FROM 'jev'`) `document_topics`
  row yet" — any importance row (jev or human) or any human topics row
  settles the Document, so jev never overwrites a human judgment or human
  tags (explicit `ids=` included; skipped as `human-annotated` with no
  vendor call). Jev-only topics never settle it (topics rewrites are diff
  no-ops, so a retry is safe). Covers all 22 Sources including the
  instagram lane.
  `ingest_sources_flow` gains `annotate: bool = True`; backfill = the same
  flow over history (bounded, cost-logged). Failures are per-doc causes,
  never an Ingestion Run error.
- **Vocabulary adds: `colombia`, `argentina` regions.** Additive only
  (retire-by-alias rule untouched). No other slug changes.
- **Period-only default filter.** `get_period_context(topics=None)` now
  assumes `DEFAULT_PERIOD_TOPICS` (16 canonical slugs: `gen-z`,
  `consumer-behavior`, `jewelry`, `social-media`, `creator-economy`,
  `marketing`, `brand-strategy`, `ai`, `latam`, `mexico`, `brazil`,
  `colombia`, `argentina`, `report`, `campaign`, `earnings`); explicit
  `topics` (including `[]` or other slugs) always wins. Search stays
  unfiltered. Unknown-tag rejection (422) unchanged.

## Flagged deviations from standing decisions

- **Deterministic-only cut broken (third break).** A calibrated-decision
  model now classifies every Document. Contained: closed vocab + unknown
  rejected + confidence gates + write-nothing fallback + full history
  retained, so every judgment is auditable and overwritable.
- **Paid vendor #4.** ~$0.00007/doc measured; backfill ≈ $0.05 one-off;
  daily increment sub-cent. Per-run cost returned in the flow result dict
  and logged (no `ingestion_runs` schema change). Repricing risk: pin
  model id in code.

## Consequences

- New `annotate.py` lane (client wrapper, question set, thresholds,
  `annotate_document` returning tags/importance/causes without touching
  the DB directly — writes via importance/topics lanes).
- New flows `annotate_source_flow` / `annotate_sources_flow` + `annotate`
  flag on `ingest_sources_flow`; prefect deployment unchanged (same cron).
- `TOPICS` += 2 regions; `DEFAULT_PERIOD_TOPICS` in `period.py`,
  default applied in `service.get_period_context`.
- **Test pins:** multi-label threshold (0.2), confidence gates (0.5),
  write-nothing paths, idempotency selector, chain on/off, default-filter
  semantics (None → default, [] → unfiltered, explicit → exact),
  `colombia`/`argentina` canonicalization, HTTP↔MCP parity on changed
  period defaults, key sets byte-identical.
- **Deferred:** per-reader Jev rows (single `jev` reporter only),
  threshold tuning from production confidence distributions, new
  dimensions beyond the three axes.
