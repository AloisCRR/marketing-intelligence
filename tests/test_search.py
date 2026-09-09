"""Contract tests for marketing_intelligence.search.search_articles (keyword search + provenance).

TDD: written FIRST against the foundation seam
(`marketing_intelligence.db.get_connection`, tables sources(name) + documents(...)).
Uses a fake DB-API connection via monkeypatch — no live Postgres needed.
"""

from __future__ import annotations

import datetime as _dt
import os
import sys

# Make `marketing_intelligence.*` (src layout) importable without packaging config,
# so this single file runs greenfield via `pytest tests/test_search.py`.
_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(os.path.dirname(_HERE), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import marketing_intelligence.search as search_module  # noqa: E402


def _dt_utc(*args: int) -> _dt.datetime:
    return _dt.datetime(*args, tzinfo=_dt.UTC)


ROWS = [
    # (title, url, canonical_url, source, published_at, author, content,
    #  flag_reason, flag_detail, flagged_at, flagged_by, read_at, read_by)
    (
        "Gen Z marketing trends to watch",
        "https://socialmediatoday.com/articles/1",
        "https://socialmediatoday.com/articles/1",
        "Social Media Today",
        _dt_utc(2026, 8, 20, 12, 0, 0),
        "Jane Doe",
        "A longform piece about Gen Z marketing trends and creator budgets "
        "with plenty of surrounding context for the snippet window.",
        None,
        None,
        None,
        None,
        None,
        None,
    ),
    (
        "Creators reshape Gen Z budgets",
        "https://socialmediatoday.com/articles/2",
        "https://socialmediatoday.com/articles/2",
        "Social Media Today",
        _dt_utc(2026, 8, 25, 12, 0, 0),
        None,
        "How creators reshape Gen Z budgets across social platforms this quarter.",
        None,
        None,
        None,
        None,
        None,
        None,
    ),
    (
        "Unrelated jewellery piece",
        "https://example.com/jewellery/9",
        "https://example.com/jewellery/9",
        "Professional Jeweller",
        _dt_utc(2026, 8, 26, 12, 0, 0),
        "John Smith",
        "Nothing relevant here about hallmarking and retail footfall.",
        None,
        None,
        None,
        None,
        None,
        None,
    ),
]


class FakeCursor:
    """Minimal DB-API cursor: records SQL/params, serves preset rows."""

    def __init__(self, rows: list[tuple]) -> None:
        self._rows = rows
        self.last_sql: str | None = None
        self.last_params: tuple | None = None

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def execute(self, sql: str, params: tuple | None = None) -> None:
        self.last_sql = sql
        self.last_params = params

    def fetchall(self) -> list[tuple]:
        rows = list(self._rows)
        # Read-aware: the exclude_read lane adds `AND d.read_at IS NULL`.
        if self.last_sql is not None and "read_at is null" in self.last_sql.lower():
            rows = [r for r in rows if not (len(r) > 11 and r[11] is not None)]
        # Honour the LIMIT param (last positional param) like Postgres would.
        if self.last_params:
            try:
                limit = int(self.last_params[-1])
                return list(rows[:limit])
            except (ValueError, TypeError):
                pass
        return list(rows)

    def close(self) -> None:
        pass


class FakeConnection:
    def __init__(self, rows: list[tuple], cursor_holder: dict) -> None:
        self._rows = rows
        self._holder = cursor_holder

    def cursor(self) -> FakeCursor:
        cur = FakeCursor(self._rows)
        self._holder["cursor"] = cur
        return cur

    def close(self) -> None:
        pass


def _patch(monkeypatch, rows: list[tuple]) -> dict:
    """Monkeypatch marketing_intelligence.search.get_connection with a fake; return holder."""
    holder: dict = {}
    calls: list[int] = []

    def fake_get_connection():  # type: ignore[no-untyped-def]
        calls.append(1)
        return FakeConnection(rows, holder)

    holder["calls"] = calls
    monkeypatch.setattr(search_module, "get_connection", fake_get_connection)
    return holder


def test_keyword_match_returns_provenance(monkeypatch) -> None:
    _patch(monkeypatch, ROWS)
    results = search_module.search_articles("Gen Z")

    assert len(results) >= 2
    for r in results:
        assert set(r.keys()) == {
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
        # Provenance: every result carries its source name.
        assert r["source"] in ("Social Media Today", "Professional Jeweller")
    smt = [r for r in results if r["source"] == "Social Media Today"]
    assert len(smt) >= 2
    # tz-aware isoformat published_at
    for r in results:
        parsed = _dt.datetime.fromisoformat(str(r["published_at"]))
        assert parsed.tzinfo is not None
    # snippet excerpts content around the match
    assert "gen z" in str(smt[0]["snippet"]).lower()
    # nullable author passes through
    authors = {r["title"]: r["author"] for r in results}
    assert authors["Creators reshape Gen Z budgets"] is None


def test_limit_honored(monkeypatch) -> None:
    _patch(monkeypatch, ROWS)
    results = search_module.search_articles("Gen Z", limit=1)
    assert len(results) == 1


def test_empty_keyword_returns_empty_without_query(monkeypatch) -> None:
    holder = _patch(monkeypatch, ROWS)
    assert search_module.search_articles("") == []
    assert search_module.search_articles("   ") == []
    assert holder["calls"] == []  # no DB round-trip for empty keyword


def test_sql_is_parameterized(monkeypatch) -> None:
    holder = _patch(monkeypatch, ROWS)
    keyword = "Gen Z'; DROP TABLE documents; --"
    search_module.search_articles(keyword)
    cur: FakeCursor = holder["cursor"]
    assert cur.last_sql is not None
    # Keyword must NOT be interpolated into the SQL text (no f-string SQL).
    assert keyword not in cur.last_sql
    assert "DROP TABLE" not in cur.last_sql
    # Parameterized ILIKE on title+content, joined for provenance,
    # ordered by recency with a bound.
    assert "ILIKE %s" in cur.last_sql
    assert "JOIN" in cur.last_sql and "sources" in cur.last_sql
    assert "ORDER BY" in cur.last_sql and "published_at" in cur.last_sql
    assert "LIMIT" in cur.last_sql
    assert cur.last_params is not None
    assert any("Gen Z" in str(p) for p in cur.last_params)


def test_unread_rows_annotate_read_false(monkeypatch) -> None:
    _patch(monkeypatch, ROWS)
    for r in search_module.search_articles("Gen Z"):
        assert r["read"] is False
        assert r["read_at"] is None
        assert r["read_by"] is None


def test_exclude_read_filters_marked_rows(monkeypatch) -> None:
    marked = list(ROWS[0])
    marked[11] = _dt_utc(2026, 8, 27, 12, 0, 0)
    marked[12] = "reader-1"
    rows = [tuple(marked), ROWS[1], ROWS[2]]
    _patch(monkeypatch, rows)
    included = search_module.search_articles("Gen Z")
    assert len(included) == 3
    assert next(r for r in included if r["url"] == ROWS[0][1])["read"] is True
    filtered = search_module.search_articles("Gen Z", exclude_read=True)
    assert {r["url"] for r in filtered} == {ROWS[1][1], ROWS[2][1]}
