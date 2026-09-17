"""Postgres connectivity + migration runner (Ticket 01 foundation seam, ADR-0006).

Other lanes depend on these callables:
- get_connection() -> psycopg connection to DATABASE_URL
- apply_migrations(conn=None) -> yoyo: apply PENDING migrations only

Yoyo tracks applied migrations in a version table, so reruns apply pending
migrations only (the naive pre-yoyo runner re-executed every file, which
breaks on future non-idempotent statements). Plain `.sql` files need no
markers — yoyo parses them into transactional steps as-is.

- `conn` is retained for backwards compatibility but unused: yoyo manages its
  own versioned connection from DATABASE_URL (psycopg3 via the
  `postgresql+psycopg://` scheme; plain `postgresql://` selects yoyo's
  psycopg2 backend, which is not installed).
- Rollback caveat: plain `.sql` migrations carry no rollback SQL (that needs
  a `<name>.rollback.sql` sibling, which we deliberately do not ship), so
  `rollback_migrations()` unmarks them but leaves DDL in place; the next
  apply re-runs them (safe: all current SQL is idempotent).
- Baseline caveat: DBs created by the naive pre-yoyo runner have the schema
  but no version table. Run one-time `baseline_migrations()` (or
  `make migrate-baseline`) to mark everything applied WITHOUT executing;
  otherwise the first yoyo apply re-runs all files (harmless today, but noisy).
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import psycopg
from psycopg import Connection
from yoyo import get_backend, read_migrations

from marketing_intelligence.config import get_database_url

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"

#: Namespace + key scheme for deterministic source ids (migration 007).
#: stdlib `uuid5` only — no new dependency. Mirrors the UUID literals embedded
#: in migrations/007_deterministic_sources_and_not_null.sql.
SOURCE_SEED_NAMESPACE = uuid.NAMESPACE_DNS
# Legacy prefix kept for stable source UUIDs after rename
SOURCE_SEED_KEY_PREFIX = "trend-intelligence-brain:source:"


def source_seed_key(name: str) -> str:
    """Stable id-input for a source name (namespace-qualified against collisions)."""
    return f"{SOURCE_SEED_KEY_PREFIX}{name}"


def source_uuid(name: str) -> uuid.UUID:
    """Deterministic id for `name` (stdlib uuid5; same scheme as migrations 007/012/015)."""
    return uuid.uuid5(SOURCE_SEED_NAMESPACE, source_seed_key(name))


#: Full curated set: the 20 feed sources seeded by migration 007 plus the
#: ADR-0013 Instagram accounts re-asserted by migrations 012/015. Kept in
#: sync with curated-sources.json (ISO codes en/pt/es).
SEED_SOURCES: tuple[tuple[str, str | None, str, str], ...] = (
    ("Consumidor Moderno", None, "https://consumidormoderno.com.br/", "pt"),
    ("Exame", None, "https://exame.com/", "pt"),
    ("Forbes México", None, "https://forbes.com.mx/", "es"),
    # ADR-0013 premium lane: the Instagram account rows, seeded like any other
    # curated source but fetched through the Apify actor (no rss_url).
    ("ig:sabrikolod", None, "https://www.instagram.com/sabrikolod/", "es"),
    ("ig:jordisanildefonso", None, "https://www.instagram.com/jordisanildefonso/", "es"),
    ("InfoMoney", "https://www.infomoney.com.br/feed", "https://www.infomoney.com.br/", "pt"),
    ("Insider Latam", None, "https://insiderlatam.com/", "es"),
    (
        "JCK Online",
        "https://www.jckonline.com/feed/",
        "https://www.jckonline.com/category/news-trends/retail/",
        "en",
    ),
    ("Jing Daily", None, "https://jingdaily.com/", "en"),
    (
        "LVMH Press Releases",
        None,
        "https://www.lvmh.com/news-documents/press-releases/",
        "en",
    ),
    ("MarTech", "https://martech.org/feed/", "https://martech.org/", "en"),
    ("Marketing Dive", None, "https://www.marketingdive.com/", "en"),
    ("MarketingDirecto", None, "https://www.marketingdirecto.com/", "es"),
    ("Meio & Mensagem", None, "https://www.meioemensagem.com.br/", "pt"),
    ("Modaes", None, "https://www.modaes.com/", "es"),
    ("National Jeweler", None, "https://nationaljeweler.com/industry", "en"),
    (
        "Professional Jeweller",
        "https://www.professionaljeweller.com/feed/",
        "https://www.professionaljeweller.com/",
        "en",
    ),
    ("Propmark", None, "https://propmark.com.br/", "pt"),
    ("Retail Dive", None, "https://www.retaildive.com/topic/consumer-trends/", "en"),
    (
        "Richemont Media",
        None,
        "https://www.richemont.com/news-media/press-releases-news/",
        "en",
    ),
    (
        "Social Media Today",
        "https://www.socialmediatoday.com/feeds/news/",
        "https://www.socialmediatoday.com/",
        "en",
    ),
    (
        "Swarovski PR Newswire",
        "https://www.prnewswire.com/rss/swarovski",
        "https://www.prnewswire.com/news/swarovski/",
        "en",
    ),
)

SEED_SOURCES_SQL = """\
INSERT INTO sources (id, name, rss_url, hub_url, language, enabled)
VALUES (%s, %s, %s, %s, %s, true)
ON CONFLICT (name) DO NOTHING\
"""

QUARANTINE_DDL_SQL = """\
CREATE TABLE IF NOT EXISTS quarantined_documents (
  id UUID PRIMARY KEY,
  source_id UUID NULL,
  url TEXT NOT NULL,
  canonical_url TEXT NOT NULL,
  title TEXT NOT NULL,
  author TEXT NULL,
  published_at TIMESTAMPTZ NOT NULL,
  retrieved_at TIMESTAMPTZ NOT NULL,
  language TEXT NOT NULL,
  content TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  quarantined_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  quarantine_reason TEXT NOT NULL DEFAULT 'source_id IS NULL at 007 quarantine'
)\
"""

QUARANTINE_MOVE_SQL = """\
INSERT INTO quarantined_documents
  (id, source_id, url, canonical_url, title, author,
   published_at, retrieved_at, language, content, content_hash)
SELECT id, source_id, url, canonical_url, title, author,
       published_at, retrieved_at, language, content, content_hash
  FROM documents WHERE source_id IS NULL
ON CONFLICT (id) DO NOTHING\
"""

QUARANTINE_DELETE_SQL = "DELETE FROM documents WHERE source_id IS NULL"


def seed_sources(conn: Any | None = None) -> tuple[int, int]:
    """Insert the curated source set idempotently. Returns (inserted, skipped).

    New rows take deterministic :func:`source_uuid` ids; existing names are kept
    via ON CONFLICT (name) DO NOTHING (their 001/006 random ids are preserved).
    Accepts any DB-API connection (fakes welcome in tests); opens one when None.
    """
    owns_connection = False
    if conn is None:
        conn = get_connection()
        owns_connection = True
    assert conn is not None
    try:
        inserted = 0
        skipped = 0
        for name, rss_url, hub_url, language in SEED_SOURCES:
            try:
                cursor = conn.execute(
                    SEED_SOURCES_SQL, (str(source_uuid(name)), name, rss_url, hub_url, language)
                )
                if cursor is not None and getattr(cursor, "rowcount", 1) == 0:
                    skipped += 1
                else:
                    inserted += 1
            except Exception:
                skipped += 1
        conn.commit()
        return (inserted, skipped)
    finally:
        if owns_connection:
            try:
                conn.close()
            except Exception:
                pass


def quarantine_null_documents(conn: Any | None = None) -> int:
    """Move NULL-source documents to quarantined_documents. Returns rows moved.

    Backfill-via-lookup is impossible (documents has no source_name provenance),
    so unattributed rows are preserved explicitly rather than deleted or guessed.
    Idempotent: reruns move nothing new. Opens a connection when None.
    """
    owns_connection = False
    if conn is None:
        conn = get_connection()
        owns_connection = True
    assert conn is not None
    try:
        conn.execute(QUARANTINE_DDL_SQL)
        cursor = conn.execute(QUARANTINE_MOVE_SQL)
        moved = cursor.rowcount if cursor is not None else 0
        conn.execute(QUARANTINE_DELETE_SQL)
        conn.commit()
        return int(moved)
    finally:
        if owns_connection:
            try:
                conn.close()
            except Exception:
                pass


def get_connection() -> Connection:
    """Open a new psycopg connection to the configured database."""
    return psycopg.connect(get_database_url())


def _migration_files() -> list[Path]:
    """All migration files in apply order (lexicographic: 001, 002, ...)."""
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


def _yoyo_dsn() -> str:
    """DATABASE_URL adapted for yoyo (psycopg3 backend needs the +psycopg scheme)."""
    dsn = get_database_url()
    if dsn.startswith("postgresql://"):
        return "postgresql+psycopg://" + dsn[len("postgresql://") :]
    if dsn.startswith("postgres://"):
        return "postgresql+psycopg://" + dsn[len("postgres://") :]
    return dsn


def _get_backend() -> Any:
    """Yoyo backend for the configured database (seam: monkeypatchable in tests)."""
    return get_backend(_yoyo_dsn())


def _read_migrations() -> Any:
    """Yoyo migration collection for MIGRATIONS_DIR (seam: monkeypatchable)."""
    files = _migration_files()
    assert files, f"no migration files found in {MIGRATIONS_DIR}"
    return read_migrations(str(MIGRATIONS_DIR))


def pending_migrations() -> list[str]:
    """Ids of migrations not yet applied, in apply order."""
    backend = _get_backend()
    return [str(m.id) for m in backend.to_apply(_read_migrations())]


def apply_migrations(conn: Connection | None = None) -> list[str]:
    """Apply pending migrations only (no-op when none). Return applied ids.

    `conn` is accepted for backwards compatibility and ignored — yoyo tracks
    and applies via its own versioned connection.
    """
    backend = _get_backend()
    pending = backend.to_apply(_read_migrations())
    backend.apply_migrations(pending)
    return [str(m.id) for m in pending]


def baseline_migrations() -> list[str]:
    """One-time baseline for pre-yoyo DBs: mark pending applied WITHOUT executing.

    Return marked ids. After this, apply_migrations() is a no-op until a new
    migration file appears.
    """
    backend = _get_backend()
    pending = backend.to_apply(_read_migrations())
    backend.mark_migrations(pending)
    return [str(m.id) for m in pending]


def rollback_migrations(steps: int = 1) -> list[str]:
    """Roll back the last `steps` applied migrations (newest first). Return ids.

    Plain `.sql` migrations have no rollback SQL, so this unmarks them while
    leaving DDL in place (see module caveat); re-apply restores the marks.
    """
    if steps < 1:
        raise ValueError(f"steps must be >= 1, got {steps}")
    backend = _get_backend()
    todo = list(backend.to_rollback(_read_migrations()))[:steps]
    backend.rollback_migrations(todo)
    return [str(m.id) for m in todo]
