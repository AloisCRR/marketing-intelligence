"""Instagram premium lane payload side table tests (ADR-0013, migration 012).

Fake-conn only (no live Postgres): the migration's DDL is applied through a
fake that rejects non-idempotent statements, and the payload write-after-upsert
runs against a fake documents+payloads store.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import sys
import uuid
from pathlib import Path
from typing import Any

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_SRC = os.path.join(_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import marketing_intelligence.ingest as ingest  # noqa: E402
import marketing_intelligence.payloads as payloads  # noqa: E402
from marketing_intelligence.db import source_uuid  # noqa: E402
from marketing_intelligence.normalize import NormalizedDocument, make_document  # noqa: E402

SOURCE = "ig:sabrikolod"
SOURCE_UUID = "2554e4eb-8e39-5c76-8536-79cfc3d0cf08"
MIGRATION = Path(_ROOT) / "migrations" / "012_document_payloads.sql"

URL = "https://www.instagram.com/p/DdOz587EXSx/"
TRACKED_URL = "https://www.instagram.com/p/DdOz587EXSx/?utm_source=copy"
DOC_ID = str(uuid.uuid5(uuid.NAMESPACE_DNS, URL))

PAYLOAD = {
    "username": "sabrikolod",
    "caption": "Chismecito Marketinero: el drop de otoño ya está aquí",
    "likesCount": 1651,
    "commentsCount": 23,
    "hashtags": ["ChismecitoMarketinero"],
}


def _utc(*args: int) -> _dt.datetime:
    return _dt.datetime(*args, tzinfo=_dt.UTC)


def _doc(url: str = URL) -> NormalizedDocument:
    return make_document(
        source=SOURCE,
        url=url,
        title="Chismecito Marketinero",
        content="El drop de otoño ya está aquí.",
        published_at=_utc(2026, 9, 1, 12, 0),
        language="es",
    )


def _migration_text() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def _statements(sql_text: str) -> list[str]:
    """Split the migration into statements, dropping `--` comment lines."""
    body = "\n".join(line for line in sql_text.splitlines() if not line.strip().startswith("--"))
    return [stmt.strip() for stmt in body.split(";") if stmt.strip()]


class _StubCursor:
    def __init__(self, rows: list[tuple[Any, ...]] | None = None, rowcount: int = 0) -> None:
        self._rows = list(rows or [])
        self.rowcount = rowcount
        self.closed = False

    def fetchone(self) -> Any | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[Any]:
        return list(self._rows)

    def close(self) -> None:
        self.closed = True


class _DdlConnection:
    """DDL fake: rejects non-idempotent forms, tracks what a second apply does."""

    def __init__(self) -> None:
        self.executed: list[str] = []
        self.tables: set[str] = set()
        self.sources: dict[str, tuple[str, ...]] = {}

    def execute(self, sql: str, params: Any = None) -> _StubCursor:
        del params
        norm = " ".join(str(sql).split())
        upper = norm.upper()
        if upper.startswith(("ALTER TABLE", "DROP", "CREATE INDEX", "UPDATE", "DELETE")):
            raise AssertionError(f"012 must not touch existing objects: {norm}")
        if upper.startswith("CREATE TABLE"):
            if "IF NOT EXISTS" not in upper:
                raise AssertionError(f"non-idempotent CREATE: {norm}")
            match = re.match(r"CREATE TABLE IF NOT EXISTS (\w+)", norm, re.IGNORECASE)
            assert match, norm
            self.tables.add(match.group(1))
        elif upper.startswith("INSERT INTO SOURCES"):
            if "ON CONFLICT (NAME) DO NOTHING" not in upper:
                raise AssertionError(f"non-idempotent source INSERT: {norm}")
            values = re.search(r"VALUES \(([^)]*)\)", norm, re.IGNORECASE)
            assert values, norm
            fields = [v.strip().strip("'") for v in values.group(1).split(",")]
            self.sources.setdefault(fields[1], tuple(fields[2:]))
        else:
            raise AssertionError(f"unexpected statement in 012: {norm}")
        self.executed.append(norm)
        return _StubCursor()

    def commit(self) -> None:  # pragma: no cover - yoyo commits, fake does not care
        pass

    def close(self) -> None:  # pragma: no cover
        pass


class _StoreConnection:
    """Fake documents+payloads store: upsert rows, payload writes keyed by URL."""

    def __init__(self, source_id: str = "9d1f1c60-0000-4000-8000-000000000001") -> None:
        self.source_id = source_id
        self.documents: dict[str, dict[str, Any]] = {}  # url -> {"id", "canonical_url"}
        self.payloads: dict[str, str] = {}  # document_id -> serialized JSON
        self.payload_writes = 0
        self.resolutions: list[str] = []  # "url" | "canonical"
        self.statements: list[tuple[str, Any]] = []
        self.committed = 0
        self.closed = 0

    def execute(self, sql: str, params: Any = None) -> _StubCursor:
        self.statements.append((sql, params))
        if sql == ingest.SOURCE_ID_SQL:
            return _StubCursor([(self.source_id,)], 1)
        if sql == ingest.INSERT_SQL:
            url, canonical = params[1], params[2]
            existing = url in self.documents or any(
                entry["canonical_url"] == canonical for entry in self.documents.values()
            )
            if existing:
                return _StubCursor(rowcount=0)
            self.documents[url] = {
                "id": str(uuid.uuid5(uuid.NAMESPACE_DNS, url)),
                "canonical_url": canonical,
            }
            return _StubCursor(rowcount=1)
        if sql == payloads._DOCUMENT_ID_SQL:
            url, canonical = params
            if url in self.documents:
                self.resolutions.append("url")
                return _StubCursor([(self.documents[url]["id"],)], 1)
            for entry in self.documents.values():
                if entry["canonical_url"] == canonical:
                    self.resolutions.append("canonical")
                    return _StubCursor([(entry["id"],)], 1)
            return _StubCursor(rowcount=0)
        if sql == payloads._PAYLOAD_UPSERT_SQL:
            document_id, serialized = params
            self.payloads[document_id] = serialized
            self.payload_writes += 1
            return _StubCursor(rowcount=1)
        raise AssertionError(f"unexpected SQL: {sql}")

    def commit(self) -> None:
        self.committed += 1

    def close(self) -> None:
        self.closed += 1


# --- migration 012 -----------------------------------------------------------------


def test_migration_ddl_is_idempotent_and_scoped_to_the_side_table() -> None:
    conn = _DdlConnection()
    for _ in range(2):  # apply twice: fresh table, then a no-op re-apply
        for statement in _statements(_migration_text()):
            conn.execute(statement)

    assert conn.tables == {"document_payloads"}
    create = next(s for s in conn.executed if s.upper().startswith("CREATE TABLE"))
    assert "document_id UUID PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE" in create
    assert "payload JSONB NOT NULL" in create
    assert "retrieved_at TIMESTAMPTZ NOT NULL DEFAULT now()" in create


def test_migration_reasserts_the_premium_source_deterministically() -> None:
    text = _migration_text()
    assert str(source_uuid(SOURCE)) in text  # literal matches db.source_uuid
    assert str(source_uuid(SOURCE)) == SOURCE_UUID

    conn = _DdlConnection()
    for _ in range(2):
        for statement in _statements(text):
            conn.execute(statement)
    assert conn.sources == {
        SOURCE: (
            "NULL",  # rss_url: Instagram has no feed
            "https://www.instagram.com/sabrikolod/",
            "es",
            "true",
        )
    }


# --- payload write-after-upsert ------------------------------------------------------


def test_write_after_upsert_resolves_by_url_and_stores_payload() -> None:
    conn = _StoreConnection()
    doc = _doc()
    assert ingest.upsert_documents([doc], conn=conn) == (1, 0)

    assert payloads.write_document_payloads([(doc, PAYLOAD)], conn=conn) == (1, 0)
    assert conn.resolutions == ["url"]
    assert json.loads(conn.payloads[DOC_ID]) == PAYLOAD
    assert list(conn.payloads) == [DOC_ID]


def test_rewrite_upserts_in_place_without_duplicating() -> None:
    conn = _StoreConnection()
    doc = _doc()
    ingest.upsert_documents([doc], conn=conn)

    assert payloads.write_document_payloads([(doc, PAYLOAD)], conn=conn) == (1, 0)
    updated = {**PAYLOAD, "likesCount": 1702}
    assert payloads.write_document_payloads([(doc, updated)], conn=conn) == (1, 0)

    assert list(conn.payloads) == [DOC_ID]  # one row per document, overwritten
    assert json.loads(conn.payloads[DOC_ID])["likesCount"] == 1702
    assert conn.payload_writes == 2


def test_canonical_url_resolves_when_pair_url_differs_after_upsert() -> None:
    conn = _StoreConnection()
    ingest.upsert_documents([_doc(TRACKED_URL)], conn=conn)  # stored, canonical-identical

    assert payloads.write_document_payloads([(_doc(URL), PAYLOAD)], conn=conn) == (1, 0)
    assert conn.resolutions == ["canonical"]


def test_unknown_url_counts_skipped_and_never_raises() -> None:
    conn = _StoreConnection()  # no document rows at all

    assert payloads.write_document_payloads([(_doc(), PAYLOAD)], conn=conn) == (0, 1)
    assert conn.payloads == {}


def test_partial_batch_counts_known_and_unknown() -> None:
    conn = _StoreConnection()
    known = _doc()
    ingest.upsert_documents([known], conn=conn)
    unknown = _doc("https://www.instagram.com/p/ZZZZZZZZZZZ/")

    assert payloads.write_document_payloads([(known, PAYLOAD), (unknown, PAYLOAD)], conn=conn) == (
        1,
        1,
    )


def test_empty_pairs_is_a_noop() -> None:
    conn = _StoreConnection()
    assert payloads.write_document_payloads([], conn=conn) == (0, 0)
    assert conn.statements == []


def test_payload_json_keeps_non_ascii_text_readable() -> None:
    conn = _StoreConnection()
    doc = _doc()
    ingest.upsert_documents([doc], conn=conn)
    payloads.write_document_payloads([(doc, PAYLOAD)], conn=conn)

    assert "otoño" in conn.payloads[DOC_ID]
    assert "\\u" not in conn.payloads[DOC_ID]


# --- connection ownership ------------------------------------------------------------


def test_injected_connection_is_committed_but_not_closed() -> None:
    conn = _StoreConnection()
    conn.documents[URL] = {"id": DOC_ID, "canonical_url": URL}

    assert payloads.write_document_payloads([(_doc(), PAYLOAD)], conn=conn) == (1, 0)
    assert conn.committed == 1
    assert conn.closed == 0


def test_owned_connection_is_opened_committed_and_closed(monkeypatch: Any) -> None:
    conn = _StoreConnection()
    conn.documents[URL] = {"id": DOC_ID, "canonical_url": URL}
    monkeypatch.setattr(payloads, "get_connection", lambda: conn)

    assert payloads.write_document_payloads([(_doc(), PAYLOAD)]) == (1, 0)
    assert conn.committed == 1
    assert conn.closed == 1


# --- untouched contracts -------------------------------------------------------------


def test_documents_contract_stays_untouched() -> None:
    assert tuple(NormalizedDocument.__dataclass_fields__) == (
        "source",
        "url",
        "canonical_url",
        "title",
        "author",
        "published_at",
        "retrieved_at",
        "language",
        "content",
        "content_hash",
    )
    assert "payload" not in ingest.INSERT_SQL.lower()
    text = _migration_text()
    assert "ALTER TABLE documents" not in text
    assert "CREATE TABLE document_payloads" not in text  # only the IF NOT EXISTS form
