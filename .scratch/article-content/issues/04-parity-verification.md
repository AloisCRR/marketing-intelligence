# 04 — Parity and stability verification across both surfaces

**What to build:** end-to-end proof that the whole feature holds together: Weekly Context and search lists byte-stable in shape, the one-item lookup identical over HTTP and MCP, enrichment plus fallback behaving per policy with visible run accounting, and exactly one canonical stored text per Document ready for future vectorization.

**Blocked by:** 01 — One-item article lookup on both surfaces; 02 — Thin-triggered Markdown enrichment in the Ingestion Run; 03 — Gated fallback plus per-Source enrichment policy.

**Status:** ready-for-agent

- [ ] Weekly Context and search list payloads match their pre-feature shapes key-for-key at the same limits
- [ ] One-item lookup returns identical payloads over HTTP and MCP, including identical validation failures
- [ ] An Ingestion Run over mixed content (sufficient RSS, thin RSS, bot-blocked page, paywalled page) stores the expected bodies and reports per-item causes with the run completing
- [ ] Full test suite passes with no model calls on any covered path
