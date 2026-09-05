.PHONY: dev test lint mcp db-up migrate ingest help

help:
	@echo "dev      - FastAPI + /docs on :8123 (reload)"
	@echo "mcp      - MCP server (FastMCP stdio)"
	@echo "test     - pytest (no live DB needed)"
	@echo "lint     - mypy src"
	@echo "db-up    - docker compose up -d db"
	@echo "migrate  - apply migrations/*.sql (idempotent)"
	@echo "ingest   - ingest one source (SOURCE=\"Social Media Today\")"

dev:
	PYTHONPATH=src uv run --frozen uvicorn api.app:app --port 8123 --reload

mcp:
	uv run --frozen python src/mcp/server.py

test:
	uv run --frozen pytest

lint:
	uv run --frozen mypy src

db-up:
	docker compose up -d db

migrate:
	PYTHONPATH=src uv run --frozen python -c "from brain.db import apply_migrations; apply_migrations()"

ingest:
	PREFECT_API_URL="" PREFECT_SERVER_ALLOW_EPHEMERAL_MODE=true PYTHONPATH=src uv run --frozen python -c "from brain.flows import ingest_source_flow; print(ingest_source_flow('$(or $(SOURCE),Social Media Today)'))"
