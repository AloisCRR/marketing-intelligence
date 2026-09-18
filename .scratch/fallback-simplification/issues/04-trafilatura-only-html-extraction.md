# 04: Trafilatura-only HTML extraction

**What to build:** HTML-to-Markdown extraction runs a single converter. The legacy regex converter and the HTML-level boilerplate scanner are deleted; the Markdown-level family cleanup survives only if its fixtures fail without it.

**Blocked by:** 03 (Provider Markdown accepted as-is).

**Status:** done

- [x] No HTML body reaches a regex converter; extraction output is unchanged-or-cleaner on the family fixtures
- [x] The Markdown-level family cleanup is kept or deleted based on fixture results, not opinion, with the decision recorded in the ticket
- [x] Architecture decision record updated to supersede the regex-primary era
- [ ] Full suite green (`pytest -n auto`, `ruff check`, `ruff format --check`, `mypy src`) — left to the phase-end gate

## Decision

**Markdown-level family cleanup: KEPT** (`_nj_clean_markdown` in
`src/marketing_intelligence/enrich.py`), on fixture evidence, not opinion.
Measured on the two family fixtures with trafilatura only (family URL,
`deduplicate=False`, `include_links=True`):

| fixture | trafilatura raw | with family cleanup (= pinned clean body) |
| --- | --- | --- |
| `nj_article_genz_polluted.html` | 883 chars, `](`/URL links kept | 756 chars, byte-identical to `nj_article_genz_clean.md` |
| `nj_article_closure_polluted.html` | 726 chars, `](`/URL links kept | 646 chars, byte-identical to `nj_article_closure_clean.md` |

Without it `tests/test_nj_extraction_repair.py` fails its pinned-body and
`"](" not in markdown` assertions, so the cleanup (anchor flattening plus the
boilerplate-trailer cut, which stays as part of the same family pass) is
load-bearing.

**HTML-level boilerplate scanner: DELETED** (`_strip_nj_boilerplate`,
`_nj_marker_in`, `_CLASS_OR_ID_RE`, `_HTML_TAG_RE`, `_OPEN_TAG_RE`,
`_CLOSE_TAG_RE`, `_NJ_CONTAINER_TAGS`, `_VOID_TAGS`,
`_NJ_BOILERPLATE_MARKERS`) along with the regex converter (`_regex_to_markdown`,
`_anchor_to_markdown`, `_BLOCK_RE`, `_TAG_RE`, `_ANCHOR_RE`, `_WS_RE`,
`_NON_VISIBLE_RE`, `import html as _html`). Fixture evidence: trafilatura on
the raw polluted HTML already drops all NJ chrome (nav / related /
newsletter / footer — raw output is chrome-free), and the pre-change pipeline
with the HTML-level scanner produced byte-identical output to the pipeline
without it on both fixtures. `clean_to_markdown` now has no fallback: HTML
trafilatura cannot extract yields `""`, so callers keep their thin-check /
keep-RSS / `UnparseableBody` semantics (no degraded tag-stripped dump).

Scoped proof: `uv run --frozen pytest -q tests/test_enrichment.py
tests/test_nj_extraction_repair.py` → 37 passed; grep confirms zero
references to the deleted converter/scanner symbols.

**Follow-on decision (test evidence):** trafilatura's `deduplicate=True` was
turned off for all URLs, not just the family. Its process-global LRU segment
cache makes a re-extraction of the same article return `None` ("discarding
data" — e.g. `tests/test_sitemap_discovery.py::test_rerun_upsert_is_noop`);
the regex fallback used to mask that, so with fallback removed the flag would
silently store empty bodies on every rerun/second-in-process extraction.
`favor_precision=True` already drops repeated boilerplate, and with
`deduplicate=False` `tests/test_sitemap_discovery.py` is green again
(86 passed across sitemap + enrichment + NJ repair).
