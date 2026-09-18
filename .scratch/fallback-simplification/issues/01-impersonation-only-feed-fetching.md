# 01: Impersonation-only feed fetching

**What to build:** Feed retrieval runs the impersonated fetch only. A dead or emptied feed is still recovered through the candidate-URL retry, and failures stay explicit — but no feed URL is ever sent to a Markdown reader or scraper, whose output the entry parser could never use.

**Blocked by:** None (can start immediately).

**Status:** ready-for-agent

- [ ] Ingesting an RSS source performs no reader/scraper calls for feed URLs under any failure
- [ ] A dead primary feed still recovers via the next candidate URL; an exhausted list still raises the explicit empty-feed error
- [ ] Leg tests that assumed reader legs return feed entries are rewritten to the new contract
- [ ] Full suite green (`pytest -n auto`, `ruff check`, `ruff format --check`, `mypy src`)
