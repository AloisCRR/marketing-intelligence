"""Yoyo migration-runner tests (migrations lane, ADR-0006).

Two tiers, both green under plain `pytest -q`:
- sqlite + tmp migration dirs: pending-only apply, re-apply safety, baseline
  (mark without executing), rollback with/without `.rollback.sql` siblings.
  No live Postgres needed.
- live Postgres (scratch database, skipped if unreachable): the real
  `migrations/*.sql` apply end-to-end, including full rollback + re-apply.
"""

from __future__ import annotations

import os
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(os.path.dirname(_HERE), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import pytest  # noqa: E402
from conftest import maintenance_url  # noqa: E402

import marketing_intelligence.db as db  # noqa: E402

SQL_001 = "CREATE TABLE t1 (id INTEGER PRIMARY KEY, v TEXT);\n"
SQL_002 = "CREATE TABLE t2 (id INTEGER PRIMARY KEY, v TEXT);\n"
ROLLBACK_001 = "DROP TABLE t1;\n"


@pytest.fixture()
def sqlite_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point marketing_intelligence.db at a tmp migrations dir + sqlite backend."""
    mig = tmp_path / "migrations"
    mig.mkdir()
    (mig / "001_a.sql").write_text(SQL_001, encoding="utf-8")
    (mig / "002_b.sql").write_text(SQL_002, encoding="utf-8")
    monkeypatch.setattr(db, "MIGRATIONS_DIR", mig)
    monkeypatch.setattr(db, "_yoyo_dsn", lambda: f"sqlite:///{tmp_path}/t.db")
    return mig


def test_apply_runs_pending_only(sqlite_env: Path) -> None:
    assert db.apply_migrations() == ["001_a", "002_b"]
    assert db.apply_migrations() == []
    assert db.pending_migrations() == []


def test_reapply_safe(sqlite_env: Path) -> None:
    db.apply_migrations()
    db.apply_migrations()  # must not raise; naive runner would re-execute DDL
    assert db.pending_migrations() == []


def test_baseline_marks_without_executing(sqlite_env: Path) -> None:
    from yoyo import get_backend

    assert db.baseline_migrations() == ["001_a", "002_b"]
    assert db.pending_migrations() == []
    assert db.apply_migrations() == []  # nothing to execute after baseline
    backend = get_backend(f"sqlite:///{sqlite_env.parent}/t.db")
    assert "t1" not in backend.list_tables()  # SQL never ran
    assert "t2" not in backend.list_tables()


def test_rollback_with_sibling_undoes_ddl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from yoyo import get_backend

    mig = tmp_path / "migrations"
    mig.mkdir()
    (mig / "001_a.sql").write_text(SQL_001, encoding="utf-8")
    (mig / "001_a.rollback.sql").write_text(ROLLBACK_001, encoding="utf-8")
    monkeypatch.setattr(db, "MIGRATIONS_DIR", mig)
    monkeypatch.setattr(db, "_yoyo_dsn", lambda: f"sqlite:///{tmp_path}/t.db")
    assert db.apply_migrations() == ["001_a"]
    assert db.rollback_migrations(1) == ["001_a"]  # sibling: DDL undone + unmarked
    backend = get_backend(f"sqlite:///{tmp_path}/t.db")
    assert "t1" not in backend.list_tables()
    assert db.pending_migrations() == ["001_a"]
    assert db.apply_migrations() == ["001_a"]  # re-apply restores
    assert "t1" in get_backend(f"sqlite:///{tmp_path}/t.db").list_tables()


def test_rollback_plain_sql_unmarks_but_keeps_ddl(sqlite_env: Path) -> None:
    from yoyo import get_backend

    db.apply_migrations()
    assert db.rollback_migrations(2) == ["002_b", "001_a"]
    backend = get_backend(f"sqlite:///{sqlite_env.parent}/t.db")
    # No .rollback.sql siblings: DDL stays, ids return to pending.
    assert "t1" in backend.list_tables()
    assert "t2" in backend.list_tables()
    assert db.pending_migrations() == ["001_a", "002_b"]


def test_rollback_rejects_nonpositive_steps(sqlite_env: Path) -> None:
    with pytest.raises(ValueError):
        db.rollback_migrations(0)


def test_yoyo_dsn_selects_psycopg3_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://brain:brain@localhost:5433/brain")
    assert db._yoyo_dsn() == "postgresql+psycopg://brain:brain@localhost:5433/brain"
    monkeypatch.setenv("DATABASE_URL", "postgres://brain:brain@localhost:5433/brain")
    assert db._yoyo_dsn() == "postgresql+psycopg://brain:brain@localhost:5433/brain"


# --- live Postgres: real migrations dir on an isolated scratch database ------


@pytest.fixture()
def scratch_db(scratch_db_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """Create/drop an isolated scratch DB (probe-once URL from conftest)."""
    import psycopg

    url = scratch_db_url
    # Unique per test so parallel xdist workers never share a scratch DB.
    name = f"brain_yoyo_scratch_{os.getpid()}_{uuid.uuid4().hex[:8]}"
    admin = psycopg.connect(maintenance_url(url), autocommit=True)
    try:
        with admin.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{name}"')
            cur.execute(f'CREATE DATABASE "{name}"')
    finally:
        admin.close()
    base, _, _ = url.rpartition("/")
    scratch_url = f"{base}/{name}"
    monkeypatch.setenv("DATABASE_URL", scratch_url)
    yield scratch_url
    admin = psycopg.connect(maintenance_url(url), autocommit=True)
    try:
        # Yoyo backends hold their connections open; terminate them so the
        # DROP below does not hit ObjectInUse.
        with admin.cursor() as cur:
            cur.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (name,),
            )
            cur.execute(f'DROP DATABASE IF EXISTS "{name}"')
    finally:
        admin.close()


@pytest.mark.live_db
def test_live_real_migrations_pending_only_and_rerunnable(scratch_db: str) -> None:
    applied = db.apply_migrations()
    assert applied == [
        "001_init",
        "002_canonical_url_unique",
        "003_ingestion_runs",
        "005_extraction_flag",
        "006_seed_all_sources",
        "007_deterministic_sources_and_not_null",
        "008_read_state",
        "009_importance",
    ]
    assert db.apply_migrations() == []
    assert db.pending_migrations() == []
    assert db.baseline_migrations() == []  # nothing left to mark


@pytest.mark.live_db
def test_live_rollback_unmarks_and_reapply_restores(scratch_db: str) -> None:
    import psycopg

    db.apply_migrations()
    assert db.rollback_migrations(99) == [
        "009_importance",
        "008_read_state",
        "007_deterministic_sources_and_not_null",
        "006_seed_all_sources",
        "005_extraction_flag",
        "003_ingestion_runs",
        "002_canonical_url_unique",
        "001_init",
    ]
    # DDL stays (no .rollback.sql siblings shipped), ids return to pending.
    with psycopg.connect(scratch_db) as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.sources')")
        assert cur.fetchone()[0] == "sources"
    assert db.pending_migrations() == [
        "001_init",
        "002_canonical_url_unique",
        "003_ingestion_runs",
        "005_extraction_flag",
        "006_seed_all_sources",
        "007_deterministic_sources_and_not_null",
        "008_read_state",
        "009_importance",
    ]
    assert db.apply_migrations() == [
        "001_init",
        "002_canonical_url_unique",
        "003_ingestion_runs",
        "005_extraction_flag",
        "006_seed_all_sources",
        "007_deterministic_sources_and_not_null",
        "008_read_state",
        "009_importance",
    ]
    assert db.pending_migrations() == []
