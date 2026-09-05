# 01 — Foundation + first source end-to-end

**What to build:** A local brain that ingests one RSS source and makes it searchable: registry → retrieval → normalized storage → keyword search API, rerun-safe.

**Blocked by:** None — can start immediately.

**Status:** ready-for-human — implemented, needs human verify + accept

- [x] Compose starts Postgres 18 + pgvector on a named volume at the PG18 mount path; data survives restart with no anonymous volumes
- [x] Social Media Today ingests into the normalized document contract (source, URL/canonical, title, nullable author, tz-aware published/retrieved timestamps, language, content, hash)
- [x] Rerunning ingest creates no duplicate documents
- [x] Keyword search returns ingested articles with source provenance

## Verification (2026-09-05, agent)
- Image `pgvector/pgvector:0.8.6-pg18-trixie`, `pgdata:/var/lib/postgresql`, `brain-db` healthy; `compose down/up` preserves rows (3 docs before/after); only named volume `marketing-intelligence_pgdata` attached.
- Fixture ingest: run1 3/0, rerun 0/3; contract fields incl. nullable author + tz-aware timestamps verified.
- `search_articles('TikTok')` returns SMT hit with full provenance keys.
- `mypy src`: clean (8 files). `pytest`: 24 passed, 1 skipped (live-DB guard).
- @oracle review: 0 acceptance failures; 2 fixes applied (search LEFT JOIN for orphan docs, flow passes source_name to parse_task).
