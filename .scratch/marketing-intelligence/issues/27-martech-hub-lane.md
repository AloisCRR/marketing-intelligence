# 27: MarTech hub lane via reader

**What to build:** MarTech ingests end-to-end again despite the Cloudflare wall on its feed — the Source moves off the dead 403 feed to hub discovery that works through the reader leg, article URLs are recovered from the hub payload, and article bodies enrich via the existing reader fallback, producing real inserts (`inserted > 0`) instead of a red Ingestion Run.

**Blocked by:** 25 (Sitemap fetch impersonated-only).

**Status:** done

- [x] MarTech Ingestion Run completes without error and inserts real articles (`inserted > 0` on a live run)
- [x] Hub discovery recovers article URLs from a reader-leg hub payload (proven: ~200 article links available)
- [x] Feed URL itself never routes through the reader as feed XML (the impersonated-only feed rule holds; the Source changes lanes instead)
- [x] Per-source rerun stays isolated with explicit partial failure (one bad article never aborts the run)
- [x] CONTEXT.md updated if the V1-cuts retrieval paragraph contradicts hub-via-reader discovery, so no contradiction remains
- [x] Typecheck + test suite gate green before claiming done

## Implementation notes (agent, 2026-09-12)

- Curated stanza (both JSON copies, byte-identical): `{"type": "hub", "policy":
  "impersonated-feed", "extractor": "generic", "hub": "https://martech.org/",
  "link_pattern": "-", "sitemap_exclude": [...], "pacing_ms": 1000, "max_urls": 50}`.
  `rss_url` stays as provenance (the dead 403 feed no longer selects the lane —
  routing reads the effective stanza type, which ticket 28 landed).
- `link_pattern: "-"`: martech.org serves root-level `<slug>/` article URLs, so no
  substring isolates them from the taxonomy/author/archive families by itself;
  the hyphen is the marker every article slug carries that the site root (`/`)
  and the bare `/page/N` indices do not, so those never become jobs. The
  taxonomy (`/topic/`, `/author/`, `/page/`, `/conference/`) and corporate
  (`/about-martech-org/`, `/privacy-policy/`, `/white-papers/`, ...) families
  are dropped by the stanza `sitemap_exclude`, which plan_harvest also applies
  to hub-found links.
- Live 2026-09-12: every martech.org impersonated fetch is Cloudflare 403 (hub,
  feed, robots.txt, all sitemaps, articles). The ONLY working leg is the Jina
  reader, which serves the hub as Markdown (26,444 bytes, 205 Markdown links,
  zero `<a href>`) and article bodies as Markdown. Two generic discovery.py
  changes were therefore required (Main-authorized, not source-keyed):
  `extract_hub_links` also matches Markdown links (images are never links; an
  image wrapped in a link contributes the link target), and `extract_article`
  accepts the reader header block (`Title:` / `Published Time:` / `Author:`)
  when `markdown=True`, stripping that chrome from the stored body.
  `_jina_reader_get` now keeps the requested URL as the fetch identity (the
  `r.jina.ai` hop is never an article URL).
- Offline/live proof with the real reader payload: plan = 39 article jobs, zero
  non-article jobs, no feed/sitemap fetch; one planned article through the real
  chain extracts a titled document (real martech.org URL/canonical, published
  2026-09-11T14:27:40+00:00, 9,775 body chars, reader chrome stripped).
- Tests: test_retrieval.py (stanza + config shape), test_sitemap_discovery.py
  (HTML hub seam, Markdown hub seam, reader-Markdown article provenance, lane
  routing, one bad article never aborts), plus MarTech-as-RSS vehicles migrated
  to other RSS sources in test_jck_feed.py / test_period_context.py /
  test_dedupe_quality.py / test_flows_retry.py / test_remaining_sources.py.

_Resolution (2026-09-12):_ live proof for the reader-leg checkbox is 39 planned article jobs (zero non-article jobs) from the real martech.org reader Markdown payload — the ~200 raw links shrink to 39 once the stanza link pattern and excludes apply — with one planned article extracting a titled 9,775-char document.
