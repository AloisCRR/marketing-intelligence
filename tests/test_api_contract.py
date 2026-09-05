"""HTTP-adapter contract tests (Ticket 05) — TestClient, service monkeypatched.

No live Postgres: `brain.service` functions are stubbed, so these tests pin
the adapter behaviour only — same payloads, 422 mapping, /docs demoable.
"""

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(os.path.dirname(_HERE), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import brain.service as service  # noqa: E402
from api.app import app  # noqa: E402

SEARCH_PAYLOAD = [
    {
        "title": "TikTok Adds Voice Notes",
        "url": "https://www.socialmediatoday.com/news/tiktok/1/",
        "canonical_url": "https://www.socialmediatoday.com/news/tiktok/1/",
        "source": "Social Media Today",
        "published_at": "2026-09-08T14:30:00+00:00",
        "author": "Andrew Hutchinson",
        "snippet": "…TikTok rolls out voice notes…",
    }
]

WEEKLY_PAYLOAD = {
    "period": {
        "from": "2026-09-07T00:00:00-05:00",
        "to": "2026-09-14T00:00:00-05:00",
        "timezone": "America/Panama",
    },
    "important_articles": [
        {
            "title": "TikTok Adds Voice Notes",
            "url": "https://www.socialmediatoday.com/news/tiktok/1/",
            "canonical_url": "https://www.socialmediatoday.com/news/tiktok/1/",
            "source": "Social Media Today",
            "published_at": "2026-09-08T14:30:00+00:00",
            "author": "Andrew Hutchinson",
        }
    ],
    "top_stories": [],
    "emerging_topics": [],
    "topic_movements": [],
    "notable_entities": [],
    "source_convergence": [],
}


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(service, "search_articles", lambda keyword, limit=20, conn=None: SEARCH_PAYLOAD)
    monkeypatch.setattr(
        service,
        "get_weekly_context",
        lambda from_date, to_date, **kw: WEEKLY_PAYLOAD,
    )
    return TestClient(app)


def test_search_returns_service_payload(client: TestClient) -> None:
    resp = client.get("/search", params={"q": "TikTok"})
    assert resp.status_code == 200
    assert resp.json() == {"results": SEARCH_PAYLOAD}


def test_search_forwards_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}
    def fake(keyword: str, limit: int = 20, conn: object = None) -> list:
        seen["keyword"] = keyword
        seen["limit"] = limit
        return SEARCH_PAYLOAD
    monkeypatch.setattr(service, "search_articles", fake)
    resp = TestClient(app).get("/search", params={"q": "TikTok", "limit": 5})
    assert resp.status_code == 200
    assert seen == {"keyword": "TikTok", "limit": 5}


def test_search_validation_maps_to_422() -> None:
    # Real service (no monkeypatch): blank / out-of-range -> 422, never 500.
    live = TestClient(app)
    assert live.get("/search", params={"q": "   "}).status_code == 422
    assert live.get("/search", params={"q": "TikTok", "limit": 0}).status_code == 422
    assert live.get("/search", params={"q": "TikTok", "limit": 101}).status_code == 422
    assert live.get("/search").status_code == 422  # missing q


def test_weekly_returns_service_payload(client: TestClient) -> None:
    resp = client.post(
        "/weekly-context",
        json={"from_date": "2026-09-07", "to_date": "2026-09-13"},
    )
    assert resp.status_code == 200
    assert resp.json() == WEEKLY_PAYLOAD


def test_weekly_forwards_sources_and_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}
    def fake(from_date: object, to_date: object, **kw: object) -> dict:
        seen.update(kw)
        seen["from_date"] = from_date
        seen["to_date"] = to_date
        return WEEKLY_PAYLOAD
    monkeypatch.setattr(service, "get_weekly_context", fake)
    resp = TestClient(app).post(
        "/weekly-context",
        json={
            "from_date": "2026-09-07",
            "to_date": "2026-09-13",
            "sources": ["MarTech"],
            "limit": 5,
        },
    )
    assert resp.status_code == 200
    assert seen["sources"] == ["MarTech"]
    assert seen["limit"] == 5


def test_weekly_validation_maps_to_422() -> None:
    live = TestClient(app)
    assert live.post(
        "/weekly-context", json={"from_date": "not-a-date", "to_date": "2026-09-13"}
    ).status_code == 422
    assert live.post(
        "/weekly-context",
        json={"from_date": "2026-09-07", "to_date": "2026-09-13", "sources": ["Nope"]},
    ).status_code == 422
    assert live.post(
        "/weekly-context",
        json={"from_date": "2026-09-07", "to_date": "2026-09-13", "limit": 101},
    ).status_code == 422


def test_openapi_docs_demoable(client: TestClient) -> None:
    spec = client.get("/openapi.json")
    assert spec.status_code == 200
    paths = spec.json()["paths"]
    assert "/search" in paths and "/weekly-context" in paths
    assert client.get("/docs").status_code == 200
