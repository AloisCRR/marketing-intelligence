# 11: Dive sites pipelines

**What to build:** Retail Dive and Marketing Dive ingest via hub anchors plus sitemap index and news sitemap (impersonated retry only for the news sitemap), with generic extraction and full provenance.

**Blocked by:** 08 — Pilot sitemap-discovery slice.

**Status:** done

- [x] Hub listings yield `/news/` URLs; sitemap index + news sitemap extend coverage
- [x] Plain-HTTP-first holds; impersonated retry fires only where plain fetch is refused
- [x] Bodies are substantive (thin threshold governs keep-vs-flag, no bypass)
- [x] Each Source rerunnable independently with explicit partial failure
- [x] Typecheck + test suite gate green before claiming done
