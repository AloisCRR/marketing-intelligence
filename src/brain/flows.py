"""Prefect orchestration for source ingestion.

Flows run locally without a server (`uv run python -m ...` or direct call).
Tasks delegate to module-global functions so tests can substitute fakes.
"""

from __future__ import annotations

from typing import Any

from prefect import flow, task

from brain.ingest import fetch_rss, parse_feed, upsert_documents
from brain.normalize import NormalizedDocument
from brain.sources import get_source


@task
def fetch_task(url: str) -> bytes:
    """Retrieve raw feed bytes for a source URL."""
    return fetch_rss(url)


@task
def parse_task(
    xml: bytes,
    source: str = "Social Media Today",
    language: str | None = None,
) -> list[NormalizedDocument]:
    """Normalize raw feed bytes into documents."""
    return parse_feed(xml, source=source, language=language)


@task
def upsert_task(docs: list[NormalizedDocument]) -> dict[str, int]:
    """Persist documents idempotently; returns {inserted, skipped}."""
    inserted, skipped = upsert_documents(docs)
    return {"inserted": inserted, "skipped": skipped}


@flow
def ingest_source_flow(source_name: str = "Social Media Today") -> dict[str, Any]:
    """Ingest one source end-to-end. Returns {inserted, skipped[, error]}.

    Stage failures are explicit (returned as `error`, never raised), so one
    failing source never invalidates the rest of the pipeline.
    """
    try:
        source = get_source(source_name)
    except KeyError as exc:
        return {"inserted": 0, "skipped": 0, "error": str(exc)}
    try:
        xml = fetch_task(str(source["rss_url"]))
    except Exception as exc:
        return {"inserted": 0, "skipped": 0, "error": f"fetch failed: {exc}"}
    try:
        docs = parse_task(xml, str(source.get("name", source_name)), str(source.get("language") or "en"))
    except Exception as exc:
        return {"inserted": 0, "skipped": 0, "error": f"parse failed: {exc}"}
    try:
        return upsert_task(docs)
    except Exception as exc:
        return {"inserted": 0, "skipped": 0, "error": f"persist failed: {exc}"}


@flow
def ingest_sources_flow(source_names: list[str] | None = None) -> dict[str, dict[str, Any]]:
    """Ingest multiple sources; one failure never blocks the others.

    Returns a per-source mapping of {inserted, skipped[, error]}.
    Defaults to every registry source when `source_names` is None.
    """
    from brain.sources import list_sources

    names = (
        list(source_names)
        if source_names is not None
        else [str(entry["name"]) for entry in list_sources()]
    )
    results: dict[str, dict[str, Any]] = {}
    for name in names:
        results[name] = ingest_source_flow(source_name=name)
    return results


@flow
def ingest_all_sources_flow() -> dict[str, dict[str, Any]]:
    """Ingest every registry source; per-source {inserted, skipped[, error]}."""
    return ingest_sources_flow()
