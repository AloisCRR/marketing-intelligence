# syntax=docker/dockerfile:1
# Marketing Intelligence — single deploy image (Dokploy + local compose).
#
# One image, two processes (plain HTTP only — Traefik terminates TLS):
#   API (FastAPI):  uvicorn api.app:app --host 0.0.0.0 --port 8123   (default CMD)
#   MCP (HTTP):     uvicorn --app-dir src/mcp server:http_app --host 0.0.0.0 --port 8124
#
# NOTE: `uvicorn mcp.server:http_app` must NOT be used — the local `src/mcp/`
# dir intentionally has no `__init__.py`, so plain `import mcp` resolves to the
# installed `mcp` distribution. `--app-dir src/mcp` imports our `server.py` as
# the top-level `server` module instead. Likewise `python src/mcp/server.py
# --http` is local-only: FastMCP constructor defaults bind 127.0.0.1:8000
# (explicit defaults beat FASTMCP_* env vars), which is unreachable inside a
# container.
#
# Migrate-on-deploy is NOT baked into the entrypoint: run the `migrate`
# compose service (or Dokploy pre-deploy command) first:
#   python -c "from marketing_intelligence.db import apply_migrations; apply_migrations()"

FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Deps first (better layer cache), then project sources, then install project.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

COPY src ./src
COPY migrations ./migrations
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev


FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    PATH=/app/.venv/bin:$PATH \
    PORT=8123

WORKDIR /app

RUN useradd --create-home --uid 10001 appuser

COPY --from=builder --chown=appuser:appuser /app/.venv /app/.venv
COPY --from=builder --chown=appuser:appuser /app/src /app/src
COPY --from=builder --chown=appuser:appuser /app/migrations /app/migrations
COPY --from=builder --chown=appuser:appuser /app/pyproject.toml /app/pyproject.toml

USER appuser

EXPOSE 8123 8124

# Liveness probe for both processes: unauthenticated GET /health must be 200
# (api + mcp serve it without a bearer token). Python one-liner (slim image
# has no curl). Compose sets PORT=8123 (api) / 8124 (mcp) per service.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import http.client,os;c=http.client.HTTPConnection('localhost',int(os.environ.get('PORT','8123')));c.request('GET','/health');raise SystemExit(0 if c.getresponse().status==200 else 1)"

# Default: FastAPI. Compose overrides CMD for the `mcp` service (see compose.yml).
CMD ["uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "8123"]
