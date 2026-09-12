"""Service-adapter contract tests (Ticket 05) — fake conns, no live Postgres.

Covers the shared validated interface in `marketing_intelligence.service`:
- validation (blank keyword, bad limits, bad dates, unknown sources)
- 11-key search schema + provenance + tz-aware published_at
- period bundle shape, provenance, [] trend keys, Panama tz handling
- string coercion for period bounds, bounded limit (101 rejected)
"""

from __future__ import annotations

import datetime as _dt
import os
import sys
from datetime import date, datetime

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(os.path.dirname(_HERE), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from marketing_intelligence.service import (  # noqa: E402
    DEFAULT_PERIOD_LIMIT,
    DEFAULT_SEARCH_LIMIT,
    MAX_LIMIT,
    InvalidRequest,
    get_period_context,
    mark_article_read,
    search_articles,
)

SEARCH_EXPECTED_KEYS = {
    "title",
    "url",
    "canonical_url",
    "source",
    "published_at",
    "author",
    "snippet",
    "flag_reason",
    "flag_detail",
    "flagged_at",
    "flagged_by",
    "read",
    "read_at",
    "read_by",
}

PERIOD_EXPECTED_KEYS = {
    "title",
    "url",
    "canonical_url",
    "source",
    "published_at",
    "rank",
    "author",
    "flag_reason",
    "flag_detail",
    "flagged_at",
    "flagged_by",
    "read",
    "read_at",
    "read_by",
}


def _utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=_dt.UTC)


# --- fakes -------------------------------------------------------------------

SEARCH_ROWS = [
    # (title, url, canonical_url, source, published_at, author, content,
    #  flag_reason, flag_detail, flagged_at, flagged_by, read_at, read_by)
    (
        "TikTok Adds Voice Notes",
        "https://www.socialmediatoday.com/news/tiktok/1/",
        "https://www.socialmediatoday.com/news/tiktok/1/",
        "Social Media Today",
        _utc(2026, 9, 8, 14, 30),
        "Andrew Hutchinson",
        "TikTok rolls out voice notes and image carousels for comments globally.",
        None,
        None,
        None,
        None,
        None,
        None,
    ),
    (
        "Signal Loss Rebuild",
        "https://martech.org/signal-loss/2/",
        "https://martech.org/signal-loss/2/",
        "MarTech",
        _utc(2026, 9, 9, 13, 0),
        None,
        "How marketers rebuild measurement after signal loss this quarter.",
        None,
        None,
        None,
        None,
        None,
        None,
    ),
]


class _SearchCursor:
    def __init__(self, rows: list[tuple]) -> None:
        self._rows = rows
        self.last_sql: str | None = None
        self.last_params: tuple | None = None

    def execute(self, sql: str, params: tuple | None = None) -> None:
        self.last_sql = sql
        self.last_params = params

    def fetchall(self) -> list[tuple]:
        rows = list(self._rows)
        # Read-aware: the exclude_read lane adds `AND d.read_at IS NULL`.
        if self.last_sql is not None and "read_at is null" in self.last_sql.lower():
            rows = [r for r in rows if not (len(r) > 11 and r[11] is not None)]
        if self.last_params:
            try:
                return list(rows[: int(self.last_params[-1])])
            except (ValueError, TypeError):
                pass
        return list(rows)

    def close(self) -> None:
        pass


class _SearchConnection:
    """Cursor-style fake (matches marketing_intelligence.search conn usage)."""

    def __init__(self, rows: list[tuple] = SEARCH_ROWS) -> None:
        self._rows = rows
        self.calls = 0
        self.closed = 0

    def cursor(self) -> _SearchCursor:
        self.calls += 1
        return _SearchCursor(self._rows)

    def close(self) -> None:
        self.closed += 1


PERIOD_ROWS = [
    # (title, url, canonical_url, source, published_at, author,
    #  flag_reason, flag_detail, flagged_at, flagged_by, read_at, read_by)
    (
        "TikTok Adds Voice Notes",
        "https://www.socialmediatoday.com/news/tiktok/1/",
        "https://www.socialmediatoday.com/news/tiktok/1/",
        "Social Media Today",
        _utc(2026, 9, 8, 14, 30),
        "Andrew Hutchinson",
        None,
        None,
        None,
        None,
        None,
        None,
    ),
    (
        "Signal Loss Rebuild",
        "https://martech.org/signal-loss/2/",
        "https://martech.org/signal-loss/2/",
        "MarTech",
        _utc(2026, 9, 9, 13, 0),
        None,
        None,
        None,
        None,
        None,
        None,
        None,
    ),
]


class _PeriodCursor:
    """Execute-style fake with real range/source/limit semantics."""

    def __init__(self, rows: list[tuple]) -> None:
        self._rows = rows
        self.last_sql: str | None = None
        self.last_params: tuple | None = None
        self._result: list[tuple] = []

    def execute(self, sql: str, params: tuple | None = None) -> _PeriodCursor:
        assert params is not None
        self.last_sql = sql
        self.last_params = params
        start, end, names, limit = params
        kept = [r for r in self._rows if r[4] >= start and r[4] < end and r[3] in set(names)]
        # Read-aware: the exclude_read lane adds `AND d.read_at IS NULL`.
        if "read_at is null" in sql.lower():
            kept = [r for r in kept if not (len(r) > 10 and r[10] is not None)]
        kept.sort(key=lambda r: r[4], reverse=True)
        self._result = kept[: int(limit)]
        return self

    def fetchall(self) -> list[tuple]:
        return self._result


class _PeriodConnection:
    def __init__(self, rows: list[tuple] = PERIOD_ROWS) -> None:
        self._rows = rows
        self.calls = 0

    def execute(self, sql: str, params: tuple | None = None) -> _PeriodCursor:
        self.calls += 1
        return _PeriodCursor(self._rows).execute(sql, params)

    def commit(self) -> None:
        pass

    def close(self) -> None:
        pass


# --- search ------------------------------------------------------------------


def test_search_returns_eleven_key_schema_with_provenance() -> None:
    results = search_articles("TikTok", conn=_SearchConnection())
    assert len(results) >= 1
    for row in results:
        assert set(row.keys()) == SEARCH_EXPECTED_KEYS
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


# --- period ------------------------------------------------------------------


def test_period_shape_provenance_and_truthful_recency_list() -> None:
    ctx = get_period_context(date(2026, 9, 7), date(2026, 9, 13), conn=_PeriodConnection())
    # No empty analytics placeholders: period + recency-ordered articles only.
    assert set(ctx) == {"period", "recent_articles"}
    assert set(ctx["period"]) == {"from", "to", "timezone"}
    assert ctx["period"]["timezone"] == "America/Panama"
    articles = ctx["recent_articles"]
    assert [a["title"] for a in articles] == ["Signal Loss Rebuild", "TikTok Adds Voice Notes"]
    assert [a["rank"] for a in articles] == [1, 2]
    for article in articles:
        assert set(article) == PERIOD_EXPECTED_KEYS
        parsed = datetime.fromisoformat(str(article["published_at"]))
        assert parsed.tzinfo is not None


def test_period_panama_day_boundaries() -> None:
    ctx = get_period_context(date(2026, 9, 7), date(2026, 9, 13), conn=_PeriodConnection())
    assert ctx["period"]["from"] == "2026-09-07T00:00:00-05:00"
    assert ctx["period"]["to"] == "2026-09-14T00:00:00-05:00"


def test_period_string_bounds_coerced_like_dates() -> None:
    from_str = get_period_context("2026-09-07", "2026-09-13", conn=_PeriodConnection())
    from_dates = get_period_context(date(2026, 9, 7), date(2026, 9, 13), conn=_PeriodConnection())
    assert from_str["period"] == from_dates["period"]
    assert from_str["recent_articles"] == from_dates["recent_articles"]
    # Datetime strings stay exact instants.
    ctx = get_period_context("2026-09-07T09:00:00", "2026-09-08T09:00:00", conn=_PeriodConnection())
    assert ctx["period"]["from"] == "2026-09-07T09:00:00-05:00"


def test_period_naive_datetime_assumed_panama() -> None:
    ctx = get_period_context(
        datetime(2026, 9, 7, 9, 0), datetime(2026, 9, 7, 10, 0), conn=_PeriodConnection()
    )
    assert ctx["period"]["from"] == "2026-09-07T09:00:00-05:00"
    ctx2 = get_period_context(
        datetime(2026, 9, 7, 14, 0, tzinfo=_dt.UTC),
        datetime(2026, 9, 8, 14, 0, tzinfo=_dt.UTC),
        conn=_PeriodConnection(),
    )
    assert ctx2["period"]["from"] == "2026-09-07T09:00:00-05:00"


def test_period_unknown_source_rejected() -> None:
    with pytest.raises(InvalidRequest):
        get_period_context(
            date(2026, 9, 7),
            date(2026, 9, 13),
            sources=["No Such Source"],
            conn=_PeriodConnection(),
        )
    with pytest.raises(InvalidRequest):
        get_period_context(
            date(2026, 9, 7),
            date(2026, 9, 13),
            sources="MarTech",
            conn=_PeriodConnection(),  # type: ignore[arg-type]
        )


def test_period_explicit_known_source_passes_through() -> None:
    ctx = get_period_context(
        date(2026, 9, 7),
        date(2026, 9, 13),
        sources=["MarTech"],
        conn=_PeriodConnection(),
    )
    assert [a["title"] for a in ctx["recent_articles"]] == ["Signal Loss Rebuild"]


def test_period_bad_bounds_and_limits_rejected() -> None:
    conn = _PeriodConnection()
    with pytest.raises(InvalidRequest):
        get_period_context(date(2026, 9, 13), date(2026, 9, 7), conn=conn)
    with pytest.raises(InvalidRequest):
        get_period_context("not-a-date", date(2026, 9, 7), conn=conn)
    with pytest.raises(InvalidRequest):
        get_period_context("", date(2026, 9, 7), conn=conn)
    with pytest.raises(InvalidRequest):
        get_period_context(123, date(2026, 9, 7), conn=conn)  # type: ignore[arg-type]
    with pytest.raises(InvalidRequest):
        get_period_context(date(2026, 9, 7), date(2026, 9, 13), conn=conn, limit=0)
    with pytest.raises(InvalidRequest):
        get_period_context(date(2026, 9, 7), date(2026, 9, 13), conn=conn, limit=MAX_LIMIT + 1)
    assert DEFAULT_PERIOD_LIMIT == 50
    assert MAX_LIMIT == 100


# --- read state ---------------------------------------------------------------

READ_URL = "https://www.socialmediatoday.com/news/tiktok/1/"
UNREAD_URL = "https://martech.org/signal-loss/2/"

READ_SEARCH_ROWS = [
    # (title, url, canonical_url, source, published_at, author, content,
    #  flag_reason, flag_detail, flagged_at, flagged_by, read_at, read_by)
    (
        "TikTok Adds Voice Notes",
        READ_URL,
        READ_URL,
        "Social Media Today",
        _utc(2026, 9, 8, 14, 30),
        "Andrew Hutchinson",
        "TikTok rolls out voice notes and image carousels for comments globally.",
        None,
        None,
        None,
        None,
        _utc(2026, 9, 10, 12, 0),
        "reader-1",
    ),
    (
        "Signal Loss Rebuild",
        UNREAD_URL,
        UNREAD_URL,
        "MarTech",
        _utc(2026, 9, 9, 13, 0),
        None,
        "How marketers rebuild measurement after signal loss this quarter.",
        None,
        None,
        None,
        None,
        None,
        None,
    ),
]

READ_PERIOD_ROWS = [
    # (title, url, canonical_url, source, published_at, author,
    #  flag_reason, flag_detail, flagged_at, flagged_by, read_at, read_by)
    (
        "TikTok Adds Voice Notes",
        READ_URL,
        READ_URL,
        "Social Media Today",
        _utc(2026, 9, 8, 14, 30),
        "Andrew Hutchinson",
        None,
        None,
        None,
        None,
        _utc(2026, 9, 10, 12, 0),
        "reader-1",
    ),
    (
        "Signal Loss Rebuild",
        UNREAD_URL,
        UNREAD_URL,
        "MarTech",
        _utc(2026, 9, 9, 13, 0),
        None,
        None,
        None,
        None,
        None,
        None,
        None,
    ),
]


def test_search_annotates_read_state_by_default() -> None:
    results = search_articles("TikTok", conn=_SearchConnection(READ_SEARCH_ROWS))
    assert len(results) == 2
    by_url = {r["url"]: r for r in results}
    marked = by_url[READ_URL]
    assert set(marked.keys()) == SEARCH_EXPECTED_KEYS
    assert marked["read"] is True
    assert marked["read_by"] == "reader-1"
    assert _dt.datetime.fromisoformat(str(marked["read_at"])).tzinfo is not None
    unread = by_url[UNREAD_URL]
    assert unread["read"] is False
    assert unread["read_at"] is None
    assert unread["read_by"] is None


def test_search_exclude_read_filters_marked() -> None:
    included = search_articles("TikTok", conn=_SearchConnection(READ_SEARCH_ROWS))
    assert len(included) == 2
    filtered = search_articles(
        "TikTok", exclude_read=True, conn=_SearchConnection(READ_SEARCH_ROWS)
    )
    assert [r["url"] for r in filtered] == [UNREAD_URL]


def test_search_exclude_read_must_be_bool() -> None:
    for bad in ("yes", 1, 0, None):
        with pytest.raises(InvalidRequest):
            search_articles("TikTok", exclude_read=bad, conn=_SearchConnection())  # type: ignore[arg-type]


def test_period_annotates_read_state_by_default() -> None:
    ctx = get_period_context(
        date(2026, 9, 7), date(2026, 9, 13), conn=_PeriodConnection(READ_PERIOD_ROWS)
    )
    by_url = {a["url"]: a for a in ctx["recent_articles"]}
    assert len(by_url) == 2
    marked = by_url[READ_URL]
    assert set(marked.keys()) == PERIOD_EXPECTED_KEYS
    assert marked["read"] is True
    assert marked["read_by"] == "reader-1"
    assert _dt.datetime.fromisoformat(str(marked["read_at"])).tzinfo is not None
    unread = by_url[UNREAD_URL]
    assert unread["read"] is False
    assert unread["read_at"] is None
    assert unread["read_by"] is None


def test_period_exclude_read_filters_marked() -> None:
    ctx = get_period_context(
        date(2026, 9, 7), date(2026, 9, 13), conn=_PeriodConnection(READ_PERIOD_ROWS)
    )
    assert len(ctx["recent_articles"]) == 2
    filtered = get_period_context(
        date(2026, 9, 7),
        date(2026, 9, 13),
        exclude_read=True,
        conn=_PeriodConnection(READ_PERIOD_ROWS),
    )
    assert [a["url"] for a in filtered["recent_articles"]] == [UNREAD_URL]


def test_period_exclude_read_must_be_bool() -> None:
    for bad in ("yes", 1, 0, None):
        with pytest.raises(InvalidRequest):
            get_period_context(
                date(2026, 9, 7),
                date(2026, 9, 13),
                exclude_read=bad,  # type: ignore[arg-type]
                conn=_PeriodConnection(),
            )


_READ_STORE_ARTICLE_COLS = (
    "title",
    "url",
    "canonical_url",
    "source",
    "published_at",
    "author",
    "content",
    "flag_reason",
    "flag_detail",
    "flagged_at",
    "flagged_by",
)

READ_MARK_URL = "https://www.socialmediatoday.com/news/tiktok/1/?utm_source=rss#top"
READ_MARK_CANON = "https://www.socialmediatoday.com/news/tiktok/1/"


def _make_read_store() -> list[dict]:
    return [
        {
            "title": "TikTok Adds Voice Notes",
            "url": READ_MARK_URL,
            "canonical_url": READ_MARK_CANON,
            "source": "Social Media Today",
            "published_at": _utc(2026, 9, 8, 14, 30),
            "author": "Andrew Hutchinson",
            "content": "TikTok rolls out voice notes and image carousels for comments globally.",
            "flag_reason": None,
            "flag_detail": None,
            "flagged_at": None,
            "flagged_by": None,
            "read_at": None,
            "read_by": None,
        },
    ]


class _ReadCursor:
    """Store-backed fake: read UPDATEs, read-state fetch, then article SELECTs."""

    def __init__(self, store: list[dict]) -> None:
        self._store = store
        self._result: list[tuple] = []
        self.rowcount = -1

    def execute(self, sql: str, params: tuple | None = None) -> None:
        params = params or ()
        head = sql.strip().upper()
        if head.startswith("UPDATE"):
            if "READ_AT = NULL" in head:
                url_key, canon_key = params[0], params[1]
                matched = [
                    d for d in self._store if d["url"] == url_key or d["canonical_url"] == canon_key
                ]
                for doc in matched:
                    doc["read_at"] = None
                    doc["read_by"] = None
                self.rowcount = len(matched)
                self._result = []
            else:
                read_by, url_key, canon_key = params
                matched = [
                    d for d in self._store if d["url"] == url_key or d["canonical_url"] == canon_key
                ]
                for doc in matched:
                    doc["read_at"] = _dt.datetime.now(_dt.UTC)
                    doc["read_by"] = read_by
                self.rowcount = len(matched)
                self._result = []
            return
        if head.startswith("SELECT READ_AT"):
            url_key, canon_key = params[0], params[1]
            matched = [
                d for d in self._store if d["url"] == url_key or d["canonical_url"] == canon_key
            ]
            self._result = [(matched[0]["read_at"], matched[0]["read_by"])] if matched else []
            return
        if "CANONICAL_URL" in head and "WHERE" in head:
            key = params[0]
            by_canonical = "canonical_url" in sql.lower().split("where", 1)[1]
            if by_canonical:
                matched = [d for d in self._store if d["canonical_url"] == key]
            else:
                matched = [d for d in self._store if d["url"] == key]
            self._result = [
                tuple(d[c] for c in _READ_STORE_ARTICLE_COLS) + (d["read_at"], d["read_by"])
                for d in matched
            ]
            return
        self._result = []

    def fetchall(self) -> list[tuple]:
        return list(self._result)

    def close(self) -> None:
        pass


class _ReadConnection:
    def __init__(self, store: list[dict] | None = None) -> None:
        self.store = store if store is not None else _make_read_store()
        self.calls = 0
        self.closed = 0

    def cursor(self) -> _ReadCursor:
        self.calls += 1
        return _ReadCursor(self.store)

    def commit(self) -> None:
        pass

    def close(self) -> None:
        self.closed += 1


def test_mark_article_read_round_trip() -> None:
    conn = _ReadConnection()
    article = mark_article_read(READ_MARK_URL, read_by="reader-1", conn=conn)
    assert article["read"] is True
    assert article["read_by"] == "reader-1"
    assert _dt.datetime.fromisoformat(str(article["read_at"])).tzinfo is not None
    assert article["title"] == "TikTok Adds Voice Notes"
    assert article["url"] == READ_MARK_URL
    assert conn.closed == 0


def test_mark_article_read_clear_round_trip() -> None:
    conn = _ReadConnection()
    mark_article_read(READ_MARK_URL, read_by="reader-1", conn=conn)
    cleared = mark_article_read(READ_MARK_URL, clear=True, conn=conn)
    assert cleared["read"] is False
    assert cleared["read_at"] is None
    assert cleared["read_by"] is None
    assert cleared["title"] == "TikTok Adds Voice Notes"


def test_mark_article_read_blank_rejected_without_query() -> None:
    conn = _ReadConnection()
    for bad in ("", "   ", None, 123):
        with pytest.raises(InvalidRequest):
            mark_article_read(bad, read_by="r", conn=conn)  # type: ignore[arg-type]
    assert conn.calls == 0


def test_mark_article_read_bad_read_by_rejected_without_query() -> None:
    conn = _ReadConnection()
    with pytest.raises(InvalidRequest):
        mark_article_read(READ_MARK_URL, read_by="y" * 101, conn=conn)
    assert conn.calls == 0
    with pytest.raises(InvalidRequest):
        mark_article_read(READ_MARK_URL, read_by=123, conn=conn)  # type: ignore[arg-type]


def test_mark_article_read_unknown_raises_invalid_request() -> None:
    with pytest.raises(InvalidRequest):
        mark_article_read(
            "https://unknown.example/nope/", read_by="reader-1", conn=_ReadConnection()
        )
