"""Thin HTTP adapter over marketing_intelligence.service (Ticket 05).

Same validated payloads as the MCP tools by construction: both call the
shared service functions. `InvalidRequest` maps to 422; FastAPI also serves
`/docs` for the demo.
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from marketing_intelligence import service
from marketing_intelligence.auth import require_bearer
from marketing_intelligence.healthcheck import (
    health_payload,  # noqa: F401  (installs access-log filter)
)
from marketing_intelligence.service import (
    DEFAULT_PERIOD_LIMIT,
    DEFAULT_SEARCH_LIMIT,
    InvalidRequest,
)

app = FastAPI(title="Marketing Intelligence", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    """Unauthenticated liveness probe (no bearer token required)."""
    return health_payload()


@app.exception_handler(InvalidRequest)
async def _invalid_request_handler(request: Any, exc: InvalidRequest) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.get("/search")
def search(
    q: str = Query(..., description="Keyword matched against title and content"),
    limit: int = Query(DEFAULT_SEARCH_LIMIT, description="Max results [1..100]"),
    exclude_read: bool = Query(False, description="When true, hide read articles"),
    min_importance: float | None = Query(
        None,
        description=("Importance floor in [0,1]; unannotated articles are excluded only when set"),
    ),
    topics: list[str] | None = Query(  # noqa: B008 - FastAPI Query idiom, same as above
        None,
        description="Topic filter (canonical slugs or synonyms); repeat for several",
    ),
    _: None = Depends(require_bearer),
) -> dict[str, Any]:
    """Keyword search, newest first (importance-first when a floor is set).

    20-key dicts: 7 base + 4 flag + 3 read + 4 importance + 1 topics
    + 1 image-text presence.
    """
    return {
        "results": service.search_articles(
            q,
            limit=limit,
            exclude_read=exclude_read,
            min_importance=min_importance,
            topics=topics,
        )
    }


class PeriodRequest(BaseModel):
    from_date: str
    to_date: str
    sources: list[str] | None = None
    limit: int = DEFAULT_PERIOD_LIMIT
    exclude_read: bool = False
    per_source_limit: int | None = None
    min_importance: float | None = None
    topics: list[str] | None = None


@app.post("/period-context")
def period_context(body: PeriodRequest, _: None = Depends(require_bearer)) -> dict[str, Any]:
    """Evidence bundle for [from_date, to_date] (ISO dates), filtered on request.

    `min_importance`/`topics` select on the annotation layer (importance-first
    ordering when a floor is set); `per_source_limit` caps how many items any
    one source contributes (freed slots go to other sources); `limit` still
    bounds the bundle.
    """
    return service.get_period_context(
        body.from_date,
        body.to_date,
        sources=body.sources,
        limit=body.limit,
        exclude_read=body.exclude_read,
        per_source_limit=body.per_source_limit,
        min_importance=body.min_importance,
        topics=body.topics,
    )


@app.get("/article")
def get_article(
    url: str = Query(..., description="Article URL or canonical URL"),
    _: None = Depends(require_bearer),
) -> dict[str, Any]:
    """One-item full-text lookup by URL/canonical URL (full body + provenance)."""
    return service.get_article(url)


class FlagRequest(BaseModel):
    identifier: str
    reason: str | None = None
    detail: str | None = None
    flagged_by: str | None = None
    clear: bool = False


@app.post("/flag-extraction")
def flag_extraction(body: FlagRequest, _: None = Depends(require_bearer)) -> dict[str, Any]:
    """Flag (or clear) an extraction issue on one article; returns updated Article."""
    return service.flag_extraction(  # type: ignore[attr-defined, no-any-return]
        body.identifier,
        reason=body.reason,
        detail=body.detail,
        flagged_by=body.flagged_by,
        clear=body.clear,
    )


class MarkReadRequest(BaseModel):
    identifier: str
    read_by: str | None = None
    clear: bool = False


@app.post("/mark-read")
def mark_read(body: MarkReadRequest, _: None = Depends(require_bearer)) -> dict[str, Any]:
    """Mark (or clear) an article as read; returns updated Article."""
    return service.mark_article_read(  # type: ignore[attr-defined, no-any-return]
        body.identifier,
        read_by=body.read_by,
        clear=body.clear,
    )


@app.get("/sources")
def source_inventory(_: None = Depends(require_bearer)) -> dict[str, Any]:
    """Read-only Source inventory (name, article count, last ingest, cadence)."""
    return {"sources": service.list_sources_inventory()}


class ImportanceRequest(BaseModel):
    identifier: str
    score: float
    rationale: str | None = None
    reporter: str | None = None


@app.post("/importance")
def set_importance(body: ImportanceRequest, _: None = Depends(require_bearer)) -> dict[str, Any]:
    """Set (latest-wins) a 0-1 importance annotation; returns updated Article."""
    return service.set_importance(  # type: ignore[attr-defined, no-any-return]
        body.identifier,
        body.score,
        rationale=body.rationale,
        reporter=body.reporter,
    )


@app.get("/importance")
def get_importance(
    url: str = Query(..., description="Article URL or canonical URL"),
    _: None = Depends(require_bearer),
) -> dict[str, Any]:
    """Latest importance annotation for one article (all keys None if unannotated)."""
    return service.get_importance(url)


@app.get("/vocabulary")
def vocabulary(_: None = Depends(require_bearer)) -> dict[str, Any]:
    """Read-only controlled Topic vocabulary (canonical slugs, synonyms, aliases)."""
    return {"vocabulary": service.list_vocabulary()}


class TopicsRequest(BaseModel):
    identifier: str
    topics: list[str]
    reporter: str | None = None


@app.post("/topics")
def set_document_topics(body: TopicsRequest, _: None = Depends(require_bearer)) -> dict[str, Any]:
    """Set (latest-wins) an article's canonical Topics; returns updated Article."""
    return service.set_document_topics(
        body.identifier,
        body.topics,
        reporter=body.reporter,
    )


class DigestPicksRequest(BaseModel):
    digest_date: str
    identifiers: list[str]
    reporter: str | None = None


@app.post("/digest-picks")
def record_digest_picks(
    body: DigestPicksRequest, _: None = Depends(require_bearer)
) -> dict[str, Any]:
    """Record a digest's pick set for one date; returns the set + added/removed diff."""
    return service.record_digest_picks(
        body.digest_date,
        body.identifiers,
        reporter=body.reporter,
    )


@app.get("/digest-picks")
def get_digest_picks(
    digest_date: str = Query(..., description="Digest date, ISO YYYY-MM-DD"),
    _: None = Depends(require_bearer),
) -> dict[str, Any]:
    """Read back one digest date's pick set (identity + reporter + timestamp)."""
    return service.get_digest_picks(digest_date)


@app.delete("/digest-picks")
def clear_digest_picks(
    digest_date: str = Query(..., description="Digest date, ISO YYYY-MM-DD"),
    _: None = Depends(require_bearer),
) -> dict[str, Any]:
    """Clear one digest date's pick set (returns how many picks were removed)."""
    return service.clear_digest_picks(digest_date)
