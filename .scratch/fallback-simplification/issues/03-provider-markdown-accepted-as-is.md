# 03: Provider Markdown accepted as-is

**What to build:** Markdown that arrives already extracted (reader leg, scrape leg, structured-data body) is stored after a thin-check only — it is never run back through the HTML cleaner, so links, images, and formatting survive intact at every call site.

**Blocked by:** None (can start immediately).

**Status:** ready-for-agent

- [ ] Reader, scrape, and structured-data Markdown bypass the HTML cleaner on all four call sites; thin provider output still counts as a miss
- [ ] A regression test with links/images in provider Markdown proves the cleaner no longer alters it
- [ ] The test that pinned byte-identical cleaner round-trips is replaced by the new contract
- [ ] Enrichment gating unchanged: only primary-miss signals enter the fallback chain
- [ ] Full suite green (`pytest -n auto`, `ruff check`, `ruff format --check`, `mypy src`)
