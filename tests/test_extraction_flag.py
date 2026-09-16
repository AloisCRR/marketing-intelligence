"""Extraction Flag seam tests (Lane 1) — fake conns, no live Postgres.

TDD vertical slices against the new seam:
- `marketing_intelligence.service.flag_extraction` (validated; lane ValueError -> InvalidRequest)
- `marketing_intelligence.flag.flag_extraction` (lane; ValueError/TypeError/LookupError only)
- read-back annotation on `get_article` / `search_articles` / `get_period_context`

Locked contract (ADR-0005 + CONTEXT.md Extraction Flag term):
- FLAG_REASONS = thin | js_shell | paywall_challenge | truncated | wrong_body |
  unrecoverable | other
- detail max 2000 chars; `other` requires non-blank detail
- flagged_by optional, max 100 chars; clear=True ignores reason/detail, NULLs
  the 4 columns and updates nothing else; re-flag overwrites.
- `unrecoverable` is filed by ingestion (system reporter) when a document's
  whole article-content chain fails and only the thin RSS body survives; the
  flag tool accepts and clears it and still rejects unknown reasons.
"""

from __future__ import annotations

import datetime as _dt
import os
import sys
from datetime import date
from typing import Any

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(os.path.dirname(_HERE), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import marketing_intelligence.flag as flag_lane  # noqa: E402
import marketing_intelligence.service as service  # noqa: E402


def _utc(*args: int) -> _dt.datetime:
    return _dt.datetime(*args, tzinfo=_dt.UTC)


ARTICLE_URL = "https://www.socialmediatoday.com/news/tiktok/1/?utm_source=rss#top"
ARTICLE_CANON = "https://www.socialmediatoday.com/news/tiktok/1/"
ARTICLE_TITLE = "TikTok Adds Voice Notes"
ARTICLE_CONTENT = (
    "TikTok rolls out voice notes and image carousels for comments globally, "
    "with a longform body that must come back whole."
)

ARTICLE_EXPECTED_KEYS = {
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
    "read",
    "read_at",
    "read_by",
    "importance_score",
    "importance_rationale",
    "importance_reporter",
    "importance_updated_at",
    "topics",
    "image_texts",
}

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
    "importance_score",
    "importance_rationale",
    "importance_reporter",
    "importance_updated_at",
    "topics",
    "has_image_text",
}


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

_PERIOD_COLS = (
    "title",
    "url",
    "canonical_url",
    "source",
    "published_at",
    "author",
    "flag_reason",
    "flag_detail",
    "flagged_at",
    "flagged_by",
)


class _FakeCursor:
    """Cursor-style fake honouring exact-then-canonical SELECTs plus flag UPDATEs."""

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
            if "= NULL" in head:
                # Clear path: params (url_key, canonical_key).
                url_key, canon_key = params[0], params[1]
                matched = [
                    d for d in self._store if d["url"] == url_key or d["canonical_url"] == canon_key
                ]
                for doc in matched:
                    doc["flag_reason"] = None
                    doc["flag_detail"] = None
                    doc["flagged_at"] = None
                    doc["flagged_by"] = None
                self.rowcount = len(matched)
                self._result = []
            else:
                # Set path: params (reason, detail, flagged_by, url_key, canonical_key).
                reason, detail, flagged_by, url_key, canon_key = params
                matched = [
                    d for d in self._store if d["url"] == url_key or d["canonical_url"] == canon_key
                ]
                for doc in matched:
                    doc["flag_reason"] = reason
                    doc["flag_detail"] = detail
                    doc["flagged_at"] = _dt.datetime.now(_dt.UTC)
                    doc["flagged_by"] = flagged_by
                self.rowcount = len(matched)
                self._result = []
            return
        if head.startswith("SELECT DT.TOPIC_SLUG"):
            # Effective-topic fetch (Ticket 20): these stores carry no topics.
            self._result = []
            return
        if "ILIKE" in sql.upper():
            # Search: params (pattern, pattern, limit); ILIKE over title/content.
            keyword = str(params[0]).strip("%").lower() if params else ""
            limit = int(params[2]) if len(params) > 2 else len(self._store)
            hits = [
                d
                for d in self._store
                if keyword in str(d["title"]).lower() or keyword in str(d["content"]).lower()
            ]
            hits.sort(key=lambda d: d["published_at"], reverse=True)
            self._result = [tuple(d[c] for c in _ARTICLE_COLS) for d in hits[:limit]]
            return
        if "CANONICAL_URL" in sql.upper() and "WHERE" in sql.upper():
            # Canonical lookup carries the canonical key; exact lookup the raw URL.
            matched = self._match(
                params[0], by_canonical="canonical_url" in sql.lower().split("where", 1)[1]
            )
            self._result = [tuple(d[c] for c in _ARTICLE_COLS) for d in matched]
            return
        self._result = []

    def fetchall(self) -> list[tuple]:
        return list(self._result)

    def fetchone(self) -> tuple | None:
        return self._fetchone_row

    def close(self) -> None:
        pass


class _FakeConnection:
    """Single fake serving cursor-style (article/search/flag) and execute-style (period)."""

    def __init__(self, store: list[dict[str, Any]] | None = None) -> None:
        self.store = store if store is not None else _make_store()
        self.calls = 0
        self.closed = 0
        self.committed = 0
        self._source_name = "Social Media Today"

    def cursor(self) -> _FakeCursor:
        self.calls += 1
        return _FakeCursor(self.store)

    def execute(self, sql: str, params: tuple | None = None) -> _FakeCursor:
        """Period path, plus the ingest upsert path (ON CONFLICT DO NOTHING)."""
        self.calls += 1
        head = sql.strip().upper()
        if head.startswith("SELECT ID FROM SOURCES"):
            cur = _FakeCursor(self.store)
            cur._fetchone_row = (1,)
            if params:
                self._source_name = str(params[0])
            return cur
        if head.startswith("INSERT INTO DOCUMENTS"):
            assert params is not None
            _, url, canon, title, author, published_at, _retrieved, language, content, _ = params
            cur = _FakeCursor(self.store)
            if any(d["url"] == url or d["canonical_url"] == canon for d in self.store):
                # Duplicate: ON CONFLICT DO NOTHING leaves the row (incl. flags) untouched.
                cur.rowcount = 0
            else:
                cur.rowcount = 1
                self.store.append(
                    {
                        "title": title,
                        "url": url,
                        "canonical_url": canon,
                        "source": self._source_name,
                        "published_at": published_at,
                        "author": author,
                        "content": content,
                        "flag_reason": None,
                        "flag_detail": None,
                        "flagged_at": None,
                        "flagged_by": None,
                    }
                )
            _ = language
            return cur
        assert params is not None
        start, end, names, limit = params
        kept = [
            d
            for d in self.store
            if d["published_at"] >= start and d["published_at"] < end and d["source"] in set(names)
        ]
        kept.sort(key=lambda d: d["published_at"], reverse=True)
        cur = _FakeCursor(self.store)
        cur._result = [tuple(d[c] for c in _PERIOD_COLS) for d in kept[: int(limit)]]
        return cur

    def commit(self) -> None:
        self.committed += 1

    def close(self) -> None:
        self.closed += 1


def _flagged(conn: _FakeConnection) -> dict[str, Any]:
    return service.flag_extraction(
        ARTICLE_URL,
        reason="thin",
        detail="body under 200 chars, looks like RSS teaser only",
        flagged_by="digest-agent",
        conn=conn,
    )


# --- slice 1: flag set + get_article shows flag ---------------------------------


def test_flag_set_returns_full_article_with_flag_fields() -> None:
    conn = _FakeConnection()
    article = _flagged(conn)
    assert set(article.keys()) == ARTICLE_EXPECTED_KEYS
    assert article["title"] == ARTICLE_TITLE
    assert article["content"] == ARTICLE_CONTENT
    assert article["flag_reason"] == "thin"
    assert article["flag_detail"] == "body under 200 chars, looks like RSS teaser only"
    assert article["flagged_by"] == "digest-agent"
    parsed = _dt.datetime.fromisoformat(str(article["flagged_at"]))
    assert parsed.tzinfo is not None


def test_get_article_shows_flag_after_set() -> None:
    conn = _FakeConnection()
    _flagged(conn)
    article = service.get_article(ARTICLE_URL, conn=conn)
    assert set(article.keys()) == ARTICLE_EXPECTED_KEYS
    assert article["flag_reason"] == "thin"
    assert article["flag_detail"] == "body under 200 chars, looks like RSS teaser only"
    assert article["flagged_by"] == "digest-agent"
    assert _dt.datetime.fromisoformat(str(article["flagged_at"])).tzinfo is not None


def test_unflagged_article_returns_none_flags() -> None:
    article = service.get_article(ARTICLE_URL, conn=_FakeConnection())
    assert article["flag_reason"] is None
    assert article["flag_detail"] is None
    assert article["flagged_at"] is None
    assert article["flagged_by"] is None


def test_flag_via_canonical_identifier_matches_same_row() -> None:
    conn = _FakeConnection()
    article = service.flag_extraction(
        ARTICLE_CANON,
        reason="js_shell",
        detail="page renders an empty JS shell",
        conn=conn,
    )
    assert article["url"] == ARTICLE_URL
    assert article["flag_reason"] == "js_shell"


def test_flagged_by_optional() -> None:
    article = service.flag_extraction(
        ARTICLE_URL, reason="truncated", detail="body ends mid-sentence", conn=_FakeConnection()
    )
    assert article["flag_reason"] == "truncated"
    assert article["flagged_by"] is None


def test_injected_conn_is_neither_committed_nor_closed() -> None:
    conn = _FakeConnection()
    _flagged(conn)
    assert conn.calls >= 1
    assert conn.committed == 0
    assert conn.closed == 0


def test_lane_unknown_url_raises_value_error() -> None:
    with pytest.raises(ValueError):
        flag_lane.flag_extraction(
            "https://unknown.example/nope/",
            reason="thin",
            detail="missing",
            conn=_FakeConnection(),
        )


# --- slice 2: re-flag overwrites -------------------------------------------------


def test_reflag_overwrites_all_four_columns() -> None:
    conn = _FakeConnection()
    first = _flagged(conn)
    assert first["flag_reason"] == "thin"
    second = service.flag_extraction(
        ARTICLE_URL,
        reason="wrong_body",
        detail="body belongs to a different story",
        flagged_by="reviewer-2",
        conn=conn,
    )
    assert second["flag_reason"] == "wrong_body"
    assert second["flag_detail"] == "body belongs to a different story"
    assert second["flagged_by"] == "reviewer-2"
    assert _dt.datetime.fromisoformat(str(second["flagged_at"])).tzinfo is not None
    # Nothing else changed.
    assert second["title"] == ARTICLE_TITLE
    assert second["content"] == ARTICLE_CONTENT
    assert service.get_article(ARTICLE_URL, conn=conn)["flag_reason"] == "wrong_body"


# --- slice 3: clear nulls --------------------------------------------------------


def test_clear_nulls_four_columns_and_updates_nothing_else() -> None:
    conn = _FakeConnection()
    _flagged(conn)
    cleared = service.flag_extraction(ARTICLE_URL, clear=True, conn=conn)
    assert cleared["flag_reason"] is None
    assert cleared["flag_detail"] is None
    assert cleared["flagged_at"] is None
    assert cleared["flagged_by"] is None
    assert cleared["title"] == ARTICLE_TITLE
    assert cleared["content"] == ARTICLE_CONTENT
    assert cleared["url"] == ARTICLE_URL


def test_clear_ignores_reason_and_detail() -> None:
    conn = _FakeConnection()
    _flagged(conn)
    cleared = service.flag_extraction(
        ARTICLE_URL, reason="bogus-reason", detail="x" * 5000, clear=True, conn=conn
    )
    assert cleared["flag_reason"] is None
    assert cleared["flag_detail"] is None
    assert cleared["flagged_at"] is None
    assert cleared["flagged_by"] is None


def test_clear_unflagged_article_is_noop_success() -> None:
    cleared = service.flag_extraction(ARTICLE_URL, clear=True, conn=_FakeConnection())
    assert cleared["flag_reason"] is None
    assert cleared["title"] == ARTICLE_TITLE


def test_clear_unknown_url_raises_invalid_request() -> None:
    with pytest.raises(service.InvalidRequest):
        service.flag_extraction("https://unknown.example/nope/", clear=True, conn=_FakeConnection())


# --- slice 4: validation 422-paths -----------------------------------------------


def test_blank_identifier_rejected_without_query() -> None:
    conn = _FakeConnection()
    for bad in ("", "   ", None, 123):
        with pytest.raises(service.InvalidRequest):
            service.flag_extraction(bad, reason="thin", detail="d", conn=conn)  # type: ignore[arg-type]
    assert conn.calls == 0


def test_unknown_url_raises_invalid_request() -> None:
    with pytest.raises(service.InvalidRequest):
        service.flag_extraction(
            "https://unknown.example/nope/", reason="thin", detail="d", conn=_FakeConnection()
        )


def test_bad_reason_rejected_without_query() -> None:
    conn = _FakeConnection()
    for bad in ("bogus", "", "   ", None, 123):
        with pytest.raises(service.InvalidRequest):
            service.flag_extraction(ARTICLE_URL, reason=bad, detail="d", conn=conn)  # type: ignore[arg-type]
    assert conn.calls == 0


def test_all_spec_reasons_accepted() -> None:
    for reason in (
        "thin",
        "js_shell",
        "paywall_challenge",
        "truncated",
        "wrong_body",
        "unrecoverable",
    ):
        article = service.flag_extraction(
            ARTICLE_URL, reason=reason, detail=f"detail for {reason}", conn=_FakeConnection()
        )
        assert article["flag_reason"] == reason


def test_unrecoverable_flag_clears_via_tool() -> None:
    conn = _FakeConnection()
    flagged = service.flag_extraction(
        ARTICLE_URL,
        reason="unrecoverable",
        detail="fallback failed for ... (primary: ...; reader: ...; firecrawl: ...)",
        flagged_by=flag_lane.SYSTEM_REPORTER,
        conn=conn,
    )
    assert flagged["flag_reason"] == "unrecoverable"
    assert flagged["flagged_by"] == "system"
    cleared = service.flag_extraction(ARTICLE_URL, clear=True, conn=conn)
    assert cleared["flag_reason"] is None
    assert cleared["flag_detail"] is None
    assert cleared["flagged_at"] is None
    assert cleared["flagged_by"] is None


def test_detail_over_2000_chars_rejected() -> None:
    conn = _FakeConnection()
    with pytest.raises(service.InvalidRequest):
        service.flag_extraction(ARTICLE_URL, reason="thin", detail="x" * 2001, conn=conn)
    assert conn.calls == 0
    ok = service.flag_extraction(ARTICLE_URL, reason="thin", detail="x" * 2000, conn=conn)
    assert ok["flag_detail"] == "x" * 2000


def test_other_requires_non_blank_detail() -> None:
    conn = _FakeConnection()
    for bad in (None, "", "   "):
        with pytest.raises(service.InvalidRequest):
            service.flag_extraction(ARTICLE_URL, reason="other", detail=bad, conn=conn)  # type: ignore[arg-type]
    assert conn.calls == 0
    ok = service.flag_extraction(
        ARTICLE_URL, reason="other", detail="unlisted extraction problem", conn=conn
    )
    assert ok["flag_reason"] == "other"


def test_non_string_detail_rejected() -> None:
    with pytest.raises(service.InvalidRequest):
        service.flag_extraction(
            ARTICLE_URL,
            reason="thin",
            detail=123,
            conn=_FakeConnection(),  # type: ignore[arg-type]
        )


def test_flagged_by_over_100_chars_rejected() -> None:
    conn = _FakeConnection()
    with pytest.raises(service.InvalidRequest):
        service.flag_extraction(
            ARTICLE_URL, reason="thin", detail="d", flagged_by="y" * 101, conn=conn
        )
    assert conn.calls == 0
    ok = service.flag_extraction(
        ARTICLE_URL, reason="thin", detail="d", flagged_by="z" * 100, conn=conn
    )
    assert ok["flagged_by"] == "z" * 100


def test_non_string_flagged_by_rejected() -> None:
    with pytest.raises(service.InvalidRequest):
        service.flag_extraction(
            ARTICLE_URL,
            reason="thin",
            detail="d",
            flagged_by=123,
            conn=_FakeConnection(),  # type: ignore[arg-type]
        )


def test_lane_bad_reason_and_detail_raise_value_error() -> None:
    conn = _FakeConnection()
    with pytest.raises(ValueError):
        flag_lane.flag_extraction(ARTICLE_URL, reason="bogus", detail="d", conn=conn)
    with pytest.raises(ValueError):
        flag_lane.flag_extraction(ARTICLE_URL, reason="thin", detail="x" * 2001, conn=conn)
    with pytest.raises(ValueError):
        flag_lane.flag_extraction(ARTICLE_URL, reason="other", detail=None, conn=conn)
    with pytest.raises(ValueError):
        flag_lane.flag_extraction(
            ARTICLE_URL, reason="thin", detail="d", flagged_by="y" * 101, conn=conn
        )


# --- slice 5: search/period annotation -------------------------------------------


def test_search_annotates_flag_without_filtering() -> None:
    conn = _FakeConnection()
    _flagged(conn)
    results = service.search_articles("TikTok", conn=conn)
    assert len(results) >= 1
    flagged = next(r for r in results if r["url"] == ARTICLE_URL)
    assert set(flagged.keys()) == SEARCH_EXPECTED_KEYS
    assert flagged["flag_reason"] == "thin"
    assert flagged["flag_detail"] == "body under 200 chars, looks like RSS teaser only"
    assert flagged["flagged_by"] == "digest-agent"
    assert _dt.datetime.fromisoformat(str(flagged["flagged_at"])).tzinfo is not None
    assert "tiktok" in str(flagged["snippet"]).lower()


def test_search_unflagged_rows_carry_none_flags() -> None:
    results = service.search_articles("marketers", conn=_FakeConnection())
    assert len(results) >= 1
    for row in results:
        assert set(row.keys()) == SEARCH_EXPECTED_KEYS
        assert row["flag_reason"] is None
        assert row["flag_detail"] is None
        assert row["flagged_at"] is None
        assert row["flagged_by"] is None


def test_period_annotates_flag_without_filtering() -> None:
    conn = _FakeConnection()
    _flagged(conn)
    ctx = service.get_period_context(date(2026, 9, 7), date(2026, 9, 13), conn=conn)
    flagged = next(a for a in ctx["recent_articles"] if a["url"] == ARTICLE_URL)
    assert set(flagged.keys()) == PERIOD_EXPECTED_KEYS
    assert flagged["flag_reason"] == "thin"
    assert flagged["flag_detail"] == "body under 200 chars, looks like RSS teaser only"
    assert flagged["flagged_by"] == "digest-agent"
    assert _dt.datetime.fromisoformat(str(flagged["flagged_at"])).tzinfo is not None
    # Flagged articles are still listed (annotate, not filter).
    assert {a["title"] for a in ctx["recent_articles"]} == {
        ARTICLE_TITLE,
        "Signal Loss Rebuild",
    }


# --- slice 6: rowcount semantics (real psycopg returns no rows after UPDATE) ---


class _PinnedUpdateCursor:
    """UPDATE-only cursor: fetchall raises like real psycopg (no results to fetch)."""

    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount
        self.fetchall_calls = 0

    def execute(self, sql: str, params: tuple | None = None) -> None:
        assert sql.strip().upper().startswith("UPDATE")

    def fetchall(self) -> list[tuple]:
        self.fetchall_calls += 1
        raise AssertionError("flag lane must not fetchall() after UPDATE (no results)")

    def close(self) -> None:
        pass


class _RowcountPinnedConn(_FakeConnection):
    """First cursor() is the pinned UPDATE cursor; later ones read the store."""

    def __init__(self, store: list[dict[str, Any]], rowcount: int) -> None:
        super().__init__(store)
        self.update_cursor = _PinnedUpdateCursor(rowcount)
        self._cursors_taken = 0

    def cursor(self) -> Any:
        self.calls += 1
        self._cursors_taken += 1
        if self._cursors_taken == 1:
            return self.update_cursor
        return _FakeCursor(self.store)


def test_flag_execute_uses_rowcount_zero_means_unknown() -> None:
    conn = _RowcountPinnedConn(_make_store(), 0)
    with pytest.raises(ValueError):
        flag_lane.flag_extraction(ARTICLE_URL, reason="thin", detail="d", conn=conn)
    assert conn.update_cursor.fetchall_calls == 0


def test_flag_execute_uses_rowcount_one_reads_back_without_fetchall() -> None:
    conn = _RowcountPinnedConn(_make_store(), 1)
    article = flag_lane.flag_extraction(ARTICLE_URL, reason="thin", detail="d", conn=conn)
    assert conn.update_cursor.fetchall_calls == 0
    assert article["url"] == ARTICLE_URL
    assert article["title"] == ARTICLE_TITLE


# --- slice 7: flag survives re-ingest (ON CONFLICT DO NOTHING) ------------------


def test_flag_survives_reingest_upsert() -> None:
    from marketing_intelligence.ingest import upsert_documents
    from marketing_intelligence.normalize import make_document

    conn = _FakeConnection()
    _flagged(conn)
    assert service.get_article(ARTICLE_URL, conn=conn)["flag_reason"] == "thin"
    # Re-ingest the same URL (even with a changed body): the upsert is
    # ON CONFLICT DO NOTHING, so the stored row — incl. flag columns — is untouched.
    doc = make_document(
        source="Social Media Today",
        url=ARTICLE_URL,
        title=ARTICLE_TITLE,
        content=ARTICLE_CONTENT + " updated body that must not overwrite the flag",
        published_at=_utc(2026, 9, 8, 14, 30),
        author="Andrew Hutchinson",
    )
    assert upsert_documents([doc], conn=conn) == (0, 1)
    article = service.get_article(ARTICLE_URL, conn=conn)
    assert article["flag_reason"] == "thin"
    assert article["flag_detail"] == "body under 200 chars, looks like RSS teaser only"
    assert article["flagged_by"] == "digest-agent"
    assert _dt.datetime.fromisoformat(str(article["flagged_at"])).tzinfo is not None
    assert article["title"] == ARTICLE_TITLE
    assert article["content"] == ARTICLE_CONTENT


# --- slice 8: terminal unrecoverable emission (enrich -> flag) ------------------


def _thin_rss_doc() -> Any:
    """RSS-lane document for the seeded article: thin teaser, same stored URL."""
    from marketing_intelligence.normalize import make_document

    return make_document(
        source="Social Media Today",
        url=ARTICLE_URL,
        title=ARTICLE_TITLE,
        content="Short RSS teaser only.",
        published_at=_utc(2026, 9, 8, 14, 30),
        author="Andrew Hutchinson",
    )


def _total_chain_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail the primary fetch and both fallback legs (a total-chain failure)."""
    from marketing_intelligence import enrich, firecrawl

    monkeypatch.setattr(
        enrich,
        "fetch_and_clean",
        lambda url, timeout=30: (_ for _ in ()).throw(
            enrich.FetchFailed(f"fetch failed for {url}: cloudflare challenge captcha")
        ),
    )
    monkeypatch.setattr(
        enrich,
        "try_fallback_reader",
        lambda url, timeout=30: (_ for _ in ()).throw(enrich.FetchFailed("reader leg down")),
    )

    def _firecrawl_down(url: str, timeout: int = 30) -> bytes:
        raise firecrawl.FirecrawlFailed("firecrawl leg down")

    monkeypatch.setattr(firecrawl, "fetch_via_firecrawl", _firecrawl_down)


def test_enrich_total_chain_failure_emits_unrecoverable_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marketing_intelligence import enrich

    _total_chain_failure(monkeypatch)
    events: list[tuple[str, str]] = []
    kept, method, cause = enrich.enrich_document_or_keep(
        _thin_rss_doc(), on_unrecoverable=lambda url, detail: events.append((url, detail))
    )
    assert kept.content == "Short RSS teaser only."  # RSS body kept, never stubbed
    assert method == "rss"
    assert cause is not None
    assert len(events) == 1  # one terminal event per document, not per leg
    url, detail = events[0]
    assert url == ARTICLE_URL
    # The chained cause names the primary error and every failed leg.
    assert "cloudflare" in detail
    assert "reader" in detail
    assert "firecrawl" in detail

    # The flow files the flag once the row exists (post-upsert); read it back.
    conn = _FakeConnection()
    article = flag_lane.flag_unrecoverable(url, detail=detail, conn=conn)
    assert article["flag_reason"] == "unrecoverable"
    assert article["flagged_by"] == "system"
    read_back = service.get_article(ARTICLE_URL, conn=conn)
    assert read_back["flag_reason"] == "unrecoverable"
    assert "reader" in str(read_back["flag_detail"])


def test_enrich_failure_with_sufficient_body_emits_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # force_on: the chain still fails, but the RSS body already carries the
    # article — that document is not unrecoverable and must stay unflagged.
    from marketing_intelligence import enrich
    from marketing_intelligence.normalize import make_document

    _total_chain_failure(monkeypatch)
    template = _thin_rss_doc()
    substantial = make_document(
        source=template.source,
        url=template.url,
        title=template.title,
        content="x" * 600,
        published_at=template.published_at,
        retrieved_at=template.retrieved_at,
        language=template.language,
    )
    events: list[tuple[str, str]] = []
    kept, method, cause = enrich.enrich_document_or_keep(
        substantial,
        force=True,
        on_unrecoverable=lambda url, detail: events.append((url, detail)),
    )
    assert kept is substantial
    assert method == "rss"
    assert cause is not None
    assert events == []


def test_enrich_total_chain_failure_without_sink_keeps_rss_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marketing_intelligence import enrich

    _total_chain_failure(monkeypatch)
    kept, method, cause = enrich.enrich_document_or_keep(_thin_rss_doc())
    assert kept.content == "Short RSS teaser only."
    assert method == "rss"
    assert cause is not None


def test_unrecoverable_sink_failure_never_breaks_keep(monkeypatch: pytest.MonkeyPatch) -> None:
    from marketing_intelligence import enrich

    _total_chain_failure(monkeypatch)

    def boom(url: str, detail: str) -> None:
        raise RuntimeError("flag store down")

    kept, method, cause = enrich.enrich_document_or_keep(_thin_rss_doc(), on_unrecoverable=boom)
    assert kept.content == "Short RSS teaser only."
    assert method == "rss"
    assert cause is not None


def test_upsert_then_flag_unrecoverable_persists_terminal_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The flow's integration order: keep-RSS upsert first, then the flag write."""
    from marketing_intelligence import enrich
    from marketing_intelligence.ingest import upsert_documents

    _total_chain_failure(monkeypatch)
    events: list[tuple[str, str]] = []
    kept, _method, _cause = enrich.enrich_document_or_keep(
        _thin_rss_doc(), on_unrecoverable=lambda url, detail: events.append((url, detail))
    )
    conn = _FakeConnection(store=[])
    assert upsert_documents([kept], conn=conn) == (1, 0)
    url, detail = events[0]
    flag_lane.flag_unrecoverable(url, detail=detail, conn=conn)
    article = service.get_article(ARTICLE_URL, conn=conn)
    assert article["flag_reason"] == "unrecoverable"
    assert article["flagged_by"] == "system"
    assert article["content"] == "Short RSS teaser only."  # body unchanged by flagging


def test_flag_unrecoverable_truncates_overlong_chained_cause() -> None:
    conn = _FakeConnection()
    article = flag_lane.flag_unrecoverable(
        ARTICLE_URL, detail="fallback failed for " + "x" * 5000, conn=conn
    )
    assert article["flag_reason"] == "unrecoverable"
    assert article["flagged_by"] == "system"
    assert len(article["flag_detail"]) == flag_lane.DETAIL_MAX_LENGTH


def test_unrecoverable_reason_stays_under_importance_cap() -> None:
    from marketing_intelligence import importance

    assert (
        importance.effective_score(0.95, "unrecoverable", ARTICLE_CONTENT)
        == importance.IMPORTANCE_CAP
    )
    # Control: an unflagged score is untouched, so the cap keys off the flag.
    assert importance.effective_score(0.95, None, ARTICLE_CONTENT) == 0.95
