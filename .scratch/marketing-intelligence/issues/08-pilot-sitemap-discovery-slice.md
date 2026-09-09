# 08: Pilot sitemap-discovery slice (one WordPress Source)

**What to build:** One WordPress Source (MarketingDirecto) flows end-to-end via sitemap-first discovery: discover URLs → fetch articles → extract bodies → normalize with provenance → dedupe upsert, rerunnable with explicit partial failure.

**Blocked by:** 07 — Prefactor retrieval config seam.

**Status:** done

- [x] Sitemap index traversal yields article URLs (newest-first bounded backfill)
- [x] Articles carry full provenance (Source, canonical URL, timestamps, language, hash) identical in shape to RSS Documents
- [x] Rerun is a no-op; one bad URL never aborts the Ingestion Run (explicit per-Document skip, per-Source error isolation)
- [x] Canonical-URL tracking defeats slug changes (no wrong-article stored)
- [x] Typecheck + test suite gate green before claiming done
