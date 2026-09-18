# 31: Retrieval exposure — `image_texts[]` + `has_image_text`

**What to build:** Callers read per-frame image text through the Service Adapter on both surfaces (HTTP + MCP by construction): full ordered frames on single-article reads, airgapped boolean on list payloads, frozen key sets grown additively.

**Blocked by:** 30 (Image-text lane + side-table storage — rows must exist before exposure).

**Status:** implemented (commit 614edf3; gates green: ruff check/format, mypy, 758 passed 1 skipped)

- [ ] `get_article` gains `image_texts[]`: ordered by `frame_index`, each item `{frame_index, image_text, model, extracted_at}`; `[]` when no rows (never null); caption `content` byte-identical to today
- [ ] SEARCH/PERIOD items gain `has_image_text: bool` only — no per-frame text in list payloads; composes with existing annotation filters (`min_importance`, `topics`, `per_source_limit`) without changing ordering semantics
- [ ] Service-adapter implementation in `service.py` (+ `article.py`/`search.py`/`period.py` as needed): single read path both adapters call; `InvalidRequest` → 422 contract unchanged
- [ ] MCP + HTTP thin adapters expose the new keys with identical shapes (parity-by-construction tests, fake-conn, no live DB)
- [ ] Regression tests: round-trip write → `get_article` returns ordered frames; unannotated documents return `[]` / `false`; SEARCH/PERIOD key sets keep every existing key plus exactly one new one
- [ ] CONTEXT.md glossary + V1-cuts updated (Document gains image-text field; deterministic-only cut notes the ADR-0014 vision exception); typecheck + full suite green
