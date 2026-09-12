# 19: Agent-writable importance scores

**What to build:** The digest consumer can record "why this matters" on any Document as a 0–1 score with rationale, read it back on search and read results, and trust that extraction-flagged or paywalled bodies can never outrank clean evidence.

**Blocked by:** 16 — Truthful Period Context API (annotations land on the honest bundle shape).

**Status:** ready-for-agent

- [ ] Setting, re-setting, and reading back an importance annotation round-trips per Document with reporter identity and timestamp; latest write wins and history is retained
- [ ] Scores outside 0–1 are rejected as caller errors; extraction-flagged or paywalled Documents are hard-capped at 0.3 by the server regardless of the submitted score
- [ ] Importance is visible wherever Documents are read (search results, single read, period bundle items) without extra calls
- [ ] Both caller surfaces expose the write identically through the Service Adapter with 422 on invalid input
