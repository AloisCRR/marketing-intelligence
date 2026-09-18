"""Deterministic source seeds + NULL-source quarantine (DB lane).

TDD for migration 007 (+ the 012/015 re-asserts of the ADR-0013 account rows):
- (a) all 20 curated feed sources seeded idempotently (ON CONFLICT DO NOTHING),
  with `ig:sabrikolod` re-asserted by 012 and `ig:jordisanildefonso` by 015
  (007 is immutable once applied),
- (b) existing NULL source_id rows quarantined explicitly (no silent drop),
- (c) rerun is idempotent.

Fast tier parses SQL/helpers with no live DB. Live tier (scratch database,
skipped if Postgres is unreachable) exercises the real yoyo apply path:
pre-007 NULL document -> 007 quarantines it -> NOT NULL enforced after.
"""

from __future__ import annotations

import json
import os
import re
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(os.path.dirname(_HERE), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import pytest  # noqa: E402
from conftest import FakeConn, maintenance_url  # noqa: E402

import marketing_intelligence.db as db  # noqa: E402
from marketing_intelligence.sources import catalog_names  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
MIGRATION_007 = ROOT / "migrations" / "007_deterministic_sources_and_not_null.sql"
PAYLOADS_MIGRATION = ROOT / "migrations" / "012_document_payloads.sql"
JORDI_MIGRATION = ROOT / "migrations" / "015_instagram_jordi_source.sql"
CURATED = ROOT / ".scratch" / "marketing-intelligence" / "curated-sources.json"

#: ADR-0013 premium account rows: seeded off the feed migrations (007 is
#: immutable and already applied) and re-asserted by migrations 012/015.
IG_SOURCE = "ig:sabrikolod"
JORDI_SOURCE = "ig:jordisanildefonso"

_LANG_CODE = {"English": "en", "Portuguese": "pt", "Spanish": "es"}

_UUID_RE = re.compile(r"'([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})'")


def _curated_names() -> set[str]:
    return {e["source_name"] for e in json.loads(CURATED.read_text(encoding="utf-8"))}


def _read_007() -> str:
    assert MIGRATION_007.exists(), f"expected migration missing: {MIGRATION_007}"
    return MIGRATION_007.read_text(encoding="utf-8")


def _read_payloads() -> str:
    """012 re-asserts the ADR-0013 ``ig:sabrikolod`` row (007 stays immutable)."""
    assert PAYLOADS_MIGRATION.exists(), f"expected migration missing: {PAYLOADS_MIGRATION}"
    return PAYLOADS_MIGRATION.read_text(encoding="utf-8")


def _seed_sql() -> str:
    """Every migration that seeds deterministic source ids (007 + 012 + 015)."""
    return _read_007() + _read_payloads() + JORDI_MIGRATION.read_text(encoding="utf-8")


# --- SQL text contract -------------------------------------------------------


def test_007_seeds_all_20_curated_feed_sources() -> None:
    # 007 keeps the 20 feed sources; the ADR-0013 Instagram accounts are
    # re-asserted by 012/015 (007 is immutable and already applied).
    curated = _curated_names() - {IG_SOURCE, JORDI_SOURCE}
    assert len(curated) == 20
    sql = _read_007()
    for name in curated:
        assert name in sql, f"007 missing curated source: {name}"


def test_012_reasserts_the_instagram_source_row() -> None:
    sql = _read_payloads()
    assert IG_SOURCE in sql
    assert str(db.source_uuid(IG_SOURCE)) in sql


def test_015_reasserts_the_jordi_source_row() -> None:
    sql = JORDI_MIGRATION.read_text(encoding="utf-8")
    assert JORDI_SOURCE in sql
    assert str(db.source_uuid(JORDI_SOURCE)) in sql
    assert "ON CONFLICT (name) DO NOTHING" in sql


def test_007_seed_is_idempotent_on_conflict_do_nothing() -> None:
    sql = _read_007()
    assert "ON CONFLICT (name) DO NOTHING" in sql


def test_seed_ids_are_deterministic_uuid5() -> None:
    """Embedded ids must equal marketing_intelligence.db.source_uuid(name) (stdlib uuid5, no new dep)."""
    sql = _seed_sql()
    found = _UUID_RE.findall(sql)
    expected = len(catalog_names())
    assert len(found) >= expected, f"expected >={expected} UUID literals, found {len(found)}"
    for name in _curated_names():
        assert str(db.source_uuid(name)) in sql, f"seed SQL missing deterministic id for: {name}"


def test_007_quarantines_null_source_rows_explicitly() -> None:
    sql = _read_007()
    assert "CREATE TABLE IF NOT EXISTS quarantined_documents" in sql
    assert "WHERE source_id IS NULL" in sql
    assert "DELETE FROM documents WHERE source_id IS NULL" in sql
    # Evidence is preserved (moved), never silently dropped.
    assert "quarantined_documents" in sql


def test_007_enforces_not_null_only_after_quarantine() -> None:
    sql = _read_007()
    guard = "ALTER TABLE documents ALTER COLUMN source_id SET NOT NULL"
    assert guard in sql
    # Guard ordering: quarantine DELETE must precede the NOT NULL enforcement.
    assert sql.index("DELETE FROM documents WHERE source_id IS NULL") < sql.index(guard)


def test_007_cleans_null_ingestion_runs() -> None:
    sql = _read_007()
    assert "ingestion_runs" in sql
    assert re.search(r"DELETE FROM ingestion_runs WHERE source_name IS NULL", sql)


# --- helper contract (no live DB) --------------------------------------------


def test_source_uuid_is_deterministic_and_unique_per_name() -> None:
    assert db.source_uuid("MarTech") == db.source_uuid("MarTech")
    assert db.source_uuid("MarTech") != db.source_uuid("Retail Dive")
    for name in _curated_names():
        assert str(db.source_uuid(name))  # stable, non-empty


def test_seed_sources_helper_covers_all_22_curated() -> None:
    assert {name for name, _, _, _ in db.SEED_SOURCES} == _curated_names()


def test_seed_sources_helper_is_idempotent_shape() -> None:
    """First run inserts; a rerun (rowcount 0, i.e. ON CONFLICT) only skips."""
    n = len(catalog_names())
    fake = FakeConn(rowcount=1)
    assert db.seed_sources(conn=fake) == (n, 0)  # type: ignore[arg-type]
    assert fake.committed
    for sql, params in fake.statements:
        assert "ON CONFLICT (name) DO NOTHING" in sql
        assert params is not None and str(params[0]) == str(db.source_uuid(params[1]))

    rerun = FakeConn(rowcount=0)
    assert db.seed_sources(conn=rerun) == (0, n)  # type: ignore[arg-type]


def test_quarantine_helper_moves_null_rows() -> None:
    fake = FakeConn(rowcount=3)
    assert db.quarantine_null_documents(conn=fake) == 3  # type: ignore[arg-type]
    blob = "\n".join(sql for sql, _ in fake.statements)
    assert "quarantined_documents" in blob
    assert "WHERE source_id IS NULL" in blob
    assert fake.committed


# --- live Postgres: real yoyo path on an isolated scratch database -----------


@pytest.fixture()
def scratch_db(scratch_db_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """Create/drop an isolated scratch DB (probe-once URL from conftest)."""
    import psycopg

    url = scratch_db_url
    # Unique per test so parallel xdist workers never share a scratch DB.
    name = f"brain_seed_scratch_{os.getpid()}_{uuid.uuid4().hex[:8]}"
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
        with admin.cursor() as cur:
            cur.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (name,),
            )
            cur.execute(f'DROP DATABASE IF EXISTS "{name}"')
    finally:
        admin.close()


def _exec_sql_file(conn: object, path: Path) -> None:
    cur = conn.cursor()  # type: ignore[union-attr]
    cur.execute(path.read_text(encoding="utf-8"))
    conn.commit()  # type: ignore[union-attr]


@pytest.mark.live_db
def test_live_007_backfills_quarantine_and_enforces_not_null(scratch_db: str) -> None:
    """Pre-007 NULL document -> 007 quarantines it -> NOT NULL holds after."""
    import psycopg

    mig = ROOT / "migrations"
    # Pre-007 schema via raw SQL (no yoyo version table yet), then one NULL row.
    with psycopg.connect(scratch_db, autocommit=True) as conn:
        for fname in (
            "001_init.sql",
            "002_canonical_url_unique.sql",
            "003_ingestion_runs.sql",
            "005_extraction_flag.sql",
            "006_seed_all_sources.sql",
        ):
            _exec_sql_file(conn, mig / fname)
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO documents
                     (source_id, url, canonical_url, title, published_at,
                      retrieved_at, language, content, content_hash)
                   VALUES (NULL, %s, %s, %s, now(), now(), 'en', %s, %s)""",
                (
                    "https://example.com/null-source",
                    "https://example.com/null-source",
                    "orphan row",
                    "orphan content",
                    "quarantine-probe-hash",
                ),
            )

    applied = db.apply_migrations()
    assert "007_deterministic_sources_and_not_null" in applied
    assert db.apply_migrations() == []  # rerun is a no-op

    with psycopg.connect(scratch_db) as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM sources")
        # One row per curated Source: the feed rows (007) + the two ADR-0013
        # Instagram rows (012 + 015).
        assert cur.fetchone()[0] == len(catalog_names())
        cur.execute("SELECT COUNT(*) FROM documents WHERE source_id IS NULL")
        assert cur.fetchone()[0] == 0
        cur.execute(
            "SELECT COUNT(*) FROM quarantined_documents WHERE content_hash = %s",
            ("quarantine-probe-hash",),
        )
        assert cur.fetchone()[0] == 1
        # NOT NULL is now enforced: future NULLs fail instead of going silent.
        with pytest.raises(psycopg.Error):
            cur.execute(
                """INSERT INTO documents
                     (source_id, url, canonical_url, title, published_at,
                      retrieved_at, language, content, content_hash)
                   VALUES (NULL, %s, %s, %s, now(), now(), 'en', %s, %s)""",
                (
                    "https://example.com/null-after",
                    "https://example.com/null-after",
                    "blocked row",
                    "blocked content",
                    "blocked-hash",
                ),
            )
