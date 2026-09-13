# 26: Stale and non-article sitemap excludes

**What to build:** Dive-family and Meio & Mensagem Ingestion Runs stop spending their backfill budget on URLs that can never become Documents — ancient `/archive/` sitemap children and `/podcasts/` audio-hub pages are excluded at discovery, so the planned URL set refills with real articles and the `missing title` / `empty after cleaning` discovery causes for those paths disappear.

**Blocked by:** None (can start immediately).

**Status:** done

- [x] Retail Dive + Marketing Dive runs no longer plan `/archive/` URLs; budget fills with fresh article URLs
- [x] Meio & Mensagem runs no longer plan `/podcasts/` URLs; budget fills with real articles
- [x] Excluded URLs never consume the per-source backfill bound and are logged distinctly from fetch failures
- [x] Fresh-article ingest counts for the three sources are unchanged or improved vs. pre-change runs
- [x] Regression test pins the exclude behavior at the discovery seam
- [x] Typecheck + test suite gate green before claiming done

_Resolution (2026-09-12):_ the exclude is a discovery-time drop, so it is met *differently* on 'logged distinctly from fetch failures' — excluded URLs are dropped silently by design (never fetched, never recorded as an error/cause), which is exactly what makes them distinct from per-URL fetch failures. Follow-up folded in: the child-loc exclusion is now recency-aware, so the 2026/08 and 2026/09 Dive archive children are still traversed (C back; fresh-article counts restored) while the 2015 child remains excluded.
