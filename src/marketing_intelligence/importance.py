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
written, whatever score the caller submitted — and the same cap is re-applied
whenever the latest annotation is surfaced (see :func:`effective_score`). A
score written while the Document was still clean therefore reads back capped
once a later flag (or paywall body) arrives, so flagged/paywalled evidence can
never outrank clean evidence on any read path.

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


def _sql_text_literal(value: str) -> str:
    """Quote a Python string as a PostgreSQL text literal (quotes doubled)."""
    return "'" + value.replace("'", "''") + "'"


#: SQL predicate matching every Document the read-side cap applies to —
#: extraction-flagged, or with a paywall marker anywhere in the stored body.
#: Mirrors :func:`is_paywalled` (case-insensitive substring search) so the
#: ranking lanes and the Python clamp agree on which rows are capped.
_DOCUMENT_CAPPED_SQL = "d.flag_reason IS NOT NULL OR " + " OR ".join(
    f"position(lower({_sql_text_literal(marker)}) in lower(d.content)) > 0"
    for marker in PAYWALL_MARKERS
)

#: SQL form of :func:`effective_score` for a correlated "latest importance"
#: lateral (``document_importance`` aliased ``i``, ``documents`` aliased ``d``).
#: The ranking lanes (search, period) select it as their score so
#: ``ORDER BY imp.score`` and the ``min_importance`` floor both see the
#: effective value: a score written before a later flag/paywall cannot outrank
#: clean evidence. Defined once here and interpolated into those lanes.
EFFECTIVE_SCORE_SQL = (
    f"CASE WHEN {_DOCUMENT_CAPPED_SQL} THEN LEAST(i.score, {IMPORTANCE_CAP}) ELSE i.score END"
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


def effective_score(score: float | None, flag_reason: Any, content: Any) -> float | None:
    """Cap a stored score at :data:`IMPORTANCE_CAP` for flagged/paywalled Documents.

    Applied both when a score is written and whenever it is surfaced, because
    the latest stored annotation may predate the flag or the paywall stub.
    ``None`` (unannotated) passes through unchanged. Mirrored in SQL by
    :data:`EFFECTIVE_SCORE_SQL` so ranking and reporting agree.
    """
    if score is None:
        return None
    value = float(score)
    if flag_reason is not None or is_paywalled(content):
        return min(value, IMPORTANCE_CAP)
    return value


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

    # Local import: `article` imports this module for `effective_score`, so a
    # module-level edge here would form an import cycle. Resolved at call time
    # instead; set_importance is the only consumer of the article lane.
    from marketing_intelligence import article as _article

    owns_connection = conn is None
    if conn is None:
        conn = get_connection()
    assert conn is not None
    try:
        document_id, flag_reason, content = _resolve(conn, key, canonical)
        effective = effective_score(clean_score, flag_reason, content)
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
        _document_id, flag_reason, content = _resolve(
            conn, key, canonical
        )  # unknown article -> ValueError
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
    annotation = _annotation_from_row(rows[0])
    annotation["importance_score"] = effective_score(
        annotation["importance_score"], flag_reason, content
    )
    return annotation


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
