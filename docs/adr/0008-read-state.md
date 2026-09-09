# ADR-0008 — Read State on documents

**Status:** accepted
**Date:** 2026-09-09

## Context

Consumers need to track which Documents they have already consumed without losing evidence; a separate per-reader table would over-model the single-consumer V1 case.

## Decision

Read State is global nullable columns on `documents` (`read_at`/`read_by`, NULL = unread), set only by an explicit mark — `get_article` never auto-marks — and surfaced by annotating `search` / `period-context` / `get_article` by default with an opt-in `exclude_read` filter, never a silent filter. Re-ingest preserves marks via the flag-pattern (`ON CONFLICT DO NOTHING` skips overwriting existing rows).

## Considered Options

- Per-reader table: rejected, over-models the single-consumer V1 case.
- Auto-mark on `get_article`: rejected, would break readOnly/idempotent expectations.
- Silent filtering of read items: rejected, would hide evidence; opt-in `exclude_read` instead.
