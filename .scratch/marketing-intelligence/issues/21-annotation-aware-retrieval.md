# 21: Annotation-aware retrieval filters

**What to build:** The digest consumer can pull a pillar-filtered, importance-ranked, source-balanced week in one call — the query where the importance and Topic annotations pay off and manual source juggling ends.

**Blocked by:** 17 — Per-source quotas; 19 — Agent-writable importance; 20 — Controlled topic vocabulary.

**Status:** done

- [x] The period bundle accepts importance floor, Topic filter, and per-source cap together in one call
- [x] Keyword search accepts the same Topic and importance filters so deep dives reuse the annotation layer
- [x] Unannotated Documents behave sanely under filtered queries (documented, never silently dropped or silently top-ranked)
- [x] Both caller surfaces expose all filters identically through the Service Adapter with 422 on invalid input; success criterion: importance ≥ 0.7 plus pillar filter plus per-source cap returns an on-pillar, source-spread week with zero manual juggling
