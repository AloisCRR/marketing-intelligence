"""Read State seam tests — fake conns, no live Postgres.

TDD vertical slices against the new seam:
- `marketing_intelligence.read.mark_article_read` (lane; ValueError/TypeError only)

Locked contract (Read State lane):
- Documents identified by URL `identifier` (url OR canonical_url, same as flag.py).
- Nullable `read_at TIMESTAMPTZ NULL, read_by TEXT NULL` on `documents` (NULL=unread).
- Explicit mark/unmark only, idempotent re-mark overwrites.
"""

from __future__ import annotations

import datetime as _dt
import os
import sys
from typing import Any

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(os.path.dirname(_HERE), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import marketing_intelligence.read as read_lane  # noqa: E402


def _utc(*args: int) -> _dt.datetime:
    return _dt.datetime(*args, tzinfo=_dt.UTC)


ARTICLE_URL = "https://www.socialmediatoday.com/news/tiktok/1/?utm_source=rss#top"
ARTICLE_CANON = "https://www.socialmediatoday.com/news/tiktok/1/"
ARTICLE_TITLE = "TikTok Adds Voice Notes"
ARTICLE_CONTENT = (
    "TikTok rolls out voice notes and image carousels for comments globally, "
    "with a longform body that must come back whole."
)


def _make_store() -> list[dict[str, Any]]:
    return [
        {
            "title": ARTICLE_TITLE,
            "url": ARTICLE_URL,
            "canonical_url": ARTICLE_CANON,
            "source": "Social Media Today",
            "published_at": _utc(2026, 9, 8, 14, 30),
            "author": "Andrew Hutchinson",
            "content": ARTICLE_CONTENT,
            "flag_reason": None,
            "flag_detail": None,
            "flagged_at": None,
            "flagged_by": None,
            "read_at": None,
            "read_by": None,
        },
        {
            "title": "Signal Loss Rebuild",
            "url": "https://martech.org/signal-loss/2/",
            "canonical_url": "https://martech.org/signal-loss/2/",
            "source": "MarTech",
            "published_at": _utc(2026, 9, 9, 13, 0),
            "author": None,
            "content": "How marketers rebuild measurement after signal loss this quarter.",
            "flag_reason": None,
            "flag_detail": None,
            "flagged_at": None,
            "flagged_by": None,
            "read_at": None,
            "read_by": None,
        },
    ]


_ARTICLE_COLS = (
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


class _FakeCursor:
    """Cursor-style fake honouring exact-then-canonical SELECTs plus read UPDATEs."""

    def __init__(self, store: list[dict[str, Any]]) -> None:
        self._store = store
        self._result: list[tuple] = []
        self._fetchone_row: tuple | None = None
        self.rowcount = -1

    def _match(self, key: str, *, by_canonical: bool) -> list[dict[str, Any]]:
        if by_canonical:
            return [d for d in self._store if d["canonical_url"] == key]
        return [d for d in self._store if d["url"] == key]

    def execute(self, sql: str, params: tuple | None = None) -> None:
        params = params or ()
        head = sql.strip().upper()
        if head.startswith("UPDATE"):
            if "= NULL" in head or "READ_AT = NULL" in head:
                # Clear path: params (url_key, canonical_key).
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
                # Set path: params (read_by, url_key, canonical_key).
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
        if head.startswith("SELECT DT.TOPIC_SLUG"):
            # Effective-topic fetch (Ticket 20): these stores carry no topics.
            self._result = []
            return
        if "READ_AT" in head and "READ_BY" in head and head.startswith("SELECT"):
            # Read-state fetch: params (url_key, canonical_key).
            url_key, canon_key = params[0], params[1]
            matched = [
                d for d in self._store if d["url"] == url_key or d["canonical_url"] == canon_key
            ]
            if matched:
                self._result = [(matched[0]["read_at"], matched[0]["read_by"])]
            else:
                self._result = []
            return
        if "CANONICAL_URL" in sql.upper() and "WHERE" in sql.upper():
            matched = self._match(
                params[0], by_canonical="canonical_url" in sql.lower().split("where", 1)[1]
            )
            self._result = [tuple(d[c] for c in _ARTICLE_COLS) for d in matched]
            return
        self._result = []

    def fetchall(self) -> list[tuple]:
        return list(self._result)

    def fetchone(self) -> tuple | None:
        if self._result:
            return self._result[0]
        return self._fetchone_row

    def close(self) -> None:
        pass


class _FakeConnection:
    def __init__(self, store: list[dict[str, Any]] | None = None) -> None:
        self.store = store if store is not None else _make_store()
        self.calls = 0
        self.closed = 0
        self.committed = 0

    def cursor(self) -> _FakeCursor:
        self.calls += 1
        return _FakeCursor(self.store)

    def commit(self) -> None:
        self.committed += 1

    def close(self) -> None:
        self.closed += 1


# --- slice 1: mark sets read_at/read_by + re-read returns them -----------------


def test_mark_sets_read_at_and_read_by() -> None:
    conn = _FakeConnection()
    article = read_lane.mark_article_read(ARTICLE_URL, read_by="reader-1", conn=conn)
    assert article["read_by"] == "reader-1"
    parsed = _dt.datetime.fromisoformat(str(article["read_at"]))
    assert parsed.tzinfo is not None
    # Store truth mirrors the returned payload.
    assert conn.store[0]["read_by"] == "reader-1"
    assert conn.store[0]["read_at"] is not None


def test_mark_without_read_by_leaves_reader_null() -> None:
    conn = _FakeConnection()
    article = read_lane.mark_article_read(ARTICLE_URL, conn=conn)
    assert article["read_by"] is None
    assert _dt.datetime.fromisoformat(str(article["read_at"])).tzinfo is not None


def test_mark_via_canonical_identifier_matches_same_row() -> None:
    conn = _FakeConnection()
    article = read_lane.mark_article_read(ARTICLE_CANON, read_by="reader-1", conn=conn)
    assert article["url"] == ARTICLE_URL
    assert article["read_by"] == "reader-1"
    assert _dt.datetime.fromisoformat(str(article["read_at"])).tzinfo is not None


def test_mark_returns_base_article_fields() -> None:
    conn = _FakeConnection()
    article = read_lane.mark_article_read(ARTICLE_URL, read_by="reader-1", conn=conn)
    assert article["title"] == ARTICLE_TITLE
    assert article["content"] == ARTICLE_CONTENT
    assert article["url"] == ARTICLE_URL


def test_injected_conn_is_neither_committed_nor_closed() -> None:
    conn = _FakeConnection()
    read_lane.mark_article_read(ARTICLE_URL, read_by="reader-1", conn=conn)
    assert conn.calls >= 1
    assert conn.committed == 0
    assert conn.closed == 0


# --- slice 2: clear nulls ------------------------------------------------------


def test_clear_nulls_read_at_and_read_by() -> None:
    conn = _FakeConnection()
    read_lane.mark_article_read(ARTICLE_URL, read_by="reader-1", conn=conn)
    cleared = read_lane.mark_article_read(ARTICLE_URL, clear=True, conn=conn)
    assert cleared["read_at"] is None
    assert cleared["read_by"] is None
    assert cleared["title"] == ARTICLE_TITLE
    assert cleared["content"] == ARTICLE_CONTENT
    assert conn.store[0]["read_at"] is None
    assert conn.store[0]["read_by"] is None


def test_clear_ignores_read_by() -> None:
    conn = _FakeConnection()
    read_lane.mark_article_read(ARTICLE_URL, read_by="reader-1", conn=conn)
    cleared = read_lane.mark_article_read(
        ARTICLE_URL, read_by="someone-else", clear=True, conn=conn
    )
    assert cleared["read_at"] is None
    assert cleared["read_by"] is None


def test_clear_unread_article_is_noop_success() -> None:
    cleared = read_lane.mark_article_read(ARTICLE_URL, clear=True, conn=_FakeConnection())
    assert cleared["read_at"] is None
    assert cleared["read_by"] is None
    assert cleared["title"] == ARTICLE_TITLE


# --- slice 3: unknown + blank --------------------------------------------------


def test_unknown_identifier_raises_value_error() -> None:
    with pytest.raises(ValueError):
        read_lane.mark_article_read(
            "https://unknown.example/nope/", read_by="reader-1", conn=_FakeConnection()
        )


def test_clear_unknown_identifier_raises_value_error() -> None:
    with pytest.raises(ValueError):
        read_lane.mark_article_read(
            "https://unknown.example/nope/", clear=True, conn=_FakeConnection()
        )


def test_blank_identifier_rejected_without_query() -> None:
    conn = _FakeConnection()
    for bad in ("", "   ", None, 123):
        with pytest.raises(ValueError):
            read_lane.mark_article_read(bad, read_by="r", conn=conn)  # type: ignore[arg-type]
    assert conn.calls == 0


# --- slice 4: idempotent re-mark + validation ----------------------------------


def test_idempotent_remark_passes_and_refreshes() -> None:
    conn = _FakeConnection()
    first = read_lane.mark_article_read(ARTICLE_URL, read_by="reader-1", conn=conn)
    assert first["read_by"] == "reader-1"
    second = read_lane.mark_article_read(ARTICLE_URL, read_by="reader-1", conn=conn)
    assert second["read_by"] == "reader-1"
    assert _dt.datetime.fromisoformat(str(second["read_at"])).tzinfo is not None
    assert second["title"] == ARTICLE_TITLE


def test_read_by_over_100_chars_rejected_without_query() -> None:
    conn = _FakeConnection()
    with pytest.raises(ValueError):
        read_lane.mark_article_read(ARTICLE_URL, read_by="y" * 101, conn=conn)
    assert conn.calls == 0
    ok = read_lane.mark_article_read(ARTICLE_URL, read_by="z" * 100, conn=conn)
    assert ok["read_by"] == "z" * 100


def test_non_string_read_by_rejected() -> None:
    with pytest.raises(TypeError):
        read_lane.mark_article_read(ARTICLE_URL, read_by=123, conn=_FakeConnection())  # type: ignore[arg-type]


def test_rowcount_zero_means_unknown_without_fetch() -> None:
    class _PinnedUpdateCursor(_FakeCursor):
        def fetchall(self) -> list[tuple]:
            raise AssertionError("read lane must not fetchall() after UPDATE (no results)")

    class _RowcountPinnedConn(_FakeConnection):
        def cursor(self) -> Any:
            self.calls += 1
            if self.calls == 1:
                cur = _PinnedUpdateCursor(self.store)
                cur.rowcount = 0

                def _execute(sql: str, params: tuple | None = None) -> None:
                    assert sql.strip().upper().startswith("UPDATE")
                    cur.rowcount = 0
                    cur._result = []

                cur.execute = _execute  # type: ignore[method-assign]
                return cur
            return _FakeCursor(self.store)

    conn = _RowcountPinnedConn()
    with pytest.raises(ValueError):
        read_lane.mark_article_read(ARTICLE_URL, read_by="r", conn=conn)
