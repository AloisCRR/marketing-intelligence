"""Digest picks trace contract tests (Ticket 22) — fakes + one live-Postgres tier.

Locked contract:
- `record_digest_picks(digest_date, identifiers, reporter)` reconciles one
  digest date's stored pick set to exactly the given Documents: new ones are
  inserted, dropped ones deleted, unchanged ones untouched (their original
  `picked_at` survives). Recording the same set twice changes nothing.
- The returned `added`/`removed` lists are the re-scoring signal for an edited
  digest; re-recording an edited URL set reports the Documents added/removed.
- `get_digest_picks` reads the set back (identity + reporter + timestamp);
  `clear_digest_picks` drops it and reports how many rows went.
- Malformed dates, blank/unknown/non-list identifiers and overlong reporters
  are caller errors (`service.InvalidRequest` -> 422) raised before any write.
- HTTP and MCP expose the three operations identically through the Service
  Adapter.
"""

from __future__ import annotations

import datetime as _dt
import importlib.util
import os
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(os.path.dirname(_HERE), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import marketing_intelligence.digests as digests_lane  # noqa: E402
import marketing_intelligence.service as service  # noqa: E402
from api.app import app  # noqa: E402


def _utc(*args: int) -> _dt.datetime:
    return _dt.datetime(*args, tzinfo=_dt.UTC)


DIGEST_DATE = _dt.date(2026, 9, 7)
DIGEST_DATE_ISO = "2026-09-07"

A_URL = "https://www.socialmediatoday.com/news/alpha/1/"
B_URL = "https://www.socialmediatoday.com/news/bravo/2/"
C_URL = "https://adage.com/news/charlie/3/"
# One Document reachable through a tracking-URL spelling and its canonical URL.
D_RAW = "https://example.com/story/one?utm_source=news"
D_CANON = "https://example.com/story/one"

_PICK_KEYS = {
    "url",
    "canonical_url",
    "title",
    "source",
    "published_at",
    "reporter",
    "picked_at",
}


def _load_mcp_server() -> Any:
    path = Path(_SRC) / "mcp" / "server.py"
    spec = importlib.util.spec_from_file_location("t22_mcp_server_digest_picks", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MCP_SERVER = _load_mcp_server()


# --- stateful fake store for the digest-picks lane ---------------------------


def _docs() -> list[dict[str, Any]]:
    return [
        {
            "id": 1,
            "url": A_URL,
            "canonical_url": A_URL,
            "title": "Alpha",
            "source": "Social Media Today",
            "published_at": _utc(2026, 9, 8, 14, 30),
        },
        {
            "id": 2,
            "url": B_URL,
            "canonical_url": B_URL,
            "title": "Bravo",
            "source": "Social Media Today",
            "published_at": _utc(2026, 9, 8, 13, 0),
        },
        {
            "id": 3,
            "url": C_URL,
            "canonical_url": C_URL,
            "title": "Charlie",
            "source": "Ad Age",
            "published_at": _utc(2026, 9, 7, 9, 0),
        },
        {
            "id": 4,
            "url": D_RAW,
            "canonical_url": D_CANON,
            "title": "Delta",
            "source": "Example",
            "published_at": _utc(2026, 9, 6, 9, 0),
        },
    ]


class _DigestStore:
    """Document store plus the current (non-append-only) digest_picks set."""

    def __init__(self, docs: list[dict[str, Any]] | None = None) -> None:
        self.docs = list(_docs() if docs is None else docs)
        self.picks: dict[tuple[Any, Any], dict[str, Any]] = {}
        self._seq = 0

    def doc(self, document_id: Any) -> dict[str, Any] | None:
        for d in self.docs:
            if d["id"] == document_id:
                return d
        return None

    def find(self, key: Any, canon_key: Any) -> dict[str, Any] | None:
        for d in self.docs:
            if key in (d["url"], d["canonical_url"]) or canon_key in (
                d["url"],
                d["canonical_url"],
            ):
                return d
        return None

    def upsert(self, digest_date: Any, document_id: Any, reporter: Any) -> None:
        current = self.picks.get((digest_date, document_id))
        if current is None:
            self._seq += 1
            self.picks[(digest_date, document_id)] = {
                "reporter": reporter,
                "created_at": _utc(2026, 9, 10, 12, 0) + _dt.timedelta(seconds=self._seq),
            }
        elif reporter is not None:
            current["reporter"] = reporter


class _DigestCursor:
    def __init__(self, conn: _DigestConnection) -> None:
        self._conn = conn
        self._result: list[Any] = []
        self.rowcount = -1

    def execute(self, sql: str, params: tuple | None = None) -> None:
        params = params or ()
        head = " ".join(sql.upper().split())
        conn = self._conn
        store = conn.store
        conn.queries.append(head)
        if head.startswith("SELECT P.DOCUMENT_ID, D.URL, D.CANONICAL_URL"):
            date_value = params[0]
            rows = []
            for pick_date, document_id in store.picks:
                if pick_date != date_value:
                    continue
                doc = store.doc(document_id)
                assert doc is not None
                rows.append((document_id, doc["url"], doc["canonical_url"]))
            self._result = rows
            return
        if head.startswith("SELECT D.ID, D.URL, D.CANONICAL_URL"):
            canon_key = params[1] if len(params) > 1 else None
            doc = store.find(params[0], canon_key)
            self._result = [(doc["id"], doc["url"], doc["canonical_url"])] if doc else []
            return
        if head.startswith("INSERT INTO DIGEST_PICKS"):
            date_value, document_id, reporter = params
            store.upsert(date_value, document_id, reporter)
            conn.writes += 1
            self._result = []
            self.rowcount = 1
            return
        if head.startswith("DELETE FROM DIGEST_PICKS") and "ANY" in head:
            date_value, document_ids = params
            removed = 0
            for document_id in document_ids:
                if store.picks.pop((date_value, document_id), None) is not None:
                    removed += 1
            conn.writes += 1
            self._result = []
            self.rowcount = removed
            return
        if head.startswith("DELETE FROM DIGEST_PICKS"):
            date_value = params[0]
            doomed = [key for key in store.picks if key[0] == date_value]
            for key in doomed:
                del store.picks[key]
            conn.writes += 1
            self._result = []
            self.rowcount = len(doomed)
            return
        if head.startswith("SELECT D.URL, D.CANONICAL_URL, D.TITLE"):
            date_value = params[0]
            picks = []
            for (pick_date, document_id), pick in store.picks.items():
                if pick_date != date_value:
                    continue
                doc = store.doc(document_id)
                assert doc is not None
                picks.append(
                    (
                        doc["url"],
                        doc["canonical_url"],
                        doc["title"],
                        doc["source"],
                        doc["published_at"],
                        pick["reporter"],
                        pick["created_at"],
                    )
                )
            picks.sort(key=lambda row: row[0])
            picks.sort(key=lambda row: row[4], reverse=True)
            self._result = picks
            return
        self._result = []

    def fetchall(self) -> list[Any]:
        return list(self._result)

    def close(self) -> None:
        pass


class _DigestConnection:
    def __init__(self, store: _DigestStore) -> None:
        self.store = store
        self.queries: list[str] = []
        self.writes = 0
        self.committed = 0
        self.closed = 0

    def cursor(self) -> _DigestCursor:
        return _DigestCursor(self)

    def commit(self) -> None:
        self.committed += 1

    def close(self) -> None:
        self.closed += 1


def _patch(monkeypatch: pytest.MonkeyPatch, store: _DigestStore) -> _DigestStore:
    monkeypatch.setattr(digests_lane, "get_connection", lambda: _DigestConnection(store))
    return store


# --- round trip, idempotency, diff ------------------------------------------


def test_record_read_clear_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _DigestStore())

    recorded = service.record_digest_picks(DIGEST_DATE, [A_URL, B_URL], reporter="digest-agent")
    assert recorded["digest_date"] == DIGEST_DATE_ISO
    assert recorded["reporter"] == "digest-agent"
    assert set(recorded["added"]) == {A_URL, B_URL}
    assert recorded["removed"] == []
    assert {p["url"] for p in recorded["picks"]} == {A_URL, B_URL}
    for pick in recorded["picks"]:
        assert set(pick) == _PICK_KEYS
        assert pick["reporter"] == "digest-agent"
        assert _dt.datetime.fromisoformat(pick["picked_at"]).tzinfo is not None

    read_back = service.get_digest_picks(DIGEST_DATE)
    assert read_back == {"digest_date": DIGEST_DATE_ISO, "picks": recorded["picks"]}

    cleared = service.clear_digest_picks(DIGEST_DATE)
    assert cleared["digest_date"] == DIGEST_DATE_ISO
    assert cleared["cleared"] == 2
    assert cleared["picks"] == []
    assert service.get_digest_picks(DIGEST_DATE)["picks"] == []


def test_rerecord_same_set_is_idempotent_and_keeps_picked_at(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _patch(monkeypatch, _DigestStore())

    first = service.record_digest_picks(DIGEST_DATE, [A_URL, B_URL], reporter="digest-agent")
    stamped = {p["url"]: p["picked_at"] for p in first["picks"]}

    again = service.record_digest_picks(DIGEST_DATE, [B_URL, A_URL], reporter="digest-agent")
    assert again["added"] == []
    assert again["removed"] == []
    assert {p["url"]: p["picked_at"] for p in again["picks"]} == stamped
    assert len(store.picks) == 2


def test_edited_rerecord_reports_added_and_removed(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _DigestStore())
    service.record_digest_picks(DIGEST_DATE, [A_URL, B_URL])

    edited = service.record_digest_picks(DIGEST_DATE, [B_URL, C_URL])
    assert set(edited["added"]) == {C_URL}
    assert set(edited["removed"]) == {A_URL}
    assert {p["url"] for p in edited["picks"]} == {B_URL, C_URL}
    assert {p["url"] for p in service.get_digest_picks(DIGEST_DATE)["picks"]} == {
        B_URL,
        C_URL,
    }

    unchanged = service.record_digest_picks(DIGEST_DATE, [C_URL, B_URL])
    assert unchanged["added"] == []
    assert unchanged["removed"] == []


def test_canonical_and_duplicate_spellings_collapse_to_one_pick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _patch(monkeypatch, _DigestStore())

    result = service.record_digest_picks(DIGEST_DATE, [D_RAW, D_CANON, D_RAW])
    assert result["added"] == [D_CANON]
    assert result["removed"] == []
    assert len(result["picks"]) == 1
    assert result["picks"][0]["canonical_url"] == D_CANON
    assert len(store.picks) == 1


def test_empty_identifiers_reconcile_set_to_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _patch(monkeypatch, _DigestStore())
    service.record_digest_picks(DIGEST_DATE, [A_URL])

    result = service.record_digest_picks(DIGEST_DATE, [])
    assert result["added"] == []
    assert set(result["removed"]) == {A_URL}
    assert result["picks"] == []
    assert store.picks == {}


# --- validation: caller errors before any DB round trip ----------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"digest_date": "not-a-date", "identifiers": [A_URL]},
        {"digest_date": "", "identifiers": [A_URL]},
        {"digest_date": 20260907, "identifiers": [A_URL]},
        {"digest_date": None, "identifiers": [A_URL]},
        {"digest_date": DIGEST_DATE_ISO, "identifiers": A_URL},
        {"digest_date": DIGEST_DATE_ISO, "identifiers": 3},
        {"digest_date": DIGEST_DATE_ISO, "identifiers": None},
        {"digest_date": DIGEST_DATE_ISO, "identifiers": [A_URL, 5]},
        {"digest_date": DIGEST_DATE_ISO, "identifiers": ["   "]},
        {"digest_date": DIGEST_DATE_ISO, "identifiers": [A_URL], "reporter": "x" * 101},
    ],
)
def test_shape_errors_are_invalid_without_db_roundtrip(
    kwargs: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _DigestStore()
    conn = _DigestConnection(store)

    with pytest.raises(service.InvalidRequest):
        service.record_digest_picks(conn=conn, **kwargs)
    assert conn.queries == []
    assert store.picks == {}


def test_read_and_clear_shape_errors_are_invalid_without_db_roundtrip() -> None:
    conn = _DigestConnection(_DigestStore())
    for call in (
        lambda: service.get_digest_picks("not-a-date", conn=conn),
        lambda: service.clear_digest_picks("", conn=conn),
    ):
        with pytest.raises(service.InvalidRequest):
            call()
    assert conn.queries == []


def test_unknown_identifier_is_invalid_without_writes() -> None:
    store = _DigestStore()
    conn = _DigestConnection(store)

    with pytest.raises(service.InvalidRequest):
        service.record_digest_picks(
            DIGEST_DATE, [A_URL, "https://unknown.example/nope/"], conn=conn
        )
    assert store.picks == {}
    assert conn.writes == 0


# --- HTTP + MCP parity ------------------------------------------------------


def test_http_digest_picks_roundtrip_and_422(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _DigestStore())
    client = TestClient(app)

    created = client.post(
        "/digest-picks",
        json={
            "digest_date": DIGEST_DATE_ISO,
            "identifiers": [A_URL, B_URL],
            "reporter": "digest-agent",
        },
    )
    assert created.status_code == 200
    payload = created.json()
    assert set(payload["added"]) == {A_URL, B_URL}

    read = client.get("/digest-picks", params={"digest_date": DIGEST_DATE_ISO})
    assert read.status_code == 200
    assert read.json() == {"digest_date": DIGEST_DATE_ISO, "picks": payload["picks"]}

    assert (
        client.post(
            "/digest-picks", json={"digest_date": "not-a-date", "identifiers": [A_URL]}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/digest-picks", json={"digest_date": DIGEST_DATE_ISO, "identifiers": A_URL}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/digest-picks",
            json={
                "digest_date": DIGEST_DATE_ISO,
                "identifiers": [A_URL],
                "reporter": "x" * 101,
            },
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/digest-picks",
            json={
                "digest_date": DIGEST_DATE_ISO,
                "identifiers": ["https://unknown.example/nope/"],
            },
        ).status_code
        == 422
    )
    assert client.get("/digest-picks").status_code == 422
    assert client.get("/digest-picks", params={"digest_date": "not-a-date"}).status_code == 422

    cleared = client.delete("/digest-picks", params={"digest_date": DIGEST_DATE_ISO})
    assert cleared.status_code == 200
    assert cleared.json()["cleared"] == 2
    assert cleared.json()["picks"] == []


def test_mcp_digest_picks_matches_service_and_422(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _DigestStore())

    recorded = MCP_SERVER.record_digest_picks(DIGEST_DATE_ISO, [A_URL], reporter="digest-agent")
    assert set(recorded["added"]) == {A_URL}
    assert set(recorded["picks"][0]) == _PICK_KEYS
    assert recorded["picks"][0]["reporter"] == "digest-agent"

    assert MCP_SERVER.get_digest_picks(DIGEST_DATE_ISO) == service.get_digest_picks(DIGEST_DATE)

    cleared = MCP_SERVER.clear_digest_picks(DIGEST_DATE_ISO)
    assert cleared["cleared"] == 1
    assert cleared["picks"] == []

    for call in (
        lambda: MCP_SERVER.record_digest_picks("nope", [A_URL]),
        lambda: MCP_SERVER.record_digest_picks(DIGEST_DATE_ISO, A_URL),
        lambda: MCP_SERVER.record_digest_picks(DIGEST_DATE_ISO, [A_URL], reporter="x" * 101),
        lambda: MCP_SERVER.get_digest_picks("nope"),
        lambda: MCP_SERVER.clear_digest_picks("nope"),
    ):
        with pytest.raises(service.InvalidRequest):
            call()


def test_digest_picks_payloads_identical_over_http_and_mcp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record_payload = {
        "digest_date": DIGEST_DATE_ISO,
        "reporter": "agent",
        "picks": [],
        "added": [],
        "removed": [],
    }
    get_payload = {"digest_date": DIGEST_DATE_ISO, "picks": []}
    clear_payload = {"digest_date": DIGEST_DATE_ISO, "cleared": 0, "picks": []}
    monkeypatch.setattr(service, "record_digest_picks", lambda *a, **k: record_payload)
    monkeypatch.setattr(service, "get_digest_picks", lambda *a, **k: get_payload)
    monkeypatch.setattr(service, "clear_digest_picks", lambda *a, **k: clear_payload)
    client = TestClient(app)

    assert (
        client.post(
            "/digest-picks",
            json={"digest_date": DIGEST_DATE_ISO, "identifiers": [], "reporter": "agent"},
        ).json()
        == record_payload
    )
    assert MCP_SERVER.record_digest_picks(DIGEST_DATE_ISO, [], reporter="agent") == record_payload
    assert (
        client.get("/digest-picks", params={"digest_date": DIGEST_DATE_ISO}).json() == get_payload
    )
    assert MCP_SERVER.get_digest_picks(DIGEST_DATE_ISO) == get_payload
    assert (
        client.delete("/digest-picks", params={"digest_date": DIGEST_DATE_ISO}).json()
        == clear_payload
    )
    assert MCP_SERVER.clear_digest_picks(DIGEST_DATE_ISO) == clear_payload


# --- migration ---------------------------------------------------------------


def test_migration_011_is_idempotent_ddl() -> None:
    from marketing_intelligence.db import MIGRATIONS_DIR

    sql = (MIGRATIONS_DIR / "011_digest_picks.sql").read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS digest_picks" in sql
    assert "UNIQUE (digest_date, document_id)" in sql
    assert "REFERENCES documents(id) ON DELETE CASCADE" in sql
    assert "DROP TABLE" not in sql.upper()


# --- live Postgres: real migration/SQL end-to-end ---------------------------


@pytest.fixture()
def scratch_db(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Create/drop an isolated scratch DB with the real migrations applied."""
    import psycopg

    from marketing_intelligence.config import get_database_url

    url = os.environ.get("DATABASE_URL", get_database_url())
    try:
        probe = psycopg.connect(url, connect_timeout=2)
        probe.close()
    except Exception:
        pytest.skip("no live Postgres reachable")
    base, _, _ = url.rpartition("/")
    name = f"brain_digest_picks_{os.getpid()}_{uuid.uuid4().hex[:8]}"
    admin = psycopg.connect(f"{base}/postgres", autocommit=True)
    try:
        with admin.cursor() as cur:
            cur.execute(f'CREATE DATABASE "{name}"')
    finally:
        admin.close()
    scratch_url = f"{base}/{name}"
    monkeypatch.setenv("DATABASE_URL", scratch_url)
    yield scratch_url
    admin = psycopg.connect(f"{base}/postgres", autocommit=True)
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


@pytest.mark.live_db
def test_live_digest_picks_roundtrip_diff_and_clear(scratch_db: str) -> None:
    import psycopg

    from marketing_intelligence import db as db_mod

    assert "011_digest_picks" in db_mod.apply_migrations()
    assert db_mod.pending_migrations() == []

    with psycopg.connect(scratch_db) as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM sources ORDER BY name LIMIT 1")
        source_id = cur.fetchone()[0]
        for index, (url, title) in enumerate(
            ((A_URL, "Alpha"), (B_URL, "Bravo"), (C_URL, "Charlie"))
        ):
            cur.execute(
                "INSERT INTO documents (source_id, url, canonical_url, title, author,"
                " published_at, retrieved_at, language, content, content_hash)"
                " VALUES (%s, %s, %s, %s, 'A', now(), now(), 'en', 'body', %s)",
                (source_id, url, url, title, f"hash-digest-{index}"),
            )

    first = service.record_digest_picks(DIGEST_DATE, [A_URL, B_URL], reporter="digest-agent")
    assert set(first["added"]) == {A_URL, B_URL}
    assert first["removed"] == []
    assert {p["url"] for p in first["picks"]} == {A_URL, B_URL}
    stamped = {p["url"]: p["picked_at"] for p in first["picks"]}
    assert all(p["reporter"] == "digest-agent" for p in first["picks"])

    again = service.record_digest_picks(DIGEST_DATE, [B_URL, A_URL], reporter="digest-agent")
    assert again["added"] == []
    assert again["removed"] == []
    assert {p["url"]: p["picked_at"] for p in again["picks"]} == stamped

    edited = service.record_digest_picks(DIGEST_DATE, [B_URL, C_URL])
    assert set(edited["added"]) == {C_URL}
    assert set(edited["removed"]) == {A_URL}
    assert {p["url"] for p in edited["picks"]} == {B_URL, C_URL}

    with pytest.raises(service.InvalidRequest):
        service.record_digest_picks(DIGEST_DATE, ["https://unknown.example/nope/"])
    assert {p["url"] for p in service.get_digest_picks(DIGEST_DATE)["picks"]} == {
        B_URL,
        C_URL,
    }

    cleared = service.clear_digest_picks(DIGEST_DATE)
    assert cleared["cleared"] == 2
    assert cleared["picks"] == []
    assert service.get_digest_picks(DIGEST_DATE)["picks"] == []
