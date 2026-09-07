# Research: pgvector vs pgvectorscale vs VectorChord (Sept 2026)

**Question:** Should we move from pgvector to pgvectorscale (or VectorChord) to prevent RAM/cost issues in future?
**Repo context:** Postgres 18 + pgvector 0.8.6 in local compose (`compose.yml` → `pgvector/pgvector:0.8.6-pg18-trixie`),
V1 deterministic only, **no embeddings yet** (`CONTEXT.md`, `docs/adr/0001-local-postgres18-pgvector.md`).
**Method:** PRIMARY sources only — official READMEs, raw LICENSE files, CHANGELOG/releases, official docs.
No Reddit threads, no vendor blogs (except where a repo links its own benchmark), no code changes.

## TL;DR

**Stay on pgvector. Defer with a seam.** At this repo's stage (0 vectors, 4 RSS sources → tens of thousands of
articles/year even with generous growth), the "fortune on RAM" problem does not exist yet, and plain pgvector
already ships the mitigations (halfvec, binary quantization + re-rank, IVFFlat vs HNSW choice, iterative scans).
pgvectorscale and VectorChord are real improvements at **large scale (millions+ of vectors / high-QPS ANN)**,
but both add operational and licensing considerations that are not worth paying with zero vectors in the DB.
Revisit only when a concrete trigger fires (see §Recommendation).

## 1. Head-to-head (primary sources)

| | pgvector | pgvectorscale | VectorChord (vchord) |
|---|---|---|---|
| Maintainer / repo | `pgvector/pgvector` (C) | `timescale/pgvectorscale` (Rust + PGRX, Tiger Data/Timescale) | `tensorchord/VectorChord` (Rust; org recently moved `tensorchord` → `supervc-stack`, redirects expected) |
| Relationship | Base extension (`CREATE EXTENSION vector`) | **Complements** pgvector; requires it (`CREATE EXTENSION vectorscale CASCADE` installs pgvector) | **Depends on** pgvector types/syntax (`CREATE EXTENSION vchord CASCADE`); drop-in compatible |
| Index types | `hnsw`, `ivfflat` (+ exact scan default). Per-distance opclasses (`vector_l2_ops`, `vector_ip_ops`, `vector_cosine_ops`, …) | Adds `diskann` (StreamingDiskANN, inspired by Microsoft DiskANN) with `memory_optimized` (SBQ compression) or `plain` layout; label-filtered DiskANN (`smallint[]` + `&&`) | `vchordrq` (IVF + RaBitQ, the recommended path), experimental `vchordg` (DiskANN+RABitQ, preview: slow build, weaker inserts/deletes); native `rabitq8`/`rabitq4` quantized types (v1.1.0) |
| Compression story | `halfvec` (2 bytes/dim), `bit` + `binary_quantize()` + re-rank, `sparsevec` | Statistical Binary Quantization (SBQ, Timescale research); `num_bits_per_dimension` (2 below 900 dims, else 1) | RaBitQ + autonomous rerank; `rabitq8` (<1% recall loss claimed) / `rabitq4` low-bit types |
| License (primary) | **PostgreSQL License** (permissive) — `raw…/pgvector/master/LICENSE`: "Permission to use, copy, modify, and distribute … without fee" (UC Regents / PGDG text) | **PostgreSQL License** (permissive) — `raw…/pgvectorscale/main/LICENSE`: same grant, "TIGER DATA … THE SOFTWARE PROVIDED HEREUNDER IS ON AN 'AS IS' BASIS" | **Dual AGPLv3 OR Elastic License v2** — README §License: "You may choose either license"; commercial contact `vectorchord-inquiry@tensorchord.ai`. ⚠️ AGPL/Elastic copyleft-style constraints do **not** apply to the other two |
| PG18 support (primary) | Yes — v0.8.1 "Added support for Postgres 18 rc1"; v0.8.2/0.8.3 PG18 fixes; Docker tags `0.8.6-pg18-trixie` (the exact tag pinned in our `compose.yml`) | Yes — release **0.9.0** "Added support for PG18" (dropped PG13); 0.9.1 security hardening of DiskANN operator classes | Yes — `vchord-postgres:pg18-v1.1.1` Docker image, `postgresql-18-vchord_1.1.1` .deb, install docs list `pg18-*` tags; 0.5.3 "Build prebuilt packages for PostgreSQL 18"; 1.1.0 drops PG13 |
| Maturity signal | v0.8.6 (2026-07-29), changelog back to 0.1.0 (2021); iterative-scan, HNSW parallel build, PG18 fixes; preinstalled on many hosted providers | v0.9.1 latest; changelog shows crash fixes as recent as v0.8.0 (#193 concurrent-insert crash, #238 NULL-scan crash, rescoring-disable bug #209); 0.9.1 ships **upgrade-breaking validation** (indexes built on untyped columns must be dropped/recreated, REINDEX won't repair) | v1.1.1 latest; **1.0.0 declared "production-ready"**; headline feature is build speed (100M vectors / 20 min on 16 vCPU) and 1B-vector builds on 128 GB via sampling/dim-reduction; `vchordg` still labelled preview |
| Operational extras | `shared_buffers` tuning, `maintenance_work_mem` for builds, `COPY BINARY` bulk load, replicas/Citus/PgDog for horizontal scale | Rust/PGRX build chain (`cargo pgrx`), no prebuilt `pgvector/pgvector`-style image (TimescaleDB image or source build); query-time knobs `diskann.query_search_list_size` / `query_rescore`; unlogged tables unsupported (`ambuildempty: not yet implemented`) | Requires `shared_preload_libraries = "vchord"` + restart (affects managed-Postgres portability); out-of-the-box defaults, no complex tuning claimed |

Key README/docs links (all fetched 2026-09-05):
- pgvector README (indexing, scaling, binary quantization, PG18 docker tags): <https://github.com/pgvector/pgvector>
- pgvector CHANGELOG (0.8.1 PG18 rc1 … 0.8.6): <https://github.com/pgvector/pgvector/blob/master/CHANGELOG.md>
- pgvector LICENSE (PostgreSQL License): <https://raw.githubusercontent.com/pgvector/pgvector/master/LICENSE>
- pgvectorscale README (StreamingDiskANN, SBQ, install, tuning): <https://github.com/timescale/pgvectorscale>
- pgvectorscale LICENSE (PostgreSQL License, Tiger Data): <https://raw.githubusercontent.com/timescale/pgvectorscale/main/LICENSE>
- pgvectorscale releases (0.9.0 PG18, 0.9.1 hardening, 0.8.0 crash fixes): <https://github.com/timescale/pgvectorscale/releases>
- VectorChord README (features, license, quickstart): <https://github.com/tensorchord/VectorChord>
- VectorChord releases (1.0.0 production-ready, 0.5.3 PG18 packages, 1.1.x rabitq types): <https://github.com/tensorchord/VectorChord/releases>
- VectorChord install docs (pg18 images, `shared_preload_libraries`): <https://docs.vectorchord.ai/vectorchord/getting-started/installation.html>

## 2. The Reddit claim, assessed

Claim paraphrase: *"pgvector in 2026? Use pgvectorscale or VectorChord unless you like spending a fortune on RAM."*

**Verdict: true only past a scale threshold we are nowhere near; otherwise hype.**

- **When it is TRUE.** Plain HNSW over full-precision `vector(N)` keeps the graph in RAM for good latency
  (pgvector's own README: "No [need to fit in memory], but … you'll likely see better performance if they do").
  Back-of-envelope: 768-dim `vector` ≈ 3 KB/row + HNSW graph overhead (m=16 default) → **1M rows ≈ several GB**,
  50–100M rows ≈ hundreds of GB. That is exactly the regime the alternatives target: pgvectorscale's headline
  benchmark is **50M × 768-dim Cohere embeddings** (28× lower p95 vs Pinecone s1 at 99% recall, per its README),
  VectorChord's is **100M–1B vectors** with RaBitQ compression and 100M-index-in-20-min builds. At that scale,
  quantization/compression (SBQ, RaBitQ) and disk-oriented indexes (DiskANN) genuinely cut the RAM bill.
- **When it is HYPE.** Below ~1M vectors, a tuned pgvector fits comfortably on one node and the README's own
  mitigations apply: `halfvec` halves storage, `binary_quantize()` + HNSW + re-rank keeps indexes in memory,
  `ivfflat` uses less memory than HNSW, iterative scans (`hnsw.iterative_scan`, v0.8.0+) fix filtered-query recall.
  The alternatives' gains (28× p95, 6–26× vectors-per-dollar) are all quoted at **tens of millions+ vectors** —
  extrapolating them to a small corpus is unwarranted, and both add moving parts (Rust/PGRX builds,
  `shared_preload_libraries` for vchord, upgrade-breaking validation in vectorscale 0.9.1, preview-status `vchordg`).
- **Scale ladder (rows × 768 dims, rough):** <100K rows → exact or HNSW on pgvector, trivial RAM. 100K–1M →
  pgvector HNSW/halfvec/BQ, single node. **1M–10M → start measuring recall/latency/RAM; this is the honest
  evaluation window** for DiskANN/vchordrq. 10M+ → alternatives (or sharding/replicas) likely pay off.

## 3. Recommendation for THIS repo

**Stay on pgvector 0.8.6 / PG18 (ADR-0001 stands). Do not switch now. Defer with a seam.**

1. **No vectors exist.** V1 cuts explicitly defer embeddings/topics (`CONTEXT.md`). Switching the vector
   extension today buys nothing and invalidates the pinned, verified `compose.yml` image.
2. **Headroom is enormous.** 4 RSS sources at, say, 100 docs/day → ~36K vectors/year → ~110 MB/year at 768-dim
   float32. Even 10× growth stays single-node pgvector territory for years.
3. **License asymmetry matters.** pgvector and pgvectorscale are both permissive (PostgreSQL License), but
   VectorChord's **AGPLv3/ELv2 dual license** needs a conscious legal/ops decision (especially self-hosted VPS
   + any hosted offering) — not something to inherit casually before we have a single embedding.
4. **The seam (cheap insurance, no new dependency):** when embeddings arrive, isolate them behind the existing
   `brain.service` adapter pattern (ADR-0002): store embeddings in a dedicated table/column (e.g.
   `document_embedding(embedding VECTOR(N))`), create indexes via a migration, and keep distance-function choice
   (`<=>` vs `<->` vs `<#>`) in one place. That preserves a future `USING diskann` / `USING vchordrq` swap to a
   single migration + adapter change.
5. **Revisit triggers (any one):** >1M stored embeddings, HNSW index RAM > 50% of DB memory, p95 ANN latency
   miss at target recall, or managed-Postgres (VPS) offering only one of the three extensions. At that point,
   re-run this comparison — and note pgvectorscale is the lower-friction trial (same permissive license,
   `CASCADE` install over pgvector) while VectorChord needs the AGPL/Elastic decision plus
   `shared_preload_libraries` restart.

*Provenance: pgvector README + CHANGELOG + LICENSE; pgvectorscale README + releases + LICENSE; VectorChord
README + releases + install docs — all primary, fetched 2026-09-05. No secondary/blog/Reddit sources used for
claims above except where a repo states its own benchmark.*
