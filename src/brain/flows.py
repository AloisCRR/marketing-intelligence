"""Prefect orchestration for source ingestion.

Flows run locally without a server (`uv run python -m ...` or direct call).
Tasks delegate to module-global functions so tests can substitute fakes.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from prefect import flow, task

from brain.health import record_ingestion_run
from brain.ingest import (
    count_feed_entries,
    fetch_rss,
    parse_feed,
    parse_feed_with_report,
    upsert_documents,
)
from brain.normalize import NormalizedDocument
from brain.sources import V1_SOURCES, get_source


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
    """Ingest one source end-to-end. Returns {inserted, skipped[, parse_skipped][, error]}.

    Stage failures are explicit (returned as `error`, never raised), so one
    failing source never invalidates the rest of the pipeline. `parse_skipped`
    counts malformed feed items dropped at parse time; it is present only when
    nonzero so clean-feed results keep their exact {inserted, skipped} shape.

    Every outcome (including errors) is recorded to `ingestion_runs` on a
    best-effort basis — recording never changes the result dict and never
    raises, so DB-free unit tests keep passing without Postgres.
    """
    started_at = datetime.now(timezone.utc)

    def _finish(result: dict[str, Any], skipped_reasons: list[str] | None = None) -> dict[str, Any]:
        try:
            record_ingestion_run(
                source_name,
                result,
                started_at=started_at,
                finished_at=datetime.now(timezone.utc),
                skipped_reasons=skipped_reasons,
            )
        except Exception:
            pass
        return result

    try:
        source = get_source(source_name)
    except KeyError as exc:
        return _finish({"inserted": 0, "skipped": 0, "error": str(exc)})
    source_label = str(source.get("name", source_name))
    language = str(source.get("language") or "en")
    try:
        xml = fetch_task(str(source["rss_url"]))
    except Exception as exc:
        return _finish({"inserted": 0, "skipped": 0, "error": f"fetch failed: {exc}"})
    try:
        docs = parse_task(xml, source_label, language)
    except Exception as exc:
        return _finish({"inserted": 0, "skipped": 0, "error": f"parse failed: {exc}"})
    try:
        result = upsert_task(docs)
    except Exception as exc:
        return _finish({"inserted": 0, "skipped": 0, "error": f"persist failed: {exc}"})
    try:
        entries = count_feed_entries(xml)
    except Exception:
        entries = len(docs)
    parse_skipped = max(0, entries - len(docs))
    reasons: list[str] | None = None
    if parse_skipped:
        result["parse_skipped"] = parse_skipped
        try:
            # Single re-parse, only on messy feeds, to persist skip reasons
            # (closes 03's deferred "persist skipped_reasons" note).
            reasons = parse_feed_with_report(xml, source_label, language).skipped_reasons
        except Exception:
            reasons = None
    return _finish(result, reasons)


@flow
def ingest_sources_flow(source_names: list[str] | None = None) -> dict[str, dict[str, Any]]:
    """Ingest multiple sources; one failure never blocks the others.

    Returns a per-source mapping of {inserted, skipped[, error]}.
    Defaults to the V1 scope when `source_names` is None; extra registry
    sources (JCK, Swarovski) are ingestible by explicit name.
    """
    names = list(source_names) if source_names is not None else list(V1_SOURCES)
    results: dict[str, dict[str, Any]] = {}
    for name in names:
        results[name] = ingest_source_flow(source_name=name)
    return results


@flow
def ingest_all_sources_flow() -> dict[str, dict[str, Any]]:
    """Ingest every V1 source; per-source {inserted, skipped[, error]}."""
    return ingest_sources_flow()
