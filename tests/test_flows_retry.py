"""Prefect retry + isolation tests (lib-2 retry refactor).

Observable behavior (not privates):
- fetch_task declares retries=3 with backoff [2, 5, 15]
- transient fetch blips self-heal via task retries
- persistent fetch failure exhausts retries and raises (red task)
- ingest_source_flow propagates stage failures (no catch around task calls)
- ingest_sources_flow isolates a failed subflow via return_state, records it,
  and keeps the {inserted, skipped[, error]} per-source shape
- unknown-source validation still returns an explicit error dict
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from prefect_harness import no_engine

import marketing_intelligence.flows as flows
from marketing_intelligence.flows import fetch_task
from marketing_intelligence.ingest import upsert_documents
from marketing_intelligence.normalize import NormalizedDocument
from marketing_intelligence.sources import get_source

FIXTURES = Path(__file__).parent / "fixtures"

MARTECH = "MarTech"
SMT = "Social Media Today"
PJ = "Professional Jeweller"
INFOMONEY = "InfoMoney"


class _FakeCursor:
    def __init__(self, store: dict[str, tuple]) -> None:
        self._store = store
        self.rowcount: int = 0
        self._row: tuple | None = None

    def execute(self, sql: str, params: tuple | None = None) -> _FakeCursor:
        assert params is not None
        if sql.lstrip().upper().startswith("SELECT"):
            self._row = (1,)
            self.rowcount = 1
            return self
        url, content_hash = params[1], params[-1]
        if url in self._store or content_hash in {p[-1] for p in self._store.values()}:
            self.rowcount = 0
        else:
            self._store[url] = params
            self.rowcount = 1
        return self

    def fetchone(self) -> tuple | None:
        return self._row

    def close(self) -> None:
        pass


class FakeConnection:
    def __init__(self) -> None:
        self.store: dict[str, tuple] = {}
        self.commits = 0

    def execute(self, sql: str, params: tuple | None = None) -> _FakeCursor:
        return _FakeCursor(self.store).execute(sql, params)

    def commit(self) -> None:
        self.commits += 1


def _stub_enrich_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(flows, "enrich_document_or_keep", lambda doc, *a, **k: (doc, "rss", None))


def _fixture_slug(name: str) -> str:
    return {
        "MarTech": "martech_sample",
        "Social Media Today": "smt_sample",
        "Professional Jeweller": "pj_sample",
        "InfoMoney": "infomoney_sample",
    }[name]


# --- declarative retry config ------------------------------------------------


def test_fetch_task_declares_retries_with_backoff() -> None:
    assert fetch_task.retries == 3
    assert list(fetch_task.retry_delay_seconds) == [2, 5, 15]


# --- self-healing transient blips --------------------------------------------


def test_transient_fetch_blip_self_heals(monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = (FIXTURES / "martech_sample.xml").read_bytes()
    attempts = {"n": 0}

    def flaky(url: str, timeout: int = 30) -> bytes:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise RuntimeError("transient blip")
        return fixture

    monkeypatch.setattr(flows, "fetch_rss", flaky)
    # Zero-delay override keeps the test fast; declared [2, 5, 15] is pinned above.
    out = fetch_task.with_options(retry_delay_seconds=0)(get_source(MARTECH)["rss_url"])
    assert out == fixture
    assert attempts["n"] == 3


def test_persistent_fetch_failure_exhausts_retries_and_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = {"n": 0}

    def always_down(url: str, timeout: int = 30) -> bytes:
        attempts["n"] += 1
        raise RuntimeError("hard down")

    monkeypatch.setattr(flows, "fetch_rss", always_down)
    with pytest.raises(RuntimeError, match="hard down"):
        fetch_task.with_options(retries=2, retry_delay_seconds=0)("http://example.com/feed")
    assert attempts["n"] == 3  # initial attempt + 2 retries


# --- propagate-inside leaf ----------------------------------------------------


def test_source_flow_propagates_stage_failure(
    monkeypatch: pytest.MonkeyPatch, no_engine: None
) -> None:
    """No catch around task calls: a dead fetch raises out of the subflow."""

    def dead_fetch(url: str, source_name: str | None = None) -> bytes:
        raise RuntimeError("boom")

    monkeypatch.setattr(flows, "fetch_task", dead_fetch)
    with pytest.raises(RuntimeError, match="boom"):
        flows.ingest_source_flow(source_name=PJ)


def test_unknown_source_still_returns_explicit_error_dict(no_engine: None) -> None:
    result = flows.ingest_source_flow(source_name="No Such Source")
    assert result["inserted"] == 0
    assert result["skipped"] == 0
    assert "error" in result and result["error"]


# --- return_state isolation in the batch parent --------------------------------


def test_batch_isolates_failed_subflow_and_records_it(
    monkeypatch: pytest.MonkeyPatch, no_engine: None
) -> None:
    failing_url = get_source(PJ)["rss_url"]

    def fake_fetch(url: str, source_name: str | None = None) -> bytes:
        if url == failing_url:
            raise RuntimeError("boom")
        for name in (SMT, INFOMONEY):
            if url == get_source(name)["rss_url"]:
                return (FIXTURES / f"{_fixture_slug(name)}.xml").read_bytes()
        raise AssertionError(f"unexpected url: {url}")

    # Plain-function stub: raises immediately (no retry delays), so the PJ
    # subflow lands Failed and the parent must convert + record it.
    monkeypatch.setattr(flows, "fetch_task", fake_fetch)
    _stub_enrich_identity(monkeypatch)
    shared: dict[str, FakeConnection] = {}

    def fake_upsert(docs: list[NormalizedDocument]) -> tuple[int, int]:
        conn = shared.setdefault(docs[0].source, FakeConnection())
        return upsert_documents(docs, conn=conn)

    monkeypatch.setattr(flows, "upsert_documents", fake_upsert)
    recorded: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        flows,
        "record_ingestion_run",
        lambda source, result, **kw: recorded.append((source, dict(result))),
    )
    results = flows.ingest_sources_flow(source_names=[SMT, PJ, INFOMONEY])
    assert results[SMT] == {"inserted": 3, "skipped": 0}
    assert results[INFOMONEY] == {"inserted": 3, "skipped": 0}
    assert results[PJ]["inserted"] == 0
    assert results[PJ]["skipped"] == 0
    assert "error" in results[PJ] and results[PJ]["error"]
    # The parent recorded the Failed subflow (leaf never reaches _finish there).
    by_source = {source: result for source, result in recorded}
    assert "error" in by_source[PJ] and by_source[PJ]["error"]
