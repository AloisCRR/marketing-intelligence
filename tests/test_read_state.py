"""Read State seam tests — fake conns, no live Postgres.

TDD vertical slices against the new seam:
- `marketing_intelligence.read.mark_article_read` (lane; ValueError/TypeError only)

Locked contract (Read State lane, per-reader marks):
- Documents identified by URL `identifier` (url OR canonical_url, same as flag.py).
- Marks live in the `document_reads` set: one row per (document_id, reader);
  `documents.read_at, read_by` is only the latest-mark cache.
- `read_by` is required to mark; `clear=True` drops one reader's row when
  `read_by` is given, every reader's row otherwise. Explicit mark/unmark only,
  re-marking the same reader refreshes its own read_at. A per-reader clear that
  removed no row leaves the latest-mark cache untouched, so a legacy
  single-slot mark keeps reading as read.
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
ARTICLE_ID = "11111111-1111-1111-1111-111111111111"
ARTICLE_TITLE = "TikTok Adds Voice Notes"
ARTICLE_CONTENT = (
    "TikTok rolls out voice notes and image carousels for comments globally, "
    "with a longform body that must come back whole."
)
OTHER_ID = "22222222-2222-2222-2222-222222222222"

#: Fake clock origin; each stamped write advances one second.
_CLOCK_ORIGIN = _dt.datetime(2026, 9, 14, 12, 0, tzinfo=_dt.UTC)


def _make_store() -> list[dict[str, Any]]:
    return [
        {
            "id": ARTICLE_ID,
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
            "id": OTHER_ID,
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
    """Cursor-style fake: the document_reads set, the documents cache, reads."""

    def __init__(self, conn: _FakeConnection) -> None:
        self._conn = conn
        self._store = conn.store
        self._result: list[tuple] = []
        self.rowcount = -1

    def _doc(self, key: str, canonical: str) -> dict[str, Any] | None:
        for doc in self._store:
            if doc["url"] == key or doc["canonical_url"] == canonical:
                return doc
        return None

    def _marks(self, head: str, params: tuple) -> None:
        marks = self._conn.readers
        if head.startswith("INSERT"):
            # Mark upsert: params (document_id, reader); RETURNING read_at.
            document_id, reader = params
            stamp = self._conn.tick()
            marks.setdefault(document_id, {})[reader] = stamp
            self.rowcount = 1
            self._result = [(stamp,)]
            return
        if head.startswith("DELETE"):
            document_id = params[0]
            if len(params) > 1:
                # Per-reader clear: absent reader is a no-op success.
                present = marks.get(document_id, {}).pop(params[1], None)
                self.rowcount = 0 if present is None else 1
            else:
                self.rowcount = len(marks.pop(document_id, {}))
            self._result = []
            return
        # Latest remaining mark for the cache rebuild: params (document_id,).
        current = marks.get(params[0], {})
        if current:
            reader, stamp = max(current.items(), key=lambda item: (item[1], item[0]))
            self._result = [(stamp, reader)]
        else:
            self._result = []

    def execute(self, sql: str, params: tuple | None = None) -> None:
        params = params or ()
        head = sql.strip().upper()
        if "DOCUMENT_READS" in head:
            self._marks(head, params)
            return
        if head.startswith("SELECT ID FROM DOCUMENTS"):
            # Identity resolution: params (url_key, canonical_key).
            doc = self._doc(params[0], params[1])
            self._result = [(doc["id"],)] if doc else []
            return
        if head.startswith("UPDATE") and "SET READ_AT = %S" in head:
            # Cache refresh: params (read_at, reader, document_id).
            read_at, reader, document_id = params
            self._conn.cache_writes += 1
            for doc in self._store:
                if doc["id"] == document_id:
                    doc["read_at"] = read_at
                    doc["read_by"] = reader
            self.rowcount = 1
            self._result = []
            return
        if head.startswith("SELECT DT.TOPIC_SLUG"):
            # Effective-topic fetch (Ticket 20): these stores carry no topics.
            self._result = []
            return
        if "READ_AT" in head and "READ_BY" in head and head.startswith("SELECT"):
            # Read-state fetch: params (url_key, canonical_key).
            url_key, canon_key = params[0], params[1]
            doc = self._doc(url_key, canon_key)
            self._result = [(doc["read_at"], doc["read_by"])] if doc else []
            return
        if "CANONICAL_URL" in head and "WHERE" in head:
            # Article lookup: one param, keyed by url or canonical_url only.
            key = params[0]
            tail = sql.lower().split("where", 1)[1]
            column = "canonical_url" if "canonical_url" in tail else "url"
            matched = next((d for d in self._store if d[column] == key), None)
            self._result = [tuple(matched[c] for c in _ARTICLE_COLS)] if matched is not None else []
            return
        self._result = []

    def fetchall(self) -> list[tuple]:
        return list(self._result)

    def fetchone(self) -> tuple | None:
        return self._result[0] if self._result else None

    def close(self) -> None:
        pass


class _FakeConnection:
    def __init__(self, store: list[dict[str, Any]] | None = None) -> None:
        self.store = store if store is not None else _make_store()
        #: document_id -> {reader: read_at} — the document_reads set.
        self.readers: dict[Any, dict[str, _dt.datetime]] = {}
        #: How many times the documents latest-mark cache was rewritten.
        self.cache_writes = 0
        self.calls = 0
        self.closed = 0
        self.committed = 0
        self._ticks = 0

    def tick(self) -> _dt.datetime:
        """Monotonic stand-in for Postgres ``now()`` (one tick per mark)."""
        self._ticks += 1
        return _CLOCK_ORIGIN + _dt.timedelta(seconds=self._ticks)

    def cursor(self) -> _FakeCursor:
        self.calls += 1
        return _FakeCursor(self)

    def commit(self) -> None:
        self.committed += 1

    def close(self) -> None:
        self.closed += 1


# --- slice 1: mark writes one reader row + refreshes the cache -----------------


def test_mark_sets_read_at_and_read_by() -> None:
    conn = _FakeConnection()
    article = read_lane.mark_article_read(ARTICLE_URL, read_by="reader-1", conn=conn)
    assert article["read_by"] == "reader-1"
    parsed = _dt.datetime.fromisoformat(str(article["read_at"]))
    assert parsed.tzinfo is not None
    assert article["read"] is True
    # Set truth: one reader row, and the cache mirrors it.
    assert conn.readers[ARTICLE_ID] == {"reader-1": parsed}
    assert conn.store[0]["read_by"] == "reader-1"
    assert conn.store[0]["read_at"] == parsed
    assert conn.store[1]["read_at"] is None


def test_mark_requires_read_by() -> None:
    conn = _FakeConnection()
    for missing in (None, "", "   "):
        with pytest.raises(ValueError):
            read_lane.mark_article_read(ARTICLE_URL, read_by=missing, conn=conn)
    assert conn.calls == 0
    assert conn.readers == {}
    assert conn.store[0]["read_at"] is None


def test_mark_via_canonical_identifier_matches_same_row() -> None:
    conn = _FakeConnection()
    article = read_lane.mark_article_read(ARTICLE_CANON, read_by="reader-1", conn=conn)
    assert article["url"] == ARTICLE_URL
    assert article["read_by"] == "reader-1"
    assert _dt.datetime.fromisoformat(str(article["read_at"])).tzinfo is not None
    assert list(conn.readers) == [ARTICLE_ID]


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


# --- slice 2: per-reader set semantics ----------------------------------------


def test_two_readers_keep_separate_rows_and_cache_latest() -> None:
    conn = _FakeConnection()
    read_lane.mark_article_read(ARTICLE_URL, read_by="reader-1", conn=conn)
    second = read_lane.mark_article_read(ARTICLE_URL, read_by="reader-2", conn=conn)
    first_at = conn.readers[ARTICLE_ID]["reader-1"]
    second_at = conn.readers[ARTICLE_ID]["reader-2"]
    assert second_at > first_at
    assert set(conn.readers[ARTICLE_ID]) == {"reader-1", "reader-2"}
    # The newer mark wins the cache; the older reader's row is untouched.
    assert second["read_by"] == "reader-2"
    assert _dt.datetime.fromisoformat(str(second["read_at"])) == second_at
    assert conn.store[0]["read_by"] == "reader-2"
    assert conn.store[0]["read_at"] == second_at


def test_remark_refreshes_only_that_readers_timestamp() -> None:
    conn = _FakeConnection()
    read_lane.mark_article_read(ARTICLE_URL, read_by="reader-1", conn=conn)
    read_lane.mark_article_read(ARTICLE_URL, read_by="reader-2", conn=conn)
    other_at = conn.readers[ARTICLE_ID]["reader-2"]
    third = read_lane.mark_article_read(ARTICLE_URL, read_by="reader-1", conn=conn)
    assert conn.readers[ARTICLE_ID]["reader-1"] > other_at
    assert conn.readers[ARTICLE_ID]["reader-2"] == other_at
    assert third["read_by"] == "reader-1"
    assert third["title"] == ARTICLE_TITLE
    assert third["read"] is True


# --- slice 3: clear drops rows and re-caches ----------------------------------


def test_clear_with_reader_removes_only_that_readers_row() -> None:
    conn = _FakeConnection()
    read_lane.mark_article_read(ARTICLE_URL, read_by="reader-1", conn=conn)
    first_at = conn.readers[ARTICLE_ID]["reader-1"]
    read_lane.mark_article_read(ARTICLE_URL, read_by="reader-2", conn=conn)
    cleared = read_lane.mark_article_read(ARTICLE_URL, read_by="reader-2", clear=True, conn=conn)
    assert list(conn.readers[ARTICLE_ID]) == ["reader-1"]
    assert conn.readers[ARTICLE_ID]["reader-1"] == first_at
    # Cache falls back to the newest mark still standing.
    assert cleared["read_by"] == "reader-1"
    assert _dt.datetime.fromisoformat(str(cleared["read_at"])) == first_at
    assert cleared["read"] is True


def test_clear_with_absent_reader_is_noop_success() -> None:
    conn = _FakeConnection()
    read_lane.mark_article_read(ARTICLE_URL, read_by="reader-1", conn=conn)
    kept = conn.readers[ARTICLE_ID]["reader-1"]
    cleared = read_lane.mark_article_read(ARTICLE_URL, read_by="ghost", clear=True, conn=conn)
    assert conn.readers[ARTICLE_ID] == {"reader-1": kept}
    assert cleared["read_by"] == "reader-1"
    assert cleared["title"] == ARTICLE_TITLE


def test_clear_with_absent_reader_leaves_legacy_cache_alone() -> None:
    """A never-marked reader's clear must not drop a legacy single-slot mark.

    Legacy reader-less state (ADR-0015): the documents cache holds the mark, the
    ``document_reads`` set is empty. There is no set row to re-cache from, so a
    no-op clear that rewrote the cache would silently flip the article unread.
    """
    conn = _FakeConnection()
    legacy_at = _utc(2026, 9, 10, 8, 0)
    conn.store[0]["read_at"] = legacy_at
    conn.store[0]["read_by"] = None
    assert conn.readers == {}
    cleared = read_lane.mark_article_read(
        ARTICLE_URL, read_by="ghost-never-marked", clear=True, conn=conn
    )
    # No row to drop, no cache write, and the legacy mark still reads as read.
    assert conn.readers == {}
    assert conn.cache_writes == 0
    assert conn.store[0]["read_at"] == legacy_at
    assert conn.store[0]["read_by"] is None
    assert cleared["read"] is True
    assert cleared["read_by"] is None
    assert _dt.datetime.fromisoformat(str(cleared["read_at"])) == legacy_at
    assert cleared["title"] == ARTICLE_TITLE


def test_clear_all_removes_every_row_and_nulls_cache() -> None:
    conn = _FakeConnection()
    read_lane.mark_article_read(ARTICLE_URL, read_by="reader-1", conn=conn)
    read_lane.mark_article_read(ARTICLE_URL, read_by="reader-2", conn=conn)
    cleared = read_lane.mark_article_read(ARTICLE_URL, clear=True, conn=conn)
    assert conn.readers == {}
    assert conn.store[0]["read_at"] is None
    assert conn.store[0]["read_by"] is None
    assert cleared["read"] is False
    assert cleared["read_at"] is None
    assert cleared["read_by"] is None
    assert cleared["title"] == ARTICLE_TITLE
    assert cleared["content"] == ARTICLE_CONTENT


def test_clear_last_reader_leaves_article_unread() -> None:
    conn = _FakeConnection()
    read_lane.mark_article_read(ARTICLE_URL, read_by="reader-1", conn=conn)
    cleared = read_lane.mark_article_read(ARTICLE_URL, read_by="reader-1", clear=True, conn=conn)
    assert not conn.readers.get(ARTICLE_ID)
    assert cleared["read"] is False
    assert cleared["read_at"] is None
    assert cleared["read_by"] is None


def test_clear_unread_article_is_noop_success() -> None:
    cleared = read_lane.mark_article_read(ARTICLE_URL, clear=True, conn=_FakeConnection())
    assert cleared["read_at"] is None
    assert cleared["read_by"] is None
    assert cleared["title"] == ARTICLE_TITLE


# --- slice 4: unknown + blank --------------------------------------------------


def test_unknown_identifier_raises_value_error() -> None:
    conn = _FakeConnection()
    with pytest.raises(ValueError):
        read_lane.mark_article_read("https://unknown.example/nope/", read_by="reader-1", conn=conn)
    # Resolution runs first: an unknown URL leaves no row and no cache write.
    assert conn.readers == {}
    assert [doc["read_at"] for doc in conn.store] == [None, None]


def test_clear_unknown_identifier_raises_value_error() -> None:
    conn = _FakeConnection()
    with pytest.raises(ValueError):
        read_lane.mark_article_read("https://unknown.example/nope/", clear=True, conn=conn)
    assert conn.readers == {}


def test_blank_identifier_rejected_without_query() -> None:
    conn = _FakeConnection()
    for bad in ("", "   ", None, 123):
        with pytest.raises(ValueError):
            read_lane.mark_article_read(bad, read_by="r", conn=conn)  # type: ignore[arg-type]
    assert conn.calls == 0


# --- slice 5: read_by validation -----------------------------------------------


def test_read_by_over_100_chars_rejected_without_query() -> None:
    conn = _FakeConnection()
    with pytest.raises(ValueError):
        read_lane.mark_article_read(ARTICLE_URL, read_by="y" * 101, conn=conn)
    assert conn.calls == 0
    ok = read_lane.mark_article_read(ARTICLE_URL, read_by="z" * 100, conn=conn)
    assert ok["read_by"] == "z" * 100


def test_non_string_read_by_rejected() -> None:
    conn = _FakeConnection()
    with pytest.raises(TypeError):
        read_lane.mark_article_read(ARTICLE_URL, read_by=123, conn=conn)  # type: ignore[arg-type]
    assert conn.calls == 0


def test_overlong_read_by_rejected_on_clear_path() -> None:
    conn = _FakeConnection()
    with pytest.raises(ValueError):
        read_lane.mark_article_read(ARTICLE_URL, read_by="y" * 101, clear=True, conn=conn)
    assert conn.calls == 0
