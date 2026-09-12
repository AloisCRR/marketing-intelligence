# 20: Controlled topic vocabulary and tagging

**What to build:** The digest consumer can tag any Document with canonical pillar/region/content-type Topics from a discoverable vocabulary, so "Gen Z self-purchase" is one tag everywhere instead of three spellings — the prerequisite for any future emergence computation.

**Blocked by:** 16 — Truthful Period Context API; 19 — Agent-writable importance (shares the annotation history design).

**Status:** ready-for-agent

- [ ] A read-only vocabulary listing lets the agent discover canonical Topic slugs without leaving the caller surface
- [ ] Setting Topics accepts synonyms and canonicalizes them server-side; unknown tags are rejected or canonicalized, never stored silently as-is
- [ ] Topic tags are visible wherever Documents are read, and vocabulary changes stay additive (retire by aliasing, never deleting)
- [ ] Both caller surfaces expose tag writes and vocabulary reads identically through the Service Adapter with 422 on invalid input
