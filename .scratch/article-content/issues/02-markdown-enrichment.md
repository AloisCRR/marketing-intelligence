# 02 — Thin-triggered Markdown enrichment in the Ingestion Run

**What to build:** end-to-end enrichment of thin RSS bodies into clean stored Markdown: when an item's RSS text is thin or missing (character threshold, per-Source overridable), the Ingestion Stage fetches the article URL and cleans it to Markdown with the local-first primary extractor; success stores Markdown as the canonical Document body, any failure keeps the RSS text and records the cause without blocking the run.

**Blocked by:** None — can start immediately (runs parallel with 01).

**Status:** ready-for-agent

- [ ] Items with sufficient RSS text skip enrichment untouched (zero fetch cost, byte-identical stored body)
- [ ] Thin/missing items get clean Markdown stored as the canonical body with the dedupe hash over stored text
- [ ] Every enrichment failure (paywall, bot protection, timeout, unparseable body) keeps the RSS text, records method plus cause on the run's visible accounting, and the Ingestion Run completes with explicit partial failure
- [ ] Rerun over the same feed inserts nothing new; no model calls happen anywhere on the enrichment path
- [ ] Full test suite passes
