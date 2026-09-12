# ADR-0005 — Extraction Flag agent feedback tool

**Status:** accepted
**Date:** 2026-09-06

## Context

Consumer agents reading via `get_article` / `search_articles` hit improperly extracted bodies (thin RSS, JS-shell, challenge pages, truncated/wrong bodies). Detection lives only in ephemeral flow results (`parse_skipped`, `enrich_skipped`); `documents` has no quality column, so agents cannot report what they see.

## Decision

- New noun **Extraction Flag** (verb `flag_extraction(identifier, reason, detail, flagged_by, clear)`), full parity: `marketing_intelligence.service` + `POST /flag-extraction` + MCP tool (tools 3→4).
- Storage: nullable columns on `documents` (`flag_reason, flag_detail, flagged_at, flagged_by`), overwrite on re-flag — no history table V1.
- Read-back: annotate `search` + `weekly` + `get_article` with flag fields, filter nowhere V1.
- Lifecycle: flag survives re-ingest (`ON CONFLICT DO NOTHING`); only `clear=true` nulls columns. Returns updated Article dict. Unknown URL / bad reason / overlong detail (>2000) → `InvalidRequest` → 422.

## Consequences

- `tests/test_mcp_parity.py` pins 4 tools; contract tests pin annotate-not-filter and all 422 paths.
- No auto re-fetch, no history, no health/dashboard surfacing in V1 — each is a separate branch if needed.

## Amendment — 2026-09-12: system-filed `unrecoverable`

The retrieval pipeline now files one reason itself: when a document's whole
article-content chain fails and only the thin RSS body survives, the
`unrecoverable` reason is written with `SYSTEM_REPORTER` (`"system"`) as the
reporter and the chained, credential-scrubbed failure cause as detail. This
extends the lane from agent-only to two reporters sharing one contract:

- `unrecoverable` is a normal member of `FLAG_REASONS`: the agent tool accepts
  it (and `clear`s it), and unknown reasons still reject as before.
- Lifecycle and storage are unchanged: the same four `documents` columns,
  overwrite on re-flag, `clear=true` nulls, no history table.
- The 0.3 importance cap applies to any non-NULL `flag_reason`, so
  system-filed flags cap exactly like agent-filed ones.

The pipeline context — why the chain fails terminally, and the post-upsert
ordering that makes the flag write possible — lives in ADR-0012.
