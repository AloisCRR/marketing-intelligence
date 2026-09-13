"""Instagram premium lane tests (ADR-0013, issue 03).

Fake-only: the Apify transport is monkeypatched, the DB is an ON
CONFLICT-aware fake connection. No network, no Postgres.

Pinned observable behavior:
- identity: canonical ``/p/<code>/`` URL (reels normalized), deterministic
  title/hash; caption → content, handle → author, ISO timestamp →
  published_at, language `es`
- pointer inputs: bootstrap = limit 10 with no date key; steady = limit 15
  with ``onlyPostsNewerThan = pointer - 60s``; charge envelope + `basicData`
  on every input
- overlap/bleed re-emits dedupe via the documents UNIQUE constraint
  (inserted/skipped), and the whole run is rerunnable
- ingest-all: every billed post is upserted (the hashtag stanza never gates
  ingest — it is a query-time hint); explicit failure shape; missing token raises
- the flow's instagram branch never reaches enrichment
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from prefect_harness import no_engine

import marketing_intelligence.flows as flows
import marketing_intelligence.ingest as ingest
import marketing_intelligence.instagram as instagram

SOURCE = "ig:sabrikolod"


class _FakeCursor:
    """Pointer SELECT + ON CONFLICT DO NOTHING upsert over an in-memory store."""

    def __init__(self, conn: FakeConnection) -> None:
        self._conn = conn
        self.rowcount = 0
        self._row: tuple | None = None

    def execute(self, sql: str, params: tuple | None = None) -> _FakeCursor:
        upper = sql.lstrip().upper()
        if upper.startswith("SELECT MAX"):
            self._row = (max(self._conn.published) if self._conn.published else None,)
            self.rowcount = 1
            return self
        if upper.startswith("SELECT"):
            self._row = (1,)
            self.rowcount = 1
            return self
        assert params is not None
        content_hash = params[-1]
        if content_hash in self._conn.hashes:
            self.rowcount = 0
        else:
            self._conn.hashes.add(content_hash)
            self._conn.published.append(params[5])
            self.rowcount = 1
        return self

    def fetchone(self) -> tuple | None:
        return self._row

    def close(self) -> None:
        pass


class FakeConnection:
    def __init__(self) -> None:
        self.hashes: set[str] = set()
        self.published: list[datetime] = []
        self.commits = 0

    def execute(self, sql: str, params: tuple | None = None) -> _FakeCursor:
        return _FakeCursor(self).execute(sql, params)

    def commit(self) -> None:
        self.commits += 1


def _post(code: str, caption: str, ts: str, handle: str = "sabrikolod") -> dict[str, Any]:
    return {
        "url": f"https://www.instagram.com/reel/{code}/?igsh=tracker",
        "shortCode": code,
        "ownerUsername": handle,
        "caption": caption,
        "timestamp": ts,
        "hashtags": [],
    }


def _bootstrap_posts() -> list[dict[str, Any]]:
    return [
        _post(f"BST{i:02d}", f"Capitulo {i} del chismecito", f"2026-09-{i + 1:02d}T12:00:00.000Z")
        for i in range(10)
    ]


def _steady_dataset(bootstrap: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Overlap (newest post re-emitted) + three old "filter bleed" posts.
    return [bootstrap[9], bootstrap[8], bootstrap[7], bootstrap[5]]


# --- identity -----------------------------------------------------------------


def test_canonical_url_normalizes_reel_tracking_and_case() -> None:
    assert (
        instagram.canonical_instagram_url("https://www.instagram.com/reel/ABC123/?igsh=x#frag")
        == "https://www.instagram.com/p/ABC123/"
    )
    assert (
        instagram.canonical_instagram_url("https://WWW.Instagram.com/p/ABC123")
        == "https://www.instagram.com/p/ABC123/"
    )
    assert (
        instagram.canonical_instagram_url("https://www.instagram.com/reel/ABC123")
        == "https://www.instagram.com/p/ABC123/"
    )


def test_title_rule_first_line_else_handle_shortcode() -> None:
    caption = "\n  \n  Primera   linea del caption  \nsegunda linea"
    assert instagram.instagram_title_for(caption, "sabrikolod", "ABC123") == (
        "Primera linea del caption"
    )
    assert instagram.instagram_title_for("", "sabrikolod", "ABC123") == "@sabrikolod — ABC123"
    assert instagram.instagram_title_for(None, "sabrikolod", "ABC123") == "@sabrikolod — ABC123"


def test_map_post_is_deterministic_and_maps_the_contract() -> None:
    post = _post("DdOz587EXSx", "  Linea uno\nlinea dos  ", "2026-09-01T12:00:00.000Z")
    first = instagram.map_post_to_document(post)
    second = instagram.map_post_to_document(post)
    assert first.url == "https://www.instagram.com/p/DdOz587EXSx/"
    assert first.canonical_url == first.url
    assert first.title == "Linea uno"
    assert first.author == "sabrikolod"
    assert first.content == "Linea uno\nlinea dos"
    assert first.language == "es"
    assert first.published_at == datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    assert first.content_hash == second.content_hash
    assert first.title == second.title


def test_map_post_keeps_angle_brackets_in_caption() -> None:
    doc = instagram.map_post_to_document(
        _post("ABC123", "Te quiero <3 mucho", "2026-09-01T12:00:00.000Z")
    )
    assert doc.content == "Te quiero <3 mucho"


# --- pointer inputs -----------------------------------------------------------


def test_bootstrap_input_has_limit_10_without_date_filter() -> None:
    actor_input = instagram.build_actor_input("sabrikolod", None)
    assert instagram.BOOTSTRAP_LIMIT == 10
    assert actor_input["username"] == ["sabrikolod"]
    assert actor_input["resultsLimit"] == 10
    assert "onlyPostsNewerThan" not in actor_input
    assert actor_input["dataDetailLevel"] == "basicData"
    assert actor_input["skipPinnedPosts"] is False
    assert actor_input["maxTotalChargeUsd"] == instagram.MAX_CHARGE_USD


def test_steady_input_has_limit_15_and_60s_overlap() -> None:
    pointer = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
    actor_input = instagram.build_actor_input("sabrikolod", pointer)
    assert instagram.STEADY_LIMIT == 15
    assert instagram.OVERLAP_SECONDS == 60
    assert actor_input["resultsLimit"] == 15
    assert actor_input["onlyPostsNewerThan"] == "2026-09-10T11:59:00Z"
    assert actor_input["dataDetailLevel"] == "basicData"
    assert actor_input["maxTotalChargeUsd"] == instagram.MAX_CHARGE_USD


# --- transport ----------------------------------------------------------------


def test_run_actor_requires_token_at_call_time(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APIFY_API_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="APIFY_API_TOKEN"):
        instagram.run_actor({"username": ["sabrikolod"]})


def test_run_actor_sends_bearer_token_and_charge_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APIFY_API_TOKEN", "secret-token")
    captured: dict[str, Any] = {}

    class _Response:
        def raise_for_status(self) -> None:
            captured["raised"] = True

        def json(self) -> list[dict[str, Any]]:
            return [{"shortCode": "ABC123"}]

    def fake_post(url: str, **kwargs: Any) -> _Response:
        captured["url"] = url
        captured["kwargs"] = kwargs
        return _Response()

    monkeypatch.setattr(instagram.httpx, "post", fake_post)
    items = instagram.run_actor(
        {
            "username": ["sabrikolod"],
            "resultsLimit": 10,
            "maxTotalChargeUsd": instagram.MAX_CHARGE_USD,
        }
    )
    assert items == [{"shortCode": "ABC123"}]
    assert captured["url"].endswith("/acts/apify~instagram-post-scraper/run-sync-get-dataset-items")
    kwargs = captured["kwargs"]
    assert kwargs["headers"]["Authorization"] == "Bearer secret-token"
    assert kwargs["params"] == {"maxTotalChargeUsd": instagram.MAX_CHARGE_USD}
    assert "maxTotalChargeUsd" not in kwargs["json"]
    assert kwargs["json"]["username"] == ["sabrikolod"]
    assert "secret-token" not in str(kwargs["params"])


def test_run_actor_rejects_non_list_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APIFY_API_TOKEN", "secret-token")

    class _Response:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict[str, Any]:
            return {"error": "BLOCKED"}

    monkeypatch.setattr(instagram.httpx, "post", lambda url, **kwargs: _Response())
    with pytest.raises(RuntimeError, match="non-list"):
        instagram.run_actor({"username": ["sabrikolod"]})


# --- fetch --------------------------------------------------------------------


def test_fetch_ingests_all_posts_regardless_of_hashtag_stanza(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Never drop billed data: even posts without the tag are returned for
    # upsert + payload write; filtering happens at query time.
    posts = [
        {
            "shortCode": "A1",
            "hashtags": ["ChismecitoMarketinero"],
            "caption": "resumen semanal",
        },
        {"shortCode": "A2", "hashtags": [], "caption": "otro #chismecitomarketinero aqui"},
        {"shortCode": "A3", "hashtags": ["Marketing"], "caption": "nada que ver"},
    ]
    monkeypatch.setattr(
        instagram,
        "get_retrieval_config",
        lambda label: {"username": "sabrikolod", "hashtag_filter": "ChismecitoMarketinero"},
    )
    monkeypatch.setattr(instagram, "run_actor", lambda actor_input: posts)
    kept, raw_count = instagram.fetch_instagram_posts(SOURCE, conn=FakeConnection())
    assert raw_count == 3
    assert kept == posts


def test_fetch_passes_all_posts_without_hashtag_stanza(monkeypatch: pytest.MonkeyPatch) -> None:
    posts = [{"shortCode": "A1"}, {"shortCode": "A2"}]
    monkeypatch.setattr(
        instagram,
        "get_retrieval_config",
        lambda label: {"username": "sabrikolod", "hashtag_filter": None},
    )
    monkeypatch.setattr(instagram, "run_actor", lambda actor_input: posts)
    kept, raw_count = instagram.fetch_instagram_posts(SOURCE, conn=FakeConnection())
    assert raw_count == 2
    assert kept == posts


# --- ingest convergence -------------------------------------------------------


def test_ingest_bootstrap_then_steady_dedupes_overlap_and_bleed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = FakeConnection()
    bootstrap = _bootstrap_posts()
    datasets = iter([bootstrap, _steady_dataset(bootstrap)])
    inputs: list[dict[str, Any]] = []
    payload_writes: list[list[tuple[Any, dict[str, Any]]]] = []

    monkeypatch.setattr(instagram, "get_connection", lambda: conn)
    monkeypatch.setattr(ingest, "get_connection", lambda: conn)
    monkeypatch.setattr(
        instagram,
        "get_retrieval_config",
        lambda label: {"username": "sabrikolod", "hashtag_filter": None},
    )

    def fake_run_actor(actor_input: dict[str, Any]) -> list[dict[str, Any]]:
        inputs.append(dict(actor_input))
        return next(datasets)

    def fake_write_payloads(pairs: list[tuple[Any, dict[str, Any]]], conn: Any = None) -> tuple:
        payload_writes.append(list(pairs))
        return (len(pairs), 0)

    monkeypatch.setattr(instagram, "run_actor", fake_run_actor)
    monkeypatch.setattr(instagram, "write_document_payloads", fake_write_payloads)

    first = instagram.ingest_instagram_source(SOURCE)
    assert first == {"inserted": 10, "skipped": 0}
    assert inputs[0]["resultsLimit"] == 10
    assert "onlyPostsNewerThan" not in inputs[0]
    # Upsert precedes the payload write, one pair per stored post.
    assert len(payload_writes[0]) == 10
    doc, post = payload_writes[0][0]
    assert post["shortCode"] == "BST00"
    assert doc.url == "https://www.instagram.com/p/BST00/"

    pointer = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
    assert instagram.pointer_for_source(SOURCE, conn=conn) == pointer

    second = instagram.ingest_instagram_source(SOURCE)
    assert second == {"inserted": 0, "skipped": 4}
    assert inputs[1]["resultsLimit"] == 15
    assert inputs[1]["onlyPostsNewerThan"] == "2026-09-10T11:59:00Z"
    assert len(payload_writes[1]) == 4


def test_ingest_returns_explicit_error_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(instagram, "get_connection", lambda: FakeConnection())
    monkeypatch.setattr(instagram, "get_retrieval_config", lambda label: {"username": "sabrikolod"})

    def boom(actor_input: dict[str, Any]) -> list[dict[str, Any]]:
        raise RuntimeError("APIFY_API_TOKEN is not set")

    monkeypatch.setattr(instagram, "run_actor", boom)
    result = instagram.ingest_instagram_source(SOURCE)
    assert result["inserted"] == 0
    assert result["skipped"] == 0
    assert "APIFY_API_TOKEN" in result["error"]


# --- flow wiring --------------------------------------------------------------


def _stub_flow(monkeypatch: pytest.MonkeyPatch, helper_result: dict[str, Any]) -> list[tuple]:
    """Wire the flow to the instagram branch with enrichment hard-guarded."""
    monkeypatch.setattr(flows, "_ensure_source_row", lambda label: None)
    monkeypatch.setattr(
        flows, "get_source", lambda name: {"name": "ig:sabrikolod", "language": "es"}
    )
    monkeypatch.setattr(
        flows, "get_retrieval_config", lambda name: {"type": "instagram", "username": "sabrikolod"}
    )
    monkeypatch.setattr(flows, "ingest_instagram_source", lambda label: dict(helper_result))

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("instagram lane must bypass enrichment and the upsert task")

    monkeypatch.setattr(flows, "_enrich_docs", forbidden)
    monkeypatch.setattr(flows, "upsert_documents", forbidden)
    documented: list[tuple] = []
    monkeypatch.setattr(
        flows,
        "record_ingestion_run",
        lambda source, result, **kw: documented.append((source, dict(result))),
    )
    return documented


def test_flow_instagram_lane_bypasses_enrichment(
    monkeypatch: pytest.MonkeyPatch, no_engine: None
) -> None:
    documented = _stub_flow(monkeypatch, {"inserted": 10, "skipped": 0})
    result = flows.ingest_source_flow(source_name=SOURCE)
    assert result == {"inserted": 10, "skipped": 0}
    assert documented == [(SOURCE, {"inserted": 10, "skipped": 0})]


def test_flow_instagram_lane_returns_explicit_error(
    monkeypatch: pytest.MonkeyPatch, no_engine: None
) -> None:
    documented = _stub_flow(
        monkeypatch, {"inserted": 0, "skipped": 0, "error": "APIFY_API_TOKEN is not set"}
    )
    result = flows.ingest_source_flow(source_name=SOURCE)
    assert result["inserted"] == 0
    assert result["skipped"] == 0
    assert "APIFY_API_TOKEN" in result["error"]
    assert documented == [(SOURCE, dict(result))]
