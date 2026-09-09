# 13: Fix JCK Online zero-entry feed

**What to build:** JCK Online ingests articles again — the dead feed is diagnosed and fixed or the Source moves to the sitemap discovery lane, and an empty feed surfaces as an explicit per-source Ingestion Run outcome instead of a silent zero.

**Blocked by:** None (can start immediately).

**Status:** ready-for-agent

- [ ] Live `https://www.jckonline.com/feed/` (1053 bytes, 0 entries on 2026-09-06 run) diagnosed: dead/changed URL vs. challenge page
- [ ] Feed URL corrected or Source switched to sitemap lane with real article inserts (`inserted > 0`)
- [x] Empty/unparseable feed raises an explicit per-source error (no silent `{inserted: 0, skipped: 0}`)
- [x] Ingestion Run rerunnable independently with explicit partial failure
- [x] Typecheck + test suite gate green before claiming done

_Note: live-run boxes stay unchecked — proof is mocked (fixture payloads, no live ingest run)._
