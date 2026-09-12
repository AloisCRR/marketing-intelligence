# CONTEXT.md — Marketing Intelligence (V1 slice)

## Glossary

- **Source**: a curated origin (site/feed) with retrieval config. V1: all 20 curated sources via RSS + sitemap/hub/url-set lanes.
- **Source Inventory**: read-only per-Source listing (`name`, `article_count`, `last_ingest_at`, `cadence`) covering all 20 V1 Sources; empty and never-ingested Sources report explicit `0`/`null` instead of disappearing. `last_ingest_at` is the finish time of the most recent successful Ingestion Run (`error IS NULL`).
- **Document / Article**: one normalized retrieved item (title, content, URLs, timestamps, language, hash). The durable evidence unit.
- **Read State**: whether a Document has been marked as read by a consumer (`read_at`/`read_by`, NULL = unread). Marking reflects consuming the Document, not reading a digest. _Avoid_: seen, viewed, page mark, digest consumer as actor
- **Story / Event**: the underlying development multiple documents may cover. V1: column reserved (`story_id`, nullable) but unused — no clustering yet.
- **Topic / Entity**: thematic and named-entity annotations. V1: deferred (no LLM enrichment).
- **Ingestion Run**: one execution of `ingest_source_flow` for one source, independently rerunnable. Partial failure is explicit.
  _Avoid_: Flow Run (Prefect implementation term for the same execution), batch run
- **Ingestion Stage**: one fetch / parse / upsert step inside an Ingestion Run, implemented as a Prefect task.
  _Avoid_: Task Run (Prefect implementation term for the same step)
- **Period Context**: a prepared evidence bundle for a caller-given date range (`period`, `recent_articles` — a recency-ordered list whose items carry a 1-based `rank` position plus provenance). Callers may cap how many items any one source contributes (`per_source_limit`; freed slots go to other sources), so a single prolific source cannot monopolise a bundle. Truthful by construction: no empty analytics placeholders, no ranking-by-importance claim. V1: no velocity, no emerging-topics (no history yet).
- **Service Adapter**: the single validated interface (`marketing_intelligence.service`, `MAX_LIMIT=100`, `InvalidRequest`) behind both caller surfaces; stdlib-only, no HTTP/MCP imports.
- **Extraction Flag**: an agent-reported marker that a Document's content was improperly extracted, with reason + detail + reporter + timestamp.
  _Avoid_: Page mark, quality flag (factual accuracy is out of scope)
- **Importance**: an agent-written 0–1 score on a Document with optional rationale + reporter, stored append-only (latest write wins, full history retained). Flagged or paywalled Documents are hard-capped at 0.3 server-side so they can never outrank clean evidence; every search result, single read, and period bundle item carries the latest annotation (`importance_score`, `importance_rationale`, `importance_reporter`, `importance_updated_at`; NULL when unannotated).
  _Avoid_: rank, popularity, trend score
- **API / MCP**: two thin, parity-guaranteed surfaces over the service adapter — HTTP (`GET /search`, `POST /period-context`, `GET /article`, `GET /sources` with the Source Inventory, `POST /flag-extraction`, `POST /mark-read`, `POST /importance`, `GET /importance`, 422 on `InvalidRequest`) and MCP (8 tools: `search_articles`, `get_period_context`, `get_article`, `list_sources_inventory`, `flag_extraction`, `mark_article_read`, `set_importance`, `get_importance`, same payloads by construction). Search result dicts are 18-key (7 base + 4 flag + 3 read + 4 importance); period bundle items add `rank`.

## V1 cuts (agreed)

- RSS + sitemap/hub/url-set: all 20 curated sources (6 RSS, 14 discovery lanes).
- Deterministic only: no LLM, no embeddings, no topics/entities.
- API-only: search + `get_period_context(from, to)`; no Monday digest schedule yet.
- Retrieval: plain HTTP + RSS parsing and sitemap/hub/url-set discovery. No Firecrawl (revisit per-source when RSS/HTTP fails, e.g. bot protection/paywall).
- Postgres 18 + pgvector in local compose; Prefect `@flow/@task` run against a local server for ingest (`prefect server start` required; VPS deploy later). B2 deferred.
