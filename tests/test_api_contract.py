"""HTTP-adapter contract tests (Ticket 05) — TestClient, service monkeypatched.

No live Postgres: `marketing_intelligence.service` functions are stubbed, so these tests pin
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

import marketing_intelligence.flag as flag_lane  # noqa: E402
import marketing_intelligence.read as read_lane  # noqa: E402
import marketing_intelligence.service as service  # noqa: E402
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

PERIOD_PAYLOAD = {
    "period": {
        "from": "2026-09-07T00:00:00-05:00",
        "to": "2026-09-14T00:00:00-05:00",
        "timezone": "America/Panama",
    },
    "recent_articles": [
        {
            "title": "TikTok Adds Voice Notes",
            "url": "https://www.socialmediatoday.com/news/tiktok/1/",
            "canonical_url": "https://www.socialmediatoday.com/news/tiktok/1/",
            "source": "Social Media Today",
            "published_at": "2026-09-08T14:30:00+00:00",
            "rank": 1,
            "author": "Andrew Hutchinson",
        }
    ],
}

FLAG_PAYLOAD = {
    "title": "TikTok Adds Voice Notes",
    "url": "https://www.socialmediatoday.com/news/tiktok/1/",
    "canonical_url": "https://www.socialmediatoday.com/news/tiktok/1/",
    "source": "Social Media Today",
    "published_at": "2026-09-08T14:30:00+00:00",
    "author": "Andrew Hutchinson",
    "content": "…full body…",
    "flag_reason": "truncated",
    "flag_detail": "body ends mid-sentence",
    "flagged_at": "2026-09-14T12:00:00+00:00",
    "flagged_by": "tester",
}

READ_PAYLOAD = {
    "title": "TikTok Adds Voice Notes",
    "url": "https://www.socialmediatoday.com/news/tiktok/1/",
    "canonical_url": "https://www.socialmediatoday.com/news/tiktok/1/",
    "source": "Social Media Today",
    "published_at": "2026-09-08T14:30:00+00:00",
    "author": "Andrew Hutchinson",
    "content": "…full body…",
    "flag_reason": None,
    "flag_detail": None,
    "flagged_at": None,
    "flagged_by": None,
    "read": True,
    "read_at": "2026-09-14T12:00:00+00:00",
    "read_by": "tester",
}


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(
        service,
        "search_articles",
        lambda keyword, limit=20, conn=None, exclude_read=False: SEARCH_PAYLOAD,
    )
    monkeypatch.setattr(
        service,
        "get_period_context",
        lambda from_date, to_date, **kw: PERIOD_PAYLOAD,
    )
    # raising=False: parallel-lane compatible (green before/after Lane 1 lands).
    monkeypatch.setattr(
        service,
        "flag_extraction",
        lambda identifier, reason=None, detail=None, flagged_by=None, clear=False, conn=None: (
            FLAG_PAYLOAD
        ),
        raising=False,
    )
    # raising=False: read-state lane lands alongside this parity lane.
    monkeypatch.setattr(
        service,
        "mark_article_read",
        lambda identifier, read_by=None, clear=False, conn=None: READ_PAYLOAD,
        raising=False,
    )
    return TestClient(app)


def test_search_returns_service_payload(client: TestClient) -> None:
    resp = client.get("/search", params={"q": "TikTok"})
    assert resp.status_code == 200
    assert resp.json() == {"results": SEARCH_PAYLOAD}


def test_search_forwards_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}

    def fake(
        keyword: str, limit: int = 20, conn: object = None, exclude_read: bool = False
    ) -> list:
        seen["keyword"] = keyword
        seen["limit"] = limit
        seen["exclude_read"] = exclude_read
        return SEARCH_PAYLOAD

    monkeypatch.setattr(service, "search_articles", fake)
    resp = TestClient(app).get("/search", params={"q": "TikTok", "limit": 5})
    assert resp.status_code == 200
    assert seen == {"keyword": "TikTok", "limit": 5, "exclude_read": False}


def test_search_exclude_read_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}

    def fake(
        keyword: str, limit: int = 20, conn: object = None, exclude_read: bool = False
    ) -> list:
        seen["exclude_read"] = exclude_read
        return SEARCH_PAYLOAD

    monkeypatch.setattr(service, "search_articles", fake)
    http = TestClient(app)
    assert http.get("/search", params={"q": "TikTok"}).status_code == 200
    assert seen == {"exclude_read": False}  # identical default as the service
    assert http.get("/search", params={"q": "TikTok", "exclude_read": "true"}).status_code == 200
    assert seen == {"exclude_read": True}


def test_search_validation_maps_to_422() -> None:
    # Real service (no monkeypatch): blank / out-of-range -> 422, never 500.
    live = TestClient(app)
    assert live.get("/search", params={"q": "   "}).status_code == 422
    assert live.get("/search", params={"q": "TikTok", "limit": 0}).status_code == 422
    assert live.get("/search", params={"q": "TikTok", "limit": 101}).status_code == 422
    assert live.get("/search").status_code == 422  # missing q


def test_period_returns_service_payload(client: TestClient) -> None:
    resp = client.post(
        "/period-context",
        json={"from_date": "2026-09-07", "to_date": "2026-09-13"},
    )
    assert resp.status_code == 200
    assert resp.json() == PERIOD_PAYLOAD


def test_period_forwards_sources_and_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}

    def fake(from_date: object, to_date: object, **kw: object) -> dict:
        seen.update(kw)
        seen["from_date"] = from_date
        seen["to_date"] = to_date
        return PERIOD_PAYLOAD

    monkeypatch.setattr(service, "get_period_context", fake)
    resp = TestClient(app).post(
        "/period-context",
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


def test_period_forwards_exclude_read(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}

    def fake(from_date: object, to_date: object, **kw: object) -> dict:
        seen.update(kw)
        seen["from_date"] = from_date
        seen["to_date"] = to_date
        return PERIOD_PAYLOAD

    monkeypatch.setattr(service, "get_period_context", fake)
    http = TestClient(app)
    assert (
        http.post(
            "/period-context", json={"from_date": "2026-09-07", "to_date": "2026-09-13"}
        ).status_code
        == 200
    )
    assert seen["exclude_read"] is False  # identical default as the service
    assert (
        http.post(
            "/period-context",
            json={"from_date": "2026-09-07", "to_date": "2026-09-13", "exclude_read": True},
        ).status_code
        == 200
    )
    assert seen["exclude_read"] is True


def test_period_validation_maps_to_422() -> None:
    live = TestClient(app)
    assert (
        live.post(
            "/period-context", json={"from_date": "not-a-date", "to_date": "2026-09-13"}
        ).status_code
        == 422
    )
    assert (
        live.post(
            "/period-context",
            json={"from_date": "2026-09-07", "to_date": "2026-09-13", "sources": ["Nope"]},
        ).status_code
        == 422
    )
    assert (
        live.post(
            "/period-context",
            json={"from_date": "2026-09-07", "to_date": "2026-09-13", "limit": 101},
        ).status_code
        == 422
    )


def test_openapi_docs_demoable(client: TestClient) -> None:
    spec = client.get("/openapi.json")
    assert spec.status_code == 200
    paths = spec.json()["paths"]
    assert "/search" in paths and "/period-context" in paths
    assert "/flag-extraction" in paths
    assert "/mark-read" in paths
    assert client.get("/docs").status_code == 200


def test_flag_returns_service_payload(client: TestClient) -> None:
    resp = client.post(
        "/flag-extraction",
        json={"identifier": "https://www.socialmediatoday.com/news/tiktok/1/"},
    )
    assert resp.status_code == 200
    assert resp.json() == FLAG_PAYLOAD


def test_flag_forwards_args(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}

    def fake(
        identifier: object,
        reason: object = None,
        detail: object = None,
        flagged_by: object = None,
        clear: object = False,
        conn: object = None,
    ) -> dict:
        seen["identifier"] = identifier
        seen["reason"] = reason
        seen["detail"] = detail
        seen["flagged_by"] = flagged_by
        seen["clear"] = clear
        return FLAG_PAYLOAD

    monkeypatch.setattr(service, "flag_extraction", fake, raising=False)
    resp = TestClient(app).post(
        "/flag-extraction",
        json={
            "identifier": "https://www.socialmediatoday.com/news/tiktok/1/",
            "reason": "truncated",
            "detail": "body ends mid-sentence",
            "flagged_by": "tester",
        },
    )
    assert resp.status_code == 200
    assert resp.json() == FLAG_PAYLOAD
    assert seen == {
        "identifier": "https://www.socialmediatoday.com/news/tiktok/1/",
        "reason": "truncated",
        "detail": "body ends mid-sentence",
        "flagged_by": "tester",
        "clear": False,
    }


class _EmptyCursor:
    """Lane-seam fake for unknown URLs: UPDATE matches nothing, SELECT finds nothing."""

    rowcount = 0

    def execute(self, sql: str, params: object = None) -> None:
        pass

    def fetchall(self) -> list:
        return []

    def close(self) -> None:
        pass


class _EmptyConn:
    def cursor(self) -> _EmptyCursor:
        return _EmptyCursor()

    def commit(self) -> None:
        pass

    def close(self) -> None:
        pass


def test_flag_validation_maps_to_422(monkeypatch: pytest.MonkeyPatch) -> None:
    # Real service (no service stubs): blank id / bad reason -> 422, never 500.
    live = TestClient(app)
    assert live.post("/flag-extraction", json={"identifier": "   "}).status_code == 422
    assert (
        live.post(
            "/flag-extraction",
            json={
                "identifier": "https://www.socialmediatoday.com/news/tiktok/1/",
                "reason": "not-a-reason",
            },
        ).status_code
        == 422
    )
    url = "https://www.socialmediatoday.com/news/tiktok/1/"
    # Detail over 2000 chars -> 422 (2000 itself is accepted, so no DB hit here).
    assert (
        live.post(
            "/flag-extraction",
            json={"identifier": url, "reason": "thin", "detail": "x" * 2001},
        ).status_code
        == 422
    )
    # Reason "other" requires a non-blank detail -> 422.
    assert (
        live.post("/flag-extraction", json={"identifier": url, "reason": "other"}).status_code
        == 422
    )
    assert (
        live.post(
            "/flag-extraction",
            json={"identifier": url, "reason": "other", "detail": "   "},
        ).status_code
        == 422
    )
    # flagged_by over 100 chars -> 422.
    assert (
        live.post(
            "/flag-extraction",
            json={"identifier": url, "reason": "thin", "detail": "d", "flagged_by": "y" * 101},
        ).status_code
        == 422
    )
    # Unknown URL reaches the lane (empty store) and still maps to 422.
    monkeypatch.setattr(flag_lane, "get_connection", lambda: _EmptyConn())
    assert (
        live.post(
            "/flag-extraction",
            json={"identifier": "https://unknown.example/nope/", "reason": "thin", "detail": "d"},
        ).status_code
        == 422
    )


def test_mark_read_returns_service_payload(client: TestClient) -> None:
    resp = client.post(
        "/mark-read",
        json={"identifier": "https://www.socialmediatoday.com/news/tiktok/1/"},
    )
    assert resp.status_code == 200
    assert resp.json() == READ_PAYLOAD


def test_mark_read_forwards_args(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}

    def fake(
        identifier: object,
        read_by: object = None,
        clear: object = False,
        conn: object = None,
    ) -> dict:
        seen["identifier"] = identifier
        seen["read_by"] = read_by
        seen["clear"] = clear
        return READ_PAYLOAD

    monkeypatch.setattr(service, "mark_article_read", fake, raising=False)
    resp = TestClient(app).post(
        "/mark-read",
        json={
            "identifier": "https://www.socialmediatoday.com/news/tiktok/1/",
            "read_by": "tester",
        },
    )
    assert resp.status_code == 200
    assert resp.json() == READ_PAYLOAD
    assert seen == {
        "identifier": "https://www.socialmediatoday.com/news/tiktok/1/",
        "read_by": "tester",
        "clear": False,
    }


def test_mark_read_validation_maps_to_422(monkeypatch: pytest.MonkeyPatch) -> None:
    # Real service (no service stubs): blank id / overlong read_by -> 422, never 500.
    live = TestClient(app)
    assert live.post("/mark-read", json={"identifier": "   "}).status_code == 422
    assert live.post("/mark-read", json={}).status_code == 422  # missing identifier
    url = "https://www.socialmediatoday.com/news/tiktok/1/"
    # read_by over 100 chars -> 422 (100 itself is accepted, so no DB hit here).
    assert (
        live.post("/mark-read", json={"identifier": url, "read_by": "y" * 101}).status_code == 422
    )
    # Unknown URL reaches the lane (empty store) and still maps to 422.
    monkeypatch.setattr(read_lane, "get_connection", lambda: _EmptyConn())
    assert (
        live.post("/mark-read", json={"identifier": "https://unknown.example/nope/"}).status_code
        == 422
    )
