"""Dedupe + data-quality hardening (Ticket 03).

Observable behavior (not privates):
- canonical-URL + content-hash exact dedupe: repeats are a no-op, distinct
  paths stay independent rows
- malformed feed items are visible (counted + identifiable reasons), never
  silent; missing optional fields stay nullable
- publication vs retrieval timestamps stay distinct and tz-aware
- story_id reserved but unused; independent coverage is never collapsed

DB-free: persistence is exercised through a DB-API fake modeling Postgres
UNIQUE(url) + UNIQUE(canonical_url) + UNIQUE(content_hash) semantics.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC
from pathlib import Path

import pytest

from brain.flows import ingest_source_flow
from brain.ingest import (
    INSERT_SQL,
    ParseReport,
    count_feed_entries,
    parse_feed,
    parse_feed_with_report,
    upsert_documents,
)
from brain.normalize import NormalizedDocument, make_document

FIXTURES = Path(__file__).parent / "fixtures"
MESSY = FIXTURES / "messy_sample.xml"

V1_FIXTURES = {
    "Social Media Today": FIXTURES / "smt_sample.xml",
    "MarTech": FIXTURES / "martech_sample.xml",
    "Professional Jeweller": FIXTURES / "pj_sample.xml",
    "InfoMoney": FIXTURES / "infomoney_sample.xml",
}


def _doc(source: str, url: str, title: str, content: str) -> NormalizedDocument:
    from datetime import datetime

    return make_document(
        source=source,
        url=url,
        title=title,
        content=content,
        published_at=datetime(2026, 9, 4, 12, 0, 0, tzinfo=UTC),
    )


# --- fakes -------------------------------------------------------------------


class _FakeCursor:
    """Models Postgres UNIQUE(url) + UNIQUE(canonical_url) + UNIQUE(hash)."""

    def __init__(self, store: dict[str, tuple]) -> None:
        self._store = store
        self.rowcount: int = 0
        self._row: tuple | None = None

    def execute(self, sql: str, params: tuple | None = None) -> _FakeCursor:
        assert params is not None
        if sql.lstrip().upper().startswith("SELECT"):
            self._row = (1,)
            self.rowcount = 1
            return self
        url, canonical, content_hash = params[1], params[2], params[-1]
        if (
            url in self._store
            or canonical in {p[2] for p in self._store.values()}
            or content_hash in {p[-1] for p in self._store.values()}
        ):
            self.rowcount = 0  # conflict -> DO NOTHING
        else:
            self._store[url] = params
            self.rowcount = 1
        return self

    def fetchone(self) -> tuple | None:
        return self._row

    def close(self) -> None:
        pass


class FakeConnection:
    def __init__(self) -> None:
        self.store: dict[str, tuple] = {}
        self.commits = 0

    def execute(self, sql: str, params: tuple | None = None) -> _FakeCursor:
        return _FakeCursor(self.store).execute(sql, params)

    def commit(self) -> None:
        self.commits += 1


# --- canonical + hash exact dedupe -------------------------------------------


def test_insert_sql_collapses_any_unique_violation() -> None:
    assert "ON CONFLICT DO NOTHING" in INSERT_SQL


def test_canonical_variants_collapse_to_one_row() -> None:
    base = _doc("Social Media Today", "https://example.com/news/item/1/", "Same Story", "Same body")
    variant = _doc(
        "Social Media Today",
        "https://example.com/news/item/1/?utm_source=feed&utm_medium=rss#comments",
        "Same Story",
        "Same body",
    )
    assert variant.canonical_url == base.canonical_url
    assert variant.url != base.url
    conn = FakeConnection()
    assert upsert_documents([base], conn=conn) == (1, 0)
    assert upsert_documents([variant], conn=conn) == (0, 1)
    assert len(conn.store) == 1


def test_distinct_paths_stay_independent_rows() -> None:
    first = _doc("Social Media Today", "https://example.com/news/a/1/", "Story A", "Body A")
    second = _doc("Social Media Today", "https://example.com/news/b/2/", "Story B", "Body B")
    conn = FakeConnection()
    assert upsert_documents([first, second], conn=conn) == (2, 0)
    assert len(conn.store) == 2


def test_repeated_ingestion_is_noop() -> None:
    docs = parse_feed(V1_FIXTURES["MarTech"].read_bytes(), source="MarTech")
    conn = FakeConnection()
    assert upsert_documents(docs, conn=conn) == (3, 0)
    assert upsert_documents(docs, conn=conn) == (0, 3)
    assert len(conn.store) == 3


def test_identical_content_new_path_dedupes_on_hash() -> None:
    docs = parse_feed(V1_FIXTURES["MarTech"].read_bytes(), source="MarTech")
    conn = FakeConnection()
    upsert_documents(docs, conn=conn)
    clone = _doc(
        "MarTech", "https://martech.org/totally-different-path/999/", docs[0].title, docs[0].content
    )
    assert upsert_documents([clone], conn=conn) == (0, 1)
    assert len(conn.store) == 3


def test_migration_002_adds_canonical_unique_idempotently() -> None:
    path = FIXTURES.parent.parent / "migrations" / "002_canonical_url_unique.sql"
    assert path.exists(), "expected migrations/002_canonical_url_unique.sql"
    sql = path.read_text(encoding="utf-8")
    assert "canonical_url" in sql
    assert "UNIQUE" in sql.upper()
    assert "IF NOT EXISTS" in sql.upper()


# --- malformed items visible, optional fields nullable ------------------------


def test_parse_feed_signature_still_returns_list() -> None:
    docs = parse_feed(MESSY.read_bytes(), source="Test Source")
    assert isinstance(docs, list)
    assert len(docs) == 3


def test_parse_report_counts_and_identifies_skips() -> None:
    report = parse_feed_with_report(MESSY.read_bytes(), source="Test Source")
    assert isinstance(report, ParseReport)
    assert len(report.documents) == 3
    assert report.skipped == 3
    assert len(report.skipped_reasons) == 3
    joined = "\n".join(report.skipped_reasons)
    assert "title" in joined  # missing-title item identifiable
    assert "link" in joined  # missing-link item identifiable


def test_missing_author_nullable_and_bad_date_falls_back_tz_aware() -> None:
    report = parse_feed_with_report(MESSY.read_bytes(), source="Test Source")
    by_title = {d.title: d for d in report.documents}
    assert by_title["Anonymous Item Without Author"].author is None
    assert by_title["Clean Item With Everything Present"].author == "Jane Doe"
    garbage = by_title["Item With Garbage Date"]
    assert garbage.published_at.tzinfo is not None
    assert garbage.published_at.isoformat() == garbage.retrieved_at.isoformat()


def test_count_feed_entries_matches_fixture_items() -> None:
    assert count_feed_entries(MESSY.read_bytes()) == 6
    assert count_feed_entries(V1_FIXTURES["InfoMoney"].read_bytes()) == 3


def test_flow_surfaces_parse_skipped_without_breaking_clean_flows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import brain.flows as flows

    # Hermetic: enrichment is lane 02/03's concern — identity-enrich so this
    # test keeps asserting parse/dedupe shapes, never live article fetches.
    monkeypatch.setattr(flows, "enrich_document_or_keep", lambda doc, *a, **k: (doc, "rss", None))
    monkeypatch.setattr(flows, "fetch_rss", lambda url, timeout=30: MESSY.read_bytes())
    conn = FakeConnection()
    monkeypatch.setattr(flows, "upsert_documents", lambda docs: upsert_documents(docs, conn=conn))
    result = ingest_source_flow(source_name="Social Media Today")
    assert result["inserted"] == 3
    assert result["parse_skipped"] == 3

    # Clean feeds carry no new key: exact-shape backward compatibility.
    monkeypatch.setattr(
        flows, "fetch_rss", lambda url, timeout=30: V1_FIXTURES["MarTech"].read_bytes()
    )
    clean = ingest_source_flow(source_name="MarTech")
    assert clean == {"inserted": 3, "skipped": 0}


# --- timestamps ----------------------------------------------------------------


@pytest.mark.parametrize("name", list(V1_FIXTURES))
def test_published_vs_retrieved_distinct_and_tz_aware(name: str) -> None:
    docs = parse_feed(V1_FIXTURES[name].read_bytes(), source=name)
    assert docs
    for doc in docs:
        assert doc.published_at.tzinfo is not None
        assert doc.retrieved_at.tzinfo is not None
        assert doc.retrieved_at >= doc.published_at


def test_known_fixture_timestamps() -> None:
    smt = {
        d.title: d
        for d in parse_feed(
            V1_FIXTURES["Social Media Today"].read_bytes(), source="Social Media Today"
        )
    }
    # Non-UTC offset preserved.
    assert smt[
        "Instagram Tests Reels Templates for Collaborative Posts"
    ].published_at.isoformat() == ("2026-09-03T09:15:00-05:00")
    # Naive input assumed UTC.
    assert smt["LinkedIn Reports Record Video Engagement Among Gen Z"].published_at.isoformat() == (
        "2026-09-02T18:45:00+00:00"
    )
    for doc in smt.values():
        assert doc.retrieved_at > doc.published_at  # retrieval strictly after publication


# --- story_id reserved but unused ----------------------------------------------


def test_story_id_reserved_not_written_not_collapsing() -> None:
    assert "story_id" not in INSERT_SQL  # never persisted by ingestion
    assert "story_id" not in {f.name for f in dataclasses.fields(NormalizedDocument)}
    first = _doc("InfoMoney", "https://example.com/a/1/", "Market Story Angle A", "Body A")
    second = _doc("InfoMoney", "https://example.com/b/2/", "Market Story Angle B", "Body B")
    conn = FakeConnection()
    assert upsert_documents([first, second], conn=conn) == (2, 0)
