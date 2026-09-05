"""Thin HTTP adapter over brain.service (Ticket 05).

Same validated payloads as the MCP tools by construction: both call the
shared service functions. `InvalidRequest` maps to 422; FastAPI also serves
`/docs` for the demo.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from brain import service
from brain.service import (
    DEFAULT_SEARCH_LIMIT,
    DEFAULT_WEEKLY_LIMIT,
    InvalidRequest,
)

app = FastAPI(title="Trend Intelligence Brain", version="0.1.0")


@app.exception_handler(InvalidRequest)
async def _invalid_request_handler(request: Any, exc: InvalidRequest) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.get("/search")
def search(
    q: str = Query(..., description="Keyword matched against title and content"),
    limit: int = Query(DEFAULT_SEARCH_LIMIT, description="Max results [1..100]"),
) -> dict[str, Any]:
    """Keyword search, newest first (7-key provenance dicts)."""
    return {"results": service.search_articles(q, limit=limit)}


class WeeklyRequest(BaseModel):
    from_date: str
    to_date: str
    sources: list[str] | None = None
    limit: int = DEFAULT_WEEKLY_LIMIT


@app.post("/weekly-context")
def weekly_context(body: WeeklyRequest) -> dict[str, Any]:
    """Weekly evidence bundle for [from_date, to_date] (ISO dates)."""
    return service.get_weekly_context(
        body.from_date, body.to_date, sources=body.sources, limit=body.limit
    )


@app.get("/article")
def get_article(
    url: str = Query(..., description="Article URL or canonical URL"),
) -> dict[str, Any]:
    """One-item full-text lookup by URL/canonical URL (full body + provenance)."""
    return service.get_article(url)
