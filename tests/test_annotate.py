"""Jev annotate lane contract tests (ADR-0016) — stub SDK + fake DB, no network.

Locked contract:

- The module imports and its DB-side helpers run without ``typesafe-sdk``
  installed; only the vendor call needs it, and its absence is a clear
  ``RuntimeError`` rather than an import-time failure.
- ``build_questions`` offers the live Topic vocabulary: every live pillar /
  region / content-type slug plus the ``none_of_above`` / ``unclear``
  sentinels, retired aliases excluded, and a 4-level digest rubric.
- Multi-label read-off: every option at or above 0.2 is a tag (0.19 is not),
  the sentinels never are, and a question below 0.5 confidence contributes
  nothing.
- Importance is the digest score normalised to 0–1, written only at 0.5+
  confidence with score and confidence in the rationale; the content-type top
  pick is written regardless of its own confidence.
- A response that would write nothing yields a cause (never a guess) and the
  Document is counted skipped; a vendor failure is a cause, not an exception.
- Writes reach the database only through ``importance.set_importance`` /
  ``topics.set_document_topics``; this module issues no write SQL of its own
  (the fake connection raises if it ever does).
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(os.path.dirname(_HERE), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import marketing_intelligence.annotate as annotate  # noqa: E402
import marketing_intelligence.topics as topics_lane  # noqa: E402

# --- stub vendor client ------------------------------------------------------


class _StubQuestion:
    """Canned question object standing in for an SDK ``Choice``/``Score``."""

    def __init__(self, instructions: Any, criteria: Any) -> None:
        self.instructions = instructions
        self.criteria = criteria


class _StubChoice(_StubQuestion):
    """A canned ``Choice`` question."""


class _StubScore(_StubQuestion):
    """A canned ``Score`` question."""


class _Choice:
    """Canned Choice answer: argmax, calibrated confidence, full distribution."""

    def __init__(
        self, choice: str, confidence: float, probabilities: dict[str, float] | None = None
    ) -> None:
        self.choice = choice
        self.confidence = confidence
        self.probabilities = {choice: confidence} if probabilities is None else probabilities


class _Score:
    """Canned Score answer: expected position and calibrated confidence."""

    def __init__(self, score: float, confidence: float) -> None:
        self.score = score
        self.confidence = confidence


class _Response:
    """Canned ``system_one`` response (answers keyed by question name)."""

    def __init__(self, answers: dict[str, Any], input_tokens: int = 0) -> None:
        self.answers = answers
        self.usage = type("_Usage", (), {"input_tokens": input_tokens})()


class _StubClient:
    """Canned client: records ``system_one`` calls, optionally raising instead."""

    def __init__(self, response: _Response | None = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls: list[tuple[Any, Any]] = []
        self.closed = False

    def __enter__(self) -> _StubClient:
        return self

    def __exit__(self, *exc: Any) -> bool:
        self.closed = True
        return False

    def system_one(self, state: Any = None, questions: Any = None) -> _Response:
        self.calls.append((state, questions))
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


class _StubSdk:
    """Stub ``typesafe_sdk`` module: question types plus a recording client factory."""

    Choice = _StubChoice
    Score = _StubScore

    def __init__(self, client: _StubClient) -> None:
        self.client = client
        self.client_kwargs: dict[str, Any] = {}

    def TypeSafeClient(self, **kwargs: Any) -> _StubClient:
        self.client_kwargs = kwargs
        return self.client


@pytest.fixture()
def stub_sdk(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Install a stub SDK (and API key); call with a canned client to wire it."""

    def _install(client: _StubClient, key: str | None = "test-key") -> _StubSdk:
        stub = _StubSdk(client)
        monkeypatch.setattr(annotate, "_load_sdk", lambda: stub)
        if key is None:
            monkeypatch.delenv(annotate.API_KEY_ENV, raising=False)
        else:
            monkeypatch.setenv(annotate.API_KEY_ENV, key)
        return stub

    return _install


# --- fake database -----------------------------------------------------------


class _FakeCursor:
    """Cursor over a queued result set; refuses writes to the annotation tables."""

    def __init__(self, conn: _FakeConn) -> None:
        self.conn = conn
        self._rows: list[Any] = []

    def execute(self, sql: str, params: Any = None) -> None:
        self.conn.statements.append((sql, params))
        upper = sql.upper()
        writes = ("INSERT", "UPDATE", "DELETE")
        if any(word in upper for word in writes) and any(
            table in sql for table in ("document_importance", "document_topics")
        ):
            raise AssertionError("annotate must write through the annotation lanes only")
        self._rows = self.conn.rows_for(sql)

    def fetchall(self) -> list[Any]:
        return list(self._rows)

    def close(self) -> None:
        pass


class _FakeConn:
    """DB-API-ish connection keyed by SQL marker; tracks commit/rollback/close."""

    def __init__(self, rows_by_marker: dict[str, list[Any]] | None = None) -> None:
        self.rows_by_marker = dict(rows_by_marker or {})
        self.statements: list[tuple[str, Any]] = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def rows_for(self, sql: str) -> list[Any]:
        for marker, rows in self.rows_by_marker.items():
            if marker in sql:
                return list(rows)
        return []

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closed = True


@pytest.fixture()
def writers(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[dict[str, Any]]]:
    """Record annotation-lane writes instead of touching the database."""
    recorded: dict[str, list[dict[str, Any]]] = {"topics": [], "importance": []}

    def _set_topics(
        identifier: str, topics: list[str], reporter: str | None = None, conn: Any = None
    ) -> None:
        recorded["topics"].append(
            {"identifier": identifier, "topics": list(topics), "reporter": reporter}
        )

    def _set_importance(
        identifier: str,
        score: float,
        rationale: str | None = None,
        reporter: str | None = None,
        conn: Any = None,
    ) -> None:
        recorded["importance"].append(
            {
                "identifier": identifier,
                "score": score,
                "rationale": rationale,
                "reporter": reporter,
            }
        )

    monkeypatch.setattr(annotate._topics, "set_document_topics", _set_topics)
    monkeypatch.setattr(annotate._importance, "set_importance", _set_importance)
    return recorded


DOCUMENT_URL = "https://www.socialmediatoday.com/news/genz/1/"
DOCUMENT_ROW = (DOCUMENT_URL, DOCUMENT_URL, "Gen Z jewellery", "Social Media Today", "body text")

#: SQL markers that route a fake-connection query to its queued rows.
_PENDING_MARKER = "document_importance i"
_DOCUMENT_MARKER = "d.url, d.title"


def _live_slugs(kind: str) -> list[str]:
    """Live (non-retired) slugs of one axis, straight from the vocabulary."""
    return [
        entry["slug"]
        for entry in topics_lane.list_vocabulary()
        if entry["kind"] == kind and entry["retired_alias_of"] is None
    ]


def _clean_response(**overrides: Any) -> _Response:
    """A fully-written baseline response: one pillar, one region, a ctype, digest 3/3."""
    answers: dict[str, Any] = {
        annotate.PILLAR_QUESTION: _Choice(
            "marketing", 0.92, {"marketing": 0.9, "none_of_above": 0.1}
        ),
        annotate.REGION_QUESTION: _Choice("brazil", 0.88, {"brazil": 0.85, "unclear": 0.15}),
        annotate.CONTENT_TYPE_QUESTION: _Choice("news", 0.61, {"news": 0.7, "analysis": 0.3}),
        annotate.DIGEST_QUESTION: _Score(3.0, 0.9),
    }
    answers.update(overrides)
    return _Response(answers, input_tokens=1500)


# --- laziness + vocabulary ---------------------------------------------------


def test_module_and_db_helpers_run_without_the_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "typesafe_sdk", None)
    conn = _FakeConn({_PENDING_MARKER: [(DOCUMENT_URL,)]})
    assert annotate.pending_document_ids(conn) == [DOCUMENT_URL]
    assert annotate._truncate(None) == ""
    with pytest.raises(RuntimeError, match="typesafe-sdk"):
        annotate.build_questions()
    with pytest.raises(RuntimeError, match="typesafe-sdk"):
        annotate.annotate_document("title", "source", "body")


def test_build_questions_offers_the_live_vocabulary(stub_sdk: Any) -> None:
    stub_sdk(_StubClient(_Response({})))
    questions = annotate.build_questions()

    assert set(questions) == {
        annotate.PILLAR_QUESTION,
        annotate.REGION_QUESTION,
        annotate.CONTENT_TYPE_QUESTION,
        annotate.DIGEST_QUESTION,
    }
    pillar = questions[annotate.PILLAR_QUESTION]
    assert list(pillar.criteria) == [*_live_slugs("pillar"), annotate.PILLAR_NONE]
    assert "consumer-behavior" in pillar.criteria
    assert "consumer-trends" not in pillar.criteria  # retired alias, never an option
    assert pillar.criteria["jewelry"].startswith("Jewelry")

    region = questions[annotate.REGION_QUESTION]
    assert list(region.criteria) == [*_live_slugs("region"), annotate.REGION_UNCLEAR]
    assert {"mexico", "brazil"} <= set(region.criteria)

    content_type = questions[annotate.CONTENT_TYPE_QUESTION]
    assert list(content_type.criteria) == _live_slugs("content-type")
    assert "influencer-marketing" not in pillar.criteria

    digest = questions[annotate.DIGEST_QUESTION]
    assert isinstance(digest, _StubScore)
    assert list(digest.criteria) == list(annotate.DIGEST_RUBRIC)
    assert len(annotate.DIGEST_RUBRIC) == 4


# --- read-off rules ----------------------------------------------------------


def test_multi_label_tags_include_every_option_at_threshold(stub_sdk: Any) -> None:
    client = _StubClient(
        _Response(
            {
                annotate.PILLAR_QUESTION: _Choice(
                    "marketing",
                    0.95,
                    {"marketing": 0.2, "jewelry": 0.19, "none_of_above": 0.61},
                ),
                annotate.REGION_QUESTION: _Choice(
                    "brazil", 0.8, {"brazil": 0.7, "latam": 0.19, "unclear": 0.11}
                ),
                annotate.CONTENT_TYPE_QUESTION: _Choice("news", 0.3, {"news": 0.45}),
                annotate.DIGEST_QUESTION: _Score(0.0, 0.2),
            }
        )
    )
    stub_sdk(client)

    result = annotate.annotate_document("Thin title", "Source", "body")

    assert result["tags"] == ["marketing", "brazil", "news"]
    assert annotate.PILLAR_NONE not in result["tags"]
    assert annotate.REGION_UNCLEAR not in result["tags"]
    assert result["confidences"][annotate.PILLAR_QUESTION] == 0.95


@pytest.mark.parametrize(
    ("confidence", "expected_tags"),
    [(0.49, ["news"]), (0.5, ["marketing", "news"])],
)
def test_choice_tags_are_gated_by_question_confidence(
    stub_sdk: Any, confidence: float, expected_tags: list[str]
) -> None:
    client = _StubClient(
        _Response(
            {
                annotate.PILLAR_QUESTION: _Choice(
                    "marketing", confidence, {"marketing": 0.9, "none_of_above": 0.1}
                ),
                annotate.CONTENT_TYPE_QUESTION: _Choice("news", 0.3, {"news": 0.5}),
                annotate.DIGEST_QUESTION: _Score(1.0, 0.2),
            }
        )
    )
    stub_sdk(client)

    result = annotate.annotate_document("Title", "Source", "body")

    assert result["tags"] == expected_tags
    assert result["causes"] == []


@pytest.mark.parametrize(
    ("confidence", "expected"),
    [(0.49, (None, None)), (0.5, (0.5, "jev digest_relevance=1.50/3 conf=0.50"))],
)
def test_importance_written_only_above_the_confidence_gate(
    stub_sdk: Any, confidence: float, expected: tuple[Any, Any]
) -> None:
    client = _StubClient(
        _Response(
            {
                annotate.PILLAR_QUESTION: _Choice("marketing", 0.9, {"marketing": 0.9}),
                annotate.CONTENT_TYPE_QUESTION: _Choice("news", 0.4, {"news": 0.4}),
                annotate.DIGEST_QUESTION: _Score(1.5, confidence),
            }
        )
    )
    stub_sdk(client)

    result = annotate.annotate_document("Title", "Source", "body")

    assert (result["importance"], result["rationale"]) == expected
    assert result["tags"] == ["marketing", "news"]


def test_zero_write_response_reports_low_confidence_without_guessing(stub_sdk: Any) -> None:
    stub_sdk(_StubClient(_Response({}, input_tokens=1200)))

    result = annotate.annotate_document("Title", "Source", "body")

    assert result["tags"] == []
    assert result["importance"] is None
    assert result["rationale"] is None
    assert result["causes"] == ["low-confidence"]
    assert result["confidences"] == {}
    assert result["input_tokens"] == 1200


def test_content_type_is_written_despite_low_other_confidences(stub_sdk: Any) -> None:
    client = _StubClient(
        _Response(
            {
                annotate.PILLAR_QUESTION: _Choice("marketing", 0.1, {"marketing": 0.6}),
                annotate.REGION_QUESTION: _Choice("unclear", 0.4, {"unclear": 0.9}),
                annotate.CONTENT_TYPE_QUESTION: _Choice("campaign", 0.35, {"campaign": 0.5}),
                annotate.DIGEST_QUESTION: _Score(2.0, 0.1),
            }
        )
    )
    stub_sdk(client)

    result = annotate.annotate_document("Title", "Source", "body")

    assert result["tags"] == ["campaign"]
    assert result["importance"] is None
    assert result["causes"] == []


def test_unrecognized_content_type_is_a_cause_not_a_write(stub_sdk: Any) -> None:
    client = _StubClient(
        _Response({annotate.CONTENT_TYPE_QUESTION: _Choice("podcast", 0.9, {"podcast": 0.9})})
    )
    stub_sdk(client)

    result = annotate.annotate_document("Title", "Source", "body")

    assert result["tags"] == []
    assert result["causes"] == ["content_type: unrecognized slug 'podcast'"]


# --- vendor call plumbing ----------------------------------------------------


def test_state_is_truncated_and_questions_are_sent_together(stub_sdk: Any) -> None:
    body = "x" * (annotate.MAX_TEXT_CHARS * 2)
    client = _StubClient(_clean_response())
    stub = stub_sdk(client)

    result = annotate.annotate_document("Title", "Source", body)

    assert len(client.calls) == 1
    state, questions = client.calls[0]
    assert state["text"] == body[: annotate.MAX_TEXT_CHARS]
    assert len(state["text"]) == annotate.MAX_TEXT_CHARS
    assert state["title"] == "Title"
    assert state["source"] == "Source"
    assert set(questions) == set(annotate.build_questions())
    assert stub.client_kwargs == {"model": annotate.MODEL_ID, "api_key": "test-key"}
    assert client.closed is True
    assert "test-key" not in json.dumps(result)


def test_missing_api_key_is_a_cause_and_never_a_call(stub_sdk: Any) -> None:
    client = _StubClient(_clean_response())
    stub = stub_sdk(client, key=None)

    result = annotate.annotate_document("Title", "Source", "body")

    assert client.calls == []
    assert stub.client_kwargs == {}
    assert result["causes"] == [f"{annotate.API_KEY_ENV} is not set; refusing to bill the Jev lane"]
    assert result["tags"] == [] and result["importance"] is None
    assert result["input_tokens"] == 0


def test_vendor_failure_is_a_cause_not_an_exception(stub_sdk: Any) -> None:
    client = _StubClient(error=RuntimeError("upstream refused"))
    stub_sdk(client)

    result = annotate.annotate_document("Title", "Source", "body")

    assert client.calls != []
    assert result["causes"] == ["jev error: RuntimeError: upstream refused"]
    assert result["tags"] == [] and result["importance"] is None
    assert result["input_tokens"] == 0


def test_vendor_error_message_never_carries_the_api_key(stub_sdk: Any) -> None:
    client = _StubClient(error=RuntimeError("401 for api_key=secret-probe-key rejected"))
    stub_sdk(client, key="secret-probe-key")

    result = annotate.annotate_document("Title", "Source", "body")

    assert result["causes"] == ["jev error: RuntimeError: 401 for api_key=<redacted> rejected"]
    assert "secret-probe-key" not in json.dumps(result)


# --- selection + writes ------------------------------------------------------


def test_pending_document_ids_selects_documents_jev_has_not_touched() -> None:
    conn = _FakeConn({_PENDING_MARKER: [(DOCUMENT_URL,), ("https://example.com/b",)]})

    assert annotate.pending_document_ids(conn) == [DOCUMENT_URL, "https://example.com/b"]
    sql, params = conn.statements[0]
    assert "NOT EXISTS" in sql and "reporter = %s" in sql
    assert params == (annotate.REPORTER, None, None)

    assert annotate.pending_document_ids(conn, "Source B") == [
        DOCUMENT_URL,
        "https://example.com/b",
    ]
    assert conn.statements[-1][1] == (annotate.REPORTER, "Source B", "Source B")


def test_classify_and_write_writes_through_both_lanes(
    stub_sdk: Any, writers: dict[str, list[dict[str, Any]]]
) -> None:
    client = _StubClient(_clean_response())
    stub_sdk(client)
    conn = _FakeConn({_DOCUMENT_MARKER: [DOCUMENT_ROW]})

    result = annotate.classify_and_write(ids=DOCUMENT_URL, conn=conn)

    assert result["annotated"] == 1
    assert result["skipped"] == 0
    assert result["causes"] == []
    assert result["input_tokens"] == 1500
    assert result["cost_usd"] == pytest.approx(1500 / 1_000_000 * 0.042)
    assert writers["topics"] == [
        {"identifier": DOCUMENT_URL, "topics": ["marketing", "brazil", "news"], "reporter": "jev"}
    ]
    assert writers["importance"] == [
        {
            "identifier": DOCUMENT_URL,
            "score": 1.0,
            "rationale": "jev digest_relevance=3.00/3 conf=0.90",
            "reporter": "jev",
        }
    ]
    # An injected connection is the caller's to commit and close, never ours.
    assert (conn.commits, conn.closed) == (0, False)
    state, _ = client.calls[0]
    assert state["title"] == DOCUMENT_ROW[2]


def test_classify_and_write_source_selection_uses_pending_documents(
    stub_sdk: Any, writers: dict[str, list[dict[str, Any]]]
) -> None:
    stub_sdk(_StubClient(_clean_response()))
    conn = _FakeConn({_PENDING_MARKER: [(DOCUMENT_URL,)], _DOCUMENT_MARKER: [DOCUMENT_ROW]})

    result = annotate.classify_and_write(source="Social Media Today", conn=conn)

    assert (result["annotated"], result["skipped"]) == (1, 0)
    pending_sql, pending_params = conn.statements[0]
    assert _PENDING_MARKER in pending_sql
    assert pending_params == (annotate.REPORTER, "Social Media Today", "Social Media Today")
    assert [call["identifier"] for call in writers["topics"]] == [DOCUMENT_URL]


def test_classify_and_write_unknown_identifier_is_skipped_with_a_cause(
    stub_sdk: Any, writers: dict[str, list[dict[str, Any]]]
) -> None:
    client = _StubClient(_clean_response())
    stub_sdk(client)
    conn = _FakeConn({_DOCUMENT_MARKER: []})

    result = annotate.classify_and_write(ids=["https://example.com/gone"], conn=conn)

    assert (result["annotated"], result["skipped"]) == (0, 1)
    assert result["causes"] == ["https://example.com/gone: unknown article"]
    assert (result["input_tokens"], result["cost_usd"]) == (0, 0.0)
    assert client.calls == []
    assert writers == {"topics": [], "importance": []}


def test_classify_and_write_zero_write_document_is_skipped(
    stub_sdk: Any, writers: dict[str, list[dict[str, Any]]]
) -> None:
    stub_sdk(_StubClient(_Response({}, input_tokens=900)))
    conn = _FakeConn({_DOCUMENT_MARKER: [DOCUMENT_ROW]})

    result = annotate.classify_and_write(ids=[DOCUMENT_URL], conn=conn)

    assert (result["annotated"], result["skipped"]) == (0, 1)
    assert result["causes"] == [f"{DOCUMENT_URL}: low-confidence"]
    assert result["input_tokens"] == 900
    assert result["cost_usd"] == pytest.approx(900 / 1_000_000 * 0.042)
    assert writers == {"topics": [], "importance": []}


def test_classify_and_write_rejects_blank_identifiers() -> None:
    conn = _FakeConn()
    with pytest.raises(ValueError, match="non-empty"):
        annotate.classify_and_write(ids=["  "], conn=conn)
    with pytest.raises(ValueError, match="non-empty"):
        annotate.classify_and_write(source="", conn=conn)


def test_classify_and_write_isolates_a_failing_write_to_its_document(
    stub_sdk: Any, writers: dict[str, list[dict[str, Any]]], monkeypatch: pytest.MonkeyPatch
) -> None:
    second_url = "https://www.socialmediatoday.com/news/genz/2/"
    second_row = (second_url, second_url, "Older", "Social Media Today", "body")
    written: list[str] = []

    def _set_topics(
        identifier: str, topics: list[str], reporter: str | None = None, conn: Any = None
    ) -> None:
        if identifier == DOCUMENT_URL:
            raise RuntimeError("writer exploded")
        written.append(identifier)

    monkeypatch.setattr(annotate._topics, "set_document_topics", _set_topics)
    stub_sdk(_StubClient(_clean_response()))
    conn = _FakeConn({_DOCUMENT_MARKER: [DOCUMENT_ROW, second_row]})

    result = annotate.classify_and_write(ids=[DOCUMENT_URL, second_url], conn=conn)

    assert (result["annotated"], result["skipped"]) == (1, 1)
    assert result["causes"] == [f"{DOCUMENT_URL}: RuntimeError: writer exploded"]
    assert written == [second_url]
    executed = [sql for sql, _ in conn.statements]
    assert annotate._SAVEPOINT in executed
    assert annotate._ROLLBACK_TO_SAVEPOINT in executed
    # An injected connection is never rolled back wholesale: the caller's single
    # commit keeps the Documents that succeeded.
    assert (conn.rollbacks, conn.commits, conn.closed) == (0, 0, False)


def test_classify_and_write_owns_its_connection_by_default(
    stub_sdk: Any, writers: dict[str, list[dict[str, Any]]], monkeypatch: pytest.MonkeyPatch
) -> None:
    stub_sdk(_StubClient(_clean_response()))
    conn = _FakeConn({_DOCUMENT_MARKER: [DOCUMENT_ROW]})
    monkeypatch.setattr(annotate, "get_connection", lambda: conn)

    result = annotate.classify_and_write(ids=[DOCUMENT_URL])

    assert (result["annotated"], result["skipped"]) == (1, 0)
    assert conn.commits == 1
    assert conn.closed is True
