# 25: Sitemap fetch impersonated-only

**What to build:** Sitemap XML discovery stops failing on valid sitemaps — sitemap fetches use impersonated HTTP only and never route through the Markdown reader/scrape legs, so a reachable sitemap (e.g. the Retail Dive archive with 106 URLs) parses instead of failing `Unparseable sitemap: line 1, column 0`, and challenge/block pages surface as explicit per-URL `challenge` causes rather than XML parse errors.

**Blocked by:** None (can start immediately).

**Status:** done

- [x] Live-proven case fixed: reachable sitemap XML that the reader leg mangles (`Title: XML News Sitemap...` Markdown fed to the XML parser) now parses via the impersonated leg
- [x] Non-XML block/challenge payloads on sitemap URLs recorded as explicit challenge causes, never as `Unparseable sitemap`
- [x] Article-content fallback chain unchanged (impersonated → reader → Firecrawl still applies to article bodies)
- [x] Regression test locks the seam: sitemap parse receives XML bytes, reader legs never see sitemap URLs
- [x] CONTEXT.md updated if the V1-cuts retrieval paragraph contradicts the new behavior, so no contradiction remains
- [x] Typecheck + test suite gate green before claiming done

_Resolution (2026-09-12):_ landed as specified — sitemap traversal is the impersonated-only `fetch_sitemap_bytes` leg (`policy_get` never sees a sitemap URL), block payloads raise an explicit `challenge` cause via `_sitemap_challenge_detail`, and the CONTEXT V1-cuts retrieval paragraph agrees (lane counts restated as effective runtime lanes: 4 RSS / 16 discovery).
