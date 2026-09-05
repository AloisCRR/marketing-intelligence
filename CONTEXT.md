# CONTEXT.md — Trend Intelligence Brain (V1 slice)

## Glossary

- **Source**: a curated origin (site/feed) with retrieval config. V1: RSS-only, 4 sources.
- **Document / Article**: one normalized retrieved item (title, content, URLs, timestamps, language, hash). The durable evidence unit.
- **Story / Event**: the underlying development multiple documents may cover. V1: column reserved (`story_id`, nullable) but unused — no clustering yet.
- **Topic / Entity**: thematic and named-entity annotations. V1: deferred (no LLM enrichment).
- **Ingestion Run**: one scheduled/manual execution per source, independently rerunnable. Partial failure is explicit.
- **Weekly Context**: a prepared evidence bundle for a caller-given date range (`period`, `important_articles` with provenance). V1: no velocity, no emerging-topics (no history yet).

## V1 cuts (agreed)

- RSS-only: Social Media Today, MarTech, Professional Jeweller, InfoMoney.
- Deterministic only: no LLM, no embeddings, no topics/entities.
- API-only: search + `get_weekly_context(from, to)`; no Monday digest schedule yet.
- Retrieval: plain HTTP + RSS parsing. No Firecrawl (revisit per-source when RSS/HTTP fails, e.g. bot protection/paywall).
- Postgres 18 + pgvector in local compose; Prefect `@flow/@task` runnable locally without a server (VPS deploy later). B2 deferred.
