"""Ticket 13: JCK Online zero-entry feed.

Observable behavior (not privates):
- An empty/unparseable feed raises an explicit per-source error — never a
  silent {inserted: 0, skipped: 0}.
- A zero-entry JCK payload is diagnosed: dead/changed feed URL vs.
  blocked/challenge page.
- The dead JCK feed URL is corrected by code-level fallback routing
  (curated-sources.json untouched) so JCK ingests articles again.
- upsert_documents with an unknown source raises instead of a NULL insert.
- An Ingestion Run stays independently rerunnable with explicit partial
  failure (one bad source never blocks the others, reruns are stable).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import brain.flows as flows
from brain.flows import ingest_source_flow, ingest_sources_flow
from brain.ingest import (
    EmptyFeedError,
    diagnose_empty_feed,
    feed_candidate_urls,
    parse_feed,
    parse_feed_with_report,
    upsert_documents,
)
from brain.normalize import NormalizedDocument
from brain.sources import get_source

FIXTURES = Path(__file__).parent / "fixtures"
CURATED = (
    Path(__file__).resolve().parents[1]
    / ".scratch"
    / "trend-intelligence-brain"
    / "curated-sources.json"
)

JCK = "JCK Online"
JCK_FEED_URL = "https://www.jckonline.com/feed/"
JCK_FALLBACK_URL = "https://www.jckonline.com/category/news-trends/retail/feed/"

# Shape of the live 2026-09-06 JCK payload: HTTP 200, channel skeleton, 0 items.
EMPTY_JCK_SKELETON = b"""<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"
    xmlns:content="http://purl.org/rss/1.0/modules/content/"
    xmlns:atom="http://www.w3.org/2005/Atom">
<channel>
    <title>JCK</title>
    <atom:link href="https://www.jckonline.com/feed/" rel="self" type="application/rss+xml" />
    <link>https://www.jckonline.com</link>
    <description>The Industry Authority</description>
    <lastBuildDate>Sat, 05 Sep 2026 21:04:16 +0000</lastBuildDate>
    <language>en-US</language>
    <generator>https://wordpress.org/?v=7.1</generator>
</channel>
</rss>"""

CHALLENGE_PAGE = (
    "<html><head><title>Just a moment...</title></head>"
    "<body>Attention Required! | Cloudflare challenge \u2014 verify you are human</body></html>"
).encode("utf-8")


# --- (a) empty/unparseable feed raises an explicit per-source error ------------


def test_empty_feed_parse_raises_explicit_error() -> None:
    with pytest.raises(EmptyFeedError, match="0 entries"):
        parse_feed(EMPTY_JCK_SKELETON, source=JCK)


def test_empty_feed_error_is_a_value_error() -> None:
    # Totally unparseable feeds already raise ValueError; empty feeds join
    # that lane so the batch parent converts both to explicit per-source errors.
    assert issubclass(EmptyFeedError, ValueError)
    with pytest.raises(ValueError):
        parse_feed(b"\x00\x01 not xml at all \xff\xfe", source=JCK)


def test_empty_feed_report_raises_instead_of_empty_report() -> None:
    with pytest.raises(EmptyFeedError):
        parse_feed_with_report(EMPTY_JCK_SKELETON, source=JCK)


def test_per_item_skips_still_parse_partially() -> None:
    payload = (
        b'<?xml version="1.0"?><rss version="2.0"><channel><title>T</title>'
        b"<item><title>Good item</title><link>https://example.com/a</link>"
        b"<description>body</description></item>"
        b"<item><title>No link item</title></item>"
        b"</channel></rss>"
    )
    report = parse_feed_with_report(payload, source="Test Source")
    assert len(report.documents) == 1
    assert report.skipped == 1
    assert report.skipped_reasons and "missing link" in report.skipped_reasons[0]


def test_leaf_flow_empty_feed_raises_no_silent_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """MarTech has no fallback route: an empty feed must raise out of the leaf
    flow (red subflow) rather than return a silent {inserted: 0, skipped: 0}."""

    def empty_fetch(url: str, source_name: str | None = None) -> bytes:
        return EMPTY_JCK_SKELETON

    monkeypatch.setattr(flows, "fetch_task", empty_fetch)
    with pytest.raises(EmptyFeedError):
        ingest_source_flow(source_name="MarTech")


def test_batch_converts_empty_feed_to_explicit_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    martech_url = get_source("MarTech")["rss_url"]
    pj_url = get_source("Professional Jeweller")["rss_url"]

    def fake_fetch(url: str, source_name: str | None = None) -> bytes:
        if url == pj_url:
            return EMPTY_JCK_SKELETON
        assert url == martech_url
        return (FIXTURES / "martech_sample.xml").read_bytes()

    monkeypatch.setattr(flows, "fetch_task", fake_fetch)
    monkeypatch.setattr(flows, "enrich_document_or_keep", lambda doc, *a, **k: (doc, "rss", None))
    monkeypatch.setattr(flows, "upsert_documents", lambda docs: (len(docs), 0))
    results = ingest_sources_flow(source_names=["MarTech", "Professional Jeweller"])
    assert results["MarTech"] == {"inserted": 3, "skipped": 0}
    assert results["Professional Jeweller"]["inserted"] == 0
    assert results["Professional Jeweller"]["skipped"] == 0
    assert "error" in results["Professional Jeweller"]
    assert results["Professional Jeweller"]["error"]


# --- (b) JCK zero-entry payload diagnosed: dead/changed vs blocked -------------


def test_empty_jck_skeleton_diagnosed_dead_not_blocked() -> None:
    diagnosis = diagnose_empty_feed(EMPTY_JCK_SKELETON, url=JCK_FEED_URL, source=JCK)
    assert "dead" in diagnosis or "changed" in diagnosis
    assert "blocked" not in diagnosis and "challenge" not in diagnosis


def test_challenge_page_diagnosed_blocked() -> None:
    diagnosis = diagnose_empty_feed(CHALLENGE_PAGE, url=JCK_FEED_URL, source=JCK)
    assert "blocked" in diagnosis or "challenge" in diagnosis


def test_empty_feed_error_message_carries_diagnosis() -> None:
    with pytest.raises(EmptyFeedError) as excinfo:
        parse_feed(EMPTY_JCK_SKELETON, source=JCK)
    message = str(excinfo.value).lower()
    assert "0 entries" in message
    assert "dead" in message or "changed" in message


# --- fallback correction lives in code, curated JSON untouched -----------------


def test_jck_fallback_routing_declared_in_code() -> None:
    candidates = feed_candidate_urls(JCK_FEED_URL)
    assert candidates[0] == JCK_FEED_URL
    assert JCK_FALLBACK_URL in candidates[1:]
    # Sources without a declared correction keep the single-URL contract.
    assert feed_candidate_urls(get_source("MarTech")["rss_url"]) == (
        get_source("MarTech")["rss_url"],
    )


def test_curated_jck_entry_untouched() -> None:
    entries = {e["source_name"]: e for e in json.loads(CURATED.read_text(encoding="utf-8"))}
    assert entries[JCK]["rss_url"] == JCK_FEED_URL
    assert entries[JCK]["retrieval"] == {"type": "rss", "policy": "stdlib-only"}
    assert get_source(JCK)["rss_url"] == JCK_FEED_URL


def test_jck_flow_falls_back_and_inserts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dead primary (0 entries) routes to the category fallback in code and
    the Ingestion Run inserts articles again."""
    seen: list[str] = []
    fallback_payload = (FIXTURES / "martech_sample.xml").read_bytes()

    def fake_fetch(url: str, source_name: str | None = None) -> bytes:
        seen.append(url)
        assert source_name == JCK
        if url == JCK_FEED_URL:
            return EMPTY_JCK_SKELETON
        assert url == JCK_FALLBACK_URL
        return fallback_payload

    monkeypatch.setattr(flows, "fetch_task", fake_fetch)
    monkeypatch.setattr(flows, "enrich_document_or_keep", lambda doc, *a, **k: (doc, "rss", None))
    monkeypatch.setattr(flows, "upsert_documents", lambda docs: (len(docs), 0))
    result = ingest_source_flow(source_name=JCK)
    assert result["inserted"] > 0
    assert "error" not in result
    assert seen == [JCK_FEED_URL, JCK_FALLBACK_URL]


def test_jck_flow_all_candidates_empty_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def empty_fetch(url: str, source_name: str | None = None) -> bytes:
        return EMPTY_JCK_SKELETON

    monkeypatch.setattr(flows, "fetch_task", empty_fetch)
    with pytest.raises(EmptyFeedError):
        ingest_source_flow(source_name=JCK)


# --- (c) upsert with unknown source raises instead of NULL insert --------------


class _UnknownSourceCursor:
    """SELECT finds no source row; records every statement for the NULL-guard."""

    def __init__(self, statements: list[str]) -> None:
        self._statements = statements
        self.rowcount = 0

    def execute(self, sql: str, params: tuple | None = None) -> _UnknownSourceCursor:
        self._statements.append(sql)
        return self

    def fetchone(self) -> tuple | None:
        return None


class _UnknownSourceConnection:
    def __init__(self) -> None:
        self.statements: list[str] = []
        self.commits = 0

    def execute(self, sql: str, params: tuple | None = None) -> _UnknownSourceCursor:
        return _UnknownSourceCursor(self.statements).execute(sql, params)

    def commit(self) -> None:
        self.commits += 1


def _ghost_doc() -> NormalizedDocument:
    docs = parse_feed((FIXTURES / "martech_sample.xml").read_bytes(), source="Ghost Source")
    assert docs
    return docs[0]


def test_upsert_unknown_source_raises_no_null_insert() -> None:
    conn = _UnknownSourceConnection()
    with pytest.raises(ValueError, match="Unknown source"):
        upsert_documents([_ghost_doc()], conn=conn)  # type: ignore[arg-type]
    assert not any("INSERT" in s.upper() for s in conn.statements)
    assert conn.commits == 0


# --- (d) Ingestion Run independently rerunnable, partial failure explicit ------


def test_batch_rerunnable_with_explicit_partial_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    martech_url = get_source("MarTech")["rss_url"]
    pj_url = get_source("Professional Jeweller")["rss_url"]

    def fake_fetch(url: str, source_name: str | None = None) -> bytes:
        if url == pj_url:
            return EMPTY_JCK_SKELETON
        assert url == martech_url
        return (FIXTURES / "martech_sample.xml").read_bytes()

    monkeypatch.setattr(flows, "fetch_task", fake_fetch)
    monkeypatch.setattr(flows, "enrich_document_or_keep", lambda doc, *a, **k: (doc, "rss", None))
    monkeypatch.setattr(flows, "upsert_documents", lambda docs: (len(docs), 0))
    names = ["MarTech", "Professional Jeweller"]
    first = ingest_sources_flow(source_names=names)
    second = ingest_sources_flow(source_names=names)
    assert first == second  # independently rerunnable: stable across reruns
    assert first["MarTech"] == {"inserted": 3, "skipped": 0}
    assert "error" in first["Professional Jeweller"]  # explicit partial failure
