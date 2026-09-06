# 07: Prefactor retrieval config seam

**What to build:** The registry accepts hub/sitemap retrieval types with per-Source retrieval stanzas and seeds for all 20 Sources, with zero behavior change — all RSS Sources still ingest exactly as before.

**Blocked by:** 06 — No-RSS extraction pipelines (spec).

**Status:** done

- [x] Curated set loads all 20 Sources (RSS + no-RSS) with declared discovery route and extractor family per Source
- [x] Unknown/unconfigured Sources fall back to safe RSS defaults without raising
- [x] Existing RSS fixture ingests still pass unchanged (rerun is a no-op, isolation intact)
- [x] Typecheck + test suite gate green before claiming done
