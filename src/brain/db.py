"""Postgres connectivity + migration runner (Ticket 01 foundation seam, ADR-0006).

Other lanes depend on these callables:
- get_connection() -> psycopg connection to DATABASE_URL
- apply_migrations(conn=None) -> yoyo: apply PENDING migrations only

Yoyo tracks applied migrations in a version table, so reruns apply pending
migrations only (the naive pre-yoyo runner re-executed every file, which
breaks on future non-idempotent statements). Plain `.sql` files need no
markers — yoyo parses them into transactional steps as-is.

- `conn` is retained for backwards compatibility but unused: yoyo manages its
  own versioned connection from DATABASE_URL (psycopg3 via the
  `postgresql+psycopg://` scheme; plain `postgresql://` selects yoyo's
  psycopg2 backend, which is not installed).
- Rollback caveat: plain `.sql` migrations carry no rollback SQL (that needs
  a `<name>.rollback.sql` sibling, which we deliberately do not ship), so
  `rollback_migrations()` unmarks them but leaves DDL in place; the next
  apply re-runs them (safe: all current SQL is idempotent).
- Baseline caveat: DBs created by the naive pre-yoyo runner have the schema
  but no version table. Run one-time `baseline_migrations()` (or
  `make migrate-baseline`) to mark everything applied WITHOUT executing;
  otherwise the first yoyo apply re-runs all files (harmless today, but noisy).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import psycopg
from psycopg import Connection
from yoyo import get_backend, read_migrations

from brain.config import get_database_url

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


def get_connection() -> Connection:
    """Open a new psycopg connection to the configured database."""
    return psycopg.connect(get_database_url())


def _migration_files() -> list[Path]:
    """All migration files in apply order (lexicographic: 001, 002, ...)."""
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


def _yoyo_dsn() -> str:
    """DATABASE_URL adapted for yoyo (psycopg3 backend needs the +psycopg scheme)."""
    dsn = get_database_url()
    if dsn.startswith("postgresql://"):
        return "postgresql+psycopg://" + dsn[len("postgresql://") :]
    if dsn.startswith("postgres://"):
        return "postgresql+psycopg://" + dsn[len("postgres://") :]
    return dsn


def _get_backend() -> Any:
    """Yoyo backend for the configured database (seam: monkeypatchable in tests)."""
    return get_backend(_yoyo_dsn())


def _read_migrations() -> Any:
    """Yoyo migration collection for MIGRATIONS_DIR (seam: monkeypatchable)."""
    files = _migration_files()
    assert files, f"no migration files found in {MIGRATIONS_DIR}"
    return read_migrations(str(MIGRATIONS_DIR))


def pending_migrations() -> list[str]:
    """Ids of migrations not yet applied, in apply order."""
    backend = _get_backend()
    return [str(m.id) for m in backend.to_apply(_read_migrations())]


def apply_migrations(conn: Connection | None = None) -> list[str]:
    """Apply pending migrations only (no-op when none). Return applied ids.

    `conn` is accepted for backwards compatibility and ignored — yoyo tracks
    and applies via its own versioned connection.
    """
    backend = _get_backend()
    pending = backend.to_apply(_read_migrations())
    backend.apply_migrations(pending)
    return [str(m.id) for m in pending]


def baseline_migrations() -> list[str]:
    """One-time baseline for pre-yoyo DBs: mark pending applied WITHOUT executing.

    Return marked ids. After this, apply_migrations() is a no-op until a new
    migration file appears.
    """
    backend = _get_backend()
    pending = backend.to_apply(_read_migrations())
    backend.mark_migrations(pending)
    return [str(m.id) for m in pending]


def rollback_migrations(steps: int = 1) -> list[str]:
    """Roll back the last `steps` applied migrations (newest first). Return ids.

    Plain `.sql` migrations have no rollback SQL, so this unmarks them while
    leaving DDL in place (see module caveat); re-apply restores the marks.
    """
    if steps < 1:
        raise ValueError(f"steps must be >= 1, got {steps}")
    backend = _get_backend()
    todo = list(backend.to_rollback(_read_migrations()))[:steps]
    backend.rollback_migrations(todo)
    return [str(m.id) for m in todo]
