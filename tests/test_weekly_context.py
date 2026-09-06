"""Weekly context + observability contract tests (Ticket 04).

Semantic-interface tests with fake-DB connections (no live Postgres):

- weekly period boundaries interpreted in America/Panama (Sept = UTC-5),
  independent of server-local time
- date-range filtering, newest-first ordering, provenance keys, empty ranges,
  invalid-input validation
- per-source health readout from seeded run rows, independent of articles
- run recording wired into flows without changing result shapes
- seeded regression corpus: the fixture files ARE the corpus — re-parsing
  the 4 V1 fixtures (+ messy) must yield stable doc counts and hashes, so
  future extraction/ranking changes are caught here first
"""

from __future__ import annotations

import time
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

import brain.flows as flows
from brain.health import get_source_health, record_ingestion_run
from brain.ingest import parse_feed, upsert_documents
from brain.normalize import content_hash_for
from brain.sources import V1_SOURCES, list_v1_sources
from brain.weekly import PANAMA_NAME, get_weekly_context

FIXTURES = Path(__file__).parent / "fixtures"


def _utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


# --- fakes -------------------------------------------------------------------


class _WeeklyCursor:
    """Serves preset document rows with real range/source/limit semantics."""

    def __init__(self, rows: list[tuple]) -> None:
        self._rows = rows
        self.last_sql: str | None = None
        self.last_params: tuple | None = None
        self._result: list[tuple] = []

    def execute(self, sql: str, params: tuple | None = None) -> _WeeklyCursor:
        self.last_sql = sql
        self.last_params = params
        assert params is not None
        start, end, names, limit = params
        kept = [r for r in self._rows if r[4] >= start and r[4] < end and r[3] in set(names)]
        kept.sort(key=lambda r: r[4], reverse=True)
        self._result = kept[: int(limit)]
        return self

    def fetchall(self) -> list[tuple]:
        return self._result


class _WeeklyConnection:
    def __init__(self, rows: list[tuple]) -> None:
        self._rows = rows
        self.cursor_obj = _WeeklyCursor(rows)

    def execute(self, sql: str, params: tuple | None = None) -> _WeeklyCursor:
        return self.cursor_obj.execute(sql, params)

    def commit(self) -> None:
        pass

    def close(self) -> None:
        pass


class _RecordConnection:
    """Captures INSERT params for record_ingestion_run assertions."""

    def __init__(self) -> None:
        self.inserts: list[tuple] = []
        self.commits = 0

    def execute(self, sql: str, params: tuple | None = None) -> _RecordConnection:
        self.inserts.append((sql, params))
        return self

    def commit(self) -> None:
        self.commits += 1

    def close(self) -> None:
        pass


class _RunsCursor:
    """Returns the latest run per requested source (mimics DISTINCT ON)."""

    def __init__(self, runs: list[tuple]) -> None:
        self._runs = runs
        self.last_sql: str | None = None
        self.last_params: tuple | None = None

    def execute(self, sql: str, params: tuple | None = None) -> _RunsCursor:
        self.last_sql = sql
        self.last_params = params
        return self

    def fetchall(self) -> list[tuple]:
        names = set(self.last_params[0]) if self.last_params else set()
        latest: dict[str, tuple] = {}
        for run in self._runs:
            if run[0] in names and (run[0] not in latest or run[2] > latest[run[0]][2]):
                latest[run[0]] = run
        return list(latest.values())


class _RunsConnection:
    def __init__(self, runs: list[tuple]) -> None:
        self.cursor_obj = _RunsCursor(runs)

    def execute(self, sql: str, params: tuple | None = None) -> _RunsCursor:
        return self.cursor_obj.execute(sql, params)

    def close(self) -> None:
        pass


class _UpsertFakeConnection:
    """Minimal ON CONFLICT DO NOTHING fake for flow wiring tests."""

    def __init__(self) -> None:
        self.store: dict[str, tuple] = {}

    def execute(self, sql: str, params: tuple | None = None) -> _UpsertFakeConnection:
        assert params is not None
        if sql.lstrip().upper().startswith("SELECT"):
            self.rowcount = 1
            return self
        url = params[1]
        self.rowcount = 0 if url in self.store else 1
        if url not in self.store:
            self.store[url] = params
        return self

    def fetchone(self) -> tuple | None:
        return (1,)

    def commit(self) -> None:
        pass

    rowcount: int = 0


def _stub_enrich_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep flow tests hermetic: enrichment is lane 02/03's concern, not weekly/run shape.

    Identity-enriches every document so Ingestion Runs over fixtures keep their
    pre-enrichment result shapes and skip-reason accounting (no live fetches).
    """
    monkeypatch.setattr(flows, "enrich_document_or_keep", lambda doc, *a, **k: (doc, "rss", None))


# --- seeded article rows -------------------------------------------------------
# (title, url, canonical_url, source, published_at, author,
#  flag_reason, flag_detail, flagged_at, flagged_by)

ARTICLE_ROWS = [
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
    ),
    (
        "Vicenzaoro Opens",
        "https://www.professionaljeweller.com/vicenzaoro/3/",
        "https://www.professionaljeweller.com/vicenzaoro/3/",
        "Professional Jeweller",
        _utc(2026, 9, 10, 8, 0),
        "Sarah Jordan",
        None,
        None,
        None,
        None,
    ),
    (
        "Casas Bahia em crise",
        "https://www.infomoney.com.br/business/casas-bahia/4/",
        "https://www.infomoney.com.br/business/casas-bahia/4/",
        "InfoMoney",
        _utc(2026, 9, 11, 14, 0),
        "Mariana Ribeiro",
        None,
        None,
        None,
        None,
    ),
    (
        "JCK Extra-scope Piece",
        "https://www.jckonline.com/editorial/5/",
        "https://www.jckonline.com/editorial/5/",
        "JCK Online",
        _utc(2026, 9, 9, 15, 0),
        "Anita",
        None,
        None,
        None,
        None,
    ),
    (
        "August History",
        "https://www.socialmediatoday.com/news/old/6/",
        "https://www.socialmediatoday.com/news/old/6/",
        "Social Media Today",
        _utc(2026, 8, 20, 12, 0),
        "Jane Doe",
        None,
        None,
        None,
        None,
    ),
    (
        "Next-week Boundary",
        "https://martech.org/next-week/7/",
        "https://martech.org/next-week/7/",
        "MarTech",
        _utc(2026, 9, 14, 5, 0),  # exactly Mon 00:00 Panama -> exclusive
        "Kim Davis",
        None,
        None,
        None,
        None,
    ),
]

WEEK_FROM = date(2026, 9, 7)
WEEK_TO = date(2026, 9, 13)


def _context(rows: list[tuple] = ARTICLE_ROWS, **kwargs):  # type: ignore[no-untyped-def]
    conn = _WeeklyConnection(rows)
    return get_weekly_context(WEEK_FROM, WEEK_TO, conn=conn, **kwargs), conn


# --- V1 scope -----------------------------------------------------------------


def test_v1_sources_constant_is_exactly_the_four() -> None:
    assert V1_SOURCES == (
        "Social Media Today",
        "MarTech",
        "Professional Jeweller",
        "InfoMoney",
    )


def test_list_v1_sources_resolves_registry_entries() -> None:
    entries = list_v1_sources()
    assert [e["name"] for e in entries] == list(V1_SOURCES)
    assert all(e["rss_url"] for e in entries)


def test_weekly_defaults_to_v1_sources() -> None:
    ctx, conn = _context()
    assert conn.cursor_obj.last_params is not None
    assert set(conn.cursor_obj.last_params[2]) == set(V1_SOURCES)
    assert {a["source"] for a in ctx["important_articles"]} <= set(V1_SOURCES)


# --- weekly period + provenance -----------------------------------------------


def test_response_shape_matches_spec_conceptual_contract() -> None:
    ctx, _ = _context()
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
    # V1: no accumulated history -> no velocity/emerging claims, explicit empties.
    assert ctx["top_stories"] == []
    assert ctx["emerging_topics"] == []
    assert ctx["topic_movements"] == []
    assert ctx["notable_entities"] == []
    assert ctx["source_convergence"] == []


def test_period_boundaries_are_panama_utc_minus_five() -> None:
    ctx, _ = _context()
    assert ctx["period"]["timezone"] == PANAMA_NAME
    # September Panama has no DST: UTC-5.
    assert ctx["period"]["from"] == "2026-09-07T00:00:00-05:00"
    assert ctx["period"]["to"] == "2026-09-14T00:00:00-05:00"


def test_range_filtering_newest_first_and_provenance() -> None:
    ctx, _ = _context()
    articles = ctx["important_articles"]
    assert [a["title"] for a in articles] == [
        "Casas Bahia em crise",
        "Vicenzaoro Opens",
        "Signal Loss Rebuild",
        "TikTok Adds Voice Notes",
    ]
    for article in articles:
        assert set(article) == {
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
        }
        parsed = datetime.fromisoformat(str(article["published_at"]))
        assert parsed.tzinfo is not None
    # Out-of-range August article, next-week boundary, and non-V1 JCK excluded.
    titles = {a["title"] for a in articles}
    assert "August History" not in titles
    assert "Next-week Boundary" not in titles
    assert "JCK Extra-scope Piece" not in titles
    # Nullable author passes through.
    by_title = {a["title"]: a for a in articles}
    assert by_title["Signal Loss Rebuild"]["author"] is None


def test_explicit_sources_reach_beyond_v1() -> None:
    ctx, _ = _context(sources=["JCK Online"])
    assert [a["title"] for a in ctx["important_articles"]] == ["JCK Extra-scope Piece"]


def test_empty_range_returns_empty_articles_with_period() -> None:
    conn = _WeeklyConnection(ARTICLE_ROWS)
    ctx = get_weekly_context(date(2026, 1, 5), date(2026, 1, 11), conn=conn)
    assert ctx["important_articles"] == []
    assert ctx["period"]["timezone"] == PANAMA_NAME


def test_naive_datetime_assumed_panama_not_server_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TZ", "Pacific/Kiritimati")  # UTC+14, far from Panama
    time.tzset()
    try:
        conn = _WeeklyConnection([])
        ctx = get_weekly_context(datetime(2026, 9, 7, 9, 0), datetime(2026, 9, 7, 10, 0), conn=conn)
        assert ctx["period"]["from"] == "2026-09-07T09:00:00-05:00"
    finally:
        time.tzset()


def test_aware_datetime_converted_to_panama() -> None:
    conn = _WeeklyConnection([])
    ctx = get_weekly_context(
        datetime(2026, 9, 7, 14, 0, tzinfo=UTC),
        datetime(2026, 9, 8, 14, 0, tzinfo=UTC),
        conn=conn,
    )
    assert ctx["period"]["from"] == "2026-09-07T09:00:00-05:00"


def test_invalid_inputs_rejected() -> None:
    conn = _WeeklyConnection([])
    with pytest.raises(ValueError):
        get_weekly_context(date(2026, 9, 13), date(2026, 9, 7), conn=conn)
    with pytest.raises(TypeError):
        get_weekly_context("2026-09-07", WEEK_TO, conn=conn)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        get_weekly_context(WEEK_FROM, WEEK_TO, conn=conn, limit=0)


def test_limit_bounds_results() -> None:
    ctx, _ = _context(limit=2)
    assert len(ctx["important_articles"]) == 2


# --- health --------------------------------------------------------------------


def _run(source: str, day: int, inserted: int, error: str | None = None) -> tuple:
    return (
        source,
        _utc(2026, 9, day, 9, 0),
        _utc(2026, 9, day, 9, 1),
        inserted,
        0,
        0,
        error,
    )


def test_get_source_health_reports_latest_run_per_source() -> None:
    runs = [
        _run("Social Media Today", 7, 2),
        _run("Social Media Today", 8, 3),  # latest wins
        _run("MarTech", 8, 0, "fetch failed: boom"),
    ]
    report = get_source_health(conn=_RunsConnection(runs))
    assert [r["source"] for r in report] == list(V1_SOURCES)
    by_source = {r["source"]: r for r in report}
    assert by_source["Social Media Today"]["status"] == "ok"
    assert by_source["Social Media Today"]["inserted"] == 3
    assert by_source["MarTech"]["status"] == "error"
    assert by_source["MarTech"]["error"] == "fetch failed: boom"
    # No run recorded -> unknown, independent of article tables.
    assert by_source["InfoMoney"]["status"] == "unknown"
    assert by_source["InfoMoney"]["inserted"] is None


def test_record_ingestion_run_persists_counts_and_reasons() -> None:
    conn = _RecordConnection()
    record_ingestion_run(
        "MarTech",
        {"inserted": 1, "skipped": 0, "parse_skipped": 2},
        started_at=_utc(2026, 9, 8, 9, 0),
        finished_at=_utc(2026, 9, 8, 9, 1),
        skipped_reasons=["entry 1: missing title", "entry 2: missing link"],
        conn=conn,
    )
    assert conn.commits == 1
    sql, params = conn.inserts[0]
    assert "ingestion_runs" in sql
    assert params[0] == "MarTech"
    assert params[3:6] == (1, 0, 2)
    assert params[6] is None
    assert params[7] == ["entry 1: missing title", "entry 2: missing link"]


def test_flow_records_runs_without_changing_result_shapes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple] = []
    monkeypatch.setattr(
        flows,
        "record_ingestion_run",
        lambda source, result, **kw: captured.append((source, dict(result), kw)),
    )
    monkeypatch.setattr(
        flows, "fetch_rss", lambda url, timeout=30: (FIXTURES / "martech_sample.xml").read_bytes()
    )
    _stub_enrich_identity(monkeypatch)
    upsert_conn = _UpsertFakeConnection()
    monkeypatch.setattr(
        flows, "upsert_documents", lambda docs: upsert_documents(docs, conn=upsert_conn)
    )
    result = flows.ingest_source_flow(source_name="MarTech")
    assert result == {"inserted": 3, "skipped": 0}
    assert len(captured) == 1
    source, recorded, kw = captured[0]
    assert source == "MarTech"
    assert recorded == {"inserted": 3, "skipped": 0}
    assert kw["started_at"].tzinfo is not None and kw["finished_at"].tzinfo is not None
    assert kw["finished_at"] >= kw["started_at"]

    # Error outcomes are recorded too, with the error intact in the result.
    flows.ingest_source_flow(source_name="No Such Source")
    assert captured[-1][1]["error"]


def test_flow_records_skip_reasons_on_messy_feeds(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[tuple] = []
    monkeypatch.setattr(
        flows,
        "record_ingestion_run",
        lambda source, result, **kw: captured.append((source, dict(result), kw)),
    )
    monkeypatch.setattr(
        flows, "fetch_rss", lambda url, timeout=30: (FIXTURES / "messy_sample.xml").read_bytes()
    )
    _stub_enrich_identity(monkeypatch)
    upsert_conn = _UpsertFakeConnection()
    monkeypatch.setattr(
        flows, "upsert_documents", lambda docs: upsert_documents(docs, conn=upsert_conn)
    )
    result = flows.ingest_source_flow(source_name="MarTech")
    assert result["parse_skipped"] == 3
    assert len(captured) == 1
    assert len(captured[0][2]["skipped_reasons"]) == 3


# --- migration 003 ---------------------------------------------------------------


def test_migration_003_creates_ingestion_runs_idempotently() -> None:
    from brain.db import MIGRATIONS_DIR

    assert sorted(p.name for p in MIGRATIONS_DIR.glob("*.sql")) == [
        "001_init.sql",
        "002_canonical_url_unique.sql",
        "003_ingestion_runs.sql",
        "005_extraction_flag.sql",
    ]
    sql = (MIGRATIONS_DIR / "003_ingestion_runs.sql").read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS ingestion_runs" in sql
    for column in (
        "source_name",
        "started_at",
        "finished_at",
        "inserted",
        "skipped",
        "parse_skipped",
        "error",
        "skipped_reasons",
    ):
        assert column in sql
    assert "IF NOT EXISTS" in sql.upper()
    # 002 backfill probe documented as a comment, not a cleanup statement.
    assert "canonical_url" in sql and "GROUP BY" in sql.upper()
    assert "DELETE" not in sql.upper()


# --- seeded regression corpus ------------------------------------------------------
# The V1 (+ messy) fixture files ARE the regression corpus for future
# extraction/ranking changes: counts, first-doc hashes, cross-run stability,
# and hash self-consistency are pinned here.

CORPUS: dict[str, tuple[str, int, str]] = {
    # source -> (fixture file, expected doc count, first-doc content_hash)
    "Social Media Today": (
        "smt_sample.xml",
        3,
        "7484d54e4cbd34f24aea82439aa7e26d95903a5ba3059f2871326091c67ed225",
    ),
    "MarTech": (
        "martech_sample.xml",
        3,
        "f60c0333d4d33b297c2547be39a8b9cb1794b1629119c9d21559ddc6fea3aced",
    ),
    "Professional Jeweller": (
        "pj_sample.xml",
        3,
        "3eed2ccf4d9d9a86cfa7f17f666f0ee52c419ddf7a4255c5926d8526db311fe8",
    ),
    "InfoMoney": (
        "infomoney_sample.xml",
        3,
        "8e86c8a3ef6e5f185bf4d6a2f8fa9e3c6744fd861c53fac67169f3a45a9a2ff2",
    ),
    "Test Source": (
        "messy_sample.xml",
        3,
        "d355a76d64482a4bd00a03d50ed93e4d8f0517931798e32223db8f4138553ce2",
    ),
}


def test_regression_corpus_counts_hashes_and_stability() -> None:
    for source, (filename, count, first_hash) in CORPUS.items():
        payload = (FIXTURES / filename).read_bytes()
        docs = parse_feed(payload, source=source)
        assert len(docs) == count, f"{filename}: expected {count} docs"
        assert docs[0].content_hash == first_hash, f"{filename}: first-doc hash drift"
        again = parse_feed(payload, source=source)
        assert [d.content_hash for d in again] == [d.content_hash for d in docs]
        for doc in docs:
            assert doc.content_hash == content_hash_for(doc.title, doc.content)
