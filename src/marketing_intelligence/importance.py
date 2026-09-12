"""Importance lane (Ticket 19) — agent-writable importance annotations.

Domain contract (stable): ``set_importance(identifier, score, rationale,
reporter)`` records a 0–1 importance annotation for one Document, keeps the
full append-only history (every write inserts a new row in
``document_importance``), and returns the updated article dict (as produced by
``marketing_intelligence.article.get_article``, with the importance annotation
attached). The newest row (``ORDER BY created_at DESC, id DESC``) is the
effective annotation — latest write wins.

Server-enforced trust rule: a Document whose extraction is flagged
(``documents.flag_reason IS NOT NULL``) or whose stored body carries a paywall
marker is hard-capped at :data:`IMPORTANCE_CAP` (0.3) before the row is
written, whatever score the caller submitted. Flagged/paywalled evidence can
therefore never outrank clean evidence.

Error contract: this lane raises ``ValueError``/``TypeError``/``LookupError``
only (unknown URL -> ``ValueError``); the service adapter maps those to
``InvalidRequest`` (HTTP 422).
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

try:  # foundation seam (preferred)
    from marketing_intelligence.db import get_connection  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - defensive fallback when absent

    def get_connection() -> Any:  # type: ignore[misc]
        raise RuntimeError(
            "No database connection available: marketing_intelligence.db.get_connection "
            "is missing and no fallback is configured."
        )


from marketing_intelligence import article as _article
from marketing_intelligence.normalize import canonicalize_url

IMPORTANCE_MIN = 0.0
IMPORTANCE_MAX = 1.0
#: Server-side ceiling for extraction-flagged or paywalled Documents.
IMPORTANCE_CAP = 0.3

RATIONALE_MAX_LENGTH = 2000
REPORTER_MAX_LENGTH = 100

#: Content markers that identify a stored body as a paywall/metering stub.
#: Deliberately specific phrases (matched case-insensitively): a full article
#: that merely discusses paywalls must not trip the cap. The first marker
#: matches the Jing Daily metered fixture kept by the extraction lane.
PAYWALL_MARKERS = (
    "subscribe to continue reading",
    "subscribe to read",
    "to continue reading this story",
    "this article is for subscribers",
    "subscriber-only content",
    "members-only content",
)

_DOCUMENT_SQL = """\
SELECT d.id, d.flag_reason, d.content
  FROM documents d
 WHERE d.url = %s OR d.canonical_url = %s
 LIMIT 1\
"""

_INSERT_SQL = """\
INSERT INTO document_importance (document_id, score, rationale, reporter, created_at)
VALUES (%s, %s, %s, %s, now())\
"""

_LATEST_SQL = """\
SELECT i.score, i.rationale, i.reporter, i.created_at
  FROM document_importance i
  JOIN documents d ON d.id = i.document_id
 WHERE d.url = %s OR d.canonical_url = %s
 ORDER BY i.created_at DESC, i.id DESC
 LIMIT 1\
"""

_HISTORY_SQL = """\
SELECT i.score, i.rationale, i.reporter, i.created_at
  FROM document_importance i
  JOIN documents d ON d.id = i.document_id
 WHERE d.url = %s OR d.canonical_url = %s
 ORDER BY i.created_at DESC, i.id DESC\
"""


def _to_iso_tz_aware(value: Any) -> Any:
    """Normalise a created_at value to an isoformat tz-aware string."""
    if value is None:
        return None
    if isinstance(value, _dt.datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=_dt.UTC)
        return value.isoformat()
    if isinstance(value, str):
        try:
            parsed = _dt.datetime.fromisoformat(value)
        except ValueError:
            return value
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=_dt.UTC)
            return parsed.isoformat()
        return value
    return value


def _validate_score(score: Any) -> float:
    """Validate a 0–1 score; failures raise TypeError (type) / ValueError (range)."""
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        raise TypeError(f"score must be a number in [0, 1], got {type(score).__name__}")
    value = float(score)
    if not (IMPORTANCE_MIN <= value <= IMPORTANCE_MAX):
        raise ValueError(f"score must be in [0, 1], got {score!r}")
    return value


def _validate_rationale(rationale: Any) -> Any:
    """Optional rationale text, max 2000 chars; failures raise TypeError/ValueError."""
    if rationale is None:
        return None
    if not isinstance(rationale, str):
        raise TypeError(f"rationale must be a string or null, got {type(rationale).__name__}")
    cleaned = rationale.strip() or None
    if cleaned is not None and len(cleaned) > RATIONALE_MAX_LENGTH:
        raise ValueError(
            f"rationale must be at most {RATIONALE_MAX_LENGTH} chars, got {len(cleaned)}"
        )
    return cleaned


def _validate_reporter(reporter: Any) -> Any:
    """Optional reporter tag, max 100 chars; failures raise TypeError/ValueError."""
    if reporter is None:
        return None
    if not isinstance(reporter, str):
        raise TypeError(f"reporter must be a string or null, got {type(reporter).__name__}")
    cleaned = reporter.strip() or None
    if cleaned is not None and len(cleaned) > REPORTER_MAX_LENGTH:
        raise ValueError(
            f"reporter must be at most {REPORTER_MAX_LENGTH} chars, got {len(cleaned)}"
        )
    return cleaned


def is_paywalled(content: Any) -> bool:
    """True when a stored body carries a known paywall/metering marker."""
    if not isinstance(content, str) or not content:
        return False
    lowered = content.lower()
    return any(marker in lowered for marker in PAYWALL_MARKERS)


def _resolve(conn: Any, key: str, canonical: str) -> tuple[Any, Any, Any]:
    """Return ``(document_id, flag_reason, content)`` or raise ``ValueError``."""
    cursor = conn.cursor()
    try:
        cursor.execute(_DOCUMENT_SQL, (key, canonical))
        rows = cursor.fetchall()
    finally:
        close = getattr(cursor, "close", None)
        if callable(close):
            close()
    if not rows:
        raise ValueError(f"unknown article: {key!r}")
    row = rows[0]
    if isinstance(row, dict):
        return row.get("id"), row.get("flag_reason"), row.get("content")
    return row[0], row[1], row[2]


def _effective_score(score: float, flag_reason: Any, content: Any) -> float:
    """Apply the server-side cap: flagged/paywalled Documents never exceed 0.3."""
    if flag_reason is not None or is_paywalled(content):
        return min(score, IMPORTANCE_CAP)
    return score


def _fetch_rows(conn: Any, sql: str, params: tuple) -> list[Any]:
    cursor = conn.cursor()
    try:
        cursor.execute(sql, params)
        return list(cursor.fetchall() or [])
    finally:
        close = getattr(cursor, "close", None)
        if callable(close):
            close()


def _annotation_from_row(row: Any) -> dict[str, Any]:
    if isinstance(row, dict):
        score = row.get("score")
        rationale = row.get("rationale")
        reporter = row.get("reporter")
        created_at = row.get("created_at")
    else:
        items = tuple(row)
        score = items[0]
        rationale = items[1]
        reporter = items[2]
        created_at = items[3] if len(items) > 3 else None
    return {
        "importance_score": float(score) if score is not None else None,
        "importance_rationale": rationale,
        "importance_reporter": reporter,
        "importance_updated_at": _to_iso_tz_aware(created_at),
    }


def _canonical(key: str) -> str:
    try:
        return canonicalize_url(key)
    except Exception:
        return key


def set_importance(
    identifier: str,
    score: float,
    rationale: str | None = None,
    reporter: str | None = None,
    conn: Any | None = None,
) -> dict[str, Any]:
    """Record one importance annotation (latest wins, history retained).

    Args:
        identifier: exact article URL, or anything that canonicalizes to the
            stored canonical URL. Blank/non-string identifiers raise
            ``ValueError``.
        score: importance in ``[0, 1]``; non-numbers raise ``TypeError`` and
            out-of-range values raise ``ValueError``.
        rationale: optional free-text rationale, max 2000 chars.
        reporter: optional reporter tag, max 100 chars.
        conn: optional injected DB-API connection (fake-friendly). When None
            a connection is opened via `get_connection` and closed afterwards;
            an injected connection is never committed or closed here.

    Returns:
        The updated article dict (same keys as
        ``marketing_intelligence.article.get_article``) with the effective
        importance annotation attached. Flagged or paywalled Documents are
        capped at :data:`IMPORTANCE_CAP` (0.3) server-side.

    Raises:
        ValueError: blank identifier, unknown article, bad score/limits.
        TypeError: non-string rationale/reporter, non-numeric score.
    """
    if not isinstance(identifier, str) or not identifier.strip():
        raise ValueError(f"identifier must be a non-empty string, got {identifier!r}")
    key = identifier.strip()
    canonical = _canonical(key)
    clean_score = _validate_score(score)
    clean_rationale = _validate_rationale(rationale)
    clean_reporter = _validate_reporter(reporter)

    owns_connection = conn is None
    if conn is None:
        conn = get_connection()
    assert conn is not None
    try:
        document_id, flag_reason, content = _resolve(conn, key, canonical)
        effective = _effective_score(clean_score, flag_reason, content)
        cursor = conn.cursor()
        try:
            cursor.execute(_INSERT_SQL, (document_id, effective, clean_rationale, clean_reporter))
        finally:
            close = getattr(cursor, "close", None)
            if callable(close):
                close()
        return _article.get_article(key, conn=conn)
    finally:
        if owns_connection:
            commit = getattr(conn, "commit", None)
            if callable(commit):
                commit()
            close_conn = getattr(conn, "close", None)
            if callable(close_conn):
                close_conn()


def get_importance(identifier: str, conn: Any | None = None) -> dict[str, Any]:
    """Return the latest importance annotation for one Document.

    The four ``importance_*`` keys mirror the read-path annotation; all are
    ``None`` when the Document has never been annotated.

    Raises:
        ValueError: blank identifier or unknown article.
    """
    if not isinstance(identifier, str) or not identifier.strip():
        raise ValueError(f"identifier must be a non-empty string, got {identifier!r}")
    key = identifier.strip()
    canonical = _canonical(key)

    owns_connection = conn is None
    if conn is None:
        conn = get_connection()
    assert conn is not None
    try:
        _resolve(conn, key, canonical)  # unknown article -> ValueError
        rows = _fetch_rows(conn, _LATEST_SQL, (key, canonical))
    finally:
        if owns_connection:
            close_conn = getattr(conn, "close", None)
            if callable(close_conn):
                close_conn()
    if not rows:
        return {
            "importance_score": None,
            "importance_rationale": None,
            "importance_reporter": None,
            "importance_updated_at": None,
        }
    return _annotation_from_row(rows[0])


def get_importance_history(identifier: str, conn: Any | None = None) -> list[dict[str, Any]]:
    """Return every importance annotation for one Document, newest first.

    Raises:
        ValueError: blank identifier or unknown article.
    """
    if not isinstance(identifier, str) or not identifier.strip():
        raise ValueError(f"identifier must be a non-empty string, got {identifier!r}")
    key = identifier.strip()
    canonical = _canonical(key)

    owns_connection = conn is None
    if conn is None:
        conn = get_connection()
    assert conn is not None
    try:
        _resolve(conn, key, canonical)
        rows = _fetch_rows(conn, _HISTORY_SQL, (key, canonical))
    finally:
        if owns_connection:
            close_conn = getattr(conn, "close", None)
            if callable(close_conn):
                close_conn()
    return [_annotation_from_row(row) for row in rows]
