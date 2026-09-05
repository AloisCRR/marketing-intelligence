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
MIGRATION = ROOT / "migrations" / "001_init.sql"


def _read(path: Path) -> str:
    assert path.exists(), f"expected file missing: {path}"
    return path.read_text(encoding="utf-8")


# --- compose.yml -----------------------------------------------------------


def test_compose_uses_named_volume_pgdata_at_pg18_path():
    text = _read(COMPOSE)
    # Named volume pgdata mounted at the PG18 path (NOT the old /data subpath).
    assert "pgdata:/var/lib/postgresql" in text.replace(" ", ""), (
        "compose.yml must mount named volume pgdata at /var/lib/postgresql"
    )
    code_lines = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
    code = "\n".join(code_lines)
    assert "/var/lib/postgresql/data" not in code, (
        "PG18 data path is /var/lib/postgresql, not /var/lib/postgresql/data"
    )
    # Top-level named volume declaration.
    assert re.search(r"(?m)^volumes:\s*$", text), "compose.yml needs top-level volumes:"
    assert re.search(r"(?m)^\s{2}pgdata:\s*(#.*)?$", text), (
        "compose.yml needs a top-level named volume 'pgdata:'"
    )
    # Postgres service must be named `db`.
    assert re.search(r"(?m)^\s{2}db:\s*$", text), "compose.yml needs a service named 'db'"


def test_compose_has_no_anonymous_volumes():
    text = _read(COMPOSE)
    # Short-syntax anonymous volumes look like `- /container/path` or
    # `- /container/path:ro` (leading `/` = host path missing = anonymous).
    anonymous = [line for line in text.splitlines() if re.match(r"\s*-\s*[\"']?/", line)]
    assert not anonymous, f"anonymous short-syntax volumes found: {anonymous}"


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
    from brain.config import DATABASE_URL  # noqa: F401
    from brain.db import apply_migrations, get_connection  # noqa: F401

    assert isinstance(DATABASE_URL, str) and DATABASE_URL.startswith("postgresql://")
    assert callable(get_connection)
    assert callable(apply_migrations)
    assert "conn" in inspect.signature(apply_migrations).parameters


def test_config_default_database_url():
    os.environ.pop("DATABASE_URL", None)
    import importlib

    import brain.config as config

    importlib.reload(config)
    assert config.DATABASE_URL == "postgresql://brain:brain@localhost:5433/brain"


# --- live-DB integration (opt-in) -------------------------------------------


def test_apply_migrations_live_db():
    """Real Postgres round-trip. Skipped unless BRAIN_RUN_DB_TESTS=1."""
    if os.environ.get("BRAIN_RUN_DB_TESTS") != "1":
        import pytest

        pytest.skip("live DB test guarded by BRAIN_RUN_DB_TESTS=1")
    from brain.db import apply_migrations, get_connection

    apply_migrations()  # idempotent: run twice to prove rerun-safety
    apply_migrations()
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT name FROM sources WHERE name = 'Social Media Today'")
        assert cur.fetchone() is not None
