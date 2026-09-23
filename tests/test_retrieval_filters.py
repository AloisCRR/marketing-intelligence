"""Annotation-aware retrieval filters (Ticket 21) — fakes, no live Postgres.

Locked contract:
- The period bundle accepts an importance floor, a Topic filter and a
  per-source cap (`limit`, headlines per source group) in one call:
  importance >= 0.7 + pillar filter + cap returns an on-pillar, source-spread
  week with zero manual juggling.
- Keyword search accepts the same Topic and importance filters so deep dives
  reuse the annotation layer.
- Unannotated Documents (NULL score / no topics) are excluded only when the
  matching filter is set — never silently dropped from an unfiltered query and
  never silently top-scored. On the period lane `topics=None` now means the
  ADR-0016 priority default and an omitted `min_importance` means the 0.5
  default floor, so the fully unfiltered bundle is requested explicitly
  with `topics=[]` + `min_importance=None`; search keeps `None` = unfiltered.
- Both caller surfaces expose all filters identically through the Service
  Adapter with 422 on invalid input.
- Per-reader read marks (ADR-0015) ride along on both lanes: a Document read by
  several readers reports them all, sorted, while `read`/`read_at`/`read_by`
  stay the anyone-read latest-mark cache and unmarked Documents report `[]`.

The fake store mirrors the SQL semantics (range/source/read filters, array
overlap, importance floor, importance-first ordering with NULLS LAST, the
per-source window cap) so the assertions are about observable behavior.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import importlib.util
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(os.path.dirname(_HERE), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import marketing_intelligence.period as period_lane  # noqa: E402
import marketing_intelligence.search as search_lane  # noqa: E402
import marketing_intelligence.service as service  # noqa: E402
from api.app import app  # noqa: E402


def _load_mcp_server() -> Any:
    path = Path(_SRC) / "mcp" / "server.py"
    spec = importlib.util.spec_from_file_location("t21_mcp_server", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MCP_SERVER = _load_mcp_server()


def _utc(*args: int) -> _dt.datetime:
    return _dt.datetime(*args, tzinfo=_dt.UTC)


def _flatten(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Headlines in bundle order: groups first-seen, articles within a group."""
    return [article for group in groups for article in group["articles"]]


# --- fake store ---------------------------------------------------------------


def _doc(
    doc_id: int,
    title: str,
    source: str,
    published_at: _dt.datetime,
    *,
    score: float | None = None,
    topics: list[str] | None = None,
    readers: list[tuple[str, _dt.datetime]] | None = None,
) -> dict[str, Any]:
    """One fake Document row; ``readers`` seeds ADR-0015 read marks.

    The ``document_reads`` arrays (``readers``/``read_ats``) are served in the
    order given — deliberately unsorted where a test seeds them that way — so
    the lane's own sort is what the assertions exercise. ``read_at``/``read_by``
    mirror the `documents` latest-mark cache the write lane refreshes.
    """
    url = f"https://example.test/{doc_id}/"
    marks = list(readers or [])
    latest = max(marks, key=lambda mark: mark[1]) if marks else None
    return {
        "id": doc_id,
        "title": title,
        "url": url,
        "canonical_url": url,
        "source": source,
        "published_at": published_at,
        "author": "A",
        "content": f"Body about {title}.",
        "flag_reason": None,
        "flag_detail": None,
        "flagged_at": None,
        "flagged_by": None,
        "read_at": latest[1] if latest else None,
        "read_by": latest[0] if latest else None,
        "readers": [name for name, _at in marks],
        "read_ats": [at for _name, at in marks],
        "importance_score": score,
        "importance_rationale": None,
        "importance_reporter": None,
        "importance_updated_at": None,
        "topics": list(topics or []),
    }


#: A week of retailer/jewellery evidence: on-pillar high scorers from two
#: sources, plus decoys — off-pillar high score, on-pillar low score, an
#: unannotated on-pillar Document and an unread/read pair.
WEEK_DOCS = [
    _doc(
        1,
        "Jewellery rebound",
        "Professional Jeweller",
        _utc(2026, 9, 10, 9),
        score=0.9,
        topics=["jewelry"],
    ),
    _doc(
        2,
        "Luxury watch demand",
        "JCK Online",
        _utc(2026, 9, 9, 9),
        score=0.8,
        topics=["jewelry", "luxury"],
    ),
    _doc(
        3,
        "Gold price squeeze",
        "Professional Jeweller",
        _utc(2026, 9, 8, 9),
        score=0.75,
        topics=["jewelry"],
    ),
    _doc(
        4,
        "Jewellery trade show",
        "JCK Online",
        _utc(2026, 9, 7, 9),
        score=0.7,
        topics=["jewelry"],
    ),
    _doc(5, "AI ad spend surges", "MarTech", _utc(2026, 9, 11, 9), score=0.95, topics=["ai"]),
    _doc(
        6,
        "Retail footfall dips",
        "Retail Dive",
        _utc(2026, 9, 6, 9),
        score=0.4,
        topics=["retail"],
    ),
    _doc(
        7,
        "Unannotated jewellery note",
        "Professional Jeweller",
        _utc(2026, 9, 5, 9),
        topics=["jewelry"],
    ),
    _doc(8, "Untagged launch", "National Jeweler", _utc(2026, 9, 12, 9), score=0.85),
]

WEEK_FROM = _dt.date(2026, 9, 4)
WEEK_TO = _dt.date(2026, 9, 13)


class _FakeCursor:
    """DB-API-ish cursor over the in-memory store, mirroring the SQL lanes."""

    def __init__(self, store: list[dict[str, Any]]) -> None:
        self._store = store
        self._rows: list[dict[str, Any]] = []
        self.last_sql: str | None = None
        self.last_params: tuple | None = None

    # -- lane seam -----------------------------------------------------------
    def execute(self, sql: str, params: tuple | None = None) -> _FakeCursor:
        self.last_sql = sql
        self.last_params = params or ()
        up = " ".join(sql.split())
        if "d.published_at >= %s" in up:
            self._rows = self._period(sql=up, params=tuple(self.last_params))
        else:
            self._rows = self._search(sql=up, params=tuple(self.last_params))
        return self

    def fetchall(self) -> list[dict[str, Any]]:
        return list(self._rows)

    def close(self) -> None:
        pass

    # -- shared semantics ----------------------------------------------------
    @staticmethod
    def _matches(
        doc: dict[str, Any],
        *,
        keyword: str | None,
        start: Any,
        end: Any,
        names: Any,
        hide_read: bool,
        floor: float | None,
        topics: list[str] | None,
    ) -> bool:
        if keyword is not None:
            text = f"{doc['title']}\n{doc['content']}"
            if keyword.lower() not in text.lower():
                return False
        if start is not None and not (start <= doc["published_at"] < end):
            return False
        if names is not None and doc["source"] not in set(names):
            return False
        if hide_read and doc["read_at"] is not None:
            return False
        if floor is not None:
            # A NULL score never satisfies the floor (PostgreSQL NULL >= x).
            if doc["importance_score"] is None or doc["importance_score"] < floor:
                return False
        if topics:
            if not (set(doc["topics"]) & set(topics)):
                return False
        return True

    @staticmethod
    def _ordered(
        docs: list[dict[str, Any]],
        *,
        floor: float | None,
        per_group: int | None,
        limit: int | None,
    ) -> list[dict[str, Any]]:
        if floor is not None:
            # importance DESC (NULLs last), then recency DESC
            docs.sort(
                key=lambda d: (-(d["importance_score"] or 0.0), -d["published_at"].timestamp())
            )
        else:
            docs.sort(key=lambda d: d["published_at"], reverse=True)
        if per_group is not None:
            # Each source keeps its first `per_group` rows in the global order.
            seen: Counter[str] = Counter()
            kept: list[dict[str, Any]] = []
            for doc in docs:
                seen[doc["source"]] += 1
                if seen[doc["source"]] <= per_group:
                    kept.append(doc)
            docs = kept
        return docs if limit is None else docs[:limit]

    def _period(self, *, sql: str, params: tuple) -> list[dict[str, Any]]:
        start, end, names = params[0], params[1], params[2]
        index = 3
        floor: float | None = None
        topics: list[str] | None = None
        if "imp.score >= %s" in sql:
            floor = params[index]
            index += 1
        if "tps.topics && %s" in sql:
            topics = list(params[index])
            index += 1
        # The grouped lane's trailing parameter caps each source group; there
        # is no outer total limit.
        per_group = int(params[-1])
        docs = [
            d
            for d in self._store
            if self._matches(
                d,
                keyword=None,
                start=start,
                end=end,
                names=names,
                hide_read="d.read_at IS NULL" in sql,
                floor=floor,
                topics=topics,
            )
        ]
        return self._ordered(docs, floor=floor, per_group=per_group, limit=None)

    def _search(self, *, sql: str, params: tuple) -> list[dict[str, Any]]:
        keyword = str(params[0]).strip("%")
        index = 2
        floor: float | None = None
        topics: list[str] | None = None
        if "imp.score >= %s" in sql:
            floor = params[index]
            index += 1
        if "tps.topics && %s" in sql:
            topics = list(params[index])
            index += 1
        limit = int(params[index])
        docs = [
            d
            for d in self._store
            if self._matches(
                d,
                keyword=keyword,
                start=None,
                end=None,
                names=None,
                hide_read="d.read_at IS NULL" in sql,
                floor=floor,
                topics=topics,
            )
        ]
        return self._ordered(docs, floor=floor, per_group=None, limit=limit)


class _FakeConnection:
    """Serves the store through ``execute`` (period) and ``cursor`` (search)."""

    def __init__(self, store: list[dict[str, Any]]) -> None:
        self.store = store
        self.cursors: list[_FakeCursor] = []

    def execute(self, sql: str, params: tuple | None = None) -> _FakeCursor:
        cursor = _FakeCursor(self.store)
        self.cursors.append(cursor)
        return cursor.execute(sql, params)

    def cursor(self) -> _FakeCursor:
        cursor = _FakeCursor(self.store)
        self.cursors.append(cursor)
        return cursor

    def commit(self) -> None:
        pass

    def close(self) -> None:
        pass


@pytest.fixture()
def wired(monkeypatch: pytest.MonkeyPatch) -> _FakeConnection:
    """Patch both read lanes' connection seam with the shared fake store."""
    fake = _FakeConnection(list(WEEK_DOCS))
    monkeypatch.setattr(search_lane, "get_connection", lambda: fake)
    monkeypatch.setattr(period_lane, "get_connection", lambda: fake)
    return fake


# --- success criterion: one call, one week ------------------------------------


def test_success_criterion_pillar_floor_and_cap_in_one_call() -> None:
    """importance >= 0.7 + pillar filter + per-source cap = a usable week."""
    ctx = service.get_period_context(
        WEEK_FROM,
        WEEK_TO,
        conn=_FakeConnection(list(WEEK_DOCS)),
        min_importance=0.7,
        topics=["jewelry"],
        limit=1,
    )
    groups = ctx["recent_articles"]
    # Source-spread: one headline per source group, the top scorer of each
    # under importance order (Professional Jeweller -> JCK Online).
    assert [g["source"] for g in groups] == ["Professional Jeweller", "JCK Online"]
    assert [len(g["articles"]) for g in groups] == [1, 1]
    assert [(g["source"], g["articles"][0]["title"]) for g in groups] == [
        ("Professional Jeweller", "Jewellery rebound"),
        ("JCK Online", "Luxury watch demand"),
    ]
    articles = _flatten(groups)
    # On-pillar only: the off-pillar 0.95 article never leaks in.
    for article in articles:
        assert "jewelry" in article["topics"]
        assert article["importance_score"] >= 0.7
    # Decoys stayed out for the right reasons.
    titles = {a["title"] for a in articles}
    assert "AI ad spend surges" not in titles  # off-pillar, high score
    assert "Retail footfall dips" not in titles  # low score
    assert "Unannotated jewellery note" not in titles  # NULL score, floor set


def test_floor_and_topic_filter_group_in_importance_order() -> None:
    ctx = service.get_period_context(
        WEEK_FROM,
        WEEK_TO,
        conn=_FakeConnection(list(WEEK_DOCS)),
        min_importance=0.7,
        topics=["jewelry"],
    )
    groups = ctx["recent_articles"]
    assert [g["source"] for g in groups] == ["Professional Jeweller", "JCK Online"]
    assert [a["title"] for a in groups[0]["articles"]] == [
        "Jewellery rebound",
        "Gold price squeeze",
    ]
    assert [a["title"] for a in groups[1]["articles"]] == [
        "Luxury watch demand",
        "Jewellery trade show",
    ]


def test_topic_filter_accepts_caller_synonyms_on_service() -> None:
    """Canonicalization is server-side: "jewellery" lands on the "jewelry" slug."""
    ctx = service.get_period_context(
        WEEK_FROM,
        WEEK_TO,
        conn=_FakeConnection(list(WEEK_DOCS)),
        topics=["jewellery"],
        limit=100,
    )
    titles = {a["title"] for a in _flatten(ctx["recent_articles"])}
    assert "Jewellery rebound" in titles
    # An unannotated Document (no topics) is excluded only because the filter is set.
    assert "Untagged launch" not in titles


def test_topic_filter_matches_any_requested_topic() -> None:
    ctx = service.get_period_context(
        WEEK_FROM,
        WEEK_TO,
        conn=_FakeConnection(list(WEEK_DOCS)),
        topics=["luxury", "retail"],
        min_importance=None,
        limit=100,
    )
    # "Luxury watch demand" carries jewelry+luxury; "Retail footfall dips"
    # carries retail — overlap semantics include both.
    assert {a["title"] for a in _flatten(ctx["recent_articles"])} == {
        "Luxury watch demand",
        "Retail footfall dips",
    }


def test_explicit_topics_keep_the_default_floor() -> None:
    """An explicit topic list keeps the 0.5 floor unless cleared."""
    ctx = service.get_period_context(
        WEEK_FROM,
        WEEK_TO,
        conn=_FakeConnection(list(WEEK_DOCS)),
        topics=["luxury", "retail"],
        limit=100,
    )
    # "Retail footfall dips" scores 0.4 — on-topic but below the default floor.
    assert {a["title"] for a in _flatten(ctx["recent_articles"])} == {"Luxury watch demand"}


# --- unannotated behavior -----------------------------------------------------


def test_unannotated_included_and_ordered_by_recency_not_score() -> None:
    # `topics=[]` + `min_importance=None` is the explicit unfiltered period
    # bundle (the `None` default is the 16-slug priority view + 0.5 floor,
    # which excludes these).
    ctx = service.get_period_context(
        WEEK_FROM,
        WEEK_TO,
        conn=_FakeConnection(list(WEEK_DOCS)),
        topics=[],
        min_importance=None,
    )
    groups = ctx["recent_articles"]
    by_source = {g["source"]: [a["title"] for a in g["articles"]] for g in groups}
    # Never silently dropped: the NULL-score, no-topic Document is present.
    assert "Untagged launch" in by_source["National Jeweler"]
    assert "Unannotated jewellery note" in by_source["Professional Jeweller"]
    # Never top-scored: ordering is purely recency, so the freshest
    # unannotated Document leads its own group and the bundle's first group.
    assert list(by_source) == [
        "National Jeweler",
        "MarTech",
        "Professional Jeweller",
        "JCK Online",
        "Retail Dive",
    ]
    assert groups[0]["articles"][0]["title"] == "Untagged launch"
    assert groups[1]["articles"][0]["title"] == "AI ad spend surges"
    # Recency within every group, not score order.
    for group in groups:
        published = [a["published_at"] for a in group["articles"]]
        assert published == sorted(published, reverse=True)
    oldest = by_source["Professional Jeweller"][-1]
    assert oldest == "Unannotated jewellery note"
    assert groups[2]["articles"][-1]["importance_score"] is None


def test_default_applies_both_priority_filters() -> None:
    """Omitted period filters = default topics + 0.5 floor, importance-ordered."""
    ctx = service.get_period_context(WEEK_FROM, WEEK_TO, conn=_FakeConnection(list(WEEK_DOCS)))
    titles = {a["title"] for a in _flatten(ctx["recent_articles"])}
    # On-view and on-floor only: the off-view high scorer, the low scorer and
    # every unannotated Document stay out of the default bundle.
    assert titles == {
        "Jewellery rebound",
        "Luxury watch demand",
        "Gold price squeeze",
        "Jewellery trade show",
        "AI ad spend surges",
    }


def test_default_orders_importance_first_with_recency_tiebreak() -> None:
    docs = [
        _doc(1, "Older high scorer", "MarTech", _utc(2026, 9, 8, 9), score=0.9, topics=["ai"]),
        _doc(2, "Newer high scorer", "MarTech", _utc(2026, 9, 10, 9), score=0.9, topics=["ai"]),
        _doc(3, "Mid scorer", "JCK Online", _utc(2026, 9, 11, 9), score=0.7, topics=["jewelry"]),
    ]
    ctx = service.get_period_context(WEEK_FROM, WEEK_TO, conn=_FakeConnection(docs))
    assert [a["title"] for a in _flatten(ctx["recent_articles"])] == [
        "Newer high scorer",
        "Older high scorer",
        "Mid scorer",
    ]


def test_floor_excludes_unannotated_but_floor_none_keeps_them() -> None:
    conn = _FakeConnection(list(WEEK_DOCS))
    kept = service.get_period_context(WEEK_FROM, WEEK_TO, conn=conn, min_importance=None)
    assert any(a["importance_score"] is None for a in _flatten(kept["recent_articles"]))
    floored = service.get_period_context(WEEK_FROM, WEEK_TO, conn=conn, min_importance=0.0)
    # min_importance=0 keeps annotated 0.0+ Documents and drops NULL scores.
    assert all(a["importance_score"] is not None for a in _flatten(floored["recent_articles"]))
    assert all(a["importance_score"] >= 0.0 for a in _flatten(floored["recent_articles"]))


# --- search reuses the annotation layer ---------------------------------------


def test_search_applies_importance_and_topic_filters() -> None:
    results = service.search_articles(
        "Jewellery",
        conn=_FakeConnection(list(WEEK_DOCS)),
        min_importance=0.7,
        topics=["jewelry"],
    )
    # Both on-pillar high scorers match the keyword; importance-first ordering.
    assert [r["title"] for r in results] == ["Jewellery rebound", "Jewellery trade show"]
    assert [r["importance_score"] for r in results] == [0.9, 0.7]


def test_search_floor_orders_importance_first_and_drops_unannotated() -> None:
    results = service.search_articles(
        "a",  # matches every body
        limit=100,
        conn=_FakeConnection(list(WEEK_DOCS)),
        min_importance=0.7,
    )
    titles = [r["title"] for r in results]
    assert titles == [
        "AI ad spend surges",  # 0.95
        "Jewellery rebound",  # 0.9
        "Untagged launch",  # 0.85
        "Luxury watch demand",  # 0.8
        "Gold price squeeze",  # 0.75
        "Jewellery trade show",  # 0.7
    ]
    assert all(r["importance_score"] is not None for r in results)


def test_search_unfiltered_stays_recency_ordered_and_includes_unannotated() -> None:
    results = service.search_articles("a", limit=100, conn=_FakeConnection(list(WEEK_DOCS)))
    assert [r["title"] for r in results] == [
        "Untagged launch",
        "AI ad spend surges",
        "Jewellery rebound",
        "Luxury watch demand",
        "Gold price squeeze",
        "Jewellery trade show",
        "Retail footfall dips",
        "Unannotated jewellery note",
    ]
    assert results[0]["title"] == "Untagged launch"
    # Recency wins over score: the 0.95 article is not first, and the NULL-score
    # Document is present at its recency position, not top-scored.
    assert results[1]["title"] == "AI ad spend surges"
    assert results[-1]["title"] == "Unannotated jewellery note"
    assert results[-1]["importance_score"] is None


# --- per-reader read marks (ADR-0015) -----------------------------------------


def test_search_reports_every_reader_sorted_alongside_the_cache_summary() -> None:
    docs = [
        _doc(
            90,
            "Readers note",
            "MarTech",
            _utc(2026, 9, 11, 9),
            readers=[("zoe", _utc(2026, 9, 11, 10)), ("amara", _utc(2026, 9, 12, 8))],
        ),
        _doc(91, "Unread note", "MarTech", _utc(2026, 9, 10, 9)),
    ]
    results = search_lane.search_articles("note", conn=_FakeConnection(docs))
    by_title = {r["title"]: r for r in results}

    assert by_title["Readers note"]["readers"] == [
        {"reader": "amara", "read_at": "2026-09-12T08:00:00+00:00"},
        {"reader": "zoe", "read_at": "2026-09-11T10:00:00+00:00"},
    ]
    # The anyone-read summary is unchanged: the latest mark is the cache.
    assert by_title["Readers note"]["read"] is True
    assert by_title["Readers note"]["read_by"] == "amara"
    assert by_title["Unread note"]["readers"] == []
    assert by_title["Unread note"]["read"] is False


def test_period_reports_readers_through_the_grouped_bundle() -> None:
    docs = [
        _doc(
            90,
            "Marked note",
            "MarTech",
            _utc(2026, 9, 11, 9),
            readers=[("amara", _utc(2026, 9, 12, 8))],
        ),
        _doc(91, "Untouched note", "JCK Online", _utc(2026, 9, 10, 9)),
        _doc(92, "Older MarTech note", "MarTech", _utc(2026, 9, 9, 9)),
    ]
    untrimmed = period_lane.get_period_context(WEEK_FROM, WEEK_TO, conn=_FakeConnection(docs))
    capped = period_lane.get_period_context(WEEK_FROM, WEEK_TO, conn=_FakeConnection(docs), limit=1)
    assert [
        (g["source"], [a["title"] for a in g["articles"]]) for g in capped["recent_articles"]
    ] == [
        ("MarTech", ["Marked note"]),
        ("JCK Online", ["Untouched note"]),
    ]
    for bundle in (untrimmed, capped):
        by_title = {a["title"]: a for a in _flatten(bundle["recent_articles"])}
        assert by_title["Marked note"]["readers"] == [
            {"reader": "amara", "read_at": "2026-09-12T08:00:00+00:00"}
        ]
        assert by_title["Marked note"]["read"] is True
        assert by_title["Untouched note"]["readers"] == []


# --- parity + 422 -------------------------------------------------------------


def test_filters_identical_over_http_and_mcp(wired: _FakeConnection) -> None:
    http = TestClient(app)
    search_params = [
        ("q", "Jewellery"),
        ("min_importance", "0.7"),
        ("topics", "jewellery"),
    ]
    http_search = http.get("/search", params=search_params).json()["results"]
    mcp_search = MCP_SERVER.search_articles(
        keyword="Jewellery", min_importance=0.7, topics=["jewellery"]
    )
    assert http_search == mcp_search
    registered = MCP_SERVER.mcp.call_tool(
        "search_articles",
        {"keyword": "Jewellery", "min_importance": 0.7, "topics": ["jewellery"]},
    )
    out = asyncio.run(registered)
    if isinstance(out, tuple) and len(out) == 2 and isinstance(out[1], dict):
        out = out[1].get("result", out[1])
    assert out == http_search

    body = {
        "from_date": "2026-09-04",
        "to_date": "2026-09-13",
        "min_importance": 0.7,
        "topics": ["jewellery"],
        "limit": 1,
    }
    http_period = http.post("/period-context", json=body).json()
    mcp_period = MCP_SERVER.get_period_context(**body)
    assert http_period == mcp_period
    assert [a["title"] for a in _flatten(http_period["recent_articles"])] == [
        "Jewellery rebound",
        "Luxury watch demand",
    ]


def test_invalid_filters_422_identical_messages() -> None:
    live = TestClient(app)
    # Unknown topic: same failure text on both surfaces, before any query.
    resp = live.get("/search", params=[("q", "x"), ("topics", "quantum-jewelry")])
    assert resp.status_code == 422
    with pytest.raises(service.InvalidRequest) as excinfo:
        service.search_articles("x", topics=["quantum-jewelry"])
    assert resp.json() == {"detail": str(excinfo.value)}

    resp = live.post(
        "/period-context",
        json={
            "from_date": "2026-09-04",
            "to_date": "2026-09-13",
            "min_importance": 1.5,
        },
    )
    assert resp.status_code == 422
    with pytest.raises(service.InvalidRequest) as excinfo:
        service.get_period_context("2026-09-04", "2026-09-13", min_importance=1.5)
    assert resp.json() == {"detail": str(excinfo.value)}


@pytest.mark.parametrize("bad", [-0.01, 1.01, 2, True, "high"])
def test_min_importance_validation(bad: Any) -> None:
    with pytest.raises(service.InvalidRequest):
        service.get_period_context(WEEK_FROM, WEEK_TO, conn=_FakeConnection([]), min_importance=bad)
    with pytest.raises(service.InvalidRequest):
        service.search_articles("x", conn=_FakeConnection([]), min_importance=bad)


def test_explicit_floor_none_is_accepted_as_no_floor() -> None:
    assert (
        service.get_period_context(
            WEEK_FROM, WEEK_TO, conn=_FakeConnection([]), min_importance=None
        )["recent_articles"]
        == []
    )
    assert service.search_articles("x", conn=_FakeConnection([]), min_importance=None) == []


def test_topic_filter_validation_rejects_non_lists_and_blank_tags() -> None:
    for bad in ("jewelry", ["  "], [123], 5):
        with pytest.raises(service.InvalidRequest):
            service.search_articles("x", conn=_FakeConnection([]), topics=bad)
        with pytest.raises(service.InvalidRequest):
            service.get_period_context(WEEK_FROM, WEEK_TO, conn=_FakeConnection([]), topics=bad)


def test_empty_topic_list_adds_no_constraint() -> None:
    ctx = service.get_period_context(
        WEEK_FROM, WEEK_TO, conn=_FakeConnection(list(WEEK_DOCS)), topics=[], min_importance=None
    )
    assert len(_flatten(ctx["recent_articles"])) == len(WEEK_DOCS)


def test_empty_topics_keeps_the_default_floor() -> None:
    """`topics=[]` clears topics only — NULL scores stay out by default."""
    ctx = service.get_period_context(
        WEEK_FROM, WEEK_TO, conn=_FakeConnection(list(WEEK_DOCS)), topics=[]
    )
    titles = {a["title"] for a in _flatten(ctx["recent_articles"])}
    # The 0.4 retail scorer and the NULL-score jewellery note stay out (below
    # the 0.5 default floor / NULL); the scored-but-untagged launch returns —
    # `topics=[]` removed the only constraint that excluded it.
    assert "Retail footfall dips" not in titles
    assert "Unannotated jewellery note" not in titles
    assert titles == {
        "Jewellery rebound",
        "Luxury watch demand",
        "Gold price squeeze",
        "Jewellery trade show",
        "AI ad spend surges",
        "Untagged launch",
    }


# --- live Postgres: the real SQL predicates + parameter order -----------------


@pytest.fixture()
def scratch_db(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Create/drop an isolated scratch DB with the real migrations applied."""
    import uuid

    import psycopg

    from marketing_intelligence.config import get_database_url

    url = os.environ.get("DATABASE_URL", get_database_url())
    try:
        probe = psycopg.connect(url, connect_timeout=2)
        probe.close()
    except Exception:
        pytest.skip("no live Postgres reachable")
    base, _, _ = url.rpartition("/")
    name = f"brain_filters_{os.getpid()}_{uuid.uuid4().hex[:8]}"
    admin = psycopg.connect(f"{base}/postgres", autocommit=True)
    try:
        with admin.cursor() as cur:
            cur.execute(f'CREATE DATABASE "{name}"')
    finally:
        admin.close()
    scratch_url = f"{base}/{name}"
    monkeypatch.setenv("DATABASE_URL", scratch_url)
    yield scratch_url
    admin = psycopg.connect(f"{base}/postgres", autocommit=True)
    try:
        with admin.cursor() as cur:
            cur.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (name,),
            )
            cur.execute(f'DROP DATABASE IF EXISTS "{name}"')
    finally:
        admin.close()


@pytest.mark.live_db
def test_live_annotation_filters_real_sql(scratch_db: str) -> None:
    """Prove the real SQL: floor, array-overlap Topic filter, cap, ordering."""
    import psycopg

    from marketing_intelligence import db as db_mod

    db_mod.apply_migrations()
    docs = [
        # (source, url_slug, title, published_at, score, topic)
        (
            "Professional Jeweller",
            "on-pillar-high",
            "Live pillar high",
            "2026-09-10 14:00",
            0.9,
            "jewelry",
        ),
        ("JCK Online", "jck-high", "Live JCK high", "2026-09-09 14:00", 0.8, "jewelry"),
        (
            "Professional Jeweller",
            "on-pillar-low",
            "Live pillar low",
            "2026-09-08 14:00",
            0.4,
            "jewelry",
        ),
        ("MarTech", "off-pillar", "Live off pillar", "2026-09-11 14:00", 0.95, "ai"),
        (
            "JCK Online",
            "unannotated",
            "Live unannotated on-pillar",
            "2026-09-07 14:00",
            None,
            "jewelry",
        ),
        ("National Jeweler", "untagged", "Live untagged", "2026-09-06 14:00", 0.85, None),
    ]
    with psycopg.connect(scratch_db) as conn, conn.cursor() as cur:
        for source, slug, title, published, _score, _topic in docs:
            cur.execute("SELECT id FROM sources WHERE name = %s", (source,))
            source_id = cur.fetchone()[0]
            url = f"https://example.test/live/{slug}/"
            cur.execute(
                "INSERT INTO documents (source_id, url, canonical_url, title, author,"
                " published_at, retrieved_at, language, content, content_hash)"
                " VALUES (%s, %s, %s, %s, 'A', %s, now(), 'en', %s, %s)",
                (source_id, url, url, title, published, f"Body about {title}.", f"hash-{slug}"),
            )
    for _source, slug, _title, _published, score, topic in docs:
        url = f"https://example.test/live/{slug}/"
        if score is not None:
            service.set_importance(url, score)
        if topic is not None:
            service.set_document_topics(url, [topic])

    # One call: floor + pillar filter + per-source cap over the week.
    ctx = service.get_period_context(
        "2026-09-06",
        "2026-09-13",
        min_importance=0.7,
        topics=["jewellery"],  # synonym -> canonical "jewelry" server-side
        limit=1,
    )
    groups = ctx["recent_articles"]
    assert [g["source"] for g in groups] == ["Professional Jeweller", "JCK Online"]
    assert [g["articles"][0]["title"] for g in groups] == ["Live pillar high", "Live JCK high"]
    assert [g["articles"][0]["importance_score"] for g in groups] == [0.9, 0.8]

    # Search reuses the same predicates.
    hits = service.search_articles("Live", limit=10, min_importance=0.7, topics=["jewellery"])
    assert [r["title"] for r in hits] == ["Live pillar high", "Live JCK high"]

    # Unfiltered: unannotated Documents are present and recency-ordered.
    unfiltered = service.search_articles("Live", limit=10)
    titles = [r["title"] for r in unfiltered]
    assert "Live unannotated on-pillar" in titles
    assert titles == [
        "Live off pillar",
        "Live pillar high",
        "Live JCK high",
        "Live pillar low",
        "Live unannotated on-pillar",
        "Live untagged",
    ]
    # Untagged (no topics) is excluded only when the topic filter is set.
    tagged = service.search_articles("Live", limit=10, topics=["ai"])
    assert [r["title"] for r in tagged] == ["Live off pillar"]

    # Every filter combination composes into valid SQL on both lanes.
    combinations = [
        {},
        {"exclude_read": True},
        {"min_importance": 0.7},
        {"topics": ["jewelry"]},
        {"limit": 1},
        {"min_importance": 0.7, "limit": 1},
        {"topics": ["jewelry"], "limit": 1},
        {"exclude_read": True, "topics": ["jewelry"], "limit": 1},
        {"min_importance": 0.7, "topics": ["jewelry"], "exclude_read": True},
        {"min_importance": 0.7, "topics": ["jewelry"], "limit": 1},
    ]
    for kwargs in combinations:
        bundle = service.get_period_context("2026-09-06", "2026-09-13", **{"limit": 10, **kwargs})
        assert "recent_articles" in bundle
        bundle_articles = _flatten(bundle["recent_articles"])
        if "min_importance" in kwargs:
            assert all(a["importance_score"] >= kwargs["min_importance"] for a in bundle_articles)
        if kwargs.get("topics"):
            assert all(a["topics"] for a in bundle_articles)
        search_kwargs = {
            key: value
            for key, value in kwargs.items()
            if key in {"exclude_read", "min_importance", "topics"}
        }
        results = service.search_articles("Live", limit=10, **search_kwargs)
        assert all(r["title"].startswith("Live") for r in results)

    # ADR-0015: the `readers` list rides both lanes' real SQL, sorted, next to
    # the unchanged anyone-read cache. Two readers, marks an hour apart.
    marked_url = "https://example.test/live/on-pillar-high/"
    with psycopg.connect(scratch_db) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO document_reads (document_id, reader, read_at)"
            " SELECT d.id, r.reader, now() - r.age"
            "   FROM documents d,"
            "        (VALUES ('zoe', interval '2 hours'), ('amara', interval '1 hour'))"
            "        AS r(reader, age)"
            "  WHERE d.url = %s",
            (marked_url,),
        )
        cur.execute(
            "UPDATE documents SET read_at = now() - interval '1 hour', read_by = 'amara'"
            " WHERE url = %s",
            (marked_url,),
        )

    marked = next(
        r for r in service.search_articles("Live pillar high", limit=5) if r["url"] == marked_url
    )
    assert [entry["reader"] for entry in marked["readers"]] == ["amara", "zoe"]
    for entry in marked["readers"]:
        assert _dt.datetime.fromisoformat(entry["read_at"]).tzinfo is not None
    assert marked["read"] is True
    assert marked["read_by"] == "amara"
    # An unmarked Document reports an empty log, not a missing key.
    assert service.search_articles("Live untagged", limit=5)[0]["readers"] == []

    # The grouped bundle forwards the arrays through its explicit outer SELECT.
    capped_bundle = service.get_period_context("2026-09-06", "2026-09-13", limit=1)
    capped_item = next(
        a for a in _flatten(capped_bundle["recent_articles"]) if a["url"] == marked_url
    )
    assert [entry["reader"] for entry in capped_item["readers"]] == ["amara", "zoe"]
