"""Prefect orchestration for source ingestion.

Flows run locally without a server (`uv run python -m ...` or direct call).
Tasks delegate to module-global functions so tests can substitute fakes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from prefect import flow, get_run_logger, task

from brain.enrich import DEFAULT_THIN_THRESHOLD, enrich_document_or_keep
from brain.health import record_ingestion_run
from brain.ingest import (
    count_feed_entries,
    fetch_rss,
    parse_feed,
    parse_feed_with_report,
    upsert_documents,
)
from brain.normalize import NormalizedDocument
from brain.sources import V1_SOURCES, get_enrichment_policy, get_source


@task(task_run_name="fetch-{url}")
def fetch_task(url: str) -> bytes:
    """Retrieve raw feed bytes for a source URL."""
    raw = fetch_rss(url)
    get_run_logger().info("fetch url=%s bytes=%d", url, len(raw))
    return raw


@task(task_run_name="parse-{source}")
def parse_task(
    xml: bytes,
    source: str = "Social Media Today",
    language: str | None = None,
) -> list[NormalizedDocument]:
    """Normalize raw feed bytes into documents."""
    docs = parse_feed(xml, source=source, language=language)
    try:
        entries = count_feed_entries(xml)
    except Exception:
        entries = len(docs)
    get_run_logger().info(
        "parse source=%s docs=%d entries=%d skipped=%d",
        source,
        len(docs),
        entries,
        max(0, entries - len(docs)),
    )
    return docs


@task(task_run_name="enrich-docs")
def enrich_task(
    docs: list[NormalizedDocument],
    threshold: int = DEFAULT_THIN_THRESHOLD,
    source_name: str | None = None,
) -> tuple[list[NormalizedDocument], int, list[str]]:
    """Enrich RSS bodies to Markdown; returns (docs, skipped, causes).

    Honors the per-source enrichment policy for `source_name` (its threshold
    override threads into the thin check): `force_off` passes every document
    through untouched with zero fetch and no causes; `force_on` enriches every
    item regardless of thin; `auto` (or no source) keeps the thin-only
    behavior. Sufficient bodies pass through untouched (zero fetch). Any
    per-item failure keeps the RSS body and records `"<method>: <cause>"`
    (method is `rss` whenever the stored body stayed RSS) — the task never
    raises, so enrichment can never block an Ingestion Run. Delegates to the
    module-global `enrich_document_or_keep` so tests can substitute fakes.
    """
    mode = "auto"
    if source_name is not None:
        try:
            policy = get_enrichment_policy(source_name)
        except Exception:
            policy = {"threshold": threshold, "mode": "auto"}
        candidate = policy.get("threshold", threshold)
        if isinstance(candidate, int) and not isinstance(candidate, bool) and candidate > 0:
            threshold = candidate
        if policy.get("mode") in ("auto", "force_on", "force_off"):
            mode = str(policy["mode"])
    if mode == "force_off":
        get_run_logger().info(
            "enrich source=%s mode=force_off docs=%d enriched=0 skipped=0",
            source_name,
            len(docs),
        )
        return (list(docs), 0, [])
    force = mode == "force_on"
    enriched: list[NormalizedDocument] = []
    skipped = 0
    causes: list[str] = []
    for doc in docs:
        try:
            if force:
                new_doc, method, cause = enrich_document_or_keep(doc, threshold, force=True)
            else:
                new_doc, method, cause = enrich_document_or_keep(doc, threshold)
        except Exception as exc:  # defensive: enrichment never blocks ingestion
            enriched.append(doc)
            skipped += 1
            causes.append(f"rss: {doc.url}: enrichment failed ({exc})")
            continue
        enriched.append(new_doc)
        if cause is not None:
            skipped += 1
            causes.append(f"{method}: {cause}")
    get_run_logger().info(
        "enrich docs=%d enriched=%d skipped=%d", len(docs), len(docs) - skipped, skipped
    )
    return (enriched, skipped, causes)


@task(task_run_name="upsert-docs")
def upsert_task(docs: list[NormalizedDocument]) -> dict[str, int]:
    """Persist documents idempotently; returns {inserted, skipped}."""
    inserted, skipped = upsert_documents(docs)
    get_run_logger().info("upsert docs=%d inserted=%d skipped=%d", len(docs), inserted, skipped)
    return {"inserted": inserted, "skipped": skipped}


@flow(flow_run_name="ingest-{source_name}")
def ingest_source_flow(source_name: str = "Social Media Today") -> dict[str, Any]:
    """Ingest one source end-to-end.

    Returns {inserted, skipped[, parse_skipped][, enrich_skipped][, enrich_causes][, error]}.

    Stage failures are explicit (returned as `error`, never raised), so one
    failing source never invalidates the rest of the pipeline. `parse_skipped`
    counts malformed feed items dropped at parse time; `enrich_skipped` counts
    thin items whose article fetch failed (RSS body kept) with per-item
    `enrich_causes` entries shaped `"<method>: <cause>"` (method is `rss`
    whenever the stored body stayed RSS); both are present only when nonzero so clean-feed results
    keep their exact {inserted, skipped} shape.

    Every outcome (including errors) is recorded to `ingestion_runs` on a
    best-effort basis — recording never changes the result dict and never
    raises, so DB-free unit tests keep passing without Postgres.
    """
    logger = get_run_logger()
    started_at = datetime.now(UTC)

    def _finish(result: dict[str, Any], skipped_reasons: list[str] | None = None) -> dict[str, Any]:
        try:
            record_ingestion_run(
                source_name,
                result,
                started_at=started_at,
                finished_at=datetime.now(UTC),
                skipped_reasons=skipped_reasons,
            )
        except Exception:
            pass
        logger.info("ingest source=%s result=%s", source_name, result)
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
        docs, enrich_skipped, enrich_causes = enrich_task(docs, source_name=source_label)
    except Exception:
        # Enrichment never blocks ingestion: fall back to RSS bodies.
        enrich_skipped, enrich_causes = 0, []
    try:
        result = upsert_task.with_options(task_run_name=f"upsert-{source_label}-{len(docs)}-docs")(
            docs
        )
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
    if enrich_skipped:
        enriched_result: dict[str, Any] = dict(result)
        enriched_result["enrich_skipped"] = enrich_skipped
        enriched_result["enrich_causes"] = list(enrich_causes)
        result = enriched_result
    skipped_reasons = (reasons or []) + list(enrich_causes)
    return _finish(result, skipped_reasons or None)


@flow(flow_run_name="ingest-batch")
def ingest_sources_flow(
    source_names: list[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Ingest multiple sources; one failure never blocks the others.

    Returns a per-source mapping of {inserted, skipped[, error]}.
    Defaults to the V1 scope when `source_names` is None; extra registry
    sources (JCK, Swarovski) are ingestible by explicit name.
    """
    logger = get_run_logger()
    names = list(source_names) if source_names is not None else list(V1_SOURCES)
    batch_ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    results: dict[str, dict[str, Any]] = {}
    for name in names:
        results[name] = ingest_source_flow.with_options(flow_run_name=f"ingest-{name}-{batch_ts}")(
            source_name=name
        )
    logger.info("ingest-batch sources=%d result=%s", len(names), results)
    return results
