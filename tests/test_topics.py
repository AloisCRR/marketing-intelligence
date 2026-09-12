"""Topic vocabulary contract tests (Ticket 20) — fakes + one live-Postgres tier.

Locked contract:
- `list_vocabulary()` is read-only and additive: canonical pillar/region/
  content-type slugs plus retired aliases that stay discoverable.
- Synonyms, case/whitespace variants and retired aliases canonicalize
  server-side; unknown tags are rejected (never stored as-is).
- Setting Topics is latest-wins with full append-only history; the effective
  set becomes exactly the written list ([] clears it).
- Topics are visible wherever Documents are read (search results, single read,
  period bundle items) as canonical slugs, ``[]`` when unannotated.
- HTTP and MCP expose the vocabulary read and the write identically through
  the Service Adapter, with 422 on invalid input.
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

import marketing_intelligence.article as article_lane  # noqa: E402
import marketing_intelligence.period as period_lane  # noqa: E402
import marketing_intelligence.search as search_lane  # noqa: E402
import marketing_intelligence.service as service  # noqa: E402
import marketing_intelligence.topics as topics_lane  # noqa: E402
from api.app import app  # noqa: E402


def _load_mcp_server() -> Any:
    path = Path(_SRC) / "mcp" / "server.py"
    spec = importlib.util.spec_from_file_location("trend_mcp_server_topics", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MCP_SERVER = _load_mcp_server()

URL = "https://www.socialmediatoday.com/news/genz/1/"
CANONICAL = URL
PUBLISHED = _dt.datetime(2026, 9, 8, 14, 30, tzinfo=_dt.UTC)


# --- stateful fake store for the topic lane + article read -------------------


class _TopicStore:
    """Document store plus append-only document_topics history."""

    def __init__(self, topics: list[str] | None = None) -> None:
        self.document_id = 7
        self.url = URL
        self.canonical_url = CANONICAL
        self.rows: list[dict[str, Any]] = []
        self._seq = 0
        for slug in topics or []:
            self.insert(self.document_id, slug, True, None)

    def find(self, key: Any) -> bool:
        return key in (self.url, self.canonical_url)

    def insert(self, document_id: Any, slug: str, assigned: bool, reporter: Any) -> None:
        self._seq += 1
        self.rows.append(
            {
                "document_id": document_id,
                "topic_slug": slug,
                "assigned": assigned,
                "reporter": reporter,
                "created_at": _dt.datetime(2026, 9, 10, 12, 0, tzinfo=_dt.UTC)
                + _dt.timedelta(microseconds=self._seq),
                "seq": self._seq,
            }
        )

    def effective(self, document_id: Any) -> list[str]:
        latest: dict[str, dict[str, Any]] = {}
        for row in self.rows:
            if row["document_id"] != document_id:
                continue
            current = latest.get(row["topic_slug"])
            if current is None or (row["created_at"], row["seq"]) > (
                current["created_at"],
                current["seq"],
            ):
                latest[row["topic_slug"]] = row
        return sorted(slug for slug, row in latest.items() if row["assigned"])

    def article_row(self) -> dict[str, Any]:
        return {
            "title": "Gen Z self-purchase",
            "url": self.url,
            "canonical_url": self.canonical_url,
            "source": "Social Media Today",
            "published_at": PUBLISHED,
            "author": "A",
            "content": "A long body about Gen Z self-purchase.",
            "flag_reason": None,
            "flag_detail": None,
            "flagged_at": None,
            "flagged_by": None,
            "importance_score": None,
            "importance_rationale": None,
            "importance_reporter": None,
            "importance_updated_at": None,
        }


class _TopicCursor:
    def __init__(self, store: _TopicStore) -> None:
        self._store = store
        self._result: list[Any] = []

    def execute(self, sql: str, params: tuple | None = None) -> None:
        params = params or ()
        head = " ".join(sql.upper().split())
        store = self._store
        if head.startswith("SELECT D.ID"):
            key = params[0] if params else None
            self._result = [(store.document_id,)] if store.find(key) else []
            return
        if "DT.DOCUMENT_ID = %S" in head:  # lane effective-set fetch (by document id)
            self._result = [(slug,) for slug in store.effective(params[0])]
            return
        if head.startswith("SELECT DT.TOPIC_SLUG"):  # article read fetch (by url/canonical)
            key = params[0] if params else None
            self._result = (
                [(slug,) for slug in store.effective(store.document_id)] if store.find(key) else []
            )
            return
        if head.startswith("INSERT INTO DOCUMENT_TOPICS"):
            document_id, slug, assigned, reporter = params
            store.insert(document_id, slug, assigned, reporter)
            self._result = []
            return
        if head.startswith("SELECT D.TITLE"):
            key = params[0] if params else None
            self._result = [store.article_row()] if store.find(key) else []
            return
        if head.startswith("SELECT D.READ_AT"):
            self._result = [{"read_at": None, "read_by": None}]
            return
        self._result = []

    def fetchall(self) -> list[Any]:
        return list(self._result)

    def close(self) -> None:
        pass


class _TopicConnection:
    def __init__(self, store: _TopicStore) -> None:
        self.store = store
        self.committed = False
        self.closed = False

    def cursor(self) -> _TopicCursor:
        return _TopicCursor(self.store)

    def commit(self) -> None:
        self.committed = True

    def close(self) -> None:
        self.closed = True


# --- simple preset-row fakes for the search / period read paths --------------


class _RowCursor:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def execute(self, sql: str, params: tuple | None = None) -> None:
        pass

    def fetchall(self) -> list[Any]:
        return list(self._rows)

    def close(self) -> None:
        pass


class _RowConnection:
    """Serves the same preset rows through both cursor() and execute() styles."""

    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def cursor(self) -> _RowCursor:
        return _RowCursor(self._rows)

    def execute(self, sql: str, params: tuple | None = None) -> _RowCursor:
        return _RowCursor(self._rows)

    def close(self) -> None:
        pass


# --- vocabulary discovery ----------------------------------------------------


def test_vocabulary_is_discoverable_read_only_and_additive() -> None:
    vocab = topics_lane.list_vocabulary()
    by_slug = {entry["slug"]: entry for entry in vocab}

    assert by_slug["gen-z-self-purchase"]["kind"] == "pillar"
    assert by_slug["latam"]["kind"] == "region"
    assert by_slug["press-release"]["kind"] == "content-type"
    assert "gen z" in [s.lower() for s in by_slug["gen-z"]["synonyms"]]
    # Additive retirement: the alias stays listed, pointing at its replacement.
    assert by_slug["consumer-trends"]["retired_alias_of"] == "consumer-behavior"
    assert by_slug["influencer-marketing"]["retired_alias_of"] == "creator-economy"
    assert all(
        entry["retired_alias_of"] is None or entry["retired_alias_of"] in by_slug for entry in vocab
    )
    # Read-only and surfaced identically by the adapter.
    assert topics_lane.list_vocabulary() == vocab
    assert service.list_vocabulary() == vocab
    assert MCP_SERVER.list_vocabulary() == vocab


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Gen Z self-purchase", "gen-z-self-purchase"),
        ("  GEN_Z   SELF_PURCHASE ", "gen-z-self-purchase"),
        ("genz self purchase", "gen-z-self-purchase"),
        ("Jewellery", "jewelry"),
        ("influencer marketing", "creator-economy"),
        ("Q&A", "interview"),
        ("LATAM", "latam"),
        ("consumer trends", "consumer-behavior"),
    ],
)
def test_synonyms_canonicalize_server_side(raw: str, expected: str) -> None:
    assert topics_lane.canonicalize_topic(raw) == expected


# --- writes: rejection, canonicalization, latest-wins, history ---------------


def test_unknown_tags_are_rejected_and_never_stored() -> None:
    with pytest.raises(ValueError):
        topics_lane.canonicalize_topic("quantum-jewelry")

    conn = _TopicConnection(_TopicStore())
    with pytest.raises(ValueError):
        topics_lane.set_document_topics(URL, ["gen-z", "quantum-jewelry"], conn=conn)
    assert conn.store.rows == []  # rejected before any write

    with pytest.raises(service.InvalidRequest):
        service.set_document_topics(URL, ["quantum-jewelry"], conn=conn)
    assert conn.store.rows == []

    # Caller-side shape errors are InvalidRequest too, still without writes.
    for bad_topics in ("gen-z", 3, ["gen-z", 5], ["   "]):
        with pytest.raises(service.InvalidRequest):
            service.set_document_topics(URL, bad_topics, conn=conn)
    with pytest.raises(service.InvalidRequest):
        service.set_document_topics(URL, ["gen-z"], reporter="x" * 101, conn=conn)
    assert conn.store.rows == []


def test_set_topics_latest_wins_with_full_history() -> None:
    store = _TopicStore()
    conn = _TopicConnection(store)

    first = service.set_document_topics(
        URL, ["Gen Z", "Jewellery"], reporter="digest-agent", conn=conn
    )
    assert first["topics"] == ["gen-z", "jewelry"]
    assert set(first) == set(service.ARTICLE_KEYS)

    second = service.set_document_topics(URL, ["influencer marketing"], conn=conn)
    assert second["topics"] == ["creator-economy"]

    cleared = service.set_document_topics(URL, [], conn=conn)
    assert cleared["topics"] == []

    restored = service.set_document_topics(URL, ["LATAM"], conn=conn)
    assert restored["topics"] == ["latam"]

    history = [(row["topic_slug"], row["assigned"]) for row in store.rows]
    assert len(history) == 7  # 2 assigns + 1 assign + 2 tombstones + 1 tombstone + 1 assign
    for slug in ("gen-z", "jewelry", "creator-economy"):
        assert (slug, True) in history and (slug, False) in history  # retired, never deleted
    assert history[-1] == ("latam", True)
    assert store.rows[0]["reporter"] == "digest-agent"


def test_set_topics_unknown_article_is_invalid_request() -> None:
    conn = _TopicConnection(_TopicStore())
    with pytest.raises(service.InvalidRequest):
        service.set_document_topics("https://unknown.example/nope/", ["gen-z"], conn=conn)


# --- visibility on every Document read path ---------------------------------


def test_topics_visible_on_single_read_search_and_period_bundle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _TopicConnection(_TopicStore(topics=["jewelry", "gen-z"]))
    article = service.get_article(URL, conn=conn)
    assert article["topics"] == ["gen-z", "jewelry"]
    assert set(article) == set(service.ARTICLE_KEYS)

    # Unannotated Documents report an explicit empty list.
    empty = service.get_article(URL, conn=_TopicConnection(_TopicStore()))
    assert empty["topics"] == []

    # Search results carry the same key (dict and legacy tuple rows).
    dict_row = {
        "title": "Gen Z",
        "url": URL,
        "canonical_url": CANONICAL,
        "source": "Social Media Today",
        "published_at": PUBLISHED,
        "author": "A",
        "content": "Gen Z body",
        "topics": ["gen-z"],
    }
    tuple_row = (
        "Gen Z",
        URL,
        CANONICAL,
        "Social Media Today",
        PUBLISHED,
        "A",
        "Gen Z body",
        None,
        None,
        None,
        None,
    )
    monkeypatch.setattr(
        search_lane, "get_connection", lambda: _RowConnection([dict_row, tuple_row])
    )
    results = search_lane.search_articles("Gen Z", conn=None)
    assert results[0]["topics"] == ["gen-z"]
    assert results[1]["topics"] == []
    assert set(results[0]) == set(service.SEARCH_RESULT_KEYS)

    # Period bundle items carry it too.
    period_row = {
        "title": "Gen Z",
        "url": URL,
        "canonical_url": CANONICAL,
        "source": "Social Media Today",
        "published_at": PUBLISHED,
        "author": "A",
        "topics": ["latam", "gen-z"],
    }
    bundle = period_lane.get_period_context(
        _dt.date(2026, 9, 1), _dt.date(2026, 9, 30), conn=_RowConnection([period_row])
    )
    assert bundle["recent_articles"][0]["topics"] == ["latam", "gen-z"]
    assert set(bundle["recent_articles"][0]) == set(service.PERIOD_ARTICLE_KEYS)


# --- both caller surfaces: parity + 422 -------------------------------------


def test_vocabulary_and_write_validation_on_both_surfaces() -> None:
    client = TestClient(app)
    vocab = client.get("/vocabulary")
    assert vocab.status_code == 200
    assert vocab.json() == {"vocabulary": service.list_vocabulary()}

    # Unknown tags: 422 on HTTP, InvalidRequest on MCP — both before any write.
    assert client.post("/topics", json={"identifier": URL, "topics": ["nope"]}).status_code == 422
    assert client.post("/topics", json={"identifier": "  ", "topics": []}).status_code == 422
    with pytest.raises(service.InvalidRequest):
        MCP_SERVER.set_document_topics(identifier=URL, topics=["nope"])
    with pytest.raises(service.InvalidRequest):
        MCP_SERVER.set_document_topics(identifier=URL, topics="gen-z")


def test_topics_write_payload_identical_over_http_and_mcp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {"title": "Gen Z", "url": URL, "canonical_url": CANONICAL, "topics": ["gen-z"]}
    monkeypatch.setattr(
        service,
        "set_document_topics",
        lambda identifier, topics, reporter=None, conn=None: payload,
    )
    api_payload = (
        TestClient(app)
        .post("/topics", json={"identifier": URL, "topics": ["Gen Z"], "reporter": "agent"})
        .json()
    )
    assert api_payload == payload
    assert MCP_SERVER.set_document_topics(identifier=URL, topics=["Gen Z"]) == payload


# --- migration ---------------------------------------------------------------


def test_migration_010_is_idempotent_ddl() -> None:
    from marketing_intelligence.db import MIGRATIONS_DIR

    sql = (MIGRATIONS_DIR / "010_topics.sql").read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS document_topics" in sql
    assert "CREATE INDEX IF NOT EXISTS" in sql
    assert "document_id, created_at DESC, id DESC" in sql
    assert "DROP TABLE" not in sql.upper()


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
    name = f"brain_topics_{os.getpid()}_{uuid.uuid4().hex[:8]}"
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
def test_live_topics_roundtrip_and_visibility(scratch_db: str) -> None:
    import psycopg

    from marketing_intelligence import db as db_mod

    assert "010_topics" in db_mod.apply_migrations()
    assert db_mod.pending_migrations() == []

    with psycopg.connect(scratch_db) as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM sources ORDER BY name LIMIT 1")
        source_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO documents (source_id, url, canonical_url, title, author,"
            " published_at, retrieved_at, language, content, content_hash)"
            " VALUES (%s, %s, %s, 'Gen Z', 'A', now(), now(), 'en', 'Gen Z body', 'hash-topics')",
            (source_id, URL, URL),
        )

    first = service.set_document_topics(URL, ["Gen Z self-purchase", "jewellery"], reporter="agent")
    assert first["topics"] == ["gen-z-self-purchase", "jewelry"]
    with pytest.raises(service.InvalidRequest):
        service.set_document_topics(URL, ["quantum-jewelry"])

    second = service.set_document_topics(URL, ["latam"])
    assert second["topics"] == ["latam"]
    assert service.get_article(URL)["topics"] == ["latam"]
    assert service.search_articles("Gen Z")[0]["topics"] == ["latam"]

    bundle = period_lane.get_period_context(
        _dt.date(2026, 9, 1), _dt.date(2026, 9, 30), conn=psycopg.connect(scratch_db)
    )
    assert bundle["recent_articles"][0]["topics"] == ["latam"]

    # The per-source-capped SELECT keeps the topic array too.
    capped = period_lane.get_period_context(
        _dt.date(2026, 9, 1),
        _dt.date(2026, 9, 30),
        per_source_limit=3,
        conn=psycopg.connect(scratch_db),
    )
    assert capped["recent_articles"][0]["topics"] == ["latam"]

    # History retained append-only: retired slugs are tombstoned, never deleted.
    with psycopg.connect(scratch_db) as conn, conn.cursor() as cur:
        cur.execute("SELECT topic_slug, assigned FROM document_topics ORDER BY created_at, id")
        history = cur.fetchall()
    assert ("gen-z-self-purchase", True) in history
    assert ("gen-z-self-purchase", False) in history
    assert ("jewelry", False) in history
    assert ("latam", True) in history
