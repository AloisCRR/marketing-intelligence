"""Prefect orchestration for source ingestion.

Flows run locally without a server (`uv run python -m ...` or direct call).
Tasks delegate to module-global functions so tests can substitute fakes.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from prefect import flow, get_run_logger, task
from prefect.cache_policies import NONE
from prefect.task_runners import ThreadPoolTaskRunner

from marketing_intelligence.db import get_connection
from marketing_intelligence.discovery import (
    ArticleJob,
    fetch_extract_one,
    paced_policy_fetch,
    plan_harvest,
)
from marketing_intelligence.enrich import DEFAULT_THIN_THRESHOLD, enrich_document_or_keep
from marketing_intelligence.health import record_ingestion_run
from marketing_intelligence.ingest import (
    SOURCE_ID_SQL,
    EmptyFeedError,
    count_feed_entries,
    feed_candidate_urls,
    fetch_rss,
    parse_feed,
    parse_feed_with_report,
    upsert_documents,
)
from marketing_intelligence.normalize import NormalizedDocument
from marketing_intelligence.sources import (
    V1_SOURCES,
    get_enrichment_policy,
    get_retrieval_config,
    get_retrieval_policy,
    get_source,
)


def _retry_unless_permanent(task: Any, task_run: Any, state: Any) -> bool:
    """Prefect retry gate: never retry permanent failures (opt 4 fail-fast).

    Returns False (no retry) when the failed state carries an
    :class:`EmptyFeedError` or an HTTP 404 — retrying a dead/emptied feed
    only burns the 2s/5s/15s backoff. Anything else (transient network
    blips, 5xx, timeouts) retries as before. Never raises: an
    uninspectable state retries, preserving the self-healing default.
    """
    try:
        outcome = state.result(raise_on_failure=False)
    except Exception:
        return True
    if not isinstance(outcome, BaseException):
        return True
    if isinstance(outcome, EmptyFeedError):
        return False
    if "404" in str(outcome):
        return False
    return True


@task(
    name="fetch_feed",
    retries=3,
    retry_delay_seconds=[2, 5, 15],
    retry_condition_fn=_retry_unless_permanent,
    cache_policy=NONE,
    task_run_name="fetch-{url}",
)
def fetch_task(url: str, source_name: str | None = None) -> bytes:
    """Retrieve raw feed bytes for a source URL.

    Threads the per-source retrieval policy like `enrich_task` threads
    threshold/mode: `get_retrieval_policy` (never raises; unknown sources
    yield impersonated-feed — the only lane, chaining curl_cffi Chrome →
    Jina reader → Firecrawl) selects the `fetch_rss` lane. Declarative retries
    (3 attempts, 2s/5s/15s backoff) self-heal transient network blips; a
    genuine failure exhausts retries and lands the task Failed (red) — the
    exception propagates, never swallowed here.
    """
    policy = get_retrieval_policy(source_name)["policy"]
    raw = fetch_rss(url)
    get_run_logger().info("fetch url=%s policy=%s bytes=%d", url, policy, len(raw))
    return raw


@task(name="parse_feed_content", task_run_name="parse-{source}")
def parse_task(
    xml: bytes,
    source: str = "Social Media Today",
    language: str | None = None,
) -> list[NormalizedDocument]:
    """Normalize raw feed bytes into documents.

    A cleanly parsed feed with zero entries raises EmptyFeedError (an
    explicit per-source error, never a silent zero); per-item skips stay
    partial. The exception propagates for the Ingestion Run parent to record.
    """
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


def discovery_fetch(url: str, policy: str, gap_s: float) -> tuple[str, bytes]:
    """Paced, per-host-polite fetch behind the concurrent discovery path.

    Single module-global seam for tests: planning (sitemap/hub traversal)
    and every article worker share it, so one fixture patch covers the
    whole flow without touching production pacing (stanza gap + robots
    crawl-delay + jitter, one in-flight fetch per host).
    """
    return paced_policy_fetch(url, policy=policy, gap_s=gap_s)


@task(name="fetch_extract_article", task_run_name="article-fetch", cache_policy=NONE)
def _article_task(job: ArticleJob) -> tuple[Any | None, str | None]:
    """Fetch → decode → extract one planned article; never raises.

    Returns ``(document, None)`` or ``(None, cause)`` per
    :func:`marketing_intelligence.discovery.fetch_extract_one`, so the parent flow keeps the
    exact skipped/causes accounting. Side-effecting (network): no result
    caching. The job is an immutable value — no shared-mutable aliasing
    across workers.
    """

    def fetch(url: str) -> tuple[str, bytes]:
        return discovery_fetch(url, job.policy, job.gap_s)

    return fetch_extract_one(job, fetch_one=fetch)


@flow(
    name="marketing-intelligence.ingestion.discover",
    flow_run_name="discover-{source_name}",
    task_runner=ThreadPoolTaskRunner(max_workers=4),  # type: ignore[arg-type]
)
def discover_task(source_name: str) -> tuple[list[NormalizedDocument], int, list[str]]:
    """Discover → fetch → extract one sitemap Source; returns (docs, skipped, causes).

    Ticket 08 pilot lane, now fanned out V3-safe (opt 1): serial planning
    via the module-global `plan_harvest` (sitemap-first order, hub-anchor
    fallback, robots-Disallow filter — raises DiscoveryError on zero URLs
    like a dead RSS feed), then one `_article_task` per planned URL
    submitted to this flow's ThreadPoolTaskRunner (max 4 workers, no raw
    executor inside any task). Futures resolve in submission order, so
    document order, skipped counts, and per-URL causes match the serial
    harvest exactly; per-host politeness (slot + pacing + jitter) lives in
    `discovery_fetch`.

    Consumes `get_retrieval_config` (never raises). One bad
    sitemap/article is an explicit per-document skip (counted with causes);
    zero discovered URLs raise DiscoveryError, which propagates like a dead
    RSS feed so the Ingestion Run parent records the explicit per-source
    error.
    """
    config = get_retrieval_config(source_name)
    source = get_source(source_name)
    language = str(source.get("language") or "en")
    policy = str(config.get("policy") or "impersonated-feed")
    pacing_ms = config.get("pacing_ms")
    pacing_s = pacing_ms / 1000.0 if isinstance(pacing_ms, int) and pacing_ms > 0 else 1.0
    # Planning traverses a handful of sitemap/hub listings at stanza pace
    # (robots crawl-delay is folded into each article job's own gap below,
    # where the bulk traffic lives).
    plan = plan_harvest(
        config,
        source_name,
        language,
        fetch=lambda url: discovery_fetch(url, policy, pacing_s),
    )
    futures = [_article_task.submit(job) for job in plan.jobs]
    for future in futures:
        future.wait()
    documents: list[NormalizedDocument] = []
    skipped = plan.skipped
    causes = list(plan.causes)
    for future in futures:
        doc, cause = future.result()
        if doc is not None:
            documents.append(doc)
        else:
            skipped += 1
            causes.append(str(cause))
    get_run_logger().info(
        "discover source=%s docs=%d skipped=%d",
        source_name,
        len(documents),
        skipped,
    )
    return (documents, skipped, causes)


@task(name="enrich_single_document", task_run_name="enrich-one", cache_policy=NONE)
def _enrich_one_task(
    doc: NormalizedDocument, threshold: int, force: bool
) -> tuple[NormalizedDocument, str, str | None]:
    """Enrich one document; never raises (keeps the RSS body with a cause).

    Thin wrapper over the module-global `enrich_document_or_keep` so tests
    keep substituting fakes. Side-effecting (article fetch): no result
    caching. Inputs are deepcopied by the parent flow, so workers never
    share mutable documents.
    """
    return enrich_document_or_keep(doc, threshold, force=force)


def _enrich_docs(
    docs: list[NormalizedDocument],
    threshold: int = DEFAULT_THIN_THRESHOLD,
    source_name: str | None = None,
) -> tuple[list[NormalizedDocument], int, list[str]]:
    """Enrich RSS bodies to Markdown; returns (docs, skipped, causes).

    Plain (undecorated) helper: it must be called from inside a flow context
    and fans out to `_enrich_one_task.submit(deepcopy(doc), ...)`, so every
    task run still receives exactly one small document as params — the full
    `docs` list never rides a flow-run/task-run payload. `enrich_task` wraps
    it as a flow for direct callers; `ingest_source_flow` calls it directly so
    the Ingestion Run ships no oversized enrichment params.

    Honors the per-source enrichment policy for `source_name` (its threshold
    override threads into the thin check): `force_off` passes every document
    through untouched with zero fetch and no causes; `force_on` enriches every
    item regardless of thin; `auto` (or no source) keeps the thin-only
    behavior. Sufficient bodies pass through untouched (zero fetch). Any
    per-item failure keeps the RSS body and records `"<method>: <cause>"`
    (method is `rss` whenever the stored body stayed RSS) — it never raises,
    so enrichment can never block an Ingestion Run. Delegates to the
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
    # Fan-out V3-safe (opt 1): one task per document on this flow's
    # ThreadPoolTaskRunner; futures resolve in submission order so the
    # (docs, skipped, causes) contract matches the serial loop exactly.
    futures = [_enrich_one_task.submit(deepcopy(doc), threshold, force) for doc in docs]
    for future in futures:
        future.wait()
    enriched: list[NormalizedDocument] = []
    skipped = 0
    causes: list[str] = []
    for doc, future in zip(docs, futures, strict=True):
        try:
            new_doc, method, cause = future.result()
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


@flow(
    name="marketing-intelligence.ingestion.enrich",
    flow_run_name="enrich-docs",
    task_runner=ThreadPoolTaskRunner(max_workers=4),  # type: ignore[arg-type]
)
def enrich_task(
    docs: list[NormalizedDocument],
    threshold: int = DEFAULT_THIN_THRESHOLD,
    source_name: str | None = None,
) -> tuple[list[NormalizedDocument], int, list[str]]:
    """Enrich RSS bodies to Markdown; returns (docs, skipped, causes).

    Thin flow wrapper over the plain `_enrich_docs` helper, kept for direct
    callers and tests. `ingest_source_flow` calls the helper directly: passing
    the full `docs` list as flow-run params put it in the `POST
    /api/flow_runs/` body, which Prefect caps at 524,288 bytes (a large Exame
    run was 705,221 bytes → HTTP 422).
    """
    return _enrich_docs(docs, threshold, source_name)


@task(name="upsert_documents", task_run_name="upsert-docs")
def upsert_task(docs: list[NormalizedDocument]) -> dict[str, int]:
    """Persist documents idempotently; returns {inserted, skipped}."""
    inserted, skipped = upsert_documents(docs)
    get_run_logger().info("upsert docs=%d inserted=%d skipped=%d", len(docs), inserted, skipped)
    return {"inserted": inserted, "skipped": skipped}


def _ensure_source_row(source_label: str) -> None:
    """Fail fast when the `sources` row is missing (opt 3).

    Runs the same SELECT as the upsert guard (`ingest.SOURCE_ID_SQL`) at
    the top of the Ingestion Run, before any fetch, so a source without a
    DB row fails in milliseconds instead of after minutes of retrieval.
    Raises ValueError on a missing row; a DB that cannot be reached (unit
    tests without Postgres) is not an error here — the run proceeds and
    the upsert guard still enforces the invariant at persist time.
    """
    try:
        conn = get_connection()
    except Exception:
        return
    try:
        row = conn.execute(SOURCE_ID_SQL, (source_label,)).fetchone()
    except Exception:
        return
    finally:
        try:
            conn.close()
        except Exception:
            pass
    if row is None or row[0] is None:
        raise ValueError(
            f"Unknown source for ingest: {source_label!r} "
            "(no row in sources; refusing ingest without source_id)"
        )


@flow(name="marketing-intelligence.ingestion.ingest_source", flow_run_name="ingest-{source_name}")
def ingest_source_flow(source_name: str = "Social Media Today") -> dict[str, Any]:
    """Ingest one source end-to-end.

    Returns {inserted, skipped[, parse_skipped][, discovery_skipped][,
    discovery_causes][, enrich_skipped][, enrich_causes][, error]}.

    Propagate-inside: stage tasks are called with no catch around them, so a
    genuine stage failure (after `fetch_task` retries exhaust) marks its task
    Failed and this subflow Failed (red) — the exception propagates to the
    caller. The only error dict produced here is the pre-task registry
    validation (`KeyError` for an unknown source). `ingest_sources_flow`
    converts a Failed subflow state back into the `{inserted, skipped,
    error}` shape, so per-source isolation and the batch contract hold.

    `parse_skipped` counts malformed feed items dropped at parse time (RSS lane
    only); `discovery_skipped` counts sitemap URLs that could not become
    documents (sitemap lane only) with per-URL `discovery_causes` entries
    shaped `"<url>: <detail>"`; `enrich_skipped` counts thin items whose
    article fetch failed (body kept) with per-item `enrich_causes` entries
    shaped `"<method>: <cause>"` (method is `rss` whenever the stored body
    stayed RSS); all three are present only when nonzero so clean results
    keep their exact {inserted, skipped} shape.

    Successful outcomes (and unknown-source errors) are recorded to
    `ingestion_runs` on a best-effort basis — recording never changes the
    result dict and never raises, so DB-free unit tests keep passing without
    Postgres. Stage-failure recording belongs to the Ingestion Run parent, which owns
    the Failed-state path via `return_state=True`.
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
    # Fail-fast DB-row guard (opt 3): same SELECT as the upsert guard, but
    # before any fetch — a source without a DB row returns the explicit
    # per-source error in milliseconds instead of after minutes of fetch.
    try:
        _ensure_source_row(source_label)
    except ValueError as exc:
        return _finish({"inserted": 0, "skipped": 0, "error": str(exc)})
    # No catch around task calls: fetch retries self-heal blips, genuine
    # failures propagate (red task + red subflow) for the parent to record.
    # Lane switch (ticket 08): RSS entries keep the exact fetch → parse path;
    # sitemap-family entries run discovery → fetch → extract instead. Both
    # lanes converge on enrich → upsert with identical downstream contracts.
    retrieval_type = get_retrieval_config(source_label)["type"]
    discovery_skipped = 0
    discovery_causes: list[str] = []
    if retrieval_type == "rss" or source.get("rss_url"):
        # source_name threads the per-source retrieval policy into fetch_task.
        # Fallback routing (ticket 13): a dead/emptied primary feed raises
        # EmptyFeedError and the next code-level candidate (feed_candidate_urls)
        # is tried; the last failure propagates (red task + red subflow) for
        # the Ingestion Run parent to record as the explicit per-source error.
        # Sources without a declared correction keep the single-URL contract.
        candidates = feed_candidate_urls(str(source["rss_url"]))
        docs = []
        xml = b""
        last_error: Exception | None = None
        for candidate in candidates:
            try:
                xml = fetch_task(candidate, source_name=source_label)
                docs = parse_task(xml, source_label, language)
            except EmptyFeedError as exc:
                last_error = exc
                continue
            except Exception as exc:
                if candidate != candidates[-1]:
                    last_error = exc
                    continue
                raise
            else:
                last_error = None
                break
        if last_error is not None:
            raise last_error
    else:
        docs, discovery_skipped, discovery_causes = discover_task(source_label)
        xml = b""
    # Enrichment never raises (per-item fallback keeps the RSS body), so it
    # can never block an Ingestion Run; no guard needed here. Called as the
    # plain `_enrich_docs` helper inside this Ingestion Run, not as the
    # `enrich_task` subflow: shipping the whole `docs` list as flow-run params
    # overflowed Prefect's 524,288-byte `POST /api/flow_runs/` body on large
    # sources (Exame: 705,221 bytes → 422). The helper still fans out one
    # small `_enrich_one_task.submit(deepcopy(doc), ...)` per document.
    docs, enrich_skipped, enrich_causes = _enrich_docs(docs, DEFAULT_THIN_THRESHOLD, source_label)
    # Persist via the plain `upsert_documents` helper for the same reason (the
    # full list must never ride a task-run param payload); the log line and
    # {inserted, skipped} shape match the old `upsert_task` call exactly.
    inserted, upsert_skipped = upsert_documents(docs)
    logger.info("upsert docs=%d inserted=%d skipped=%d", len(docs), inserted, upsert_skipped)
    result = {"inserted": inserted, "skipped": upsert_skipped}
    reasons: list[str] | None = None
    if retrieval_type == "rss" or source.get("rss_url"):
        try:
            entries = count_feed_entries(xml)
        except Exception:
            entries = len(docs)
        parse_skipped = max(0, entries - len(docs))
        if parse_skipped:
            result["parse_skipped"] = parse_skipped
            try:
                # Single re-parse, only on messy feeds, to persist skip reasons
                # (closes 03's deferred "persist skipped_reasons" note).
                reasons = parse_feed_with_report(xml, source_label, language).skipped_reasons
            except Exception:
                reasons = None
    elif discovery_skipped:
        discovery_result: dict[str, Any] = dict(result)
        discovery_result["discovery_skipped"] = discovery_skipped
        discovery_result["discovery_causes"] = list(discovery_causes)
        result = discovery_result
    if enrich_skipped:
        enriched_result: dict[str, Any] = dict(result)
        enriched_result["enrich_skipped"] = enrich_skipped
        enriched_result["enrich_causes"] = list(enrich_causes)
        result = enriched_result
    skipped_reasons = (reasons or []) + list(discovery_causes) + list(enrich_causes)
    return _finish(result, skipped_reasons or None)


#: Batch fan-out chunk: at most this many Ingestion Runs in flight (opt 2),
#: matching the ThreadPoolTaskRunner bound below.
_BATCH_CHUNK = 4


@task(name="ingest_single_source", task_run_name="ingest-one", cache_policy=NONE)
def _ingest_one_task(source_name: str, flow_run_name: str) -> dict[str, Any]:
    """Run one Ingestion Run with return_state isolation; never raises.

    Calls the leaf `ingest_source_flow` exactly as the sequential batch did
    (`return_state=True`): a Completed state yields its result dict directly
    (including the unknown-source error dict, already recorded by the leaf),
    while a Failed/Crashed state is recorded here via `record_ingestion_run`
    (best-effort, never raises) and converted to `{inserted: 0, skipped: 0,
    error}`. Side-effecting (network + DB): no result caching.
    """
    logger = get_run_logger()
    started_at = datetime.now(UTC)
    state = ingest_source_flow.with_options(flow_run_name=flow_run_name)(
        source_name=source_name, return_state=True
    )
    if state.is_completed():
        try:
            return state.result()
        except Exception as exc:  # Completed but unreadable: treat as failure
            detail = str(exc) or "unreadable subflow result"
    else:
        detail = state.message or "subflow failed"
    result: dict[str, Any] = {"inserted": 0, "skipped": 0, "error": detail}
    try:
        record_ingestion_run(
            source_name,
            result,
            started_at=started_at,
            finished_at=datetime.now(UTC),
        )
    except Exception:
        pass
    logger.error("ingest-batch source=%s failed: %s", source_name, detail)
    return result


@flow(
    name="marketing-intelligence.ingestion.ingest_sources",
    flow_run_name="ingest-batch",
    task_runner=ThreadPoolTaskRunner(max_workers=4),  # type: ignore[arg-type]
)
def ingest_sources_flow(
    source_names: list[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Ingest multiple sources; one failure never blocks the others.

    Returns a per-source mapping of {inserted, skipped[, error]}.
    Defaults to the V1 scope (all 20 curated sources) when `source_names`
    is None; pass explicit names to narrow to a subset.

    Each per-source subflow is invoked with `return_state=True`: a Completed
    state yields its result dict directly (including the unknown-source error
    dict, already recorded by the leaf), while a Failed/Crashed state is
    recorded here via `record_ingestion_run` (best-effort, never raises) and
    converted to `{inserted: 0, skipped: 0, error}` — the batch return shape
    is unchanged and iteration continues to the next source.

    Sources run in chunks of `_BATCH_CHUNK` on this flow's
    ThreadPoolTaskRunner (opt 2): loop-called subflows are sequential by
    default, so the bound lives one level down — each chunk submits one
    `_ingest_one_task` per source (which owns the return_state isolation
    above), waits the chunk, then collects results in name order. A failed
    chunk task can never block its siblings: the task itself never raises,
    and an unreadable future degrades to the same explicit error shape.
    """
    logger = get_run_logger()
    names = list(source_names) if source_names is not None else list(V1_SOURCES)
    batch_ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    results: dict[str, dict[str, Any]] = {}
    for offset in range(0, len(names), _BATCH_CHUNK):
        chunk = names[offset : offset + _BATCH_CHUNK]
        chunk_started = {name: datetime.now(UTC) for name in chunk}
        futures = [
            _ingest_one_task.with_options(task_run_name=f"ingest-{name}-{batch_ts}").submit(
                source_name=name, flow_run_name=f"ingest-{name}-{batch_ts}"
            )
            for name in chunk
        ]
        for future in futures:
            future.wait()
        for name, future in zip(chunk, futures, strict=True):
            try:
                results[name] = future.result()
            except Exception as exc:  # defensive: crashed task degrades to error dict
                detail = str(exc) or "batch task failed"
                result: dict[str, Any] = {"inserted": 0, "skipped": 0, "error": detail}
                try:
                    record_ingestion_run(
                        name,
                        result,
                        started_at=chunk_started[name],
                        finished_at=datetime.now(UTC),
                    )
                except Exception:
                    pass
                logger.error("ingest-batch source=%s failed: %s", name, detail)
                results[name] = result
    logger.info("ingest-batch sources=%d result=%s", len(names), results)
    return results
