# 14: Filter Exame webstories pollution

**What to build:** Exame ingests real articles again — the sitemap discovery lane stops spending its backfill budget on `/webstories/` URLs and 404s resolve to explicit per-URL skips while genuine articles insert.

**Blocked by:** None (can start immediately).

**Status:** ready-for-agent

- [ ] 2026-09-06 run (`discovery docs=0 skipped=50`, ~47× `missing title` on `/webstories/`, 3× `404`) reproduced as sitemap-pattern gap, not extractor gap
- [x] Sitemap/link pattern excludes `/webstories/` (or equivalent non-article set) so `max_urls=50` covers real articles
- [ ] Live Ingestion Run yields `inserted > 0` with remaining 404s/unparseables as explicit `discovery_causes` skips
- [x] Ingestion Run rerunnable independently with explicit partial failure
- [x] Typecheck + test suite gate green before claiming done

_Note: live-run boxes stay unchecked — proof is mocked (fixture sitemaps, no live ingest run)._
