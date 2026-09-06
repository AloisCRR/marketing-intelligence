# ADR-0006 — Yoyo version-tracked migrations

**Status:** accepted
**Date:** 2026-09-06

## Context

`brain.db.apply_migrations()` re-executed every `migrations/*.sql` file on each
run with no version table. Files 001, 002, 003, 005 are idempotent
(`IF NOT EXISTS`), so this worked — but any future non-idempotent statement
(`ALTER TABLE … DROP`, backfills) would break on rerun. Number **004 is
missing** (no file ever committed under it); files must NOT be renumbered
(pins exist: `tests/test_weekly_context.py` asserts the exact file list, and
renumbering would fork applied-state between existing DBs).

## Decision

- `yoyo-migrations>=9.0.0` backend in `src/brain/db.py` (`read_migrations` /
  `get_backend`, pending-only apply). Plain `.sql` files need no markers —
  yoyo parses them into transactional steps as-is; no migration content
  changes, no `.rollback.sql` siblings shipped (they would break the file-list
  contract test, and DROP-style rollbacks of 001 are destructive by design).
- yoyo 9.0.0 specifics: psycopg3 requires the `postgresql+psycopg://` scheme
  (plain `postgresql://` selects yoyo's uninstalled psycopg2 backend);
  plain-`.sql` rollback unmarks the version row but leaves DDL in place
  (verified); rollback SQL would need `<name>.rollback.sql` siblings.
- One-time baseline for existing dev/prod DBs (schema present, no version
  table): `make migrate-baseline` (`baseline_migrations()` marks applied
  WITHOUT executing). First yoyo apply on an un-baselined DB re-runs all
  files — harmless today (all idempotent) but noisy.
- Surface: `apply_migrations(conn=None) -> list[str]` (signature kept for the
  Ticket 01 seam; `conn` now ignored, yoyo owns its connection),
  `pending_migrations()`, `baseline_migrations()`, `rollback_migrations(steps)`.

## Consequences

- `make migrate` / compose `migrate` one-shot / Dokploy pre-deploy are
  unchanged commands, now pending-only.
- `tests/test_migrations_yoyo.py`: sqlite tmp-dir tier (pending-only,
  re-apply, baseline-without-execute, rollback ±sibling) + live scratch-DB
  tier for the real migrations dir (rollback verified unmark-only on PG).
- Future migrations: plain `.sql`, next free number (006+ — never reuse 004);
  non-idempotent statements are now safe.
