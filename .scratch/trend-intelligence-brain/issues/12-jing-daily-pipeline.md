# 12: Jing Daily pipeline (JSON-LD-first extraction)

**What to build:** Jing Daily ingests via hub anchors plus sitemap with JSON-LD `articleBody`-first extraction (generic fallback; still-thin results kept-aside/flagged, never bypassed).

**Blocked by:** 08 — Pilot sitemap-discovery slice.

**Status:** done

- [x] Hub listing yields `/posts/` URLs; sitemap extends coverage
- [x] Open articles yield full bodies from embedded structured data where generic extraction goes thin
- [x] Metered/thin bodies resolve to keep/flag with Extraction Flag path (reason + detail + reporter + timestamp), never to circumvention
- [x] Ingestion Run rerunnable independently with explicit partial failure
- [x] Typecheck + test suite gate green before claiming done
