"""Jev annotate lane (ADR-0016) — TypeSafe classification over every Document.

One TypeSafe Jev ``system_one`` call per Document judges four things at once:
``pillar`` (Choice over the 18 live pillar slugs plus ``none_of_above``),
``region`` (Choice over the live region slugs plus ``unclear``), ``content_type``
(Choice over the live content-type slugs) and ``digest_relevance`` (Score over a
0–3 rubric). The question set is derived from :data:`topics.TOPICS` at call time,
so vocabulary growth (e.g. the ``colombia``/``argentina`` regions) reaches the
vendor prompt without editing this file.

Read-off rules (finding 1 and 2 of the ADR probe):

- Choice tags are **every** option at or above :data:`TAG_THRESHOLD`, not the
  argmax alone (a single Choice under-tags multi-theme documents).
- Pillar/region tags are written only when that question's confidence reaches
  :data:`WRITE_CONFIDENCE`; the ``content_type`` top pick is written regardless
  (it only feeds an OR filter, so a wrong content type never hides a Document
  its pillar already surfaces).
- Importance is ``digest_relevance / 3`` and is written only when its confidence
  reaches :data:`WRITE_CONFIDENCE`, with the score and confidence in the
  rationale — the write is auditable from its own row.
- A call that produced nothing usable raises no exception and stores no guess:
  it returns a ``low-confidence`` (or vendor-error) cause and is retried by the
  next run, which is exactly what the pending selector means.

Writes go through the existing annotation lanes only — :func:`importance.set_importance`
and :func:`topics.set_document_topics` — never through SQL of this module's own
(no new table, no migration, latest-wins history retained, agent overwrite wins
if it comes later). An injected connection is passed straight through to both
lanes and never committed or closed here; each Document's writes sit inside a
savepoint, so one failing write rolls back that Document alone and the run's
counts stay true to what the caller will commit.

``TYPESAFE_API_KEY`` is read from the environment at call time, never at import,
never logged, and never echoed into a return value or exception message: a
missing key is an explicit cause, not a crash while importing this module. The
``typesafe_sdk`` import is lazy for the same reason — this module imports (and
its DB-free helpers run) without the SDK installed, and only the vendor call
needs it.
"""

from __future__ import annotations

import os
from typing import Any

try:  # foundation seam (preferred)
    from marketing_intelligence.db import get_connection  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - defensive fallback when absent

    def get_connection() -> Any:  # type: ignore[misc]
        raise RuntimeError(
            "No database connection available: marketing_intelligence.db.get_connection "
            "is missing and no fallback is configured."
        )


from marketing_intelligence import importance as _importance
from marketing_intelligence import topics as _topics

#: Pinned TypeSafe Jev model id — never the moving ``jev-latest`` alias, so a
#: vendor rollout cannot silently change every judgment (ADR-0016).
MODEL_ID = "jev-1.13.0"

#: Reporter tag carried by every write of this lane, so Jev annotations stay
#: separable from agent-authored ones and the pending selector can find its work.
REPORTER = "jev"

#: A Choice option becomes a tag at or above this probability (not just argmax):
#: single-Choice read-off hides the second theme of multi-theme Documents.
TAG_THRESHOLD = 0.2

#: Question confidence floor for writing that question's judgment. Below it the
#: judgment is dropped, never guessed; the Document stays pending for a retry.
WRITE_CONFIDENCE = 0.5

#: How much of a Document's body is sent as state (title and source are sent whole).
MAX_TEXT_CHARS = 3000

#: Measured Jev price per million input tokens (2026-09-21 probe: 13,763 input
#: tokens for 8 stratified Documents ≈ $0.00058; outputs are free). Reported in
#: the run result and logged by the flow — never stored back into a table.
COST_PER_MILLION_INPUT_TOKENS_USD = 0.042

#: Environment variable read at call time — never at import, never logged, never
#: carried into a payload, return value, or exception (same rule as
#: ``FIRECRAWL_API_KEY`` / ``DEEPINFRA_API_KEY`` / ``APIFY_API_TOKEN``).
API_KEY_ENV = "TYPESAFE_API_KEY"

#: Stand-in for the API key if a vendor error ever echoes it back.
_REDACTED = "<redacted>"

#: Question names as sent to the vendor (also the keys of the answer map).
PILLAR_QUESTION = "pillar"
REGION_QUESTION = "region"
CONTENT_TYPE_QUESTION = "content_type"
DIGEST_QUESTION = "digest_relevance"

#: Choice options that name no Topic: selected freely, never written as tags.
PILLAR_NONE = "none_of_above"
REGION_UNCLEAR = "unclear"

#: Ordered 0–3 rubric for the ``digest_relevance`` Score (low end first).
DIGEST_RUBRIC: tuple[str, ...] = (
    "Irrelevant — no marketing-intelligence value for this audience",
    "Background — useful context, but not digest material on its own",
    "Relevant — a plausible candidate for the next digest",
    "Must-read — high-value evidence the next digest should prioritise",
)

#: Top of the rubric (the divisor that normalises a digest score onto 0–1).
DIGEST_MAX_SCORE = len(DIGEST_RUBRIC) - 1

#: Documents with no ``reporter='jev'`` importance row yet (idempotency
#: selector: one call per Document per lane run, topics rewrites are diff
#: no-ops). ``%s::text`` keeps the NULL source filter bindable.
_PENDING_SQL = """\
SELECT d.canonical_url
  FROM documents d
  JOIN sources s ON s.id = d.source_id
 WHERE NOT EXISTS (
       SELECT 1 FROM document_importance i
        WHERE i.document_id = d.id AND i.reporter = %s
 )
   AND (%s::text IS NULL OR s.name = %s)
 ORDER BY d.published_at, d.id\
"""

#: State rows for the requested identifiers, keyed by url and canonical url so
#: callers may pass either spelling.
_DOCUMENT_SQL = """\
SELECT d.canonical_url, d.url, d.title, s.name, d.content
  FROM documents d
  LEFT JOIN sources s ON s.id = d.source_id
 WHERE d.canonical_url = ANY(%s) OR d.url = ANY(%s)\
"""

#: Transaction control around one Document's writes. A savepoint is what keeps
#: the run's counts honest: a failed write rolls back that Document alone, so
#: the earlier Documents of the same call survive the caller's single commit
#: (nothing here is ever committed or rolled back wholesale on an injected
#: connection — the caller owns that decision).
_SAVEPOINT = "SAVEPOINT jev_annotate_document"
_RELEASE_SAVEPOINT = "RELEASE SAVEPOINT jev_annotate_document"
_ROLLBACK_TO_SAVEPOINT = "ROLLBACK TO SAVEPOINT jev_annotate_document"


def _load_sdk() -> Any:
    """Import ``typesafe_sdk`` lazily (this module imports fine without it)."""
    try:
        import typesafe_sdk
    except ImportError as exc:  # pragma: no cover - exercised via a poisoned sys.modules
        raise RuntimeError(
            "typesafe-sdk is not installed; run `uv sync` (or "
            "`pip install 'typesafe-sdk>=0.7'`) to enable the Jev annotate lane"
        ) from exc
    return typesafe_sdk


def _api_key() -> str | None:
    """The TypeSafe API key in effect, read fresh from the environment.

    Returns ``None`` when unset or blank. The value is never logged and never
    returned by a public function — only handed to the SDK client constructor.
    """
    key = (os.environ.get(API_KEY_ENV) or "").strip()
    return key or None


def _scrub(message: str, secret: str | None) -> str:
    """Drop the API key from a vendor message (a secret never lands in a cause)."""
    if not secret:
        return message
    return message.replace(secret, _REDACTED)


def _describe(entry: dict[str, Any]) -> str:
    """Vendor-facing description of one vocabulary entry: label + synonyms."""
    label = str(entry.get("label") or entry.get("slug") or "")
    synonyms = [str(s) for s in entry.get("synonyms") or () if s]
    if not synonyms:
        return label
    return f"{label} (also: {', '.join(synonyms)})"


def _live_entries(kind: str) -> list[dict[str, Any]]:
    """Vocabulary entries of one axis in registry order, retired aliases dropped.

    Retired aliases canonicalize to their target, so they must not be offered
    as separate Choice options (they would collect probability the live slug
    should own).
    """
    return [
        entry
        for entry in _topics.list_vocabulary()
        if entry["kind"] == kind and entry["retired_alias_of"] is None
    ]


def _live_slugs(kind: str) -> list[str]:
    """Canonical slugs of one axis, in registry order (excludes retired aliases)."""
    return [str(entry["slug"]) for entry in _live_entries(kind)]


def build_questions() -> dict[str, Any]:
    """The four Jev questions for one Document, as SDK question objects.

    Pillar/region/content-type criteria are built from the live Topic
    vocabulary, so a vocabulary addition (never a deletion) reaches the vendor
    prompt without touching this lane.

    Raises:
        RuntimeError: ``typesafe-sdk`` is not installed.
    """
    sdk = _load_sdk()
    pillar_criteria = {str(e["slug"]): _describe(e) for e in _live_entries("pillar")}
    pillar_criteria[PILLAR_NONE] = "No pillar above is a substantive theme of this document"
    region_criteria = {str(e["slug"]): _describe(e) for e in _live_entries("region")}
    region_criteria[REGION_UNCLEAR] = "No identifiable geography, or none of the regions above"
    return {
        PILLAR_QUESTION: sdk.Choice(
            instructions=(
                "Which marketing-intelligence pillars are substantive themes of this "
                "document? Judge every pillar on its own — a document may belong to "
                f"several. Pick {PILLAR_NONE} only when no pillar applies."
            ),
            criteria=pillar_criteria,
        ),
        REGION_QUESTION: sdk.Choice(
            instructions=(
                "Which geographies is this document's subject matter about? Judge every "
                f"region on its own; pick {REGION_UNCLEAR} when it is not identifiable."
            ),
            criteria=region_criteria,
        ),
        CONTENT_TYPE_QUESTION: sdk.Choice(
            instructions=(
                "What kind of content is this document? Pick the single best-fitting content type."
            ),
            criteria={str(e["slug"]): _describe(e) for e in _live_entries("content-type")},
        ),
        DIGEST_QUESTION: sdk.Score(
            instructions=(
                "Rate how relevant this document is to a marketing-intelligence digest "
                "covering consumer brands, jewelry, marketing and LatAm markets."
            ),
            criteria=list(DIGEST_RUBRIC),
        ),
    }


def _truncate(text: Any) -> str:
    """Document body capped at :data:`MAX_TEXT_CHARS` (missing body reads empty)."""
    return str(text or "")[:MAX_TEXT_CHARS]


def _empty(causes: list[str], confidences: dict[str, float], input_tokens: int) -> dict[str, Any]:
    """A no-write annotation result carrying its blocking cause(s)."""
    return {
        "tags": [],
        "importance": None,
        "rationale": None,
        "confidences": confidences,
        "causes": causes,
        "input_tokens": input_tokens,
    }


def _input_tokens(response: Any) -> int:
    """Input tokens the vendor reported (0 when it reported none)."""
    usage = getattr(response, "usage", None)
    value = getattr(usage, "input_tokens", None)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return int(value)


def _answer(response: Any, name: str) -> Any:
    """The answer object for one question name (``None`` when absent/unrecognized)."""
    answers = getattr(response, "answers", None)
    if isinstance(answers, dict):
        return answers.get(name)
    return None


def _confidence(answer: Any) -> float | None:
    """Calibrated confidence of a Choice/Score answer (``None`` when unreported)."""
    value = getattr(answer, "confidence", None)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _probabilities(answer: Any) -> dict[str, float]:
    """Per-option probabilities of a Choice answer, keyed by option name."""
    raw = getattr(answer, "probabilities", None)
    if not isinstance(raw, dict):
        return {}
    return {
        str(option): float(probability)
        for option, probability in raw.items()
        if isinstance(probability, (int, float)) and not isinstance(probability, bool)
    }


def _choice_tags(answer: Any, kind: str, sentinel: str | None) -> list[str]:
    """Gated multi-label read-off for one Choice answer.

    Every live slug of ``kind`` at or above :data:`TAG_THRESHOLD` becomes a tag,
    in registry order, provided the question's own confidence reaches
    :data:`WRITE_CONFIDENCE`. The sentinel option (``none_of_above`` /
    ``unclear``) is never a tag, and a low-confidence answer yields no tags at
    all rather than its argmax.
    """
    confidence = _confidence(answer)
    if confidence is None or confidence < WRITE_CONFIDENCE:
        return []
    probabilities = _probabilities(answer)
    return [
        slug
        for slug in _live_slugs(kind)
        if slug != sentinel and probabilities.get(slug, 0.0) >= TAG_THRESHOLD
    ]


def _digest_importance(answer: Any) -> tuple[float | None, str | None]:
    """``(importance, rationale)`` for one digest_relevance Score answer.

    Importance is the score normalised onto 0–1 and is written only when the
    score's confidence reaches :data:`WRITE_CONFIDENCE`; the rationale records
    both, so the stored row explains itself.
    """
    raw_score = getattr(answer, "score", None)
    confidence = _confidence(answer)
    if (
        isinstance(raw_score, bool)
        or not isinstance(raw_score, (int, float))
        or confidence is None
        or confidence < WRITE_CONFIDENCE
    ):
        return None, None
    score = min(max(float(raw_score), 0.0), float(DIGEST_MAX_SCORE))
    importance = score / DIGEST_MAX_SCORE
    rationale = f"jev digest_relevance={score:.2f}/{DIGEST_MAX_SCORE} conf={confidence:.2f}"
    return importance, rationale


def annotate_document(title: Any, source: Any, text: Any) -> dict[str, Any]:
    """Judge one Document with Jev; never touches the database.

    One ``system_one`` call carries all four questions against the state
    ``{title, source, text}`` (body truncated to :data:`MAX_TEXT_CHARS`). The
    return value is the decision, not the write:

    ``tags``
        Canonical Topic slugs to write (gated pillar/region tags plus the
        always-written content-type top pick), in registry order.
    ``importance`` / ``rationale``
        The 0–1 importance and its stored rationale, or ``None`` when the
        digest judgment was suppressed.
    ``confidences``
        Per-question confidences, for auditing suppressed judgments.
    ``causes``
        Blocking reasons this call produced nothing to write: a missing
        :data:`API_KEY_ENV`, a vendor/transport failure, an unrecognized
        content-type slug, or ``low-confidence`` when every judgment fell
        under the floors (the specific reason is reported instead of the
        generic one). Empty when at least one write is warranted.
    ``input_tokens``
        Tokens the vendor billed (0 when no call was made or none was reported).

    Raises:
        RuntimeError: ``typesafe-sdk`` is not installed. Every vendor-side
            failure is a cause instead, so one bad Document never breaks a run.
    """
    sdk = _load_sdk()
    confidences: dict[str, float] = {}
    api_key = _api_key()
    if api_key is None:
        return _empty([f"{API_KEY_ENV} is not set; refusing to bill the Jev lane"], confidences, 0)

    state = {
        "title": str(title or ""),
        "source": str(source or ""),
        "text": _truncate(text),
    }
    try:
        with sdk.TypeSafeClient(model=MODEL_ID, api_key=api_key) as client:
            response = client.system_one(state=state, questions=build_questions())
    except Exception as exc:  # vendor, transport, or malformed envelope
        message = _scrub(f"jev error: {type(exc).__name__}: {exc}", api_key)
        return _empty([message], confidences, 0)

    causes: list[str] = []
    for name in (PILLAR_QUESTION, REGION_QUESTION, CONTENT_TYPE_QUESTION, DIGEST_QUESTION):
        confidence = _confidence(_answer(response, name))
        if confidence is not None:
            confidences[name] = confidence

    tags = _choice_tags(_answer(response, PILLAR_QUESTION), "pillar", PILLAR_NONE)
    tags += _choice_tags(_answer(response, REGION_QUESTION), "region", REGION_UNCLEAR)

    content_type = getattr(_answer(response, CONTENT_TYPE_QUESTION), "choice", None)
    if isinstance(content_type, str):
        if content_type in _live_slugs("content-type"):
            tags.append(content_type)  # top pick is written regardless of confidence
        else:
            causes.append(f"{CONTENT_TYPE_QUESTION}: unrecognized slug {content_type!r}")

    importance, rationale = _digest_importance(_answer(response, DIGEST_QUESTION))
    if not tags and importance is None and not causes:
        causes.append("low-confidence")

    return {
        "tags": list(dict.fromkeys(tags)),
        "importance": importance,
        "rationale": rationale,
        "confidences": confidences,
        "causes": causes,
        "input_tokens": _input_tokens(response),
    }


def _run_control(conn: Any, sql: str) -> None:
    """Run one transaction-control statement through the DB-API seam."""
    cursor = conn.cursor()
    try:
        cursor.execute(sql)
    finally:
        close = getattr(cursor, "close", None)
        if callable(close):
            close()


def _fetch_all(conn: Any, sql: str, params: tuple) -> list[Any]:
    """Run a read-only query through the DB-API seam (rows, then close)."""
    cursor = conn.cursor()
    try:
        cursor.execute(sql, params)
        return list(cursor.fetchall() or [])
    finally:
        close = getattr(cursor, "close", None)
        if callable(close):
            close()


def _column(row: Any, *names: str) -> list[Any]:
    """One row as an ordered value list, accepting tuple- and dict-style rows."""
    if isinstance(row, dict):
        return [row.get(name) for name in names]
    return list(tuple(row)[: len(names)])


def pending_document_ids(conn: Any, source_name: str | None = None) -> list[str]:
    """Canonical urls of Documents Jev has not annotated yet, in publish order.

    "Annotated" means the Document has at least one ``reporter='jev'`` row in
    ``document_importance``: topics alone do not settle it, so a Document whose
    importance judgment was suppressed stays pending for the next run. Topics
    rewrites are diff no-ops, which is what makes that retry safe.

    Args:
        conn: DB-API connection (the caller owns transaction and lifetime).
        source_name: restrict to one Source by exact name; ``None`` means every
            Source.

    Returns:
        Canonical urls, oldest published first (``published_at``, then id).
    """
    rows = _fetch_all(conn, _PENDING_SQL, (REPORTER, source_name, source_name))
    identifiers: list[str] = []
    for row in rows:
        (value,) = _column(row, "canonical_url")
        if value is not None:
            identifiers.append(str(value))
    return identifiers


def _target_identifiers(conn: Any, ids: Any, source: Any) -> list[str]:
    """Resolve the caller's ``ids``/``source`` selection to document identifiers.

    ``ids`` are used as given (explicit work, even if already annotated),
    ``source`` adds that Source's pending Documents, and neither means every
    pending Document. Order is preserved and blank/duplicate entries dropped.
    """
    identifiers: list[str] = []
    if ids is not None:
        candidates = [ids] if isinstance(ids, str) else list(ids)
        for raw in candidates:
            if not isinstance(raw, str) or not raw.strip():
                raise ValueError(f"ids must be non-empty strings, got {raw!r}")
            identifiers.append(raw.strip())
    if source is not None:
        if not isinstance(source, str) or not source.strip():
            raise ValueError(f"source must be a non-empty string, got {source!r}")
        identifiers.extend(pending_document_ids(conn, source.strip()))
    if not identifiers and ids is None and source is None:
        identifiers = pending_document_ids(conn, None)
    return list(dict.fromkeys(identifiers))


def _document_rows(conn: Any, identifiers: list[str]) -> dict[str, tuple[Any, Any, Any]]:
    """``(title, source, content)`` per identifier, keyed by url and canonical url."""
    rows = _fetch_all(conn, _DOCUMENT_SQL, (list(identifiers), list(identifiers)))
    found: dict[str, tuple[Any, Any, Any]] = {}
    for row in rows:
        canonical, url, title, source_name, content = _column(
            row, "canonical_url", "url", "title", "name", "content"
        )
        record = (title, source_name, content)
        for key in (canonical, url):
            if key is not None:
                found[str(key)] = record
    return found


def classify_and_write(ids: Any = None, source: Any = None, conn: Any = None) -> dict[str, Any]:
    """Annotate Documents with Jev and write through the annotation lanes.

    Selection: ``ids`` are annotated as given, ``source`` adds that Source's
    pending Documents (see :func:`pending_document_ids`), and neither means
    every pending Document. Each Document costs one vendor call; unknown
    identifiers and zero-write judgments become causes and are counted as
    skipped, and no per-Document failure ever raises out of this function. Each
    Document's writes run inside their own savepoint, so a failed write rolls
    back that Document alone and leaves the earlier Documents of the call
    valid for the caller to commit.

    Args:
        ids: optional identifier or list of identifiers (url or canonical url).
        source: optional exact Source name.
        conn: optional injected DB-API connection, passed through to both
            annotation lanes and never committed or closed here. When ``None``
            a connection is opened, committed per Document, and closed.

    Returns:
        ``{"annotated", "skipped", "causes", "input_tokens", "cost_usd"}`` —
        Documents with at least one write, Documents with none (or unknown
        identifiers), the per-Document causes prefixed with their identifier,
        the vendor-reported input tokens, and the run's input cost in USD at
        :data:`COST_PER_MILLION_INPUT_TOKENS_USD`.

    Raises:
        RuntimeError: ``typesafe-sdk`` is not installed (nothing was attempted).
        ValueError: a blank/non-string identifier or source name.
    """
    owns_connection = conn is None
    if conn is None:
        conn = get_connection()
    assert conn is not None
    try:
        identifiers = _target_identifiers(conn, ids, source)
        rows = _document_rows(conn, identifiers)
        annotated = 0
        skipped = 0
        causes: list[str] = []
        input_tokens = 0
        for identifier in identifiers:
            record = rows.get(identifier)
            if record is None:
                causes.append(f"{identifier}: unknown article")
                skipped += 1
                continue
            title, source_name, content = record
            result = annotate_document(title, source_name, content)
            input_tokens += int(result["input_tokens"])
            written = False
            try:
                _run_control(conn, _SAVEPOINT)
                if result["tags"]:
                    _topics.set_document_topics(
                        identifier, list(result["tags"]), reporter=REPORTER, conn=conn
                    )
                    written = True
                if result["importance"] is not None:
                    _importance.set_importance(
                        identifier,
                        result["importance"],
                        rationale=result["rationale"],
                        reporter=REPORTER,
                        conn=conn,
                    )
                    written = True
                _run_control(conn, _RELEASE_SAVEPOINT)
                if owns_connection:
                    conn.commit()
            except Exception as exc:
                try:
                    # Undo this Document's writes only: the earlier Documents of
                    # this call stay valid for the caller's commit, so "annotated"
                    # never counts rows a rollback silently discarded.
                    _run_control(conn, _ROLLBACK_TO_SAVEPOINT)
                except Exception:  # connection already unusable; the caller decides
                    pass
                causes.append(f"{identifier}: {type(exc).__name__}: {exc}")
                skipped += 1
                continue
            causes.extend(f"{identifier}: {cause}" for cause in result["causes"])
            if written:
                annotated += 1
            else:
                skipped += 1
        return {
            "annotated": annotated,
            "skipped": skipped,
            "causes": causes,
            "input_tokens": input_tokens,
            "cost_usd": input_tokens / 1_000_000 * COST_PER_MILLION_INPUT_TOKENS_USD,
        }
    finally:
        if owns_connection:
            conn.close()
