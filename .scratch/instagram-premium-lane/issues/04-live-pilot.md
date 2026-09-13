# 04 — Live pilot: 10 sabrikolod posts, caption verification

**What to build:** Live run against `sabrikolod` on Starter plan: bootstrap run + one incremental run. Verify caption fidelity vs the public post (incl. sample `DdOz587EXSx`), embedded `latestComments`/`commentsCount` present in payload, pointer advances, second run is near-no-op (only overlap re-emit). Confirm the open research question: comment completeness of the logged-out surface. Record spend per run from Apify console. Smoke 2026-09-13 already proved caption fidelity + pointer bleed on Free tier (~$0.04); pilot re-verifies on Starter with side-table writes.

**Blocked by:** 03

**Status:** needs-triage

- [ ] 10 posts stored, captions match public source, payloads complete in side table
- [ ] Pointer = `MAX(published_at)`; second run inserts 0 new (overlap dedupes)
- [ ] Retrieval (`GET /search`, `GET /article`) serves caption posts; Source Inventory shows `ig:sabrikolod`
- [ ] Per-run result count + USD spend recorded; within $0.035/run envelope (smoke baseline: 1 + 10 + 4 results ≈ $0.04 Free; steady runs bill ~2–5 results incl. filter bleed)
- [ ] Go/no-go recorded for v2 (full comment corpus, metric columns, image text)

## Comments
