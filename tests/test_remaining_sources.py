"""Remaining V1 sources on the 01 adapter contract (Ticket 02).

Observable behavior (not privates):
- registry returns all 4 V1 sources with correct rss_url
- each fixture parses to the normalized contract (incl. pt for InfoMoney)
- rerun upsert is idempotent per source
- unknown source flow returns an explicit error dict
- multi-source flow: one source failing still ingests the others
- migration SQL seeds all 4 sources
"""

from __future__ import annotations

from pathlib import Path

import pytest

from brain.flows import ingest_source_flow, ingest_sources_flow
from brain.ingest import parse_feed, upsert_documents
from brain.normalize import NormalizedDocument
from brain.sources import get_source

FIXTURES = Path(__file__).parent / "fixtures"

SMT = "Social Media Today"
MARTECH = "MarTech"
PJ = "Professional Jeweller"
INFOMONEY = "InfoMoney"

EXPECTED_RSS = {
    SMT: "https://www.socialmediatoday.com/feeds/news/",
    MARTECH: "https://martech.org/feed/",
    PJ: "https://www.professionaljeweller.com/feed/",
    INFOMONEY: "https://www.infomoney.com.br/feed",
}

FIXTURE_FILES = {
    SMT: FIXTURES / "smt_sample.xml",
    MARTECH: FIXTURES / "martech_sample.xml",
    PJ: FIXTURES / "pj_sample.xml",
    INFOMONEY: FIXTURES / "infomoney_sample.xml",
}

EXPECTED_LANGUAGE = {SMT: "en", MARTECH: "en", PJ: "en", INFOMONEY: "pt"}


# --- registry ---------------------------------------------------------------


def test_registry_returns_all_four_v1_sources_with_correct_rss_url() -> None:
    for name, rss in EXPECTED_RSS.items():
        src = get_source(name)
        assert src["name"] == name
        assert src["rss_url"] == rss


def test_registry_language_codes_are_iso() -> None:
    assert get_source(SMT)["language"] == "en"
    assert get_source(MARTECH)["language"] == "en"
    assert get_source(PJ)["language"] == "en"
    assert get_source(INFOMONEY)["language"] == "pt"


# --- normalization contract -------------------------------------------------


@pytest.mark.parametrize("name", [MARTECH, PJ, INFOMONEY])
def test_fixture_parses_to_documents(name: str) -> None:
    docs = parse_feed(FIXTURE_FILES[name].read_bytes(), source=name)
    assert len(docs) == 3
    for doc in docs:
        assert isinstance(doc, NormalizedDocument)
        assert doc.source == name
        assert doc.url.startswith("http")
        assert doc.canonical_url.startswith("http")
        assert doc.title.strip()
        assert doc.content.strip()
        assert doc.content_hash
        assert doc.published_at.tzinfo is not None
        assert doc.retrieved_at.tzinfo is not None
    assert {d.language for d in docs} == {EXPECTED_LANGUAGE[name]}


def test_martech_contract_details() -> None:
    docs = {d.title: d for d in parse_feed(FIXTURE_FILES[MARTECH].read_bytes(), source=MARTECH)}
    # HTML stripped to visible text.
    assert (
        "measurement foundations"
        in docs["Marketing Without Signals: How to Perform When the Data Disappears"].content
    )
    assert (
        "<"
        not in docs["Marketing Without Signals: How to Perform When the Data Disappears"].content
    )
    # Missing author stays nullable; present author survives.
    assert docs["Identity Resolution Vendors Compared: 2026 Buyer's Guide"].author is None
    assert (
        docs["Marketing Without Signals: How to Perform When the Data Disappears"].author
        == "Kim Davis"
    )
    # Query/fragment dropped from canonical URL.
    assert (
        docs["Marketing Without Signals: How to Perform When the Data Disappears"].canonical_url
        == "https://martech.org/marketing-without-signals-how-to-perform-when-the-data-disappears/612044/"
    )
    # Naive ISO date assumed UTC.
    assert docs["AI Agents Move From Pilots to Marketing Workflows"].published_at.isoformat() == (
        "2026-09-02T16:20:00+00:00"
    )


def test_pj_contract_details() -> None:
    docs = {d.title: d for d in parse_feed(FIXTURE_FILES[PJ].read_bytes(), source=PJ)}
    assert docs["Independent Retailers Report Strong August Lab-Grown Sales"].author is None
    assert docs["Vicenzaoro September Kicks Off With Record Exhibitor Numbers"].author == (
        "Sarah Jordan"
    )
    assert (
        docs["Vicenzaoro September Kicks Off With Record Exhibitor Numbers"].canonical_url
        == "https://www.professionaljeweller.com/vicenzaoro-september-kicks-off-today/102341/"
    )
    assert docs["Gold Price Rally Reshapes Autumn Buying Plans"].published_at.isoformat() == (
        "2026-09-02T09:00:00+00:00"
    )


def test_infomoney_docs_are_portuguese() -> None:
    docs = parse_feed(FIXTURE_FILES[INFOMONEY].read_bytes(), source=INFOMONEY)
    by_title = {d.title: d for d in docs}
    for doc in docs:
        assert doc.language == "pt"
    # Portuguese content survives normalization.
    assert (
        "endividamento do varejo"
        in by_title["Casas Bahia em crise: quem mais no varejo enfrenta pressão de dívida?"].content
    )
    assert by_title["Ibovespa fecha em alta com bancos e Petrobras no radar"].author is None
    assert (
        by_title["Casas Bahia em crise: quem mais no varejo enfrenta pressão de dívida?"].author
        == "Mariana Ribeiro"
    )
    assert by_title[
        "Casas Bahia em crise: quem mais no varejo enfrenta pressão de dívida?"
    ].canonical_url == (
        "https://www.infomoney.com.br/business/"
        "casas-bahia-em-crise-quem-mais-no-varejo-enfrenta-pressao-de-divida/987101/"
    )
    assert by_title[
        "Juros, consumo e luxo: o que muda para as joalherias em 2026"
    ].published_at.isoformat() == ("2026-09-02T20:15:00+00:00")


# --- dedupe / idempotency ----------------------------------------------------


class _FakeCursor:
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
        url, content_hash = params[1], params[-1]
        if url in self._store or content_hash in {p[-1] for p in self._store.values()}:
            self.rowcount = 0
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
        self.statements: list[str] = []

    def execute(self, sql: str, params: tuple | None = None) -> _FakeCursor:
        self.statements.append(sql)
        return _FakeCursor(self.store).execute(sql, params)

    def commit(self) -> None:
        self.commits += 1


def _stub_enrich_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep flow tests hermetic: enrichment is lane 02/03's concern, not parse/dedupe.

    Identity-enriches every document so Ingestion Runs over fixtures keep their
    pre-enrichment {inserted, skipped} shapes (no live article fetches).
    """
    import brain.flows as _flows

    monkeypatch.setattr(_flows, "enrich_document_or_keep", lambda doc, *a, **k: (doc, "rss", None))


@pytest.mark.parametrize("name", [MARTECH, PJ, INFOMONEY])
def test_rerun_upsert_idempotent_per_source(name: str) -> None:
    docs = parse_feed(FIXTURE_FILES[name].read_bytes(), source=name)
    conn = FakeConnection()
    inserted, skipped = upsert_documents(docs, conn=conn)
    assert (inserted, skipped) == (3, 0)
    inserted2, skipped2 = upsert_documents(docs, conn=conn)
    assert (inserted2, skipped2) == (0, 3)
    assert len(conn.store) == 3


# --- flows -------------------------------------------------------------------


def test_unknown_source_flow_returns_explicit_error() -> None:
    result = ingest_source_flow(source_name="No Such Source")
    assert result["inserted"] == 0
    assert result["skipped"] == 0
    assert "error" in result and result["error"]


@pytest.mark.parametrize("name", [MARTECH, PJ, INFOMONEY])
def test_single_source_flow_rerunnable_independently(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    import brain.flows as flows

    fixture = FIXTURE_FILES[name].read_bytes()
    monkeypatch.setattr(flows, "fetch_rss", lambda url, timeout=30: fixture)
    _stub_enrich_identity(monkeypatch)
    conn = FakeConnection()
    monkeypatch.setattr(flows, "upsert_documents", lambda docs: upsert_documents(docs, conn=conn))
    result = ingest_source_flow(source_name=name)
    assert result == {"inserted": 3, "skipped": 0}


def test_multi_source_flow_records_failure_without_blocking_others(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import brain.flows as flows

    by_url = {
        src["rss_url"]: name for name, src in ((n, get_source(n)) for n in (MARTECH, PJ, INFOMONEY))
    }
    failing_url = get_source(PJ)["rss_url"]

    def fake_fetch(url: str, timeout: int = 30) -> bytes:
        if url == failing_url:
            raise RuntimeError("boom")
        return FIXTURE_FILES[by_url[url]].read_bytes()

    monkeypatch.setattr(flows, "fetch_rss", fake_fetch)
    _stub_enrich_identity(monkeypatch)
    shared: dict[str, FakeConnection] = {}

    def fake_upsert(docs: list[NormalizedDocument]) -> tuple[int, int]:
        conn = shared.setdefault(docs[0].source, FakeConnection())
        return upsert_documents(docs, conn=conn)

    monkeypatch.setattr(flows, "upsert_documents", fake_upsert)
    results = ingest_sources_flow(source_names=[MARTECH, PJ, INFOMONEY])
    assert results[MARTECH] == {"inserted": 3, "skipped": 0}
    assert results[INFOMONEY] == {"inserted": 3, "skipped": 0}
    assert results[PJ]["inserted"] == 0
    assert "error" in results[PJ] and results[PJ]["error"]


def test_ingest_sources_flow_covers_v1_scope_default(monkeypatch: pytest.MonkeyPatch) -> None:
    import brain.flows as flows
    from brain.sources import V1_SOURCES

    def fake_fetch(url: str, timeout: int = 30) -> bytes:
        for name, path in FIXTURE_FILES.items():
            if url == get_source(name)["rss_url"]:
                return path.read_bytes()
        raise AssertionError(f"V1 default must not fetch non-V1 url: {url}")

    monkeypatch.setattr(flows, "fetch_rss", fake_fetch)

    def fake_upsert(docs: list[NormalizedDocument]) -> tuple[int, int]:
        return (len(docs), 0)

    monkeypatch.setattr(flows, "upsert_documents", fake_upsert)
    _stub_enrich_identity(monkeypatch)
    results = ingest_sources_flow()
    assert set(results) == set(V1_SOURCES)
    for name in V1_SOURCES:
        assert results[name] == {"inserted": 3, "skipped": 0}


def test_extra_registry_sources_ingestible_by_explicit_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import brain.flows as flows
    from brain.flows import ingest_sources_flow

    smt_bytes = (FIXTURES / "smt_sample.xml").read_bytes()
    monkeypatch.setattr(flows, "fetch_rss", lambda url, timeout=30: smt_bytes)

    def fake_upsert(docs: list[NormalizedDocument]) -> tuple[int, int]:
        return (len(docs), 0)

    monkeypatch.setattr(flows, "upsert_documents", fake_upsert)
    _stub_enrich_identity(monkeypatch)
    results = ingest_sources_flow(source_names=["JCK Online"])
    assert set(results) == {"JCK Online"}
    assert results["JCK Online"] == {"inserted": 3, "skipped": 0}


# --- migration ----------------------------------------------------------------


def test_migration_seeds_all_four_sources() -> None:
    sql = (FIXTURES.parent.parent / "migrations" / "001_init.sql").read_text(encoding="utf-8")
    for name, rss in EXPECTED_RSS.items():
        assert name in sql
        assert rss in sql
    assert sql.count("ON CONFLICT") >= 4
    assert "https://martech.org/" in sql
    assert "https://www.professionaljeweller.com/" in sql
    assert "https://www.infomoney.com.br/" in sql
