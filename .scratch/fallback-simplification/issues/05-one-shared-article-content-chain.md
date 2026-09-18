# 05: One shared article-content chain

**What to build:** Enrichment and discovery fetch article content through a single shared three-leg implementation instead of three duplicated reader legs — one behavior, one failure-cause format, with enrichment's fallback gating preserved and discovery's ungated chain preserved.

**Blocked by:** 01 (Impersonation-only feed fetching), 03 (Provider Markdown accepted as-is).

**Status:** done

- [x] A single chain serves both the enrichment and discovery paths; the duplicated legs are gone
- [x] Enrichment still gates fallback entry on primary-miss signals; discovery still walks every leg on fetch failure
- [x] All-legs-failed causes name each leg once with no doubled prefixes, secrets still scrubbed
- [x] Ingestion behavior verified end to end on one RSS and one sitemap source (inserted/skipped/causes shape unchanged)
- [ ] Full suite green (`pytest -n auto`, `ruff check`, `ruff format --check`, `mypy src`) — phase-end orchestrator gate

**Note:** `enrich.article_content_chain` is the one chain (impersonated
`fetch_impersonated` → Jina `fetch_reader` → lazy `fetch_via_firecrawl`), with
the one bounded, scrubbed cause format. Call sites: `enrich._fallback_after_primary_miss`
(gated entry, `primary_cause` + `min_chars=threshold`, thin provider output is a
miss) and `discovery.policy_get` (ungated: `primary=_impersonated_get`,
`reader=_jina_reader_get`, every leg walked on fetch failure). Discovery's two
leg functions are now thin adapters over the shared legs that re-raise
`ArticleFetchError`; its local `_decode_body`, `_chain_detail`, secret regex,
detail cap, reader body cap and `curl_cffi` import are deleted. `ProviderMarkdown`
moved to `enrich.py` (the chain tags both provider legs, so the T03 as-is storage
contract holds through the shared path; discovery imports it). The reader leg is
labelled `jina reader` in the shared detail, so each leg is named exactly once
and the historical `reader:` wording enrichment causes already carry is kept.
