# 06: Remove robots Disallow filter

**What to build:** Discovery planning no longer excludes article URLs on robots-Disallow grounds — previously filtered URLs are planned and fetched like any other, while crawl-delay and per-host pacing keep the polite fetch rhythm.

**Blocked by:** None (can start immediately).

**Status:** ready-for-agent

- [ ] A harvest plan over a source with Disallowed article paths includes those URLs (demonstrated before/after on one affected source)
- [ ] Crawl-delay, per-host pacing with jitter, and newest-first budgeting behave exactly as before
- [ ] Full suite green (`pytest -n auto`, `ruff check`, `ruff format --check`, `mypy src`)
