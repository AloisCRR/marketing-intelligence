# ADR-0001 — Local Postgres 18 + pgvector for V1

**Status:** accepted
**Date:** 2026-09-05

## Context

V1 needs a local system of record with future vector support. User chose local Docker first (VPS Prefect deploy later) and Postgres 18.

Postgres 18 Docker image moved the data mount from `/var/lib/postgresql/data` to `/var/lib/postgresql` (user-supplied finding). Mounting the old path creates anonymous volumes and risks data loss confusion.

## Decision

- Run Postgres 18 with a pgvector-compatible image in local `compose.yml`.
- Mount a **named volume** at `/var/lib/postgresql` (not the old `/data` subpath).
- Keep Prefect flows runnable locally without a server; wire to the VPS Prefect 3 instance in a later ticket.
- Defer B2 raw-artifact storage; normalized content lives in Postgres for V1.

## Consequences

- Ticket 01 must verify data survives `compose down/up` on the named volume (no anonymous volumes).
- Image tag for PG18+pgvector gets pinned at implementation time (verify tag exists, don't assume).
- Upgrade path to VPS Postgres later stays open; no schema dependency on the mount path.
