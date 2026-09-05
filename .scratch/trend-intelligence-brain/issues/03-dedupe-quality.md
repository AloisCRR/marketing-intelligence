# 03 — Dedupe + data-quality hardening

**What to build:** A brain that stays clean under repeats and messy feeds: exact duplicates collapse, bad items are visible rather than silently lost, and timestamps stay trustworthy.

**Blocked by:** 01 — Foundation + first source end-to-end (parallel with 02).

**Status:** ready-for-agent

- [ ] Canonical-URL + content-hash exact dedupe: repeated ingestion is a no-op
- [ ] Malformed items and missing optional fields are handled; incomplete records are identifiable for reprocessing/exclusion
- [ ] Publication and retrieval timestamps stay distinct and timezone-aware
- [ ] Story/event column is reserved but unused — independent coverage is preserved, not collapsed
