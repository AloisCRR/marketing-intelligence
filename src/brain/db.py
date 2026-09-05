"""Postgres connectivity + migration runner (Ticket 01 foundation seam).

Other lanes depend on two callables:
- get_connection() -> psycopg connection to DATABASE_URL
- apply_migrations(conn=None) -> executes migrations/001_init.sql (idempotent)
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import psycopg
from psycopg import Connection

from brain.config import get_database_url

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"
INIT_MIGRATION = MIGRATIONS_DIR / "001_init.sql"


def get_connection() -> Connection:
    """Open a new psycopg connection to the configured database."""
    return psycopg.connect(get_database_url())


def apply_migrations(conn: Optional[Connection] = None) -> None:
    """Execute migrations/001_init.sql. Idempotent — safe to rerun.

    Opens (and closes) its own connection when none is supplied; when a
    caller passes a connection, the caller owns commit/close semantics,
    but we commit our DDL so file execution is durable either way.
    """
    sql = INIT_MIGRATION.read_text(encoding="utf-8")
    if conn is None:
        with psycopg.connect(get_database_url(), autocommit=True) as own:
            own.execute(sql)
    else:
        conn.execute(sql)
        conn.commit()
