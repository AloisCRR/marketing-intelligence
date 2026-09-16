"""Image-text retrieval exposure (ADR-0014, Ticket 31) — fake conns, no live DB.

Covers the read side of the vision lane:

- the one-item read carries ``image_texts[]`` — every stored frame for the
  Document, ordered by ``frame_index`` — round-tripped through the storage
  lane's own write path, while ``content`` stays the untouched caption;
- a Document with no stored frames reports ``[]`` (never null, never missing);
- SEARCH and PERIOD items carry ``has_image_text: bool`` only (no frame text),
  and the frozen key sets of all three payloads grow by exactly one key each;
- HTTP routes and the MCP tools return identical payloads — the adapters are
  pass-through, so parity holds by construction.

The fakes mirror the real SQL shapes: the fake's ``has_image_text`` is derived
the way the SQL derives it (real text in at least one frame; the vision lane's
``NO_TEXT`` sentinel is not text).
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import importlib.util
import os
import subprocess
import sys
import uuid
from datetime import date
from pathlib import Path
from typing import Any

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(os.path.dirname(_HERE), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import marketing_intelligence.article as article_lane  # noqa: E402
import marketing_intelligence.image_text as image_text_lane  # noqa: E402
import marketing_intelligence.period as period_lane  # noqa: E402
import marketing_intelligence.search as search_lane  # noqa: E402
import marketing_intelligence.service as service  # noqa: E402
from api.app import app  # noqa: E402
from marketing_intelligence.normalize import NormalizedDocument  # noqa: E402


def _load_mcp_server() -> Any:
    path = Path(_SRC) / "mcp" / "server.py"
    spec = importlib.util.spec_from_file_location("trend_mcp_server_imgtxt", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MCP_SERVER = _load_mcp_server()


def _utc(*args: int) -> _dt.datetime:
    return _dt.datetime(*args, tzinfo=_dt.UTC)


DOC_ID = "8c1f1b7e-0000-4000-8000-000000000001"
URL = "https://www.instagram.com/p/Dc9PS-SkU6_/"
CANONICAL = "https://www.instagram.com/p/Dc9PS-SkU6_/"
TITLE = "jordisanildefonso — Monopoly promo"
AUTHOR = "Jordi San Ildefonso"
SOURCE = "jordisanildefonso"
CAPTION = "MONIdero x @mcdonalds: el accesorio coleccionable del Monopoly."
PUBLISHED = _utc(2026, 9, 8, 14, 30)
EXTRACTED = _utc(2026, 9, 16, 12, 0)

#: Frozen pre-existing key sets (the contract before ADR-0014). Each grows by
#: exactly one key; nothing is renamed, nothing is dropped.
PRE_ARTICLE_KEYS = {
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
    "read",
    "read_at",
    "read_by",
    "importance_score",
    "importance_rationale",
    "importance_reporter",
    "importance_updated_at",
    "topics",
}

PRE_LIST_KEYS = {
    "title",
    "url",
    "canonical_url",
    "source",
    "published_at",
    "author",
    "snippet",
    "flag_reason",
    "flag_detail",
    "flagged_at",
    "flagged_by",
    "read",
    "read_at",
    "read_by",
    "importance_score",
    "importance_rationale",
    "importance_reporter",
    "importance_updated_at",
    "topics",
}

PRE_PERIOD_KEYS = (PRE_LIST_KEYS - {"snippet"}) | {"rank"}


def _document() -> NormalizedDocument:
    return NormalizedDocument(
        source=SOURCE,
        url=URL,
        canonical_url=CANONICAL,
        title=TITLE,
        author=AUTHOR,
        published_at=PUBLISHED,
        retrieved_at=PUBLISHED,
        language="es",
        content=CAPTION,
        content_hash="0" * 64,
    )


class _Cursor:
    """Cursor fake routing the read lanes and the vision lane's write path."""

    def __init__(self, conn: _Conn) -> None:
        self._conn = conn
        self._rows: list[Any] = []
        self.rowcount = -1

    def execute(self, sql: str, params: Any = None) -> _Cursor:
        low = " ".join(sql.lower().split())
        head = " ".join(sql.upper().split())
        values = tuple(params or ())
        limit = values[-1] if values and isinstance(values[-1], int) else None
        if isinstance(limit, bool) or not isinstance(limit, int):
            limit = None
        if head.startswith("SELECT T.FRAME_INDEX"):
            self._rows = self._conn.frame_rows(values[0], values[1])
        elif "insert into document_image_texts" in low:
            self._conn.store_frame(values)
            self.rowcount = 1
            self._rows = []
        elif "select id from documents" in low:
            self._rows = [(DOC_ID,)] if self._conn.matches(values[0], values[1]) else []
        elif head.startswith("SELECT D.READ_AT"):
            # Read-state fetch: params (url_key, canonical_key).
            self._rows = [{"read_at": None, "read_by": None}]
        elif head.startswith("SELECT DT.TOPIC_SLUG"):
            # Effective-topic fetch: this Document carries no topics.
            self._rows = []
        elif "ilike" in low:
            self._rows = [self._conn.search_row()][:limit]
        elif "d.published_at >= %s" in low:
            self._rows = [self._conn.period_row()][:limit]
        elif head.startswith("SELECT D.TITLE"):
            self._rows = [self._conn.article_row()]
        else:  # pragma: no cover - guards the fake against silent drift
            raise AssertionError(f"unexpected SQL: {sql}")
        return self

    def fetchall(self) -> list[Any]:
        return list(self._rows)

    def fetchone(self) -> Any | None:
        return self._rows[0] if self._rows else None

    def close(self) -> None:
        pass


class _Conn:
    """One Document plus its frame texts; serves both lanes' query shapes."""

    def __init__(self) -> None:
        self.frames: dict[tuple[str, int], tuple[str, str, _dt.datetime]] = {}
        self.committed = 0

    def cursor(self) -> _Cursor:
        return _Cursor(self)

    def execute(self, sql: str, params: Any = None) -> _Cursor:
        return self.cursor().execute(sql, params)

    def commit(self) -> None:
        self.committed += 1

    # --- store -----------------------------------------------------------------

    def matches(self, url: str | None, canonical: str | None) -> bool:
        return url in (URL, CANONICAL) or canonical in (URL, CANONICAL)

    def store_frame(self, params: tuple) -> None:
        document_id, frame_index, text, model = params
        self.frames[(document_id, frame_index)] = (text, model, EXTRACTED)

    # --- row shapes ------------------------------------------------------------

    def _has_text(self) -> bool:
        return any(
            text != image_text_lane.NO_TEXT
            for (document_id, _), (text, _, _) in self.frames.items()
            if document_id == DOC_ID
        )

    def frame_rows(self, url: str | None, canonical: str | None) -> list[tuple]:
        if not self.matches(url, canonical):
            return []
        # Insertion order, not frame order: like a connection that serves rows
        # as stored, so the lane's own ordering is what the payload reflects.
        return [
            (index, text, model, extracted)
            for (document_id, index), (text, model, extracted) in self.frames.items()
            if document_id == DOC_ID
        ]

    def article_row(self) -> tuple:
        return (
            TITLE,
            URL,
            CANONICAL,
            SOURCE,
            PUBLISHED,
            AUTHOR,
            CAPTION,
            None,
            None,
            None,
            None,
        )

    def _flag_read_importance(self) -> list[Any]:
        # flag_reason/detail/at/by, read_at/read_by, importance score/rationale/
        # reporter/updated_at — the annotation columns every unannotated row
        # carries as NULL.
        return [None] * 10

    def search_row(self) -> tuple:
        return (
            TITLE,
            URL,
            CANONICAL,
            SOURCE,
            PUBLISHED,
            AUTHOR,
            CAPTION,
            *self._flag_read_importance(),
            None,  # topics: unannotated
            self._has_text(),
        )

    def period_row(self) -> tuple:
        return (
            TITLE,
            URL,
            CANONICAL,
            SOURCE,
            PUBLISHED,
            AUTHOR,
            *self._flag_read_importance(),
            None,  # topics: unannotated
            CAPTION,  # raw body, stripped from the payload
            self._has_text(),
        )


@pytest.fixture()
def stored_conn() -> _Conn:
    """A connection holding the Document and three written frames."""
    conn = _Conn()
    pairs = [(0, "MONIdero"), (1, "MONIdero"), (2, "1. Compra un producto")]
    assert image_text_lane.write_document_image_texts([(_document(), pairs)], conn=conn) == (
        3,
        0,
    )
    return conn


# --- one-item read: write -> read round trip -----------------------------------


def test_written_frames_read_back_ordered_and_captioned() -> None:
    conn = _Conn()
    # Written in the order the vision stage produced them, not frame order.
    assert image_text_lane.write_document_image_texts(
        [(_document(), [(2, "tercero"), (0, "portada"), (1, "segundo")])], conn=conn
    ) == (3, 0)

    article = article_lane.get_article(URL, conn=conn)

    assert set(article) == PRE_ARTICLE_KEYS | {"image_texts"}
    assert article["image_texts"] == [
        {
            "frame_index": 0,
            "image_text": "portada",
            "model": image_text_lane.MODEL_ID,
            "extracted_at": EXTRACTED.isoformat(),
        },
        {
            "frame_index": 1,
            "image_text": "segundo",
            "model": image_text_lane.MODEL_ID,
            "extracted_at": EXTRACTED.isoformat(),
        },
        {
            "frame_index": 2,
            "image_text": "tercero",
            "model": image_text_lane.MODEL_ID,
            "extracted_at": EXTRACTED.isoformat(),
        },
    ]
    # The caption is the Document's content, byte-identical: image text is
    # exposed beside it, never merged into it.
    assert article["content"] == CAPTION
    # Canonical-URL identifiers resolve to the same Document and frames.
    assert article_lane.get_article(CANONICAL, conn=conn)["image_texts"] == article["image_texts"]


def test_document_without_frames_reports_empty_list() -> None:
    conn = _Conn()

    article = article_lane.get_article(URL, conn=conn)

    assert article["image_texts"] == []
    assert article["content"] == CAPTION


def test_no_text_sentinel_frames_are_stored_but_not_image_text() -> None:
    conn = _Conn()
    image_text_lane.write_document_image_texts(
        [(_document(), [(0, image_text_lane.NO_TEXT), (1, image_text_lane.NO_TEXT)])],
        conn=conn,
    )

    article = article_lane.get_article(URL, conn=conn)

    # The frames are evidence and come back as stored...
    assert [item["image_text"] for item in article["image_texts"]] == [
        image_text_lane.NO_TEXT,
        image_text_lane.NO_TEXT,
    ]
    # ...but a post with nothing to read does not claim image text.
    assert search_lane.search_articles("MONIdero", conn=conn)[0]["has_image_text"] is False
    bundle = period_lane.get_period_context(date(2026, 9, 1), date(2026, 9, 30), conn=conn)
    assert bundle["recent_articles"][0]["has_image_text"] is False


# --- list payloads: presence only ----------------------------------------------


def test_search_item_carries_presence_only(stored_conn: _Conn) -> None:
    results = search_lane.search_articles("MONIdero", conn=stored_conn)

    assert set(results[0]) == PRE_LIST_KEYS | {"has_image_text"}
    assert results[0]["has_image_text"] is True
    assert "image_texts" not in results[0]  # no per-frame text in list payloads
    assert "content" not in results[0]


def test_period_item_carries_presence_only(stored_conn: _Conn) -> None:
    bundle = period_lane.get_period_context(date(2026, 9, 1), date(2026, 9, 30), conn=stored_conn)
    item = bundle["recent_articles"][0]

    assert set(item) == PRE_PERIOD_KEYS | {"has_image_text"}
    assert item["has_image_text"] is True
    assert "image_texts" not in item


def test_service_key_sets_grow_by_exactly_one_key() -> None:
    assert set(service.ARTICLE_KEYS) == PRE_ARTICLE_KEYS | {"image_texts"}
    assert set(service.SEARCH_RESULT_KEYS) == PRE_LIST_KEYS | {"has_image_text"}
    assert set(service.PERIOD_ARTICLE_KEYS) == PRE_PERIOD_KEYS | {"has_image_text"}
    # Order is the payload order: the new keys are appended, never interleaved.
    assert service.ARTICLE_KEYS[-1] == "image_texts"
    assert service.SEARCH_RESULT_KEYS[-1] == "has_image_text"
    assert service.PERIOD_ARTICLE_KEYS[-1] == "has_image_text"


# --- HTTP == MCP ---------------------------------------------------------------


def _unwrap_call_tool(out: Any) -> Any:
    """Normalise FastMCP.call_tool's (content, result-dict) return shape."""
    if isinstance(out, tuple) and len(out) == 2 and isinstance(out[1], dict):
        return out[1].get("result", out[1])
    return out


@pytest.fixture()
def retrieval_conns(stored_conn: _Conn, monkeypatch: pytest.MonkeyPatch) -> _Conn:
    """Point all three read lanes at the same fake Document."""
    monkeypatch.setattr(article_lane, "get_connection", lambda: stored_conn)
    monkeypatch.setattr(search_lane, "get_connection", lambda: stored_conn)
    monkeypatch.setattr(period_lane, "get_connection", lambda: stored_conn)
    return stored_conn


def test_article_payload_identical_over_http_and_mcp(retrieval_conns: _Conn) -> None:
    expected = service.get_article(URL)
    assert [item["frame_index"] for item in expected["image_texts"]] == [0, 1, 2]

    http_payload = TestClient(app).get("/article", params={"url": URL}).json()
    assert http_payload == expected

    assert MCP_SERVER.get_article(identifier=URL) == expected
    out = asyncio.run(MCP_SERVER.mcp.call_tool("get_article", {"identifier": URL}))
    assert _unwrap_call_tool(out) == expected
    assert http_payload == _unwrap_call_tool(out)


def test_list_payloads_identical_over_http_and_mcp(retrieval_conns: _Conn) -> None:
    client = TestClient(app)

    search_payload = client.get("/search", params={"q": "MONIdero"}).json()
    assert search_payload == {"results": service.search_articles("MONIdero")}
    assert search_payload["results"][0]["has_image_text"] is True
    assert MCP_SERVER.search_articles(keyword="MONIdero") == search_payload["results"]
    out = asyncio.run(MCP_SERVER.mcp.call_tool("search_articles", {"keyword": "MONIdero"}))
    assert _unwrap_call_tool(out) == search_payload["results"]

    period_payload = client.post(
        "/period-context", json={"from_date": "2026-09-01", "to_date": "2026-09-30"}
    ).json()
    expected_bundle = period_lane.get_period_context(date(2026, 9, 1), date(2026, 9, 30))
    assert period_payload == expected_bundle
    assert period_payload["recent_articles"][0]["has_image_text"] is True
    assert (
        MCP_SERVER.get_period_context(from_date="2026-09-01", to_date="2026-09-30")
        == expected_bundle
    )
    out = asyncio.run(
        MCP_SERVER.mcp.call_tool(
            "get_period_context", {"from_date": "2026-09-01", "to_date": "2026-09-30"}
        )
    )
    assert _unwrap_call_tool(out) == expected_bundle


# --- sentinel coupling ---------------------------------------------------------


def test_list_lanes_exclude_the_vision_sentinel() -> None:
    """The presence predicate must exclude `image_text.NO_TEXT`, not merely count rows.

    Both list lanes inline the sentinel as a SQL literal — the reading lanes are
    the stdlib-only adapter's imports and must not drag the vision module's httpx
    transport in behind them — so this pins each rendered predicate to the
    sentinel value `image_text` actually writes. A sentinel change that stops
    these predicates matching real text fails here, not silently at read time.
    """
    assert f"<> '{image_text_lane.NO_TEXT}'" in search_lane._SEARCH_SELECT
    assert f"<> '{image_text_lane.NO_TEXT}'" in period_lane._PERIOD_SELECT


def test_list_lanes_do_not_import_the_vision_transport() -> None:
    """`search` and `period` must not transitively import the vision lane's httpx.

    Imported in a fresh interpreter: this module already loads the vision lane,
    so an in-process `sys.modules` check would be vacuous.
    """
    probe = (
        "import sys\n"
        "import marketing_intelligence.period\n"
        "import marketing_intelligence.search\n"
        "print('httpx' in sys.modules)\n"
    )
    path = os.pathsep.join(filter(None, (_SRC, os.environ.get("PYTHONPATH"))))
    out = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": path},
        check=True,
    )
    assert out.stdout.strip() == "False", out.stderr


# --- live Postgres: the real SQL -------------------------------------------------


@pytest.fixture()
def image_scratch_db(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Isolated scratch DB with the real migrations applied (skips without PG)."""
    import psycopg

    from marketing_intelligence.config import get_database_url

    url = os.environ.get("DATABASE_URL", get_database_url())
    try:
        probe = psycopg.connect(url, connect_timeout=2)
        probe.close()
    except Exception:
        pytest.skip("no live Postgres reachable")
    base, _, _ = url.rpartition("/")
    name = f"brain_image_text_{os.getpid()}_{uuid.uuid4().hex[:8]}"
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


LIVE_TEXT_URL = "https://www.instagram.com/p/live-text/"
LIVE_PHOTO_URL = "https://www.instagram.com/p/live-photo/"
LIVE_PLAIN_URL = "https://www.instagram.com/p/live-plain/"
LIVE_CAPTION = "MONIdero x @mcdonalds (caption only)."
#: (url, title suffix, published_at) — distinct stamps, so recency order is
#: deterministic: plain (newest), photo, text (oldest).
LIVE_DOCS = (
    (LIVE_TEXT_URL, "carousel with text", _utc(2026, 9, 8, 14, 30)),
    (LIVE_PHOTO_URL, "carousel photo only", _utc(2026, 9, 8, 15, 0)),
    (LIVE_PLAIN_URL, "carousel with no frames", _utc(2026, 9, 8, 16, 0)),
)


def _live_document(url: str) -> NormalizedDocument:
    return NormalizedDocument(
        source=SOURCE,
        url=url,
        canonical_url=url,
        title=TITLE,
        author=AUTHOR,
        published_at=PUBLISHED,
        retrieved_at=PUBLISHED,
        language="es",
        content=LIVE_CAPTION,
        content_hash=f"hash-{url}",
    )


@pytest.mark.live_db
def test_live_frames_and_presence_over_real_sql(image_scratch_db: str) -> None:
    """The real SELECTs: frame fetch, sentinel guard, and both list lanes."""
    import psycopg

    from marketing_intelligence import db as db_mod

    assert "013_document_image_texts" in db_mod.apply_migrations()
    assert db_mod.pending_migrations() == []

    with psycopg.connect(image_scratch_db) as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM sources ORDER BY name LIMIT 1")
        source_id = cur.fetchone()[0]
        for url, suffix, published_at in LIVE_DOCS:
            cur.execute(
                "INSERT INTO documents (source_id, url, canonical_url, title, author,"
                " published_at, retrieved_at, language, content, content_hash)"
                " VALUES (%s, %s, %s, %s, 'A', %s, now(), 'en', %s, %s)",
                (
                    source_id,
                    url,
                    url,
                    f"Monopoly promo {suffix}",
                    published_at,
                    LIVE_CAPTION,
                    f"hash-{url}",
                ),
            )

    with psycopg.connect(image_scratch_db) as conn:
        # Frames produced out of order, plus a text-free post whose only frame
        # holds the vision lane's sentinel.
        assert image_text_lane.write_document_image_texts(
            [
                (_live_document(LIVE_TEXT_URL), [(2, "3. Canjéalo"), (0, "MONIdero"), (1, "2.")]),
                (_live_document(LIVE_PHOTO_URL), [(0, image_text_lane.NO_TEXT)]),
            ],
            conn=conn,
        ) == (4, 0)

    article = service.get_article(LIVE_TEXT_URL)
    assert [item["frame_index"] for item in article["image_texts"]] == [0, 1, 2]
    texts = [item["image_text"] for item in article["image_texts"]]
    assert texts == ["MONIdero", "2.", "3. Canjéalo"]
    assert {item["model"] for item in article["image_texts"]} == {image_text_lane.MODEL_ID}
    for item in article["image_texts"]:
        assert _dt.datetime.fromisoformat(item["extracted_at"]).tzinfo is not None
    # Caption untouched: image text lives beside it, never inside it.
    assert article["content"] == LIVE_CAPTION
    assert set(article) == PRE_ARTICLE_KEYS | {"image_texts"}

    photo = service.get_article(LIVE_PHOTO_URL)
    assert [item["image_text"] for item in photo["image_texts"]] == [image_text_lane.NO_TEXT]
    assert service.get_article(LIVE_PLAIN_URL)["image_texts"] == []

    def presence(rows: list[dict[str, Any]]) -> dict[str, bool]:
        return {row["url"]: row["has_image_text"] for row in rows}

    expected_presence = {
        LIVE_TEXT_URL: True,
        LIVE_PHOTO_URL: False,
        LIVE_PLAIN_URL: False,
    }
    recency_order = [LIVE_PLAIN_URL, LIVE_PHOTO_URL, LIVE_TEXT_URL]
    assert presence(service.search_articles("Monopoly promo")) == expected_presence
    window = (date(2026, 9, 1), date(2026, 9, 30))
    flat = period_lane.get_period_context(*window)["recent_articles"]
    assert presence(flat) == expected_presence
    assert [a["url"] for a in flat] == recency_order
    # The per-source-capped variant runs a different SELECT shape (ranked
    # subquery + explicit outer column list): the flag survives it and the
    # recency order is unchanged.
    capped = period_lane.get_period_context(*window, per_source_limit=3)
    assert presence(capped["recent_articles"]) == expected_presence
    assert [a["url"] for a in capped["recent_articles"]] == recency_order
    # A cap tighter than the bundle really caps: one source, one slot.
    tight = period_lane.get_period_context(*window, per_source_limit=1)
    assert presence(tight["recent_articles"]) == {LIVE_PLAIN_URL: False}

    # Annotation filters still compose, and the flag rides along.
    assert service.set_importance(LIVE_TEXT_URL, 0.9)["importance_score"] == 0.9
    floored = service.search_articles("Monopoly promo", min_importance=0.5)
    assert presence(floored) == {LIVE_TEXT_URL: True}
    assert all("image_texts" not in row for row in floored)
