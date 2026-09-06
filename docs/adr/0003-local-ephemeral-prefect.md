# ADR-0003 — Local ephemeral Prefect, named runs, single leaf+batch flow shape

**Status:** accepted
**Date:** 2026-09-05

## Context

Local `make ingest` runs were silently captured by the global Prefect profile (`~/.prefect` active `dokploy-instance` → `https://prefect.services.aloiscrr.dev/api`), spamming the VPS server with random-named runs; unnamed tasks/flows made those runs untraceable, and a redundant `ingest_all_sources_flow` forwarder added a second batch entrypoint for no reason.

## Decision

Local runs stay ephemeral via `PREFECT_API_URL="" PREFECT_SERVER_ALLOW_EPHEMERAL_MODE=true` in the Makefile `ingest` target (Prefect 3.8 defaults the ephemeral subprocess server to off, so blanking the URL alone raises `ValueError`; both vars are required — verified empirically) (VPS only on explicit deploy, wired in a later deploy ticket); runs are named per source (`ingest-{source_name}` direct, `ingest-{name}-{batch_ts}` for batch children via `with_options`, `ingest-batch` for the batch flow itself) with task names `fetch-{url}` / `parse-{source}` / `upsert-{source}-{count}-docs` (static `task_run_name`/`flow_run_name` templates plus `with_options` overrides where templates can't reach counts or timestamps), one `get_run_logger().info` line per stage (fetch url→bytes, parse source→docs/entries/skipped, upsert docs→inserted/skipped, flow done→result), and a single leaf (`ingest_source_flow`) + batch (`ingest_sources_flow`, `None` = V1 scope) shape after deleting the `ingest_all_sources_flow` forwarder.

## Consequences

- Devs must not `prefect profile use dokploy-instance` locally; local runs never contact the VPS and need no server.
- The deploy ticket must add explicit remote config (profile/server + deploy entrypoints) rather than relying on ambient global state.
- `record_ingestion_run` stays best-effort and return shapes stay `{inserted, skipped[, parse_skipped][, error]}`; per-source isolation (one failure never blocks the rest) is unchanged.

## Note — declarative fetch retries + propagate-inside isolation (2026-09-06)

- `fetch_task` carries declarative retries (`retries=3`, `retry_delay_seconds=[2, 5, 15]`): transient blips self-heal; genuine failures exhaust retries and land the task Failed (red). No `try/except` inside leaf tasks — exceptions propagate.
- `ingest_source_flow` keeps per-source isolation but propagates stage failures (no catch around task calls), so a bad source shows its own red subflow/task. Only pre-task registry validation (unknown source) still returns an `{inserted: 0, skipped: 0, error}` dict.
- `ingest_sources_flow` invokes each subflow with `return_state=True`: Completed states yield their result dicts; Failed/Crashed states are recorded via `record_ingestion_run` (best-effort) and converted to `{inserted: 0, skipped: 0, error}`, then iteration continues — the batch return shape is unchanged.
- `on_failure` hooks / Automations remain a future note only; no extra infra. `ingest.py:fetch_rss` is untouched (plain HTTP; retries live at the task layer).
