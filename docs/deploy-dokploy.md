# Deploy — Dokploy (Docker scaffold)

Single image (`Dockerfile` at repo root), three compose services sharing it.
Containers serve **plain HTTP only** — Traefik terminates TLS outside them.
No certs, no TLS config in the image or compose file.

## Services & ports

| Compose service | Process (container CMD) | Internal port | Purpose |
|---|---|---|---|
| `migrate` | `python -c "from brain.db import apply_migrations; apply_migrations()"` | — (one-shot, exits 0) | Idempotent `migrations/*.sql` before serve |
| `api` | `uvicorn api.app:app --host 0.0.0.0 --port 8123` (image default CMD) | 8123 | FastAPI: `GET /search`, `POST /period-context`, `GET /article`, `POST /flag-extraction`, `/docs` |
| `mcp` | `uvicorn --app-dir src/mcp server:http_app --host 0.0.0.0 --port 8124` | 8124 | MCP streamable-HTTP app (`/mcp`) |

Why `--app-dir src/mcp server:http_app` and not `mcp.server:http_app`: the
local `src/mcp/` dir intentionally has no `__init__.py`, so plain
`import mcp` resolves to the installed `mcp` distribution. `--app-dir`
imports our `server.py` as top-level `server`. (`python src/mcp/server.py
--http` is local-only — FastMCP defaults bind `127.0.0.1:8000`.)

Startup order is wired in `compose.yml`: `api`/`mcp` wait for
`migrate: service_completed_successfully` and `db: service_healthy`.
`migrate` is also runnable standalone: `make migrate-compose`
(`docker compose run --rm migrate`).

## Dokploy mapping

- **App type**: Compose (point Dokploy at this repo; compose file
  `compose.yml`, build context repo root). Alternatively use the Dockerfile
  provider twice (two services, same repo/image, one CMD each from the table).
- **Image**: built by Dokploy from `Dockerfile` (multi-stage,
  `python:3.12-slim`, frozen `uv.lock` install, non-root `appuser`,
  `HEALTHCHECK` via `$PORT`). Local equivalent: `make image`
  (`docker build -t brain-app:local .`).
- **Domains (Traefik, TLS automatic, outside containers)**:
  - `https://api.<domain>` → service `api`, container port **8123**
  - `https://mcp.<domain>` → service `mcp`, container port **8124**
- **Env vars to set in the Dokploy UI** (never commit values):
  - `DATABASE_URL` — e.g. `postgresql://brain:brain@db:5432/brain` for the
    compose `db` service, or your Dokploy-managed Postgres internal hostname.
    (Compose default already points at `db:5432`; set explicitly in Dokploy.)
  - `BRAIN_API_TOKEN` — generate with `openssl rand -hex 32`. Required in
    prod; when unset the API/MCP are open (local-dev mode only).
  - `BRAIN_PUBLIC_URL` — `https://mcp.<domain>` (public MCP base URL; used
    for OAuth resource metadata only — static bearer has no OAuth issuer).
  - Optional: `POSTGRES_USER`/`POSTGRES_PASSWORD`/`POSTGRES_DB` (must match
    between `db` and `DATABASE_URL`), `API_PORT`/`MCP_PORT` (host publish
    ports, local compose only — Traefik uses container ports).
- **Pre-deploy / migrate hook**: Dokploy deploys compose in dependency order,
  so the `migrate` one-shot runs before `api`/`mcp` start. If your Dokploy
  setup skips one-shot services, run as a pre-deploy command instead:
  `docker compose run --rm migrate` (or against the built image:
  `python -c "from brain.db import apply_migrations; apply_migrations()"`
  with `DATABASE_URL` set). No yoyo — plain `apply_migrations` hook only.
- **Postgres**: keep the compose `db` service (data on named volume `pgdata`)
  or swap `DATABASE_URL` to a Dokploy-managed Postgres; nothing else changes.

## Local check

```sh
make image          # docker build -t brain-app:local .
make up-db          # DB only (unchanged local flow)
make up             # db + migrate + api + mcp
curl localhost:8123/docs            # 200 (open mode without BRAIN_API_TOKEN)
docker compose run --rm migrate     # standalone migration re-run
```

## Prefect on Dokploy — reaching this project's database

Your Prefect deployment (separate docker-compose app on Dokploy) runs
ingestion flows whose tasks open this project's Postgres. The only problem to
solve is **network + hostname**: the worker container must share a Docker
network with the `db` service and use the compose service name as host.

Core rules:

- **Shared Docker network.** Containers resolve each other by service name
  only when they share a network. Either (a) attach the Prefect worker to
  this app's compose network, or (b) put both apps on one pre-created
  external shared network (e.g. `brain-net`) and reference it as
  `external: true` in both compose files.
- **`DATABASE_URL` must use the db hostname, never `localhost`.**
  `localhost` inside a container is the container itself, so
  `postgresql://brain:brain@localhost:5433/brain` (host-side local dev) fails
  from the worker. In-cluster use the compose service name:
  `postgresql://brain:brain@db:5432/brain` (internal port **5432**, not the
  published 5433). On Dokploy the host part is whatever the Postgres service
  is called there (`db`, `brain-db`, or the Dokploy-managed Postgres internal
  hostname) — same credentials/db as `POSTGRES_USER`/`POSTGRES_PASSWORD`/
  `POSTGRES_DB`.
- **Name resolution via compose service name.** On the shared network `db`
  resolves to the Postgres container automatically (Docker embedded DNS). No
  extra config, no static IPs.
- **TLS not needed in-cluster.** Postgres traffic stays inside the Docker
  network (plain `postgresql://`); Traefik/TLS only fronts the HTTP services.
- **Restart order: `db` → `migrate` → prefect worker.** The DB must be
  healthy, migrations applied (`migrate` one-shot exits 0), then the worker
  starts so first tasks never hit an unmigrated schema. Wire with
  `depends_on` (`db: service_healthy`, `migrate: service_completed_successfully`).

Ready-to-paste snippet — same-app profile (local / single compose project):

```yaml
# compose.yml — optional, off by default (profiles: ["prefect"]).
# docker compose --profile prefect up -d prefect-worker
prefect-worker:
  build:
    context: .
    dockerfile: Dockerfile
  image: brain-app:local
  profiles: ["prefect"]
  command: ["prefect", "worker", "start", "--pool", "${PREFECT_WORK_POOL:-default}"]
  environment:
    DATABASE_URL: ${DATABASE_URL:-postgresql://brain:brain@db:5432/brain}
    PREFECT_API_URL: ${PREFECT_API_URL:-}
  depends_on:
    migrate:
      condition: service_completed_successfully
    db:
      condition: service_healthy
  restart: unless-stopped
```

Separate Prefect app on the same Dokploy host — join this project's network:

```yaml
# In the Prefect app's compose file: join the already-running app network.
# Find its exact name with `docker network ls` (typically <project>_default).
services:
  prefect-worker:
    image: prefecthq/prefect:3-python3.12
    command: ["prefect", "worker", "start", "--pool", "${PREFECT_WORK_POOL:-default}"]
    environment:
      DATABASE_URL: postgresql://brain:brain@db:5432/brain
      PREFECT_API_URL: ${PREFECT_API_URL:-}
    networks:
      - brain-app-network
    restart: unless-stopped

networks:
  brain-app-network:
    external: true
    name: <brain-compose-project>_default   # e.g. marketing-intelligence_default
```

Or a pre-created shared network on both sides (`docker network create brain-net` once on the host):

```yaml
networks:
  brain-net:
    external: true
```

Dokploy UI steps:

1. **Networks**: if two Dokploy apps must talk, use the external-network
   variant above in both compose files (same `brain-net`), redeploy the
   brain app first so the network attachment exists, then the Prefect app.
   (Single-app case needs nothing — default compose network already shared.)
2. **Env vars** (Prefect app): set `DATABASE_URL` to the in-cluster value
   (`postgresql://brain:brain@db:5432/brain`, or the Dokploy Postgres
   internal hostname), plus `PREFECT_API_URL` pointing at your Prefect
   server/API and `PREFECT_WORK_POOL` if not `default`. Never `localhost`.
3. **Restart order**: start/restart `db`, wait healthy (`pg_isready`),
   run `migrate` (`docker compose run --rm migrate`, must exit 0), then
   (re)start the prefect worker. On redeploy, Dokploy follows `depends_on`;
   across two apps do it manually in that order.
4. **Verify from inside the worker network**:
   `docker compose exec prefect-worker python -c "from brain.db import get_engine; get_engine().connect().close(); print('db ok')"`
   (or `pg_isready -h db -p 5432`). If it says "connection refused on
   localhost", the worker's `DATABASE_URL` still points at `localhost`.
