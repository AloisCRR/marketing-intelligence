# 17: Per-source quotas in period bundles

**What to build:** A caller pulling a busy week gets a balanced Period Context bundle where no single high-volume Source can flood every slot — the digest candidate list reflects breadth across origins, not just output volume.

**Blocked by:** 16 — Truthful Period Context API (builds on the renamed recency list shape).

**Status:** ready-for-agent

- [ ] Callers can cap items per Source on the period bundle (and the cap composes with the existing Source allowlist)
- [ ] Default behavior without the cap is unchanged, so existing callers see no surprise
- [ ] Both caller surfaces expose the cap identically through the Service Adapter with 422 on invalid values
- [ ] A regression case pins a flood scenario: uncapped bundle dominated by one Source, capped bundle bounded per Source with remaining slots filled by others
