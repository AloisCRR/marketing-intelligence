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
        "readers": [{"reader": "reader-1", "read_at": "2026-09-14T12:00:00+00:00"}],
    }
]

#: Grouped period bundle: headlines hang off their source group, and `limit`
#: caps headlines per group (no flat list, no per-article `source`).
PERIOD_PAYLOAD = {
    "period": {
        "from": "2026-09-07T00:00:00-05:00",
        "to": "2026-09-14T00:00:00-05:00",
        "timezone": "America/Panama",
    },
    "recent_articles": [
        {
            "source": "Social Media Today",
            "articles": [
                {
                    "title": "TikTok Adds Voice Notes",
                    "url": "https://www.socialmediatoday.com/news/tiktok/1/",
                    "canonical_url": "https://www.socialmediatoday.com/news/tiktok/1/",
                    "published_at": "2026-09-08T14:30:00+00:00",
                    "author": "Andrew Hutchinson",
                    "read": False,
                    "read_at": None,
                    "read_by": None,
                    "readers": [{"reader": "reader-1", "read_at": "2026-09-14T12:00:00+00:00"}],
                    "flag_reason": None,
                    "importance_score": None,
                    "topics": [],
                    "has_image_text": False,
                }
            ],
        }
    ],
}

PERIOD_TOP_KEYS = {"period", "recent_articles"}
PERIOD_GROUP_KEYS = {"source", "articles"}
PERIOD_HEADLINE_KEYS = {
    "title",
    "url",
    "canonical_url",
    "published_at",
    "author",
    "read",
    "read_at",
    "read_by",
    "readers",
    "flag_reason",
    "importance_score",
    "topics",
    "has_image_text",
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
    "readers": [{"reader": "tester", "read_at": "2026-09-14T12:00:00+00:00"}],
}


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(
        service,
        "search_articles",
        lambda keyword, limit=20, conn=None, exclude_read=False, min_importance=None, topics=None: (
            SEARCH_PAYLOAD
        ),
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
    # Ticket 02: the `readers` list rides through the HTTP surface unchanged.
    assert resp.json()["results"][0]["readers"] == SEARCH_PAYLOAD[0]["readers"]


def test_search_forwards_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}

    def fake(
        keyword: str,
        limit: int = 20,
        conn: object = None,
        exclude_read: bool = False,
        min_importance: float | None = None,
        topics: list[str] | None = None,
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
        keyword: str,
        limit: int = 20,
        conn: object = None,
        exclude_read: bool = False,
        min_importance: float | None = None,
        topics: list[str] | None = None,
    ) -> list:
        seen["exclude_read"] = exclude_read
        return SEARCH_PAYLOAD

    monkeypatch.setattr(service, "search_articles", fake)
    http = TestClient(app)
    assert http.get("/search", params={"q": "TikTok"}).status_code == 200
    assert seen == {"exclude_read": False}  # identical default as the service
    assert http.get("/search", params={"q": "TikTok", "exclude_read": "true"}).status_code == 200
    assert seen == {"exclude_read": True}


def test_search_forwards_annotation_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    """HTTP passes the annotation filters straight through to the adapter."""
    seen: dict = {}

    def fake(
        keyword: str,
        limit: int = 20,
        conn: object = None,
        exclude_read: bool = False,
        min_importance: float | None = None,
        topics: list[str] | None = None,
    ) -> list:
        seen.update(min_importance=min_importance, topics=topics)
        return SEARCH_PAYLOAD

    monkeypatch.setattr(service, "search_articles", fake)
    http = TestClient(app)
    assert http.get("/search", params={"q": "TikTok"}).status_code == 200
    assert seen == {"min_importance": None, "topics": None}
    resp = http.get(
        "/search",
        params=[("q", "TikTok"), ("min_importance", "0.7"), ("topics", "jewellery")],
    )
    assert resp.status_code == 200
    # The vocabulary lane owns canonicalization; HTTP forwards the raw tag.
    assert seen == {"min_importance": 0.7, "topics": ["jewellery"]}


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
    body = resp.json()
    # Grouped shape: period + one entry per source, headlines nested.
    assert set(body) == PERIOD_TOP_KEYS
    group = body["recent_articles"][0]
    assert set(group) == PERIOD_GROUP_KEYS
    assert group["source"] == "Social Media Today"
    assert set(group["articles"][0]) == PERIOD_HEADLINE_KEYS
    # Ticket 02: period headlines carry the `readers` list through HTTP unchanged.
    assert group["articles"][0]["readers"] == [
        {"reader": "reader-1", "read_at": "2026-09-14T12:00:00+00:00"}
    ]


def test_period_forwards_sources_and_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}

    def fake(from_date: object, to_date: object, **kw: object) -> dict:
        seen.update(kw)
        return PERIOD_PAYLOAD

    monkeypatch.setattr(service, "get_period_context", fake)
    http = TestClient(app)
    # `limit` is the per-source-group headline cap and defaults to 50.
    body = {"from_date": "2026-09-07", "to_date": "2026-09-13"}
    assert http.post("/period-context", json=body).status_code == 200
    assert seen["sources"] is None
    assert seen["limit"] == service.DEFAULT_PERIOD_LIMIT == 50
    resp = http.post(
        "/period-context",
        json=dict(body, sources=["MarTech"], limit=5),
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


def test_period_forwards_annotation_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    """HTTP passes the importance floor + Topic filter straight to the adapter."""
    seen: dict = {}

    def fake(from_date: object, to_date: object, **kw: object) -> dict:
        seen.update(kw)
        return PERIOD_PAYLOAD

    monkeypatch.setattr(service, "get_period_context", fake)
    http = TestClient(app)
    body = {"from_date": "2026-09-07", "to_date": "2026-09-13"}
    assert http.post("/period-context", json=body).status_code == 200
    assert seen["min_importance"] == service.DEFAULT_PERIOD_MIN_IMPORTANCE == 0.5
    assert seen["topics"] is None
    assert (
        http.post(
            "/period-context",
            json=dict(body, min_importance=0.7, topics=["jewellery"]),
        ).status_code
        == 200
    )
    assert seen["min_importance"] == 0.7
    assert seen["topics"] == ["jewellery"]  # canonicalization happens in the adapter
    assert (
        http.post(
            "/period-context",
            json=dict(body, min_importance=None),
        ).status_code
        == 200
    )
    assert seen["min_importance"] is None


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
    # The cutover folded the standalone per-source cap into `limit`, so the
    # removed parameter is no longer part of the request model: PeriodRequest
    # (extra="forbid") rejects the stale key rather than silently ignoring it.
    assert (
        live.post(
            "/period-context",
            json={
                "from_date": "2026-09-07",
                "to_date": "2026-09-13",
                "per_source_limit": 2,
            },
        ).status_code
        == 422
    )
    for bad in (-0.01, 1.5, "high"):
        assert (
            live.post(
                "/period-context",
                json={
                    "from_date": "2026-09-07",
                    "to_date": "2026-09-13",
                    "min_importance": bad,
                },
            ).status_code
            == 422
        )
    assert (
        live.post(
            "/period-context",
            json={
                "from_date": "2026-09-07",
                "to_date": "2026-09-13",
                "topics": ["quantum-jewelry"],
            },
        ).status_code
        == 422
    )
    assert (
        live.get(
            "/search",
            params=[("q", "TikTok"), ("min_importance", "1.5")],
        ).status_code
        == 422
    )
    assert (
        live.get(
            "/search",
            params=[("q", "TikTok"), ("topics", "quantum-jewelry")],
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
        json={
            "identifier": "https://www.socialmediatoday.com/news/tiktok/1/",
            "read_by": "tester",
        },
    )
    assert resp.status_code == 200
    assert resp.json() == READ_PAYLOAD
    # Ticket 02: a named mark shows up in the returned reader log.
    assert resp.json()["readers"] == [{"reader": "tester", "read_at": "2026-09-14T12:00:00+00:00"}]


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
    # Real service (no service stubs): blank id / missing-or-blank read_by on a
    # mark / overlong read_by -> 422, never 500.
    live = TestClient(app)
    assert live.post("/mark-read", json={"identifier": "   "}).status_code == 422
    assert live.post("/mark-read", json={}).status_code == 422  # missing identifier
    url = "https://www.socialmediatoday.com/news/tiktok/1/"
    # Marking is per-reader: a reader-less (or blank-reader) mark is a 422 that
    # never reaches the lane, while a reader-less clear stays valid (clear-all).
    real_lane = read_lane.mark_article_read
    lane_calls: list[dict] = []

    def spy(
        identifier: object, read_by: object = None, clear: object = False, **kw: object
    ) -> dict:
        lane_calls.append({"identifier": identifier, "read_by": read_by, "clear": clear})
        return {"read_at": None, "read_by": None}

    monkeypatch.setattr(read_lane, "mark_article_read", spy)
    assert live.post("/mark-read", json={"identifier": url}).status_code == 422
    assert live.post("/mark-read", json={"identifier": url, "read_by": "   "}).status_code == 422
    assert lane_calls == []
    assert live.post("/mark-read", json={"identifier": url, "clear": True}).status_code == 200
    assert lane_calls == [{"identifier": url, "read_by": None, "clear": True}]
    monkeypatch.setattr(read_lane, "mark_article_read", real_lane)
    # read_by over 100 chars -> 422 (100 itself is accepted, so no DB hit here).
    assert (
        live.post("/mark-read", json={"identifier": url, "read_by": "y" * 101}).status_code == 422
    )
    # Unknown URL reaches the lane (empty store) and still maps to 422.
    monkeypatch.setattr(read_lane, "get_connection", lambda: _EmptyConn())
    assert (
        live.post(
            "/mark-read", json={"identifier": "https://unknown.example/nope/", "read_by": "t"}
        ).status_code
        == 422
    )
    assert (
        live.post(
            "/mark-read",
            json={"identifier": "https://unknown.example/nope/", "read_by": "t", "clear": True},
        ).status_code
        == 422
    )
