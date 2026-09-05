"""Source-adapter + ingestion tests for Social Media Today (first RSS source).

Contract under test (see .scratch/trend-intelligence-brain/spec.md,
"Testing Decisions": source adapters / ingestion / dedupe):
- representative fixture -> normalized document contract
- missing optional fields (author) stay nullable, never crash
- edge-case publication dates still yield tz-aware datetimes
- content hash is stable across parses
- rerunning upsert creates no duplicates (idempotent, rerun-safe)
- Prefect flow is importable and returns {inserted, skipped}

No live Postgres required: upsert is exercised through a DB-API fake
that models Postgres `ON CONFLICT DO NOTHING` semantics.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from brain.flows import ingest_source_flow
from brain.ingest import parse_feed, upsert_documents
from brain.normalize import NormalizedDocument, canonicalize_url, content_hash_for
from brain.sources import get_source

FIXTURE = Path(__file__).parent / "fixtures" / "smt_sample.xml"

SMT_RSS = "https://www.socialmediatoday.com/feeds/news/"


def _parse_fixture() -> list[NormalizedDocument]:
    return parse_feed(FIXTURE.read_bytes())


# --- registry ---------------------------------------------------------------


def test_get_source_returns_smt_registry_entry() -> None:
    src = get_source("Social Media Today")
    assert src["name"] == "Social Media Today"
    assert src["rss_url"] == SMT_RSS


def test_get_source_unknown_name_raises_key_error() -> None:
    with pytest.raises(KeyError):
        get_source("No Such Source")


# --- normalization contract -------------------------------------------------


def test_fixture_parses_to_three_documents() -> None:
    assert len(_parse_fixture()) == 3


def test_normalized_contract_fields() -> None:
    for doc in _parse_fixture():
        assert isinstance(doc, NormalizedDocument)
        assert doc.source == "Social Media Today"
        assert doc.url.startswith("https://www.socialmediatoday.com/")
        assert doc.canonical_url.startswith("https://www.socialmediatoday.com/")
        assert doc.title.strip()
        assert doc.content.strip()
        assert doc.language == "en"
        assert doc.content_hash
        assert doc.published_at.tzinfo is not None
        assert doc.retrieved_at.tzinfo is not None


def test_missing_author_is_nullable() -> None:
    docs = _parse_fixture()
    by_title = {d.title: d for d in docs}
    assert by_title["Instagram Tests Reels Templates for Collaborative Posts"].author is None
    # ...while present authors survive normalization.
    assert (
        by_title["TikTok Adds Voice Notes and Image Carousels in Comments"].author
        == "Andrew Hutchinson"
    )


def test_edge_case_dates_are_tz_aware() -> None:
    docs = {d.title: d for d in _parse_fixture()}
    # Non-UTC offset is preserved and tz-aware.
    reels = docs["Instagram Tests Reels Templates for Collaborative Posts"]
    assert reels.published_at.utcoffset() is not None
    assert reels.published_at.isoformat() == "2026-09-03T09:15:00-05:00"
    # Naive ISO-8601 input is assumed UTC (still tz-aware, never naive).
    linkedin = docs["LinkedIn Reports Record Video Engagement Among Gen Z"]
    assert linkedin.published_at.tzinfo is not None
    assert linkedin.published_at.isoformat() == "2026-09-02T18:45:00+00:00"


def test_content_hash_stable_across_parses() -> None:
    first = _parse_fixture()
    second = _parse_fixture()
    assert [d.content_hash for d in first] == [d.content_hash for d in second]
    for doc in first:
        assert doc.content_hash == content_hash_for(doc.title, doc.content)


def test_canonical_url_strips_query_fragment_and_lowercases_host() -> None:
    assert (
        canonicalize_url(
            "https://WWW.socialmediatoday.com/news/tiktok-adds-voice-notes/829640/"
            "?utm_source=feed&utm_medium=rss#comments"
        )
        == "https://www.socialmediatoday.com/news/tiktok-adds-voice-notes/829640/"
    )
    # Canonical URL of the first fixture item drops its tracking query/fragment.
    docs = _parse_fixture()
    assert (
        docs[0].canonical_url == "https://www.socialmediatoday.com/news/"
        "tiktok-adds-voice-notes-and-image-carousels-in-comments/829640/"
    )


# --- dedupe / idempotency ----------------------------------------------------


class _FakeCursor:
    """Models a Postgres cursor for INSERT ... ON CONFLICT DO NOTHING."""

    def __init__(self, store: dict[str, tuple]) -> None:
        self._store = store
        self.rowcount: int = 0
        self._row: tuple | None = None

    def execute(self, sql: str, params: tuple | None = None) -> _FakeCursor:
        assert params is not None
        if sql.lstrip().upper().startswith("SELECT"):
            self._row = (1,)  # source id lookup
            self.rowcount = 1
            return self
        url, content_hash = params[1], params[-1]
        if url in self._store or content_hash in {p[-1] for p in self._store.values()}:
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
    """Minimal DB-API double: .execute/.commit with Postgres upsert semantics."""

    def __init__(self) -> None:
        self.store: dict[str, tuple] = {}
        self.commits = 0
        self.statements: list[str] = []

    def execute(self, sql: str, params: tuple | None = None) -> _FakeCursor:
        self.statements.append(sql)
        return _FakeCursor(self.store).execute(sql, params)

    def commit(self) -> None:
        self.commits += 1


def test_rerun_upsert_creates_no_duplicates() -> None:
    docs = _parse_fixture()
    conn = FakeConnection()
    inserted, skipped = upsert_documents(docs, conn=conn)
    assert (inserted, skipped) == (3, 0)
    assert len(conn.store) == 3

    inserted2, skipped2 = upsert_documents(docs, conn=conn)
    assert (inserted2, skipped2) == (0, 3)
    assert len(conn.store) == 3  # no duplicates after rerun


def test_identical_content_new_url_dedupes_on_hash() -> None:
    docs = _parse_fixture()
    conn = FakeConnection()
    upsert_documents(docs, conn=conn)
    clone = NormalizedDocument(
        source=docs[0].source,
        url=docs[0].url + "?repost=1",
        canonical_url=docs[0].canonical_url,
        title=docs[0].title,
        author=docs[0].author,
        published_at=docs[0].published_at,
        retrieved_at=docs[0].retrieved_at,
        language=docs[0].language,
        content=docs[0].content,
        content_hash=docs[0].content_hash,
    )
    inserted, skipped = upsert_documents([clone], conn=conn)
    assert (inserted, skipped) == (0, 1)
    assert len(conn.store) == 3


def test_upsert_uses_on_conflict() -> None:
    conn = FakeConnection()
    upsert_documents(_parse_fixture(), conn=conn)
    assert any("ON CONFLICT" in s.upper() for s in conn.statements)


# --- flow --------------------------------------------------------------------


def test_flow_importable_and_returns_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import brain.flows as flows

    assert callable(ingest_source_flow)
    monkeypatch.setattr(flows, "fetch_rss", lambda url, timeout=30: FIXTURE.read_bytes())
    conn = FakeConnection()
    monkeypatch.setattr(flows, "upsert_documents", lambda docs: upsert_documents(docs, conn=conn))
    result = ingest_source_flow(source_name="Social Media Today")
    assert result["inserted"] == 3
    assert result["skipped"] == 0
