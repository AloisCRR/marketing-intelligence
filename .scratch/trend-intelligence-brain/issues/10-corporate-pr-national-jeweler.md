# 10: Corporate PR + National Jeweler pipelines

**What to build:** National Jeweler (large URL set + hub-anchor fallback with numeric-ID-reuse guard), Richemont PR (large URL set), and LVMH PR (per-locale sitemap index only) ingest with generic extraction, each verified on sitemap-discovered URLs — never on the stale curated samples.

**Blocked by:** 08 — Pilot sitemap-discovery slice.

**Status:** done

- [x] National Jeweler hub listing yields article URLs; ID-reuse/slug-change never stores a wrong article (canonical check)
- [x] Richemont releases ingest with full bodies and provenance
- [x] LVMH extractor verified on a sitemap-discovered URL (hub rendering not required)
- [x] Each Source rerunnable independently with explicit partial failure
- [x] Typecheck + test suite gate green before claiming done
