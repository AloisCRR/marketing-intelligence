"""Foundation tests (Ticket 01).

Runnable WITHOUT docker or a live DB: compose assertions parse
compose.yml as text, migration assertions parse migrations/001_init.sql
as text, and config/db assertions are import checks.

Live-DB integration is guarded by env: set BRAIN_RUN_DB_TESTS=1 with a
reachable Postgres (e.g. `docker compose up -d db`) to exercise
apply_migrations() + get_connection() for real.
"""

from __future__ import annotations

import inspect
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "compose.yml"
COMPOSE_PROD = ROOT / "compose.prod.yml"
MIGRATION = ROOT / "migrations" / "001_init.sql"


def _read(path: Path) -> str:
    assert path.exists(), f"expected file missing: {path}"
    return path.read_text(encoding="utf-8")


# --- compose.yml / compose.prod.yml ------------------------------------------


def test_compose_dev_and_prod_share_db_contract():
    # Both files keep the same Postgres service contract: service `db`,
    # named volume pgdata at the PG18 path, no anonymous volumes.
    for path in (COMPOSE, COMPOSE_PROD):
        text = _read(path)
        assert "pgdata:/var/lib/postgresql" in text.replace(" ", ""), (
            f"{path.name} must mount named volume pgdata at /var/lib/postgresql"
        )
        code_lines = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
        code = "\n".join(code_lines)
        assert "/var/lib/postgresql/data" not in code, (
            f"{path.name}: PG18 data path is /var/lib/postgresql, not /var/lib/postgresql/data"
        )
        assert re.search(r"(?m)^volumes:\s*$", text), f"{path.name} needs top-level volumes:"
        assert re.search(r"(?m)^\s{2}pgdata:\s*(#.*)?$", text), (
            f"{path.name} needs a top-level named volume 'pgdata:'"
        )
        assert re.search(r"(?m)^\s{2}db:\s*$", text), f"{path.name} needs a service named 'db'"
        anonymous = [ln for ln in text.splitlines() if re.match(r"\s*-\s*[\"']?/", ln)]
        assert not anonymous, f"{path.name}: anonymous short-syntax volumes found: {anonymous}"


def test_compose_dev_publishes_loopback_only():
    text = _read(COMPOSE)
    assert '"127.0.0.1:${POSTGRES_PORT:-5433}:5432"' in text
    assert '"127.0.0.1:${API_PORT:-8123}:8123"' in text
    assert '"127.0.0.1:${MCP_PORT:-8124}:8124"' in text


def test_compose_prod_publishes_no_ports():
    # Prod sits behind Traefik on dokploy-network: no host publishes at all.
    text = _read(COMPOSE_PROD)
    code_lines = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
    assert not re.search(r"(?m)^\s{4}ports:\s*$", "\n".join(code_lines)), (
        "compose.prod.yml must not publish any ports"
    )


# --- migrations/001_init.sql -----------------------------------------------


def test_migration_creates_sources_and_documents():
    sql = _read(MIGRATION).lower()
    assert "create extension if not exists" in sql and "vector" in sql
    assert "create table if not exists" in sql
    assert re.search(r"create table if not exists\s+sources", sql)
    assert re.search(r"create table if not exists\s+documents", sql)


def test_migration_sources_columns():
    sql = _read(MIGRATION).lower()
    for col in ("name", "rss_url", "hub_url", "language", "enabled"):
        assert col in sql, f"sources table missing column: {col}"
    assert "gen_random_uuid()" in sql


def test_migration_documents_columns():
    sql = _read(MIGRATION).lower()
    for col in (
        "source_id",
        "url",
        "canonical_url",
        "title",
        "author",
        "published_at",
        "retrieved_at",
        "language",
        "content",
        "content_hash",
        "story_id",
        "created_at",
    ):
        assert col in sql, f"documents table missing column: {col}"
    assert "references sources" in sql, "documents.source_id must REFERENCES sources(id)"
    assert "unique" in sql


def test_migration_indexes_and_seed():
    sql = _read(MIGRATION).lower()
    for col in ("content_hash", "published_at"):
        assert re.search(rf"create index if not exists.*{col}", sql), f"missing index on {col}"
    # url has a UNIQUE constraint; a dedicated index or the constraint both satisfy "indexes on url".
    assert re.search(r"(create( unique)? index if not exists.*\burl\b|url text unique)", sql), (
        "missing index/unique on url"
    )
    raw = _read(MIGRATION)
    assert "Social Media Today" in raw
    assert "https://www.socialmediatoday.com/feeds/news/" in raw


# --- config / db import surface --------------------------------------------


def test_config_and_db_importable():
    from marketing_intelligence.config import DATABASE_URL  # noqa: F401
    from marketing_intelligence.db import apply_migrations, get_connection  # noqa: F401

    assert isinstance(DATABASE_URL, str) and DATABASE_URL.startswith("postgresql://")
    assert callable(get_connection)
    assert callable(apply_migrations)
    assert "conn" in inspect.signature(apply_migrations).parameters


def test_config_default_database_url():
    os.environ.pop("DATABASE_URL", None)
    import importlib

    import marketing_intelligence.config as config

    importlib.reload(config)
    assert config.DATABASE_URL == "postgresql://brain:brain@localhost:5433/brain"


# --- live-DB integration (opt-in) -------------------------------------------


def test_apply_migrations_live_db():
    """Real Postgres round-trip. Skipped unless BRAIN_RUN_DB_TESTS=1."""
    if os.environ.get("BRAIN_RUN_DB_TESTS") != "1":
        import pytest

        pytest.skip("live DB test guarded by BRAIN_RUN_DB_TESTS=1")
    from marketing_intelligence.db import apply_migrations, get_connection

    apply_migrations()  # idempotent: run twice to prove rerun-safety
    apply_migrations()
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT name FROM sources WHERE name = 'Social Media Today'")
        assert cur.fetchone() is not None
