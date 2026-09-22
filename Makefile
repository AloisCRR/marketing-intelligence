.PHONY: dev test lint fmt mcp db-up migrate migrate-baseline ingest prefect-up help image up up-db down logs migrate-compose

help:
	@echo "dev      - FastAPI + /docs on :8123 (reload)"
	@echo "mcp      - MCP server (FastMCP stdio)"
	@echo "test     - pytest (no live DB needed)"
	@echo "lint     - mypy src"
	@echo "db-up    - docker compose up -d db"
	@echo "migrate  - yoyo: apply pending migrations only"
	@echo "migrate-baseline - one-time: mark applied without executing (pre-yoyo DBs)"
	@echo "ingest   - ingest all sources (20) (SOURCE=\"MarTech\" for one source; requires local Prefect server)"
	@echo "annotate - Jev-classify pending docs (SOURCE=\"MarTech\" for one source; requires local Prefect server)"
	@echo "annotate-backfill - same as annotate, all sources (manual backfill entrypoint)"
	@echo "image    - docker build deploy image (IMAGE=marketing-intelligence-app:local)"
	@echo "up       - docker compose up -d --build (db+migrate+api+mcp)"
	@echo "up-db    - docker compose up -d db (local DB only)"
	@echo "down     - docker compose down (keeps pgdata volume)"
	@echo "logs     - docker compose logs -f --tail=100"
	@echo "migrate-compose - run one-shot migrate service in compose net"

dev:
	PYTHONPATH=src uv run --frozen uvicorn api.app:app --port 8123 --reload

mcp:
	uv run --frozen python src/mcp/server.py

test:
	uv run --frozen pytest -n auto

lint:
	uv run --frozen mypy src
	uv run --frozen ruff check src tests
	uv run --frozen ruff format --check src tests

fmt:
	uv run --frozen ruff check --fix src tests
	uv run --frozen ruff format src tests

db-up:
	docker compose up -d db

IMAGE ?= marketing-intelligence-app:local

image:
	docker build -t $(IMAGE) .

up:
	docker compose up -d --build

up-db:
	docker compose up -d db

down:
	docker compose down

logs:
	docker compose logs -f --tail=100

migrate-compose:
	docker compose run --rm migrate

migrate:
	PYTHONPATH=src uv run --frozen python -c "from marketing_intelligence.db import apply_migrations; print(apply_migrations())"

migrate-baseline:
	PYTHONPATH=src uv run --frozen python -c "from marketing_intelligence.db import baseline_migrations; print(baseline_migrations())"

PREFECT_API_URL ?= http://127.0.0.1:4200/api

prefect-up:
	prefect server start

annotate:
	@scripts/check-prefect.sh
	PREFECT_API_URL="$(PREFECT_API_URL)" PYTHONPATH=src uv run --frozen python -c "from marketing_intelligence.flows import annotate_sources_flow; print(annotate_sources_flow(['$(SOURCE)'] if '$(SOURCE)' else None))"
annotate-backfill:
	@scripts/check-prefect.sh
	PREFECT_API_URL="$(PREFECT_API_URL)" PYTHONPATH=src uv run --frozen python -c "from marketing_intelligence.flows import annotate_sources_flow; print(annotate_sources_flow(None))"
