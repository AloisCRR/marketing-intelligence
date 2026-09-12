# 24: Computed trends and earned analytics (future)

**What to build:** After roughly four weeks of Topic and importance data, the period bundle earns back one computed analytics field — an explainable emerging-Topics report the human independently recognizes — instead of the empty placeholders removed in ticket 16.

**Blocked by:** 21 — Annotation-aware retrieval (needs weeks of real annotation data, not just the filters existing).

**Status:** ready-for-agent

- [ ] Emergence is defined explainably (new vs prior weeks, multi-source spread, week-over-week doubling) and reported split global vs LATAM via region tags
- [ ] The first computed report passes the second-opinion test: it flags at least one trend the human independently recognized that week
- [ ] Previously removed analytics names return only as each becomes genuinely computed; anything still uncomputed stays absent
- [ ] Explicitly deferred beyond this ticket: multilingual embeddings for cross-lingual dedup and clustering, content-type classification, and human-edit calibration loops — each needs the volume or history this ticket starts accumulating
