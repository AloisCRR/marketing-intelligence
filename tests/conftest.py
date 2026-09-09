"""Shared Document-store seam: fakes + one-shot live-Postgres probe.

Candidate 1 seam over ``marketing_intelligence.db.get_connection``: fast fakes for the
deterministic lane, live PG kept as source of truth behind opt-in
(``live_db`` marker + session-scoped probe that skips fast when
Postgres is unreachable). See ADR-0001.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "live_db: requires live Postgres (scratch DB, skipped if unreachable)"
    )


class FakeCursor:
    """DB-API-ish cursor: rows-in/list-out, rowcount, no fetch after writes."""

    def __init__(self, store: list[dict[str, Any]], rowcount: int = 1) -> None:
        self._store = store
        self.rowcount = rowcount
        self._is_write = False

    def execute(self, sql: str, params: Any = None) -> None:
        del params
        if sql.strip().upper().startswith(("UPDATE", "INSERT", "DELETE")):
            self._is_write = True

    def fetchall(self) -> list[Any]:
        if self._is_write:
            raise AssertionError("no results to fetch after UPDATE/INSERT/DELETE")
        return list(self._store)

    def fetchone(self) -> Any | None:
        if self._is_write or not self._store:
            return None
        return self._store[0]

    def close(self) -> None:
        pass


class FakeConn:
    """Minimal fake for the ``get_connection`` seam (execute + cursor styles)."""

    def __init__(self, store: list[dict[str, Any]] | None = None, rowcount: int = 1) -> None:
        self.store: list[dict[str, Any]] = store if store is not None else []
        self._rowcount = rowcount
        self.statements: list[tuple[str, Any]] = []
        self.committed = False
        self.closed = False

    def execute(self, sql: str, params: Any = None) -> FakeCursor:
        self.statements.append((sql, params))
        cur = FakeCursor(self.store, self._rowcount)
        cur.execute(sql, params)
        return cur

    def cursor(self) -> FakeCursor:
        return FakeCursor(self.store, self._rowcount)

    def commit(self) -> None:
        self.committed = True

    def close(self) -> None:
        self.closed = True


def maintenance_url(url: str) -> str:
    """Admin URL for CREATE/DROP DATABASE (same host, ``postgres`` db)."""
    base, _, _ = url.rpartition("/")
    return f"{base}/postgres"


@pytest.fixture(scope="session")
def scratch_db_url() -> Iterator[str]:
    """Probed live URL once per session; fast skip when PG is unreachable."""
    import psycopg

    url = os.environ.get("DATABASE_URL", "postgresql://brain:brain@localhost:5433/brain")
    try:
        conn = psycopg.connect(url, connect_timeout=2)
        conn.close()
    except Exception:
        pytest.skip("no live Postgres reachable")
    yield url


@pytest.fixture()
def fake_conn() -> FakeConn:
    """Fresh write-tracking fake for the ``get_connection`` seam."""
    return FakeConn()
