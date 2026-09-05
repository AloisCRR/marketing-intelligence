# 04 — Weekly context + observability

**What to build:** A caller passing an explicit week gets a grounded evidence bundle, and an operator can see ingestion health per source — no trend claims before history exists.

**Blocked by:** 02 — Remaining 3 sources; 03 — Dedupe + data-quality hardening.

**Status:** ready-for-agent

- [ ] `get_weekly_context(from, to)` takes explicit dates interpreted in America/Panama and returns period + important articles with provenance
- [ ] Ingestion-run and per-source health status are observable independently of article data
- [ ] Contract tests cover the semantic interface; a seeded regression corpus exists for future extraction/ranking changes
- [ ] No velocity or emerging-topic claims in V1 (documented as needing accumulated history)
