"""Importance lane contract tests (Ticket 19) — fakes + one live-Postgres tier.

Locked contract:
- `set_importance(identifier, score, rationale, reporter)` writes an
  append-only annotation row; latest write wins, full history retained.
- Scores outside 0-1 are caller errors (service.InvalidRequest -> 422).
- Extraction-flagged or paywalled Documents are hard-capped at 0.3 by the
  server regardless of the submitted score.
- The latest annotation is visible on search results, single reads, and
  period bundle items without extra calls.
- HTTP and MCP expose the write identically through the Service Adapter.
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
import marketing_intelligence.importance as importance_lane  # noqa: E402
import marketing_intelligence.period as period_lane  # noqa: E402
import marketing_intelligence.service as service  # noqa: E402
from api.app import app  # noqa: E402


def _utc(*args: int) -> _dt.datetime:
    return _dt.datetime(*args, tzinfo=_dt.UTC)


CLEAN_URL = "https://www.socialmediatoday.com/news/clean/1/"
CLEAN_CANON = "https://www.socialmediatoday.com/news/clean/1/"
FLAGGED_URL = "https://www.socialmediatoday.com/news/flagged/2/"
PAYWALLED_URL = "https://jingdaily.com/news/metered/3/"
PAYWALL_BODY = "Shanghai clubs promise discretion.\nSubscribe to continue reading this story."

_BASE_COLS = (
    "title",
    "url",
    "canonical_url",
    "source",
    "published_at",
    "author",
    "content",
    "flag_reason",
    "flag_detail",
    "flagged_at",
    "flagged_by",
)
_IMP_COLS = ("score", "rationale", "reporter", "created_at")


def _doc(
    doc_id: int,
    url: str,
    *,
    content: str,
    flag_reason: str | None = None,
    title: str = "Title",
) -> dict[str, Any]:
    return {
        "id": doc_id,
        "url": url,
        "canonical_url": url,
        "title": title,
        "source": "Social Media Today",
        "published_at": _utc(2026, 9, 8, 14, 30),
        "author": "A",
        "content": content,
        "flag_reason": flag_reason,
        "flag_detail": None,
        "flagged_at": None,
        "flagged_by": None,
        "read_at": None,
        "read_by": None,
    }


class _ImportanceConn:
    """Stateful fake for the importance + read-back article SQL shapes."""

    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self.store = list(docs)
        self.rows: list[dict[str, Any]] = []
        self._seq = 0
        self.closed = 0

    # -- fake DB internals ---------------------------------------------------
    def _find(self, url_key: Any, canon_key: Any) -> dict[str, Any] | None:
        for d in self.store:
            if url_key is not None and d["url"] == url_key:
                return d
            if canon_key is not None and d["canonical_url"] == canon_key:
                return d
        return None

    def _history(self, doc_id: Any) -> list[dict[str, Any]]:
        rows = [r for r in self.rows if r["document_id"] == doc_id]
        return sorted(rows, key=lambda r: (r["created_at"], r["seq"]), reverse=True)

    def _latest(self, doc_id: Any) -> dict[str, Any] | None:
        history = self._history(doc_id)
        return history[0] if history else None

    def cursor(self) -> _ImportanceCursor:
        return _ImportanceCursor(self)

    def commit(self) -> None:
        pass

    def close(self) -> None:
        self.closed += 1


class _ImportanceCursor:
    def __init__(self, conn: _ImportanceConn) -> None:
        self._conn = conn
        self._result: list[Any] = []

    def execute(self, sql: str, params: tuple | None = None) -> None:
        params = params or ()
        head = " ".join(sql.upper().split())
        c = self._conn
        if head.startswith("SELECT D.ID, D.FLAG_REASON, D.CONTENT"):
            doc = c._find(params[0], params[1])
            self._result = [(doc["id"], doc["flag_reason"], doc["content"])] if doc else []
            return
        if head.startswith("INSERT INTO DOCUMENT_IMPORTANCE"):
            document_id, score, rationale, reporter = params
            c._seq += 1
            c.rows.append(
                {
                    "document_id": document_id,
                    "score": score,
                    "rationale": rationale,
                    "reporter": reporter,
                    "created_at": _utc(2026, 9, 10, 12, 0) + _dt.timedelta(microseconds=c._seq),
                    "seq": c._seq,
                }
            )
            self._result = []
            return
        if head.startswith("SELECT D.READ_AT, D.READ_BY"):
            doc = c._find(params[0], params[1])
            self._result = [(doc["read_at"], doc["read_by"])] if doc else []
            return
        if head.startswith("SELECT D.TITLE"):
            if "WHERE D.CANONICAL_URL" in head:
                doc = c._find(None, params[0])
            else:
                doc = c._find(params[0], None)
            if doc is None:
                self._result = []
                return
            latest = c._latest(doc["id"])
            imp = tuple(latest[k] for k in _IMP_COLS) if latest else (None, None, None, None)
            self._result = [tuple(doc[k] for k in _BASE_COLS) + imp]
            return
        if "SELECT I.SCORE, I.RATIONALE, I.REPORTER, I.CREATED_AT" in head:
            doc = c._find(params[0], params[1])
            rows = c._history(doc["id"]) if doc else []
            if "LIMIT 1" in head:
                rows = rows[:1]
            self._result = [tuple(r[k] for k in _IMP_COLS) for r in rows]
            return
        self._result = []

    def fetchall(self) -> list[Any]:
        return list(self._result)

    def close(self) -> None:
        pass


def _patch(monkeypatch: pytest.MonkeyPatch, conn: _ImportanceConn) -> _ImportanceConn:
    monkeypatch.setattr(importance_lane, "get_connection", lambda: conn)
    monkeypatch.setattr(article_lane, "get_connection", lambda: conn)
    return conn


def _docs() -> list[dict[str, Any]]:
    return [
        _doc(1, CLEAN_URL, content="A full clean body about marketing.", title="Clean"),
        _doc(2, FLAGGED_URL, content="A thin body.", flag_reason="thin", title="Flagged"),
        _doc(3, PAYWALLED_URL, content=PAYWALL_BODY, title="Metered"),
    ]


def _load_mcp_server() -> Any:
    path = Path(_SRC) / "mcp" / "server.py"
    spec = importlib.util.spec_from_file_location("t19_mcp_server", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MCP_SERVER = _load_mcp_server()


# --- round trip, latest wins, history ---------------------------------------


def test_set_roundtrips_score_rationale_reporter_timestamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _patch(monkeypatch, _ImportanceConn(_docs()))
    result = service.set_importance(
        CLEAN_URL, 0.9, rationale="high signal", reporter="digest-agent"
    )
    assert set(result) == set(service.ARTICLE_KEYS)
    assert result["importance_score"] == 0.9
    assert result["importance_rationale"] == "high signal"
    assert result["importance_reporter"] == "digest-agent"
    assert _dt.datetime.fromisoformat(result["importance_updated_at"]).tzinfo is not None
    assert len(conn.rows) == 1


def test_latest_write_wins_and_history_is_retained(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _patch(monkeypatch, _ImportanceConn(_docs()))
    service.set_importance(CLEAN_URL, 0.9, rationale="first", reporter="agent")
    service.set_importance(CLEAN_URL, 0.4, rationale="revised", reporter="agent")

    latest = service.get_importance(CLEAN_URL)
    assert latest["importance_score"] == 0.4
    assert latest["importance_rationale"] == "revised"
    history = importance_lane.get_importance_history(CLEAN_URL)
    assert [h["importance_score"] for h in history] == [0.4, 0.9]
    assert len(conn.rows) == 2  # append-only, nothing overwritten


def test_unannotated_document_reports_explicit_nones(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _ImportanceConn(_docs()))
    assert service.get_importance(CLEAN_URL) == {
        "importance_score": None,
        "importance_rationale": None,
        "importance_reporter": None,
        "importance_updated_at": None,
    }


# --- validation + server-side cap -------------------------------------------


@pytest.mark.parametrize("bad", [-0.01, 1.01, 2, True, "high"])
def test_scores_outside_zero_one_are_invalid(bad: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _ImportanceConn(_docs()))
    with pytest.raises(service.InvalidRequest):
        service.set_importance(CLEAN_URL, bad)


def test_flagged_document_is_hard_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _ImportanceConn(_docs()))
    result = service.set_importance(FLAGGED_URL, 0.9, rationale="cheat", reporter="r")
    assert result["importance_score"] == 0.3
    assert service.get_importance(FLAGGED_URL)["importance_score"] == 0.3


def test_paywalled_document_is_hard_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _ImportanceConn(_docs()))
    result = service.set_importance(PAYWALLED_URL, 0.95, rationale="metered", reporter="r")
    assert result["importance_score"] == 0.3


def test_cap_never_raises_a_low_score(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _ImportanceConn(_docs()))
    assert service.set_importance(FLAGGED_URL, 0.1)["importance_score"] == 0.1


def test_blank_and_unknown_identifiers_are_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _ImportanceConn(_docs()))
    for bad in ("", "   "):
        with pytest.raises(service.InvalidRequest):
            service.set_importance(bad, 0.5)
    with pytest.raises(service.InvalidRequest):
        service.set_importance("https://example.com/nope/", 0.5)


# --- visibility on the three read paths -------------------------------------


def test_period_bundle_items_carry_importance(monkeypatch: pytest.MonkeyPatch) -> None:
    row = (
        "Title",
        CLEAN_URL,
        CLEAN_CANON,
        "Social Media Today",
        _utc(2026, 9, 8, 14, 30),
        "A",
        None,
        None,
        None,
        None,
        None,
        None,
        0.8,
        "why it matters",
        "digest-agent",
        _utc(2026, 9, 10, 12, 0),
    )

    class _PeriodConn:
        def execute(self, sql: str, params: tuple) -> Any:
            return type("C", (), {"fetchall": lambda self: [row]})()

    result = period_lane.get_period_context(
        _dt.date(2026, 9, 7), _dt.date(2026, 9, 13), conn=_PeriodConn()
    )
    item = result["recent_articles"][0]
    assert set(item) == set(service.PERIOD_ARTICLE_KEYS)
    assert item["importance_score"] == 0.8
    assert item["importance_rationale"] == "why it matters"
    assert item["importance_reporter"] == "digest-agent"
    assert item["importance_updated_at"] == "2026-09-10T12:00:00+00:00"


# --- HTTP + MCP parity ------------------------------------------------------


def test_api_importance_roundtrip_and_422(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _ImportanceConn(_docs()))
    client = TestClient(app)

    ok = client.post(
        "/importance",
        json={
            "identifier": CLEAN_URL,
            "score": 0.7,
            "rationale": "keep",
            "reporter": "digest-agent",
        },
    )
    assert ok.status_code == 200
    payload = ok.json()
    assert set(payload) == set(service.ARTICLE_KEYS)
    assert payload["importance_score"] == 0.7

    assert client.get("/importance", params={"url": CLEAN_URL}).json()["importance_score"] == 0.7
    assert (
        client.post("/importance", json={"identifier": CLEAN_URL, "score": 1.5}).status_code == 422
    )


def test_mcp_importance_tool_matches_service(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _ImportanceConn(_docs()))
    mcp_result = MCP_SERVER.set_importance(CLEAN_URL, 0.6, rationale="mcp", reporter="agent")
    assert set(mcp_result) == set(service.ARTICLE_KEYS)
    assert mcp_result["importance_score"] == 0.6
    assert mcp_result["importance_rationale"] == "mcp"
    assert mcp_result["importance_reporter"] == "agent"
    assert MCP_SERVER.get_importance(CLEAN_URL) == service.get_importance(CLEAN_URL)
    with pytest.raises(service.InvalidRequest):
        MCP_SERVER.set_importance(CLEAN_URL, 2.0)


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
    name = f"brain_importance_{os.getpid()}_{uuid.uuid4().hex[:8]}"
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
def test_live_importance_roundtrip_cap_and_visibility(scratch_db: str) -> None:
    import psycopg

    from marketing_intelligence import db as db_mod

    assert "009_importance" in db_mod.apply_migrations()
    assert db_mod.pending_migrations() == []

    with psycopg.connect(scratch_db) as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM sources ORDER BY name LIMIT 1")
        source_id = cur.fetchone()[0]
        for url, title, content, flag in (
            (CLEAN_URL, "Clean", "A clean body about marketing.", None),
            (FLAGGED_URL, "Flagged", "A thin body.", "thin"),
            (PAYWALLED_URL, "Metered", PAYWALL_BODY, None),
        ):
            cur.execute(
                "INSERT INTO documents (source_id, url, canonical_url, title, author,"
                " published_at, retrieved_at, language, content, content_hash, flag_reason)"
                " VALUES (%s, %s, %s, %s, 'A', now(), now(), 'en', %s, %s, %s)",
                (source_id, url, url, title, content, f"hash-{title}", flag),
            )

    first = service.set_importance(CLEAN_URL, 0.9, rationale="high", reporter="digest-agent")
    assert first["importance_score"] == 0.9
    assert first["importance_reporter"] == "digest-agent"
    second = service.set_importance(CLEAN_URL, 0.4, rationale="revised", reporter="digest-agent")
    assert second["importance_score"] == 0.4
    assert len(importance_lane.get_importance_history(CLEAN_URL)) == 2

    assert service.set_importance(FLAGGED_URL, 0.9)["importance_score"] == 0.3
    assert service.set_importance(PAYWALLED_URL, 0.95)["importance_score"] == 0.3

    hits = {r["url"]: r for r in service.search_articles("body")}
    assert hits[CLEAN_URL]["importance_score"] == 0.4
    assert hits[FLAGGED_URL]["importance_score"] == 0.3
    assert service.get_article(PAYWALLED_URL)["importance_score"] == 0.3

    bundle = period_lane.get_period_context(
        _dt.date(2026, 9, 1), _dt.date(2026, 9, 30), conn=psycopg.connect(scratch_db)
    )
    by_url = {a["url"]: a for a in bundle["recent_articles"]}
    assert by_url[CLEAN_URL]["importance_score"] == 0.4
    assert by_url[CLEAN_URL]["importance_rationale"] == "revised"

    # The per-source-capped variant keeps the importance columns too.
    capped = period_lane.get_period_context(
        _dt.date(2026, 9, 1),
        _dt.date(2026, 9, 30),
        per_source_limit=3,
        conn=psycopg.connect(scratch_db),
    )
    capped_by_url = {a["url"]: a for a in capped["recent_articles"]}
    assert capped_by_url[CLEAN_URL]["importance_score"] == 0.4
    assert capped_by_url[FLAGGED_URL]["importance_score"] == 0.3
