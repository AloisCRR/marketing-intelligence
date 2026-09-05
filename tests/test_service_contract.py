"""Service-adapter contract tests (Ticket 05) — fake conns, no live Postgres.

Covers the shared validated interface in `brain.service`:
- validation (blank keyword, bad limits, bad dates, unknown sources)
- 7-key search schema + provenance + tz-aware published_at
- weekly bundle shape, provenance, [] trend keys, Panama tz handling
- string coercion for weekly bounds, bounded limit (101 rejected)
"""

from __future__ import annotations

import datetime as _dt
import os
import sys
from datetime import date, datetime, timezone

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(os.path.dirname(_HERE), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from brain.service import (  # noqa: E402
    DEFAULT_SEARCH_LIMIT,
    DEFAULT_WEEKLY_LIMIT,
    MAX_LIMIT,
    InvalidRequest,
    get_weekly_context,
    search_articles,
)


def _utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


# --- fakes -------------------------------------------------------------------

SEARCH_ROWS = [
    # (title, url, canonical_url, source, published_at, author, content)
    (
        "TikTok Adds Voice Notes",
        "https://www.socialmediatoday.com/news/tiktok/1/",
        "https://www.socialmediatoday.com/news/tiktok/1/",
        "Social Media Today",
        _utc(2026, 9, 8, 14, 30),
        "Andrew Hutchinson",
        "TikTok rolls out voice notes and image carousels for comments globally.",
    ),
    (
        "Signal Loss Rebuild",
        "https://martech.org/signal-loss/2/",
        "https://martech.org/signal-loss/2/",
        "MarTech",
        _utc(2026, 9, 9, 13, 0),
        None,
        "How marketers rebuild measurement after signal loss this quarter.",
    ),
]


class _SearchCursor:
    def __init__(self, rows: list[tuple]) -> None:
        self._rows = rows
        self.last_params: tuple | None = None

    def execute(self, sql: str, params: tuple | None = None) -> None:
        self.last_params = params

    def fetchall(self) -> list[tuple]:
        if self.last_params:
            try:
                return list(self._rows[: int(self.last_params[-1])])
            except (ValueError, TypeError):
                pass
        return list(self._rows)

    def close(self) -> None:
        pass


class _SearchConnection:
    """Cursor-style fake (matches brain.search conn usage)."""

    def __init__(self, rows: list[tuple] = SEARCH_ROWS) -> None:
        self._rows = rows
        self.calls = 0
        self.closed = 0

    def cursor(self) -> _SearchCursor:
        self.calls += 1
        return _SearchCursor(self._rows)

    def close(self) -> None:
        self.closed += 1


WEEKLY_ROWS = [
    # (title, url, canonical_url, source, published_at, author)
    (
        "TikTok Adds Voice Notes",
        "https://www.socialmediatoday.com/news/tiktok/1/",
        "https://www.socialmediatoday.com/news/tiktok/1/",
        "Social Media Today",
        _utc(2026, 9, 8, 14, 30),
        "Andrew Hutchinson",
    ),
    (
        "Signal Loss Rebuild",
        "https://martech.org/signal-loss/2/",
        "https://martech.org/signal-loss/2/",
        "MarTech",
        _utc(2026, 9, 9, 13, 0),
        None,
    ),
]


class _WeeklyCursor:
    """Execute-style fake with real range/source/limit semantics."""

    def __init__(self, rows: list[tuple]) -> None:
        self._rows = rows
        self.last_params: tuple | None = None
        self._result: list[tuple] = []

    def execute(self, sql: str, params: tuple | None = None) -> _WeeklyCursor:
        assert params is not None
        self.last_params = params
        start, end, names, limit = params
        kept = [r for r in self._rows if r[4] >= start and r[4] < end and r[3] in set(names)]
        kept.sort(key=lambda r: r[4], reverse=True)
        self._result = kept[: int(limit)]
        return self

    def fetchall(self) -> list[tuple]:
        return self._result


class _WeeklyConnection:
    def __init__(self, rows: list[tuple] = WEEKLY_ROWS) -> None:
        self._rows = rows
        self.calls = 0

    def execute(self, sql: str, params: tuple | None = None) -> _WeeklyCursor:
        self.calls += 1
        return _WeeklyCursor(self._rows).execute(sql, params)

    def commit(self) -> None:
        pass

    def close(self) -> None:
        pass


# --- search ------------------------------------------------------------------


def test_search_returns_seven_key_schema_with_provenance() -> None:
    results = search_articles("TikTok", conn=_SearchConnection())
    assert len(results) >= 1
    for row in results:
        assert set(row.keys()) == {
            "title",
            "url",
            "canonical_url",
            "source",
            "published_at",
            "author",
            "snippet",
        }
        assert row["source"] in ("Social Media Today", "MarTech")
        parsed = _dt.datetime.fromisoformat(str(row["published_at"]))
        assert parsed.tzinfo is not None
    assert "tiktok" in str(results[0]["snippet"]).lower()


def test_search_blank_keyword_rejected_without_query() -> None:
    conn = _SearchConnection()
    for bad in ("", "   ", None, 123):
        with pytest.raises(InvalidRequest):
            search_articles(bad, conn=conn)  # type: ignore[arg-type]
    assert conn.calls == 0


def test_search_limit_bounded() -> None:
    conn = _SearchConnection()
    for bad in (0, -1, MAX_LIMIT + 1, "20", 2.5, True):
        with pytest.raises(InvalidRequest):
            search_articles("TikTok", limit=bad, conn=conn)  # type: ignore[arg-type]
    assert conn.calls == 0
    # Boundaries pass through.
    assert len(search_articles("TikTok", limit=1, conn=_SearchConnection())) == 1
    assert search_articles("TikTok", limit=MAX_LIMIT, conn=_SearchConnection())
    assert DEFAULT_SEARCH_LIMIT == 20


def test_search_injected_conn_is_not_closed() -> None:
    conn = _SearchConnection()
    search_articles("TikTok", conn=conn)
    assert conn.calls == 1
    assert conn.closed == 0


# --- weekly ------------------------------------------------------------------


def test_weekly_shape_provenance_and_empty_trend_keys() -> None:
    ctx = get_weekly_context(date(2026, 9, 7), date(2026, 9, 13), conn=_WeeklyConnection())
    assert set(ctx) == {
        "period",
        "important_articles",
        "top_stories",
        "emerging_topics",
        "topic_movements",
        "notable_entities",
        "source_convergence",
    }
    assert set(ctx["period"]) == {"from", "to", "timezone"}
    assert ctx["period"]["timezone"] == "America/Panama"
    for key in (
        "top_stories",
        "emerging_topics",
        "topic_movements",
        "notable_entities",
        "source_convergence",
    ):
        assert ctx[key] == []
    articles = ctx["important_articles"]
    assert [a["title"] for a in articles] == ["Signal Loss Rebuild", "TikTok Adds Voice Notes"]
    for article in articles:
        assert set(article) == {
            "title",
            "url",
            "canonical_url",
            "source",
            "published_at",
            "author",
        }
        parsed = datetime.fromisoformat(str(article["published_at"]))
        assert parsed.tzinfo is not None


def test_weekly_panama_day_boundaries() -> None:
    ctx = get_weekly_context(date(2026, 9, 7), date(2026, 9, 13), conn=_WeeklyConnection())
    assert ctx["period"]["from"] == "2026-09-07T00:00:00-05:00"
    assert ctx["period"]["to"] == "2026-09-14T00:00:00-05:00"


def test_weekly_string_bounds_coerced_like_dates() -> None:
    from_str = get_weekly_context("2026-09-07", "2026-09-13", conn=_WeeklyConnection())
    from_dates = get_weekly_context(
        date(2026, 9, 7), date(2026, 9, 13), conn=_WeeklyConnection()
    )
    assert from_str["period"] == from_dates["period"]
    assert from_str["important_articles"] == from_dates["important_articles"]
    # Datetime strings stay exact instants.
    ctx = get_weekly_context(
        "2026-09-07T09:00:00", "2026-09-08T09:00:00", conn=_WeeklyConnection()
    )
    assert ctx["period"]["from"] == "2026-09-07T09:00:00-05:00"


def test_weekly_naive_datetime_assumed_panama() -> None:
    ctx = get_weekly_context(
        datetime(2026, 9, 7, 9, 0), datetime(2026, 9, 7, 10, 0), conn=_WeeklyConnection()
    )
    assert ctx["period"]["from"] == "2026-09-07T09:00:00-05:00"
    ctx2 = get_weekly_context(
        datetime(2026, 9, 7, 14, 0, tzinfo=timezone.utc),
        datetime(2026, 9, 8, 14, 0, tzinfo=timezone.utc),
        conn=_WeeklyConnection(),
    )
    assert ctx2["period"]["from"] == "2026-09-07T09:00:00-05:00"


def test_weekly_unknown_source_rejected() -> None:
    with pytest.raises(InvalidRequest):
        get_weekly_context(
            date(2026, 9, 7), date(2026, 9, 13),
            sources=["No Such Source"], conn=_WeeklyConnection(),
        )
    with pytest.raises(InvalidRequest):
        get_weekly_context(
            date(2026, 9, 7), date(2026, 9, 13),
            sources="MarTech", conn=_WeeklyConnection(),  # type: ignore[arg-type]
        )


def test_weekly_explicit_known_source_passes_through() -> None:
    ctx = get_weekly_context(
        date(2026, 9, 7), date(2026, 9, 13),
        sources=["MarTech"], conn=_WeeklyConnection(),
    )
    assert [a["title"] for a in ctx["important_articles"]] == ["Signal Loss Rebuild"]


def test_weekly_bad_bounds_and_limits_rejected() -> None:
    conn = _WeeklyConnection()
    with pytest.raises(InvalidRequest):
        get_weekly_context(date(2026, 9, 13), date(2026, 9, 7), conn=conn)
    with pytest.raises(InvalidRequest):
        get_weekly_context("not-a-date", date(2026, 9, 7), conn=conn)
    with pytest.raises(InvalidRequest):
        get_weekly_context("", date(2026, 9, 7), conn=conn)
    with pytest.raises(InvalidRequest):
        get_weekly_context(123, date(2026, 9, 7), conn=conn)  # type: ignore[arg-type]
    with pytest.raises(InvalidRequest):
        get_weekly_context(
            date(2026, 9, 7), date(2026, 9, 13), conn=conn, limit=0
        )
    with pytest.raises(InvalidRequest):
        get_weekly_context(
            date(2026, 9, 7), date(2026, 9, 13), conn=conn, limit=MAX_LIMIT + 1
        )
    assert DEFAULT_WEEKLY_LIMIT == 50
    assert MAX_LIMIT == 100
