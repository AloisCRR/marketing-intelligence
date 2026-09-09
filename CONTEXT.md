# CONTEXT.md — Trend Intelligence Brain (V1 slice)

## Glossary

- **Source**: a curated origin (site/feed) with retrieval config. V1: all 20 curated sources via RSS + sitemap/hub/url-set lanes.
- **Document / Article**: one normalized retrieved item (title, content, URLs, timestamps, language, hash). The durable evidence unit.
- **Story / Event**: the underlying development multiple documents may cover. V1: column reserved (`story_id`, nullable) but unused — no clustering yet.
- **Topic / Entity**: thematic and named-entity annotations. V1: deferred (no LLM enrichment).
- **Ingestion Run**: one execution of `ingest_source_flow` for one source, independently rerunnable. Partial failure is explicit.
  _Avoid_: Flow Run (Prefect implementation term for the same execution), batch run
- **Ingestion Stage**: one fetch / parse / upsert step inside an Ingestion Run, implemented as a Prefect task.
  _Avoid_: Task Run (Prefect implementation term for the same step)
- **Period Context**: a prepared evidence bundle for a caller-given date range (`period`, `important_articles` with provenance). V1: no velocity, no emerging-topics (no history yet).
- **Service Adapter**: the single validated interface (`brain.service`, `MAX_LIMIT=100`, `InvalidRequest`) behind both caller surfaces; stdlib-only, no HTTP/MCP imports.
- **Extraction Flag**: an agent-reported marker that a Document's content was improperly extracted, with reason + detail + reporter + timestamp.
  _Avoid_: Page mark, quality flag (factual accuracy is out of scope)
- **API / MCP**: two thin, parity-guaranteed surfaces over the service adapter — HTTP (`GET /search` with 11-key dicts, `POST /period-context` with 10-key dicts, `GET /article`, `POST /flag-extraction`, 422 on `InvalidRequest`) and MCP (4 tools: `search_articles`, `get_period_context`, `get_article`, `flag_extraction`, same payloads by construction).

## V1 cuts (agreed)

- RSS + sitemap/hub/url-set: all 20 curated sources (6 RSS, 14 discovery lanes).
- Deterministic only: no LLM, no embeddings, no topics/entities.
- API-only: search + `get_period_context(from, to)`; no Monday digest schedule yet.
- Retrieval: plain HTTP + RSS parsing and sitemap/hub/url-set discovery. No Firecrawl (revisit per-source when RSS/HTTP fails, e.g. bot protection/paywall).
- Postgres 18 + pgvector in local compose; Prefect `@flow/@task` run against a local server for ingest (`prefect server start` required; VPS deploy later). B2 deferred.
