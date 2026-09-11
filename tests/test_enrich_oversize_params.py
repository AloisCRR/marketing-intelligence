"""Regression: an oversize document batch must not ride Prefect run params.

Prefect rejects flow-run/task-run parameters above 524,288 bytes (the
`POST /api/flow_runs/` body cap; a large Exame source serialized to 705,221
bytes and returned HTTP 422). `ingest_source_flow` therefore must never hand
the full document list to a flow or task run: it calls the plain module-level
`_enrich_docs` / `upsert_documents` helpers, and the public `enrich_task` /
`upsert_task` orchestration wrappers stay uncalled on the hot path.

All I/O is faked (monkeypatch, no network, no Postgres); `no_engine` keeps the
local Prefect engine from starting.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import UTC, datetime
from typing import Any

import pytest
from prefect_harness import no_engine

from marketing_intelligence.normalize import NormalizedDocument, make_document

OVERSIZE_SOURCE = "OversizeSource"
NOW = datetime(2026, 9, 11, 12, 0, 0, tzinfo=UTC)
DOC_COUNT = 20
#: Prefect's flow-run/task-run parameter body cap.
PREFECT_PARAM_LIMIT = 524_288
#: ~40 KB per document x 20 docs: comfortably past the cap when serialized.
BODY_CHARS = 40_000


def _oversize_docs() -> list[NormalizedDocument]:
    """N deterministic docs whose batch serializes well past Prefect's cap."""
    body = "content " * (BODY_CHARS // len("content "))
    return [
        make_document(
            source=OVERSIZE_SOURCE,
            url=f"https://oversize.example.com/articles/{i}",
            title=f"Oversize story {i}",
            content=f"doc {i} {body}",
            published_at=NOW,
            retrieved_at=NOW,
            language="en",
        )
        for i in range(DOC_COUNT)
    ]


def test_oversize_batch_ingests_without_full_list_run_params(
    monkeypatch: pytest.MonkeyPatch, no_engine: None
) -> None:
    import marketing_intelligence.flows as flows

    docs = _oversize_docs()
    serialized = json.dumps([dataclasses.asdict(doc) for doc in docs], default=str)
    assert len(serialized.encode("utf-8")) > PREFECT_PARAM_LIMIT, (
        "test premise: batch must exceed Prefect's 524,288-byte run-param cap"
    )

    # Spy on the public full-list-carrying orchestration objects: the fix is
    # that neither ever runs on this path (they remain compat wrappers).
    hot_calls = {"enrich_task": 0, "upsert_task": 0}

    def _spy(name: str, real: Any) -> Any:
        def _call(*args: Any, **kwargs: Any) -> Any:
            hot_calls[name] += 1
            return real(*args, **kwargs)

        return _call

    monkeypatch.setattr(flows, "enrich_task", _spy("enrich_task", flows.enrich_task))
    monkeypatch.setattr(flows, "upsert_task", _spy("upsert_task", flows.upsert_task))

    # Persistence seam is the plain helper; capture exactly what it receives.
    upserted: list[list[NormalizedDocument]] = []

    def _fake_upsert(received: list[NormalizedDocument]) -> tuple[int, int]:
        upserted.append(list(received))
        return (len(received), 0)

    monkeypatch.setattr(flows, "upsert_documents", _fake_upsert)
    monkeypatch.setattr(flows, "record_ingestion_run", lambda *a, **k: None)
    monkeypatch.setattr(flows, "_ensure_source_row", lambda label: None)
    monkeypatch.setattr(
        flows,
        "get_source",
        lambda name: {
            "name": name,
            "rss_url": "https://oversize.example.com/rss",
            "language": "en",
        },
    )
    monkeypatch.setattr(flows, "get_retrieval_config", lambda name: {"type": "rss"})
    # `auto` + default 500-char threshold: 40 KB bodies pass through untouched
    # (zero fetch), so the whole batch stays deterministic and offline.
    monkeypatch.setattr(
        flows, "get_enrichment_policy", lambda name: {"threshold": 500, "mode": "auto"}
    )
    monkeypatch.setattr(flows, "fetch_task", lambda url, source_name=None: b"<rss/>")
    monkeypatch.setattr(flows, "parse_task", lambda xml, source, language=None: docs)

    result = flows.ingest_source_flow(OVERSIZE_SOURCE)

    assert result == {"inserted": DOC_COUNT, "skipped": 0}
    # The oversize list never crossed a Prefect flow/task boundary.
    assert hot_calls == {"enrich_task": 0, "upsert_task": 0}
    # ...yet every document still reached persistence, byte-identical.
    assert len(upserted) == 1
    assert len(upserted[0]) == DOC_COUNT
    assert upserted[0] == docs
