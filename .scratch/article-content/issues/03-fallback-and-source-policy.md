# 03 — Gated fallback plus per-Source enrichment policy

**What to build:** end-to-end coverage for the pages the primary extractor misses: when local enrichment fails on bot protection, JS-heavy rendering, or rate limiting, a gated zero-ops fallback reader is tried under data-minimizing headers with backoff; each Source carries its own enrichment policy (threshold, force-on, force-off) so paywalled or bot-protected Sources get deliberate treatment.

**Blocked by:** 02 — Thin-triggered Markdown enrichment in the Ingestion Run (needs the enrichment stage seam).

**Status:** ready-for-agent

- [ ] Primary-miss pages (challenge/bot/JS signals) succeed via the gated fallback and store clean Markdown
- [ ] Fallback respects privacy headers, never sends credentials, backs off on rate limits, and any fallback failure still keeps the RSS text with recorded cause
- [ ] Per-Source policy (threshold override, force enrichment on/off) is honored by the Ingestion Run
- [ ] No fallback traffic happens for Sources with enrichment forced off
- [ ] Full test suite passes
