#!/usr/bin/env bash
# Guard for `make ingest`: require a local Prefect server.
# Fails fast with a clear fix when http://127.0.0.1:4200 is not serving.
set -euo pipefail

HEALTH_URL="http://127.0.0.1:4200/api/health"

if curl -sf --max-time 5 "$HEALTH_URL" > /dev/null; then
  exit 0
fi

cat >&2 <<'EOF'
Prefect server not reachable at http://127.0.0.1:4200 (GET /api/health failed).

`make ingest` requires a local Prefect server. Start and point your profile at it:

  prefect server start
  prefect config set PREFECT_API_URL="http://127.0.0.1:4200/api"

Then re-run `make ingest`. (Ephemeral mode is intentionally not used.)
EOF
exit 1
