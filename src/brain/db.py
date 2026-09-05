"""Postgres connectivity + migration runner (Ticket 01 foundation seam).

Other lanes depend on two callables:
- get_connection() -> psycopg connection to DATABASE_URL
- apply_migrations(conn=None) -> executes migrations/*.sql in order (idempotent)
"""

from __future__ import annotations

from pathlib import Path

import psycopg
from psycopg import Connection

from brain.config import get_database_url

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


def get_connection() -> Connection:
    """Open a new psycopg connection to the configured database."""
    return psycopg.connect(get_database_url())


def _migration_files() -> list[Path]:
    """All migration files in apply order (lexicographic: 001, 002, ...)."""
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


def apply_migrations(conn: Connection | None = None) -> None:
    """Execute migrations/*.sql in order. Idempotent — safe to rerun.

    Opens (and closes) its own connection when none is supplied; when a
    caller passes a connection, the caller owns commit/close semantics,
    but we commit our DDL so file execution is durable either way.
    """
    files = _migration_files()
    assert files, f"no migration files found in {MIGRATIONS_DIR}"
    if conn is None:
        with psycopg.connect(get_database_url(), autocommit=True) as own:
            for path in files:
                own.execute(path.read_text(encoding="utf-8"))
    else:
        for path in files:
            conn.execute(path.read_text(encoding="utf-8"))
        conn.commit()
