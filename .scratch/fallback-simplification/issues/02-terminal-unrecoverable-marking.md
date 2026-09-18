# 02: Terminal unrecoverable marking

**What to build:** When the full article-content chain fails for an RSS-lane document, the document carries the `unrecoverable` Extraction Flag with a system reporter instead of an unflagged thin body — so agents and ranking treat it as known-bad, and the MCP flag tool accepts and clears the new reason.

**Blocked by:** None (can start immediately).

**Status:** ready-for-agent

- [ ] A document whose primary fetch and every fallback leg fail is persisted with the `unrecoverable` flag and its chained cause, not as a silent thin body
- [ ] Flagged documents stay under the existing importance cap; agent-set flags behave exactly as before
- [ ] The MCP wrong-content tool accepts and clears the new reason; unknown reasons still reject
- [ ] Discovery-lane behavior unchanged: failed articles are still dropped with run-level causes, never stubbed
- [ ] Full suite green (`pytest -n auto`, `ruff check`, `ruff format --check`, `mypy src`)
