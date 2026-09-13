# 29: MarketingDirecto hub via reader

**What to build:** MarketingDirecto ingests via its hub through the reader leg — all of its sitemaps and homepage are 403 via impersonation, so hub discovery reuses the reader-tolerant path proven for MarTech, producing real inserts instead of `DiscoveryError: sitemap discovery yielded no URLs`.

**Blocked by:** 25 (Sitemap fetch impersonated-only), 27 (MarTech hub lane via reader).

**Status:** done

- [x] MarketingDirecto Ingestion Run completes without error and inserts real articles (`inserted > 0` on a live run)
- [x] Reuses the reader-tolerant hub discovery built for MarTech (no second implementation of the same path)
- [x] Impersonated-blocked sitemap/hub payloads surface as explicit challenge causes, never as `Unparseable sitemap`
- [x] Per-source pacing for the source is respected; per-source rerun stays isolated with explicit partial failure
- [x] CONTEXT.md updated if the V1-cuts retrieval paragraph contradicts hub-via-reader discovery, so no contradiction remains
- [x] Typecheck + test suite gate green before claiming done

_Resolution (2026-09-12):_ the source runs a hub-only registry stanza (no `sitemaps`, so the sitemap leg is unreachable for this source and `Unparseable sitemap` cannot arise at all; a blocked hub surfaces an explicit `challenge` cause), reusing the MarTech reader-tolerant hub path with no second implementation.
