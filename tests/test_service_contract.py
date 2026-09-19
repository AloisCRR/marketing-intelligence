"""Service-adapter contract tests (Ticket 05) — fake conns, no live Postgres.

Covers the shared validated interface in `marketing_intelligence.service`:
- validation (blank keyword, bad limits, bad dates, unknown sources)
- 21-key search schema + provenance + tz-aware published_at
- period bundle grouped by source: shape, provenance, annotations, Panama tz
- string coercion for period bounds, bounded limit (101 rejected)
- per-reader `readers` log on every Document payload (Ticket 02)
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

from marketing_intelligence import period as _period  # noqa: E402
from marketing_intelligence import read as _read  # noqa: E402
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
    "importance_score",
    "importance_rationale",
    "importance_reporter",
    "importance_updated_at",
    "topics",
    "has_image_text",
    "readers",
}

#: The 13 headline keys inside each `{source, articles}` group — provenance,
#: the anyone-read cache plus the per-reader log, and the annotations. No
#: per-article `source` (it lives on the group) and no ordering index.
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


def _flatten(groups: list[dict]) -> list[dict]:
    """Headlines in bundle order: groups first-seen, articles within a group."""
    return [article for group in groups for article in group["articles"]]


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
    """Execute-style fake with real range/source/per-group cap semantics."""

    def __init__(self, rows: list[tuple]) -> None:
        self._rows = rows
        self.last_sql: str | None = None
        self.last_params: tuple | None = None
        self._result: list[tuple] = []

    def execute(self, sql: str, params: tuple | None = None) -> _PeriodCursor:
        assert params is not None
        self.last_sql = sql
        self.last_params = params
        # Grouped lane's fixed parameter order: bounds, source names, the
        # optional annotation filters, then `limit` — the per-source cap, with
        # no outer LIMIT.
        start, end, names = params[0], params[1], params[2]
        limit = int(params[-1])
        kept = [r for r in self._rows if r[4] >= start and r[4] < end and r[3] in set(names)]
        # Read-aware: the exclude_read lane adds `AND d.read_at IS NULL`.
        if "read_at is null" in sql.lower():
            kept = [r for r in kept if not (len(r) > 10 and r[10] is not None)]
        kept.sort(key=lambda r: r[4], reverse=True)
        # Mirror PARTITION BY source <= limit: each source keeps its first
        # `limit` rows in the global order; every source with a hit survives.
        seen: dict[str, int] = {}
        capped: list[tuple] = []
        for row in kept:
            count = seen.get(row[3], 0) + 1
            seen[row[3]] = count
            if count <= limit:
                capped.append(row)
        self._result = capped
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


def test_search_returns_twenty_one_key_schema_with_provenance() -> None:
    results = search_articles("TikTok", conn=_SearchConnection())
    assert len(results) >= 1
    for row in results:
        assert set(row.keys()) == SEARCH_EXPECTED_KEYS
        assert row["readers"] == []  # Ticket 02: short fake rows are unread
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
    # No empty analytics placeholders: period + source-grouped headlines only.
    assert set(ctx) == {"period", "recent_articles"}
    assert set(ctx["period"]) == {"from", "to", "timezone"}
    assert ctx["period"]["timezone"] == "America/Panama"
    groups = ctx["recent_articles"]
    assert all(set(group) == {"source", "articles"} for group in groups)
    # Groups follow the first-seen order of the globally recency-ordered rows.
    assert [g["source"] for g in groups] == ["MarTech", "Social Media Today"]
    articles = _flatten(groups)
    assert [a["title"] for a in articles] == ["Signal Loss Rebuild", "TikTok Adds Voice Notes"]
    for article in articles:
        assert set(article) == PERIOD_HEADLINE_KEYS
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
    assert [g["source"] for g in ctx["recent_articles"]] == ["MarTech"]
    assert [a["title"] for a in _flatten(ctx["recent_articles"])] == ["Signal Loss Rebuild"]


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


def test_period_limit_validated_and_forwarded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict = {}

    def fake(from_date: object, to_date: object, **kw: object) -> dict:
        seen.update(kw)
        return {"period": {}, "recent_articles": []}

    monkeypatch.setattr(_period, "get_period_context", fake)
    conn = _PeriodConnection()
    for bad in (0, -1, MAX_LIMIT + 1, "2", 2.5, True):
        with pytest.raises(InvalidRequest):
            get_period_context(
                date(2026, 9, 7),
                date(2026, 9, 13),
                conn=conn,
                limit=bad,  # type: ignore[arg-type]
            )
    assert seen == {}  # rejected before the lane runs
    get_period_context(date(2026, 9, 7), date(2026, 9, 13), conn=conn, limit=3)
    assert seen["limit"] == 3
    get_period_context(date(2026, 9, 7), date(2026, 9, 13), conn=conn)
    assert seen["limit"] == DEFAULT_PERIOD_LIMIT


def test_period_limit_caps_each_source_group_and_composes_with_sources() -> None:
    """Adapter-level flood check: one source cannot dominate the bundle."""
    rows = [
        (
            f"Flood {i}",
            f"https://www.socialmediatoday.com/flood/{i}/",
            f"https://www.socialmediatoday.com/flood/{i}/",
            "Social Media Today",
            _utc(2026, 9, 11, 12 - i, 0),
            None,
            None,
            None,
            None,
            None,
            None,
            None,
        )
        for i in range(8)
    ] + [
        (
            "Signal Loss Rebuild",
            "https://martech.org/signal-loss/2/",
            "https://martech.org/signal-loss/2/",
            "MarTech",
            _utc(2026, 9, 10, 13, 0),
            None,
            None,
            None,
            None,
            None,
            None,
            None,
        )
    ]
    # The default limit keeps the whole flood in its own group.
    untrimmed = get_period_context(
        date(2026, 9, 7), date(2026, 9, 13), conn=_PeriodConnection(rows)
    )
    assert {g["source"]: len(g["articles"]) for g in untrimmed["recent_articles"]} == {
        "Social Media Today": 8,
        "MarTech": 1,
    }
    capped = get_period_context(
        date(2026, 9, 7),
        date(2026, 9, 13),
        conn=_PeriodConnection(rows),
        limit=2,
    )
    groups = capped["recent_articles"]
    assert [g["source"] for g in groups] == ["Social Media Today", "MarTech"]
    assert [len(g["articles"]) for g in groups] == [2, 1]
    assert [a["title"] for a in groups[0]["articles"]] == ["Flood 0", "Flood 1"]
    # Composes with the allowlist: the cap still applies within the narrowed set.
    narrowed = get_period_context(
        date(2026, 9, 7),
        date(2026, 9, 13),
        conn=_PeriodConnection(rows),
        sources=["Social Media Today"],
        limit=2,
    )
    assert [g["source"] for g in narrowed["recent_articles"]] == ["Social Media Today"]
    assert [len(g["articles"]) for g in narrowed["recent_articles"]] == [2]


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
    # Ticket 02 rows carry the `readers` list; these short fake rows are unread.
    assert marked["readers"] == []
    assert unread["readers"] == []


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
    by_url = {a["url"]: a for a in _flatten(ctx["recent_articles"])}
    assert len(by_url) == 2
    marked = by_url[READ_URL]
    assert set(marked.keys()) == PERIOD_HEADLINE_KEYS
    assert marked["read"] is True
    assert marked["read_by"] == "reader-1"
    assert _dt.datetime.fromisoformat(str(marked["read_at"])).tzinfo is not None
    unread = by_url[UNREAD_URL]
    assert unread["read"] is False
    assert unread["read_at"] is None
    assert unread["read_by"] is None
    # Ticket 02 rows carry the `readers` list; these short fake rows are unread.
    assert marked["readers"] == []
    assert unread["readers"] == []


def test_period_exclude_read_filters_marked() -> None:
    ctx = get_period_context(
        date(2026, 9, 7), date(2026, 9, 13), conn=_PeriodConnection(READ_PERIOD_ROWS)
    )
    assert len(_flatten(ctx["recent_articles"])) == 2
    filtered = get_period_context(
        date(2026, 9, 7),
        date(2026, 9, 13),
        exclude_read=True,
        conn=_PeriodConnection(READ_PERIOD_ROWS),
    )
    assert [a["url"] for a in _flatten(filtered["recent_articles"])] == [UNREAD_URL]


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
#: Stable fake Document id (the lane resolves url/canonical -> id first).
READ_MARK_ID = "0f5e7c62-2f0e-4d0a-9b7f-1a2b3c4d5e6f"


def _make_read_store() -> list[dict]:
    return [
        {
            "id": READ_MARK_ID,
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
            # Per-reader Read State set (Ticket 01 side table).
            "reads": {},
        },
    ]


class _ReadCursor:
    """Store-backed fake for the per-reader Read State lane (Ticket 01).

    Routes the lane's statement families — id resolution, the
    ``document_reads`` INSERT/DELETE/latest-SELECT set writes, the
    ``documents`` cache refresh, read-state fetch, then article SELECTs —
    against an in-memory store, so round-trips stay observable.
    """

    def __init__(self, store: list[dict]) -> None:
        self._store = store
        self._result: list[tuple] = []
        self.rowcount = -1

    def _match(self, url_key: str, canon_key: str) -> list[dict]:
        return [d for d in self._store if d["url"] == url_key or d["canonical_url"] == canon_key]

    def _by_id(self, doc_id: object) -> dict | None:
        return next((d for d in self._store if d["id"] == doc_id), None)

    def _latest(self, doc: dict) -> tuple | None:
        if not doc["reads"]:
            return None
        reader = max(doc["reads"], key=lambda r: (doc["reads"][r], r))
        return (doc["reads"][reader], reader)

    def execute(self, sql: str, params: tuple | None = None) -> None:
        params = params or ()
        head = sql.strip().upper()
        if head.startswith("SELECT R.READER"):
            # Per-reader `readers` fetch (Ticket 02): (reader, read_at) rows, reader asc.
            url_key, canon_key = params[0], params[1]
            matched = self._match(url_key, canon_key)
            self._result = (
                sorted(matched[0]["reads"].items(), key=lambda pair: pair[0]) if matched else []
            )
            return
        if "DOCUMENT_READS" in head:
            if head.startswith("INSERT"):
                doc_id, reader = params
                doc = self._by_id(doc_id)
                if doc is None:
                    self.rowcount = 0
                    self._result = []
                    return
                now = _dt.datetime.now(_dt.UTC)
                doc["reads"][reader] = now
                self.rowcount = 1
                self._result = [(now,)]
                return
            if head.startswith("DELETE"):
                doc = self._by_id(params[0])
                if doc is None:
                    self.rowcount = 0
                    return
                if len(params) > 1:  # per-reader clear
                    removed = doc["reads"].pop(params[1], None)
                    self.rowcount = 0 if removed is None else 1
                else:  # clear every reader
                    self.rowcount = len(doc["reads"])
                    doc["reads"].clear()
                self._result = []
                return
            # Latest-mark fetch: (read_at, reader) or no rows.
            doc = self._by_id(params[0])
            latest = self._latest(doc) if doc is not None else None
            self._result = [] if latest is None else [latest]
            return
        if head.startswith("UPDATE"):
            read_at, read_by, doc_id = params
            doc = self._by_id(doc_id)
            if doc is not None:
                doc["read_at"] = read_at
                doc["read_by"] = read_by
            self.rowcount = 0 if doc is None else 1
            self._result = []
            return
        if head.startswith("SELECT ID"):
            url_key, canon_key = params[0], params[1]
            matched = self._match(url_key, canon_key)
            self._result = [(matched[0]["id"],)] if matched else []
            return
        if head.startswith("SELECT READ_AT, READ_BY"):
            url_key, canon_key = params[0], params[1]
            matched = self._match(url_key, canon_key)
            self._result = [(matched[0]["read_at"], matched[0]["read_by"])] if matched else []
            return
        if head.startswith("SELECT DT.TOPIC_SLUG"):
            # Effective-topic fetch (Ticket 20): these stores carry no topics.
            self._result = []
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

    def fetchone(self) -> tuple | None:
        return self._result[0] if self._result else None

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
    assert set(conn.store[0]["reads"]) == {"reader-1"}
    # Ticket 02: the payload pairs the anyone-read cache with the `readers` list.
    assert [r["reader"] for r in article["readers"]] == ["reader-1"]
    assert _dt.datetime.fromisoformat(article["readers"][0]["read_at"]).tzinfo is not None
    assert conn.closed == 0


def test_mark_article_read_keeps_one_row_per_reader() -> None:
    """A second reader's mark must not erase the first (Ticket 01)."""
    conn = _ReadConnection()
    mark_article_read(READ_MARK_URL, read_by="reader-1", conn=conn)
    second = mark_article_read(READ_MARK_CANON, read_by="reader-2", conn=conn)
    assert set(conn.store[0]["reads"]) == {"reader-1", "reader-2"}
    # The legacy cache still carries the latest mark (anyone-read unchanged).
    assert second["read"] is True
    assert second["read_by"] == "reader-2"
    # ...while `readers` reports both, sorted by reader (Ticket 02).
    assert [r["reader"] for r in second["readers"]] == ["reader-1", "reader-2"]


def test_mark_article_read_clear_round_trip() -> None:
    conn = _ReadConnection()
    mark_article_read(READ_MARK_URL, read_by="reader-1", conn=conn)
    cleared = mark_article_read(READ_MARK_URL, clear=True, conn=conn)
    assert cleared["read"] is False
    assert cleared["read_at"] is None
    assert cleared["read_by"] is None
    assert cleared["title"] == "TikTok Adds Voice Notes"
    assert conn.store[0]["reads"] == {}
    assert cleared["readers"] == []  # every reader row dropped (Ticket 02)


def test_clear_with_read_by_drops_only_that_reader() -> None:
    conn = _ReadConnection()
    mark_article_read(READ_MARK_URL, read_by="reader-1", conn=conn)
    mark_article_read(READ_MARK_URL, read_by="reader-2", conn=conn)
    cleared = mark_article_read(READ_MARK_URL, read_by="reader-2", clear=True, conn=conn)
    assert set(conn.store[0]["reads"]) == {"reader-1"}
    # Cache falls back to the survivor, so the article still reads as read.
    assert cleared["read"] is True
    assert cleared["read_by"] == "reader-1"
    # The surviving reader is the only one left in the log.
    assert [r["reader"] for r in cleared["readers"]] == ["reader-1"]


def test_clear_forwards_optional_read_by_to_lane(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict] = []

    def fake(
        identifier: str, read_by: object = None, clear: object = False, conn: object = None
    ) -> dict:
        seen.append({"identifier": identifier, "read_by": read_by, "clear": clear})
        return {"read_at": None, "read_by": None}

    monkeypatch.setattr(_read, "mark_article_read", fake)
    mark_article_read(READ_MARK_URL, read_by=" reader-1 ", clear=True)
    mark_article_read(READ_MARK_URL, clear=True)
    mark_article_read(READ_MARK_URL, read_by="   ", clear=True)
    assert [s["read_by"] for s in seen] == ["reader-1", None, None]
    assert all(s["clear"] is True for s in seen)


def test_mark_without_read_by_rejected_without_query() -> None:
    """Read State is per-reader: an unattributed mark is refused up front."""
    conn = _ReadConnection()
    for missing in (None, "", "   "):
        with pytest.raises(InvalidRequest):
            mark_article_read(READ_MARK_URL, read_by=missing, conn=conn)
    assert conn.calls == 0


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
    with pytest.raises(InvalidRequest):
        mark_article_read("https://unknown.example/nope/", clear=True, conn=_ReadConnection())
