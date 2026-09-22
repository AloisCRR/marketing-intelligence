"""ADR-0016 Jev annotate lane: the flows and the post-ingest chain.

Observable behavior (not privates):

- an unknown Source returns the zeroed lane dict plus an explicit `error`,
  without touching the annotate module or the connection seam
- a Source run hands the pending Document ids of that Source to the lane and
  returns the lane dict unchanged (cost/token accounting included)
- a lane that cannot run (no DB, no vendor client) is contained as a
  per-source error, never an exception — so one broken Source can never turn a
  batch run red
- the batch flow annotates every Source in scope and keeps going when one of
  them blows up
- `ingest_sources_flow` nests exactly one `annotate` dict per Source that
  actually ingested new Documents; errored and empty Ingestion Runs keep their
  pre-ADR-0016 `{inserted, skipped[, error]}` shape with no `annotate` key
- `annotate=False` restores that pre-ADR-0016 batch shape byte for byte and
  never touches the lane seam
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from prefect_harness import no_engine

import marketing_intelligence.flows as flows
from marketing_intelligence.sources import catalog_names

FIXTURES = Path(__file__).parent / "fixtures"

SMT = "Social Media Today"
PJ = "Professional Jeweller"
INFOMONEY = "InfoMoney"
JCK = "JCK Online"
IG_SABRI = "ig:sabrikolod"
IG_JORDI = "ig:jordisanildefonso"
UNKNOWN = "No Such Source"

#: The lane's documented result key set (an `error` is additive on failures).
LANE_KEYS = {"annotated", "skipped", "causes", "input_tokens", "cost_usd"}


class _FakeConn:
    """Owned-connection stand-in: only the flow's commit/close teardown is used.

    `dead` makes both teardown calls raise, like a connection the server
    closed while the lane was running.
    """

    def __init__(self, *, dead: bool = False) -> None:
        self.commits = 0
        self.closes = 0
        self.dead = dead

    def commit(self) -> None:
        self.commits += 1
        if self.dead:
            raise RuntimeError("server closed the connection unexpectedly")

    def close(self) -> None:
        self.closes += 1
        if self.dead:
            raise RuntimeError("server closed the connection unexpectedly")


def _lane_result(annotated: int = 1) -> dict[str, Any]:
    """A plausible annotate-module result dict (shape is the module's contract)."""
    return {
        "annotated": annotated,
        "skipped": 0,
        "causes": [],
        "input_tokens": 1_234,
        "cost_usd": 0.00007,
    }


def _stub_annotate_module(
    monkeypatch: pytest.MonkeyPatch,
    *,
    pending: dict[str, list[str]] | None = None,
    classify: Any | None = None,
    dead_conn: bool = False,
) -> dict[str, Any]:
    """Stub the annotate seam + the connection seam; returns the recorded calls.

    `pending` maps a Source label to its pending Document ids (default: none);
    `classify` optionally replaces the writer. `dead_conn` hands back a
    connection whose commit/close raise. The recorded dict carries the fake
    connection plus the `pending_document_ids` call log.
    """
    conn = _FakeConn(dead=dead_conn)
    seen: dict[str, Any] = {"conn": conn, "pending": [], "classify": []}

    def fake_pending(c: Any, source_name: str | None = None) -> list[str]:
        seen["pending"].append((c, source_name))
        return (pending or {}).get(str(source_name), [])

    def fake_classify(ids: Any = None, source: Any = None, conn: Any = None) -> dict[str, Any]:
        seen["classify"].append((ids, conn))
        return _lane_result(len(ids or []))

    monkeypatch.setattr(flows.annotate, "pending_document_ids", fake_pending)
    monkeypatch.setattr(flows.annotate, "classify_and_write", classify or fake_classify)
    monkeypatch.setattr(flows, "get_connection", lambda: conn)
    return seen


def _feed_bytes() -> bytes:
    """The shared parseable fixture feed (3 Documents, every RSS fixture shape)."""
    return (FIXTURES / "smt_sample.xml").read_bytes()


def _stub_ingest(monkeypatch: pytest.MonkeyPatch, *, empty: frozenset[str] = frozenset()) -> None:
    """Fake the ingest lanes' network + DB seams (fixture feed, no writes).

    Every RSS source reads the same parseable fixture; `empty` names the
    sources whose upsert inserts nothing (a clean run with zero new Documents).
    """
    payload = _feed_bytes()

    def fake_upsert(docs: list[Any]) -> tuple[int, int]:
        if docs and docs[0].source in empty:
            return (0, len(docs))
        return (len(docs), 0)

    monkeypatch.setattr(flows, "fetch_rss", lambda url, timeout=30: payload)
    monkeypatch.setattr(flows, "enrich_document_or_keep", lambda doc, *a, **k: (doc, "rss", None))
    monkeypatch.setattr(flows, "upsert_documents", fake_upsert)
    monkeypatch.setattr(
        flows, "ingest_instagram_source", lambda name: {"inserted": 3, "skipped": 0}
    )
    monkeypatch.setattr(flows, "_ensure_source_row", lambda label: None)
    monkeypatch.setattr(flows, "record_ingestion_run", lambda *a, **k: None)


# --- annotate_source_flow -----------------------------------------------------


def test_flow_declarations_pin_the_lane_names() -> None:
    """The Prefect names ADR-0016 fixes (deployments and run logs ride them)."""
    assert flows.annotate_source_flow.name == "marketing-intelligence.annotate.annotate_source"
    assert flows.annotate_source_flow.flow_run_name == "annotate-{source_name}"
    assert flows.annotate_sources_flow.name == "marketing-intelligence.annotate.annotate_sources"
    assert flows.annotate_sources_flow.flow_run_name == "annotate-batch"


def test_unknown_source_returns_explicit_error_without_touching_the_lane(
    monkeypatch: pytest.MonkeyPatch, no_engine: None
) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the annotate lane must not run for an unknown source")

    monkeypatch.setattr(flows.annotate, "pending_document_ids", forbidden)
    monkeypatch.setattr(flows, "get_connection", forbidden)

    result = flows.annotate_source_flow(source_name=UNKNOWN)

    assert result["annotated"] == 0
    assert result["skipped"] == 0
    assert result["input_tokens"] == 0
    assert result["cost_usd"] == 0.0
    assert result["error"]
    assert set(result) == LANE_KEYS | {"error"}


def test_source_flow_passes_the_pending_ids_and_returns_the_lane_dict(
    monkeypatch: pytest.MonkeyPatch, no_engine: None
) -> None:
    lane = _lane_result(annotated=2)
    calls: list[tuple[Any, Any]] = []

    def classify(ids: Any = None, source: Any = None, conn: Any = None) -> dict[str, Any]:
        calls.append((ids, conn))
        return lane

    seen = _stub_annotate_module(
        monkeypatch, pending={INFOMONEY: ["doc-1", "doc-2"]}, classify=classify
    )

    result = flows.annotate_source_flow(source_name=INFOMONEY)

    assert result == lane  # cost/token/cause accounting survives untouched
    assert seen["pending"] == [(seen["conn"], INFOMONEY)]
    assert calls == [(["doc-1", "doc-2"], seen["conn"])]
    assert seen["conn"].commits == 1 and seen["conn"].closes == 1


def test_source_flow_contains_a_dead_connection_as_an_error(
    monkeypatch: pytest.MonkeyPatch, no_engine: None
) -> None:
    def no_db() -> Any:
        raise RuntimeError("could not connect to server: Connection refused")

    monkeypatch.setattr(flows, "get_connection", no_db)

    result = flows.annotate_source_flow(source_name=INFOMONEY)

    assert result["annotated"] == 0 and result["skipped"] == 0
    assert "Connection refused" in result["error"]
    assert result["causes"] and "Connection refused" in result["causes"][0]
    assert set(result) == LANE_KEYS | {"error"}


def test_source_flow_contains_a_vendor_failure_after_teardown(
    monkeypatch: pytest.MonkeyPatch, no_engine: None
) -> None:
    def exploding(ids: Any = None, source: Any = None, conn: Any = None) -> dict[str, Any]:
        raise RuntimeError("typesafe-sdk is not installed")

    seen = _stub_annotate_module(monkeypatch, pending={PJ: ["doc-1"]}, classify=exploding)

    result = flows.annotate_source_flow(source_name=PJ)

    assert result["annotated"] == 0
    assert "typesafe-sdk" in result["error"]
    assert "typesafe-sdk" in result["causes"][0]
    assert seen["conn"].commits == 1 and seen["conn"].closes == 1


def test_source_flow_never_lets_a_dead_teardown_out_raise_the_lane(
    monkeypatch: pytest.MonkeyPatch, no_engine: None
) -> None:
    """A connection dying at commit/close is swallowed: the lane outcome stands."""
    lane = _lane_result(annotated=2)
    seen = _stub_annotate_module(
        monkeypatch,
        pending={SMT: ["s-1", "s-2"]},
        classify=lambda ids=None, source=None, conn=None: lane,
        dead_conn=True,
    )

    result = flows.annotate_source_flow(source_name=SMT)

    assert result == lane  # a finished lane survives teardown, never an error
    assert seen["pending"] == [(seen["conn"], SMT)]

    def exploding(ids: Any = None, source: Any = None, conn: Any = None) -> dict[str, Any]:
        raise RuntimeError("vendor down")

    monkeypatch.setattr(flows.annotate, "classify_and_write", exploding)

    failed = flows.annotate_source_flow(source_name=SMT)

    assert failed["annotated"] == 0 and "vendor down" in failed["error"]
    assert set(failed) == LANE_KEYS | {"error"}
    # Both teardown attempts ran (close still attempted after commit blew up).
    assert seen["conn"].commits == 2 and seen["conn"].closes == 2


# --- annotate_sources_flow ----------------------------------------------------


def test_batch_flow_annotates_every_source_and_isolates_a_failure(
    monkeypatch: pytest.MonkeyPatch, no_engine: None
) -> None:
    pending = {SMT: ["s-1"], INFOMONEY: ["i-1", "i-2"]}
    seen = _stub_annotate_module(monkeypatch, pending=pending)
    reached: list[str] = []

    def failing_pending(c: Any, source_name: str | None = None) -> list[str]:
        reached.append(str(source_name))
        if source_name == PJ:
            raise RuntimeError("connection reset by peer")
        return pending.get(str(source_name), [])

    monkeypatch.setattr(flows.annotate, "pending_document_ids", failing_pending)

    results = flows.annotate_sources_flow([SMT, PJ, INFOMONEY])

    assert list(results) == [SMT, PJ, INFOMONEY]
    assert results[SMT]["annotated"] == 1
    assert results[INFOMONEY]["annotated"] == 2
    assert results[PJ]["annotated"] == 0 and "connection reset" in results[PJ]["error"]
    # The failure did not stop the run: the sibling after it was still annotated.
    assert reached == [SMT, PJ, INFOMONEY]
    assert seen["conn"].closes == 3


def test_batch_flow_defaults_to_the_full_catalog(
    monkeypatch: pytest.MonkeyPatch, no_engine: None
) -> None:
    seen = _stub_annotate_module(monkeypatch)

    results = flows.annotate_sources_flow()

    assert list(results) == list(catalog_names())
    assert [label for _conn, label in seen["pending"]] == list(catalog_names())


# --- the post-ingest chain ----------------------------------------------------


def test_annotate_false_keeps_the_exact_ingest_shape(
    monkeypatch: pytest.MonkeyPatch, no_engine: None
) -> None:
    _stub_ingest(monkeypatch)

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("annotate=False must not touch the lane")

    monkeypatch.setattr(flows.annotate, "pending_document_ids", forbidden)
    monkeypatch.setattr(flows, "get_connection", forbidden)

    results = flows.ingest_sources_flow(source_names=[SMT, INFOMONEY], annotate=False)

    assert results == {SMT: {"inserted": 3, "skipped": 0}, INFOMONEY: {"inserted": 3, "skipped": 0}}


def test_chain_is_on_by_default_and_nests_under_the_successful_source(
    monkeypatch: pytest.MonkeyPatch, no_engine: None
) -> None:
    _stub_ingest(monkeypatch)
    lane = _lane_result(annotated=3)
    seen = _stub_annotate_module(monkeypatch, pending={SMT: ["s-1", "s-2", "s-3"]})
    monkeypatch.setattr(
        flows.annotate,
        "classify_and_write",
        lambda ids=None, source=None, conn=None: lane,
    )

    results = flows.ingest_sources_flow(source_names=[SMT])

    assert results[SMT] == {"inserted": 3, "skipped": 0, "annotate": lane}
    assert seen["pending"] == [(seen["conn"], SMT)]


def test_chain_skips_failed_and_empty_sources(
    monkeypatch: pytest.MonkeyPatch, no_engine: None
) -> None:
    _stub_ingest(monkeypatch, empty=frozenset({INFOMONEY}))
    seen = _stub_annotate_module(monkeypatch, pending={SMT: ["s-1"]})

    def fake_fetch(url: str, timeout: int = 30) -> bytes:
        if "professionaljeweller" in url:
            raise RuntimeError("boom: HTTP Error 404")
        return _feed_bytes()

    monkeypatch.setattr(flows, "fetch_rss", fake_fetch)

    results = flows.ingest_sources_flow(source_names=[SMT, PJ, INFOMONEY])

    assert results[SMT]["annotate"]["annotated"] == 1
    assert "error" in results[PJ] and "annotate" not in results[PJ]
    assert results[INFOMONEY] == {"inserted": 0, "skipped": 3}
    # Only the Source that ingested new Documents paid for a vendor pass.
    assert seen["pending"] == [(seen["conn"], SMT)]


def test_chain_runs_after_every_chunk_of_the_batch(
    monkeypatch: pytest.MonkeyPatch, no_engine: None
) -> None:
    _stub_ingest(monkeypatch)
    # Six Sources across two chunks of `_BATCH_CHUNK` (4): the four RSS feeds
    # plus the two ADR-0013 instagram accounts (stubbed above).
    names = [SMT, PJ, INFOMONEY, JCK, IG_SABRI, IG_JORDI]
    seen = _stub_annotate_module(monkeypatch)

    results = flows.ingest_sources_flow(source_names=names)

    assert [label for _conn, label in seen["pending"]] == names
    for name in names:
        assert results[name]["inserted"] == 3, name
        assert "annotate" in results[name], name
