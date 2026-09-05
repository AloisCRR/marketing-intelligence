# 01 — One-item article lookup on both surfaces

**What to build:** end-to-end one-item full-text fetch: the agent passes a single Article identifier and gets back the full stored Markdown plus full provenance (title, URLs, Source, timestamps, author), identically over HTTP and MCP through the Service Adapter, with the existing validation failure shape.

**Blocked by:** None — can start immediately.

**Status:** ready-for-agent

- [ ] Lookup by one canonical key (URL, canonical URL, or identifier) returns full stored body plus provenance for a known Article on both surfaces with identical payloads
- [ ] Unknown/blank identifier fails with the existing validation shape (HTTP 422 / MCP tool error)
- [ ] Weekly Context and search list shapes are unchanged (provenance only, same keys and limits)
- [ ] Full test suite passes
