"""Ingestion-run history + per-source health (Ticket 04).

Read/write seam over the `ingestion_runs` table (migrations/003):

- ``record_ingestion_run`` persists one flow result (best-effort from flows;
  strict here so tests can assert failures loudly).
- ``get_source_health`` reports the latest run per source independently of
  article tables — operators see ingestion status with zero documents stored.

Both accept an injected DB-API connection (fakes welcome); when ``conn`` is
None a connection is opened via ``marketing_intelligence.db.get_connection``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from marketing_intelligence.db import get_connection
from marketing_intelligence.sources import V1_SOURCES

INSERT_RUN_SQL = """\
INSERT INTO ingestion_runs
  (source_name, started_at, finished_at,
   inserted, skipped, parse_skipped, error, skipped_reasons)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s)\
"""

LATEST_RUNS_SQL = """\
SELECT DISTINCT ON (source_name)
  source_name, started_at, finished_at,
  inserted, skipped, parse_skipped, error
  FROM ingestion_runs
 WHERE source_name = ANY(%s)
 ORDER BY source_name, finished_at DESC\
"""


def record_ingestion_run(
    source_name: str,
    result: dict[str, Any],
    *,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
    skipped_reasons: list[str] | None = None,
    conn: Any | None = None,
) -> None:
    """Persist one ingestion result as a run row. Closes only owned conns."""
    now = datetime.now(UTC)
    owns_connection = False
    if conn is None:
        conn = get_connection()
        owns_connection = True
    assert conn is not None
    try:
        conn.execute(
            INSERT_RUN_SQL,
            (
                source_name,
                started_at or now,
                finished_at or now,
                int(result.get("inserted", 0)),
                int(result.get("skipped", 0)),
                int(result.get("parse_skipped", 0)),
                result.get("error"),
                list(skipped_reasons) if skipped_reasons else None,
            ),
        )
        conn.commit()
    finally:
        if owns_connection:
            try:
                conn.close()
            except Exception:
                pass


def get_source_health(
    sources: list[str] | None = None,
    conn: Any | None = None,
) -> list[dict[str, Any]]:
    """Latest run per source; sources with no run report status 'unknown'.

    Status is 'ok' when the latest run has no error, 'error' otherwise.
    Read-only: never commits, closes only owned connections.
    """
    names = list(sources) if sources is not None else list(V1_SOURCES)
    owns_connection = False
    if conn is None:
        conn = get_connection()
        owns_connection = True
    assert conn is not None
    try:
        cursor = conn.execute(LATEST_RUNS_SQL, (names,))
        rows = cursor.fetchall()
    finally:
        if owns_connection:
            try:
                conn.close()
            except Exception:
                pass
    latest: dict[str, Any] = {}
    for row in rows or []:
        name, started, finished, inserted, skipped, parse_skipped, error = row
        latest[str(name)] = {
            "source": str(name),
            "status": "error" if error else "ok",
            "started_at": started,
            "finished_at": finished,
            "inserted": inserted,
            "skipped": skipped,
            "parse_skipped": parse_skipped,
            "error": error,
        }
    report: list[dict[str, Any]] = []
    for name in names:
        if name in latest:
            report.append(latest[name])
        else:
            report.append(
                {
                    "source": name,
                    "status": "unknown",
                    "started_at": None,
                    "finished_at": None,
                    "inserted": None,
                    "skipped": None,
                    "parse_skipped": None,
                    "error": None,
                }
            )
    return report
