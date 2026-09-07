# Prefect local server (dev)

`make ingest` requires a local Prefect server — ephemeral mode is not used.

```sh
make prefect-up  # or: prefect server start
prefect config set PREFECT_API_URL="http://127.0.0.1:4200/api"
make ingest              # all sources; SOURCE="MarTech" for one source
```

- UI: http://127.0.0.1:4200, API: http://127.0.0.1:4200/api
- Liveness: `curl -s http://127.0.0.1:4200/api/health` → `true`
- The `ingest` target guards on `/api/health` via `scripts/check-prefect.sh`
  and fails with the fix above when the server is down.
- Env vars override the Prefect profile (`prefect profile inspect`); the
  opencode Prefect MCP stanza pins `PREFECT_API_URL` to the local API.
- Prefect MCP tools are read-only; writes go via CLI/SDK.
