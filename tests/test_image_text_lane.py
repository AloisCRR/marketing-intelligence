"""Instagram image-text lane tests (ADR-0014, migration 013).

Fake-only: the impersonated frame download and the DeepInfra transport are
monkeypatched, the DB is an ON CONFLICT-aware fake documents+image-texts store.
No network, no Postgres, no billed call.

Pinned observable behavior:
- frame enumeration: cover `displayUrl` first, then `childPosts[].displayUrl`,
  malformed entries and blank URLs skipped
- billing: frames are deduped by the SHA-256 of their bytes before the vision
  call, so a cover byte-identical to the first child is transcribed once while
  both frame rows are stored
- billing cap: at most `MAX_FRAMES_PER_POST` (20) unique frames are fetched and
  billed per post; the deduped overflow is never downloaded and surfaces as
  per-index causes, leaving the run's `{inserted, skipped}` shape intact
- `NO_TEXT` frames are stored, not skipped; failures are `"<frame_index>:
  <detail>"` causes and never exceptions
- the vision call pins model id / prompt / `reasoning_effort: none` /
  temperature 0 / max_tokens 1000 / base64 upload / 120 s timeout, reads
  `DEEPINFRA_API_KEY` at call time, and never puts the key in the payload
- side-table writes are post-upsert, keyed `(document_id, frame_index)`,
  resolved by URL then canonical; unknown URLs skip, never raise
- the lane stage runs after the payload write and only for opted-in accounts;
  its failures leave `{inserted, skipped}` intact and never return `error`
"""

from __future__ import annotations

import datetime as _dt
import os
import sys
import uuid
from typing import Any

import pytest
from prefect_harness import no_engine  # noqa: F401 - fixture used by flow tests

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_SRC = os.path.join(_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import marketing_intelligence.flows as flows  # noqa: E402
import marketing_intelligence.image_text as image_text  # noqa: E402
import marketing_intelligence.ingest as ingest  # noqa: E402
import marketing_intelligence.instagram as instagram  # noqa: E402
from marketing_intelligence.normalize import NormalizedDocument, make_document  # noqa: E402

SOURCE = "ig:jordisanildefonso"
SOURCE_UUID = "9d1f1c60-0000-4000-8000-000000000001"
URL = "https://www.instagram.com/p/Dc9PS-SkU6_/"
TRACKED_URL = "https://www.instagram.com/p/Dc9PS-SkU6_/?utm_source=copy"
DOC_ID = str(uuid.uuid5(uuid.NAMESPACE_DNS, URL))

COVER = b"\xff\xd8\xffCOVER-BYTES"
FRAME_ONE = b"\xff\xd8\xffFRAME-ONE-BYTES"
FRAME_TWO = b"\x89PNG\r\n\x1a\nFRAME-TWO-BYTES"


def _utc(*args: int) -> _dt.datetime:
    return _dt.datetime(*args, tzinfo=_dt.UTC)


def _doc(url: str = URL) -> NormalizedDocument:
    return make_document(
        source=SOURCE,
        url=url,
        title="MONIdero",
        content="El drop ya está aquí.",
        published_at=_utc(2026, 9, 1, 12, 0),
        language="es",
    )


class _StubCursor:
    def __init__(self, rows: list[tuple[Any, ...]] | None = None, rowcount: int = 0) -> None:
        self._rows = list(rows or [])
        self.rowcount = rowcount

    def fetchone(self) -> Any | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[Any]:
        return list(self._rows)

    def close(self) -> None:
        pass


class _StoreConnection:
    """Fake documents+image-texts store: upsert rows, frames keyed by (id, index)."""

    def __init__(self, source_id: str = SOURCE_UUID) -> None:
        self.source_id = source_id
        self.documents: dict[str, dict[str, Any]] = {}  # url -> {"id", "canonical_url"}
        self.image_texts: dict[tuple[str, int], tuple[str, str]] = {}
        self.frame_writes = 0
        self.fail_frame_index: int | None = None
        self.resolutions: list[str] = []
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
        if sql == image_text._DOCUMENT_ID_SQL:
            url, canonical = params
            if url in self.documents:
                self.resolutions.append("url")
                return _StubCursor([(self.documents[url]["id"],)], 1)
            for entry in self.documents.values():
                if entry["canonical_url"] == canonical:
                    self.resolutions.append("canonical")
                    return _StubCursor([(entry["id"],)], 1)
            return _StubCursor(rowcount=0)
        if sql == image_text._IMAGE_TEXT_UPSERT_SQL:
            document_id, frame_index, text, model = params
            if self.fail_frame_index is not None and frame_index == self.fail_frame_index:
                raise RuntimeError("frame write blew up")
            self.image_texts[(document_id, frame_index)] = (text, model)
            self.frame_writes += 1
            return _StubCursor(rowcount=1)
        raise AssertionError(f"unexpected SQL: {sql}")

    def commit(self) -> None:
        self.committed += 1

    def close(self) -> None:
        self.closed += 1


# --- frame enumeration ---------------------------------------------------------------


def test_enumerate_frame_urls_covers_cover_and_children_in_order() -> None:
    post = {
        "displayUrl": " https://cdn.example/cover.jpg ",
        "childPosts": [
            {"displayUrl": "https://cdn.example/f1.jpg"},
            {"displayUrl": ""},
            "not-a-dict",
            {"displayUrl": None},
            {"displayUrl": "https://cdn.example/f2.jpg"},
        ],
    }
    assert image_text.enumerate_frame_urls(post) == [
        "https://cdn.example/cover.jpg",
        "https://cdn.example/f1.jpg",
        "https://cdn.example/f2.jpg",
    ]


def test_enumerate_frame_urls_tolerates_malformed_payload() -> None:
    assert image_text.enumerate_frame_urls({}) == []
    assert image_text.enumerate_frame_urls({"displayUrl": None, "childPosts": None}) == []
    assert image_text.enumerate_frame_urls({"childPosts": {"displayUrl": "x"}}) == []


# --- byte dedupe / billing -----------------------------------------------------------


def test_extract_frames_bills_each_unique_image_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """cover == frame 0 (byte-identical): one vision call, both frames stored."""
    downloads = {0: COVER, 1: COVER, 2: FRAME_TWO}
    calls: list[bytes] = []

    def fake_download(url: str, timeout: float = image_text.TIMEOUT_SECONDS) -> bytes:
        del timeout
        return downloads[int(url.rsplit("/", 1)[-1])]

    def fake_extract(
        image_bytes: bytes, timeout: float = image_text.TIMEOUT_SECONDS
    ) -> tuple[str | None, str | None]:
        del timeout
        calls.append(image_bytes)
        return ("Texto del marco" if image_bytes == COVER else "MONIdero", None)

    monkeypatch.setattr(image_text, "fetch_frame_bytes", fake_download)
    monkeypatch.setattr(image_text, "extract_image_text", fake_extract)

    rows, causes = image_text.extract_frames(
        ["https://cdn.example/0", "https://cdn.example/1", "https://cdn.example/2"]
    )

    assert calls == [COVER, FRAME_TWO]  # two billed frames, not three
    assert rows == [(0, "Texto del marco"), (1, "Texto del marco"), (2, "MONIdero")]
    assert causes == []


def test_extract_frames_stores_no_text_frames(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(image_text, "fetch_frame_bytes", lambda url, timeout=0: COVER)
    monkeypatch.setattr(
        image_text, "extract_image_text", lambda image_bytes, timeout=0: (image_text.NO_TEXT, None)
    )

    rows, causes = image_text.extract_frames(["https://cdn.example/0"])

    assert rows == [(0, image_text.NO_TEXT)]  # stored, not skipped
    assert causes == []


def test_extract_frames_records_frame_index_causes(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_download(url: str, timeout: float = 0) -> bytes:
        del timeout
        if url.endswith("/0"):
            raise RuntimeError("fetch failed for frame 0")
        return FRAME_ONE

    def fake_extract(image_bytes: bytes, timeout: float = 0) -> tuple[str | None, str | None]:
        del timeout, image_bytes
        return (None, "vision model returned no text")

    monkeypatch.setattr(image_text, "fetch_frame_bytes", fake_download)
    monkeypatch.setattr(image_text, "extract_image_text", fake_extract)

    rows, causes = image_text.extract_frames(
        ["https://cdn.example/0", "https://cdn.example/1", "https://cdn.example/2"]
    )

    assert rows == []
    assert causes == [
        "0: fetch failed for frame 0",
        "1: vision model returned no text",
        # frame 2 is byte-identical to the frame whose call failed: a second
        # billed call would be waste, so it inherits the recorded failure.
        "2: vision model returned no text",
    ]


def test_extract_frames_does_not_rebill_a_failed_image(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[bytes] = []

    def fake_extract(image_bytes: bytes, timeout: float = 0) -> tuple[str | None, str | None]:
        del timeout
        calls.append(image_bytes)
        return (None, "boom")

    monkeypatch.setattr(image_text, "fetch_frame_bytes", lambda url, timeout=0: FRAME_ONE)
    monkeypatch.setattr(image_text, "extract_image_text", fake_extract)

    rows, causes = image_text.extract_frames(["https://cdn.example/0", "https://cdn.example/1"])

    assert rows == []
    assert calls == [FRAME_ONE]
    assert causes == ["0: boom", "1: boom"]


def test_extract_frames_caps_unique_frames_per_post(monkeypatch: pytest.MonkeyPatch) -> None:
    """Past the cap nothing is downloaded, let alone billed; it becomes a cause."""
    cap = image_text.MAX_FRAMES_PER_POST
    downloads: list[str] = []
    calls: list[bytes] = []

    def fake_download(url: str, timeout: float = 0) -> bytes:
        del timeout
        downloads.append(url)
        return b"\xff\xd8\xff" + url.encode()

    def fake_extract(image_bytes: bytes, timeout: float = 0) -> tuple[str | None, str | None]:
        del timeout
        calls.append(image_bytes)
        return (f"texto {len(calls)}", None)

    monkeypatch.setattr(image_text, "fetch_frame_bytes", fake_download)
    monkeypatch.setattr(image_text, "extract_image_text", fake_extract)

    urls = [f"https://cdn.example/{index}" for index in range(cap + 3)]
    rows, causes = image_text.extract_frames(urls)

    assert image_text.MAX_FRAMES_PER_POST == 20  # the 17-frame pilot, with margin
    assert len(calls) == cap == 20
    assert downloads == urls[:cap]  # the overflow is never even fetched
    assert rows == [(index, f"texto {index + 1}") for index in range(cap)]
    assert causes == [
        f"{index}: frame cap ({cap}) exceeded, skipped" for index in range(cap, cap + 3)
    ]


def test_frame_cap_counts_unique_images_not_frame_indices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cap is deduped work: a duplicate inside it neither bills nor consumes a slot."""
    cap = image_text.MAX_FRAMES_PER_POST
    unique_urls = [f"https://cdn.example/{index}" for index in range(cap)]
    urls = [unique_urls[0], unique_urls[0], *unique_urls[1:]]  # cover twice, then the rest
    calls: list[bytes] = []

    def fake_download(url: str, timeout: float = 0) -> bytes:
        del timeout
        return b"\xff\xd8\xff" + url.encode()

    def fake_extract(image_bytes: bytes, timeout: float = 0) -> tuple[str | None, str | None]:
        del timeout
        calls.append(image_bytes)
        return ("texto", None)

    monkeypatch.setattr(image_text, "fetch_frame_bytes", fake_download)
    monkeypatch.setattr(image_text, "extract_image_text", fake_extract)

    rows, causes = image_text.extract_frames(urls)

    assert len(calls) == cap  # cap unique images billed, the duplicate free
    assert [index for index, _ in rows] == list(range(cap + 1))  # every frame still stored
    assert causes == []  # the duplicate did not eat the last slot


# --- vision call pins -----------------------------------------------------------------


def test_constants_pin_model_prompt_and_budget() -> None:
    assert image_text.MODEL_ID == "deepseek-ai/DeepSeek-V4.1-Flash"
    assert image_text.NO_TEXT == "NO_TEXT"
    assert image_text.TIMEOUT_SECONDS == 120.0
    assert image_text.MAX_FRAMES_PER_POST == 20  # 17-frame pilot + margin
    assert image_text.MAX_TOKENS == 1000
    assert image_text.TEMPERATURE == 0
    assert image_text.IMAGE_TEXT_EXTRACT == "extract"
    assert image_text.NO_TEXT in image_text.PROMPT  # sentinel is part of the prompt
    assert "Transcribe" in image_text.PROMPT


def test_vision_call_sends_base64_pinned_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPINFRA_API_KEY", "secret-key")
    captured: dict[str, Any] = {}

    class _Response:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict[str, Any]:
            return {"choices": [{"message": {"content": "MONIdero\nColeccionable"}}]}

    def fake_post(url: str, **kwargs: Any) -> _Response:
        captured["url"] = url
        captured["kwargs"] = kwargs
        return _Response()

    monkeypatch.setattr(image_text.httpx, "post", fake_post)

    text, cause = image_text.extract_image_text(COVER)

    assert (text, cause) == ("MONIdero\nColeccionable", None)
    assert captured["url"] == "https://api.deepinfra.com/v1/openai/chat/completions"
    kwargs = captured["kwargs"]
    assert kwargs["headers"]["Authorization"] == "Bearer secret-key"
    assert kwargs["timeout"] == image_text.TIMEOUT_SECONDS
    payload = kwargs["json"]
    assert payload["model"] == image_text.MODEL_ID
    assert payload["temperature"] == 0
    assert payload["max_tokens"] == 1000
    assert payload["reasoning_effort"] == "none"
    parts = payload["messages"][0]["content"]
    assert parts[0]["text"] == image_text.PROMPT
    data_url = parts[1]["image_url"]["url"]
    assert data_url.startswith("data:image/jpeg;base64,")  # bytes uploaded, never the CDN URL
    assert "secret-key" not in str(payload)


def test_vision_call_without_key_returns_cause(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEEPINFRA_API_KEY", raising=False)
    text, cause = image_text.extract_image_text(COVER)
    assert text is None
    assert cause is not None and "DEEPINFRA_API_KEY" in cause


def test_vision_call_failure_returns_cause_never_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEEPINFRA_API_KEY", "secret-key")

    def boom(url: str, **kwargs: Any) -> Any:
        raise RuntimeError("connection reset by peer")

    monkeypatch.setattr(image_text.httpx, "post", boom)
    assert image_text.extract_image_text(COVER) == (None, "connection reset by peer")


def test_empty_completion_returns_cause(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPINFRA_API_KEY", "secret-key")

    class _Response:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict[str, Any]:
            return {"choices": [{"message": {"content": None}}]}

    monkeypatch.setattr(image_text.httpx, "post", lambda url, **kwargs: _Response())
    text, cause = image_text.extract_image_text(COVER)
    assert text is None
    assert cause == "vision model returned no text"


def test_sentinel_answer_normalizes_to_the_sentinel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPINFRA_API_KEY", "secret-key")

    class _Response:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict[str, Any]:
            return {"choices": [{"message": {"content": "no_text.\n"}}]}

    monkeypatch.setattr(image_text.httpx, "post", lambda url, **kwargs: _Response())
    assert image_text.extract_image_text(COVER) == (image_text.NO_TEXT, None)


def test_webp_and_png_uploads_carry_their_media_type(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPINFRA_API_KEY", "secret-key")
    captured: list[dict[str, Any]] = []

    class _Response:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict[str, Any]:
            return {"choices": [{"message": {"content": "x"}}]}

    def fake_post(url: str, **kwargs: Any) -> _Response:
        captured.append(kwargs["json"])
        return _Response()

    monkeypatch.setattr(image_text.httpx, "post", fake_post)
    image_text.extract_image_text(FRAME_TWO)
    image_text.extract_image_text(b"RIFF\x00\x00\x00\x00WEBPVP8 ")

    assert captured[0]["messages"][0]["content"][1]["image_url"]["url"].startswith(
        "data:image/png;base64,"
    )
    assert captured[1]["messages"][0]["content"][1]["image_url"]["url"].startswith(
        "data:image/webp;base64,"
    )


# --- side-table writes ----------------------------------------------------------------


def test_write_after_upsert_resolves_by_url_and_stores_frames() -> None:
    conn = _StoreConnection()
    doc = _doc()
    assert ingest.upsert_documents([doc], conn=conn) == (1, 0)

    frames = [(0, "MONIdero"), (1, image_text.NO_TEXT)]
    assert image_text.write_document_image_texts([(doc, frames)], conn=conn) == (2, 0)

    assert conn.resolutions == ["url"]
    assert conn.image_texts[(DOC_ID, 0)] == ("MONIdero", image_text.MODEL_ID)
    assert conn.image_texts[(DOC_ID, 1)] == (image_text.NO_TEXT, image_text.MODEL_ID)
    assert len(conn.image_texts) == 2  # one row per frame index


def test_reextraction_overwrites_in_place() -> None:
    conn = _StoreConnection()
    doc = _doc()
    ingest.upsert_documents([doc], conn=conn)

    image_text.write_document_image_texts([(doc, [(0, "viejo")])], conn=conn)
    assert image_text.write_document_image_texts([(doc, [(0, "nuevo")])], conn=conn) == (1, 0)

    assert list(conn.image_texts) == [(DOC_ID, 0)]
    assert conn.image_texts[(DOC_ID, 0)][0] == "nuevo"
    assert conn.frame_writes == 2


def test_canonical_url_resolves_when_pair_url_differs_after_upsert() -> None:
    conn = _StoreConnection()
    ingest.upsert_documents([_doc(TRACKED_URL)], conn=conn)

    assert image_text.write_document_image_texts([(_doc(URL), [(0, "x")])], conn=conn) == (1, 0)
    assert conn.resolutions == ["canonical"]


def test_unknown_url_counts_frames_skipped_and_never_raises() -> None:
    conn = _StoreConnection()  # no document rows at all

    assert image_text.write_document_image_texts([(_doc(), [(0, "x"), (1, "y")])], conn=conn) == (
        0,
        2,
    )
    assert conn.image_texts == {}


def test_partial_batch_counts_known_and_unknown() -> None:
    conn = _StoreConnection()
    known = _doc()
    ingest.upsert_documents([known], conn=conn)
    unknown = _doc("https://www.instagram.com/p/ZZZZZZZZZZZ/")

    assert image_text.write_document_image_texts(
        [(known, [(0, "x")]), (unknown, [(0, "y"), (1, "z")])], conn=conn
    ) == (1, 2)


def test_empty_pairs_is_a_noop() -> None:
    conn = _StoreConnection()
    assert image_text.write_document_image_texts([], conn=conn) == (0, 0)
    assert conn.statements == []


def test_one_failed_frame_write_keeps_the_earlier_frames() -> None:
    conn = _StoreConnection()
    doc = _doc()
    ingest.upsert_documents([doc], conn=conn)
    conn.fail_frame_index = 1

    assert image_text.write_document_image_texts([(doc, [(0, "x"), (1, "y")])], conn=conn) == (1, 1)
    assert list(conn.image_texts) == [(DOC_ID, 0)]


def test_injected_connection_is_committed_but_not_closed() -> None:
    conn = _StoreConnection()
    conn.documents[URL] = {"id": DOC_ID, "canonical_url": URL}

    assert image_text.write_document_image_texts([(_doc(), [(0, "x")])], conn=conn) == (1, 0)
    assert conn.committed == 1
    assert conn.closed == 0


def test_owned_connection_is_opened_committed_and_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _StoreConnection()
    conn.documents[URL] = {"id": DOC_ID, "canonical_url": URL}
    monkeypatch.setattr(image_text, "get_connection", lambda: conn)

    assert image_text.write_document_image_texts([(_doc(), [(0, "x")])]) == (1, 0)
    assert conn.committed == 1
    assert conn.closed == 1


# --- stage ----------------------------------------------------------------------------


def test_stage_writes_rows_and_counts_frames(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _StoreConnection()
    doc = _doc()
    ingest.upsert_documents([doc], conn=conn)
    monkeypatch.setattr(image_text, "fetch_frame_bytes", lambda url, timeout=0: COVER)
    monkeypatch.setattr(
        image_text, "extract_image_text", lambda image_bytes, timeout=0: ("MONIdero", None)
    )

    frames, causes = image_text.image_texts_for_posts(
        [(doc, ["https://cdn.example/0", "https://cdn.example/1"])], conn=conn
    )

    assert (frames, causes) == (2, [])
    assert conn.image_texts[(DOC_ID, 0)][0] == "MONIdero"
    assert conn.image_texts[(DOC_ID, 1)][0] == "MONIdero"


def test_stage_failure_is_a_cause_never_an_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(pairs: Any, conn: Any = None) -> tuple[int, int]:
        raise RuntimeError("connection refused")

    monkeypatch.setattr(image_text, "write_document_image_texts", boom)
    monkeypatch.setattr(image_text, "fetch_frame_bytes", lambda url, timeout=0: COVER)
    monkeypatch.setattr(
        image_text, "extract_image_text", lambda image_bytes, timeout=0: ("x", None)
    )

    frames, causes = image_text.image_texts_for_posts([(_doc(), ["https://cdn.example/0"])])

    assert frames == 0
    assert causes == ["0: connection refused"]


def test_stage_is_a_noop_without_posts() -> None:
    assert image_text.image_texts_for_posts([]) == (0, [])


# --- instagram lane wiring ------------------------------------------------------------


def _stub_ingest(
    monkeypatch: pytest.MonkeyPatch,
    *,
    image_text_mode: str,
    stage_result: tuple[int, list[str]] | None = None,
) -> list[str]:
    """Wire the lane with fakes; returns the ordered stage names that ran."""
    events: list[str] = []
    monkeypatch.setattr(
        instagram,
        "get_retrieval_config",
        lambda label: {"username": "jordisanildefonso", "image_text": image_text_mode},
    )
    monkeypatch.setattr(
        instagram,
        "fetch_instagram_posts",
        lambda label, conn=None: (
            [
                {
                    "displayUrl": "https://cdn.example/cover.jpg",
                    "shortCode": "Dc9PS-SkU6_",
                    "caption": "MONIdero",
                    "timestamp": "2026-09-01T12:00:00.000Z",
                    "ownerUsername": "jordisanildefonso",
                }
            ],
            1,
        ),
    )

    def fake_upsert(docs: Any, conn: Any = None) -> tuple[int, int]:
        events.append("upsert")
        return (1, 0)

    def fake_payloads(pairs: Any, conn: Any = None) -> tuple[int, int]:
        events.append("payloads")
        return (len(pairs), 0)

    def fake_stage(pairs: Any, conn: Any = None) -> tuple[int, list[str]]:
        events.append("image_text")
        return stage_result if stage_result is not None else (2, [])

    monkeypatch.setattr(instagram, "upsert_documents", fake_upsert)
    monkeypatch.setattr(instagram, "write_document_payloads", fake_payloads)
    monkeypatch.setattr(instagram, "image_texts_for_posts", fake_stage)
    monkeypatch.setattr(
        instagram, "enumerate_frame_urls", lambda post: ["https://cdn.example/c.jpg"]
    )
    return events


def test_lane_stage_runs_after_the_payload_write(monkeypatch: pytest.MonkeyPatch) -> None:
    events = _stub_ingest(monkeypatch, image_text_mode="extract")

    result = instagram.ingest_instagram_source(SOURCE)

    assert events == ["upsert", "payloads", "image_text"]
    assert result == {"inserted": 1, "skipped": 0, "image_text_frames": 2}


def test_lane_skips_the_stage_when_not_opted_in(monkeypatch: pytest.MonkeyPatch) -> None:
    events = _stub_ingest(monkeypatch, image_text_mode="ignore")

    result = instagram.ingest_instagram_source(SOURCE)

    assert events == ["upsert", "payloads"]
    assert result == {"inserted": 1, "skipped": 0}  # exact result shape, no image-text keys


def test_lane_stage_failure_keeps_inserted_and_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    events = _stub_ingest(monkeypatch, image_text_mode="extract", stage_result=(0, ["1: boom"]))

    result = instagram.ingest_instagram_source(SOURCE)

    assert events == ["upsert", "payloads", "image_text"]
    assert result == {"inserted": 1, "skipped": 0, "image_text_causes": ["1: boom"]}
    assert "error" not in result  # a vision failure never fails the run


def test_lane_stage_exception_degrades_to_a_cause(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_ingest(monkeypatch, image_text_mode="extract")

    def boom(pairs: Any, conn: Any = None) -> tuple[int, list[str]]:
        raise RuntimeError("vision lane exploded")

    monkeypatch.setattr(instagram, "image_texts_for_posts", boom)

    result = instagram.ingest_instagram_source(SOURCE)

    assert result == {"inserted": 1, "skipped": 0, "image_text_causes": ["0: vision lane exploded"]}
    assert "error" not in result


def test_lane_keeps_its_summary_when_a_post_hits_the_frame_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End to end through the real stage: a 22-frame post writes 20 rows, not 22.

    The two frames past the cap cost nothing (never downloaded), show up as
    causes, and the run still reports its own `{inserted, skipped}`.
    """
    conn = _StoreConnection()
    conn.documents[URL] = {"id": DOC_ID, "canonical_url": URL}
    cap = image_text.MAX_FRAMES_PER_POST
    urls = [f"https://cdn.example/{index}" for index in range(cap + 2)]
    calls: list[bytes] = []

    def fake_extract(image_bytes: bytes, timeout: float = 0) -> tuple[str | None, str | None]:
        del timeout
        calls.append(image_bytes)
        return ("MONIdero", None)

    _stub_ingest(monkeypatch, image_text_mode="extract")
    monkeypatch.setattr(instagram, "image_texts_for_posts", image_text.image_texts_for_posts)
    monkeypatch.setattr(instagram, "enumerate_frame_urls", lambda post: list(urls))
    monkeypatch.setattr(image_text, "get_connection", lambda: conn)
    monkeypatch.setattr(
        image_text, "fetch_frame_bytes", lambda url, timeout=0: b"\xff\xd8\xff" + url.encode()
    )
    monkeypatch.setattr(image_text, "extract_image_text", fake_extract)

    result = instagram.ingest_instagram_source(SOURCE)

    assert result == {
        "inserted": 1,
        "skipped": 0,
        "image_text_frames": cap,
        "image_text_causes": [
            f"{index}: frame cap ({cap}) exceeded, skipped" for index in range(cap, cap + 2)
        ],
    }
    assert len(calls) == cap
    assert sorted(index for _, index in conn.image_texts) == list(range(cap))


def test_lane_stage_causes_are_nonzero_only(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_ingest(monkeypatch, image_text_mode="extract", stage_result=(0, []))

    result = instagram.ingest_instagram_source(SOURCE)

    assert result == {"inserted": 1, "skipped": 0}
    assert "image_text_frames" not in result
    assert "image_text_causes" not in result


def test_lane_passes_enumerated_frame_urls_to_the_stage(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_ingest(monkeypatch, image_text_mode="extract")
    captured: list[Any] = []
    monkeypatch.setattr(
        instagram,
        "image_texts_for_posts",
        lambda pairs, conn=None: (captured.append(list(pairs)), (2, []))[1],
    )

    instagram.ingest_instagram_source(SOURCE)

    doc, frame_urls = captured[0][0]
    assert frame_urls == ["https://cdn.example/c.jpg"]
    assert doc.url == URL


def test_opted_in_account_asks_the_actor_for_detailed_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, Any]] = []
    monkeypatch.setattr(
        instagram,
        "get_retrieval_config",
        lambda label: {"username": "jordisanildefonso", "image_text": "extract"},
    )
    monkeypatch.setattr(instagram, "pointer_for_source", lambda label, conn=None: None)
    monkeypatch.setattr(
        instagram, "run_actor", lambda actor_input: captured.append(dict(actor_input)) or []
    )

    instagram.fetch_instagram_posts(SOURCE)

    assert instagram.DETAILED_DATA_LEVEL == "detailedData"
    assert captured[0]["dataDetailLevel"] == "detailedData"
    assert captured[0]["maxTotalChargeUsd"] == instagram.MAX_CHARGE_USD


def test_opted_out_account_keeps_basic_data(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[dict[str, Any]] = []
    monkeypatch.setattr(
        instagram,
        "get_retrieval_config",
        lambda label: {"username": "sabrikolod", "image_text": "ignore"},
    )
    monkeypatch.setattr(instagram, "pointer_for_source", lambda label, conn=None: None)
    monkeypatch.setattr(
        instagram, "run_actor", lambda actor_input: captured.append(dict(actor_input)) or []
    )

    instagram.fetch_instagram_posts("ig:sabrikolod")

    assert captured[0]["dataDetailLevel"] == instagram.DATA_DETAIL_LEVEL == "basicData"
    # The default input shape is unchanged for accounts that declare no mode.
    assert instagram.build_actor_input("sabrikolod", None)["dataDetailLevel"] == "basicData"


def test_flow_carries_image_text_keys_without_an_error(
    monkeypatch: pytest.MonkeyPatch, no_engine: None
) -> None:
    documented: list[dict[str, Any]] = []
    monkeypatch.setattr(flows, "_ensure_source_row", lambda label: None)
    monkeypatch.setattr(flows, "get_source", lambda name: {"name": SOURCE, "language": "es"})
    monkeypatch.setattr(
        flows, "get_retrieval_config", lambda name: {"type": "instagram", "username": "x"}
    )
    monkeypatch.setattr(
        flows,
        "ingest_instagram_source",
        lambda label: {
            "inserted": 2,
            "skipped": 0,
            "image_text_frames": 4,
            "image_text_causes": ["3: boom"],
        },
    )
    monkeypatch.setattr(
        flows, "record_ingestion_run", lambda source, result, **kw: documented.append(dict(result))
    )

    result = flows.ingest_source_flow(source_name=SOURCE)

    assert result == {
        "inserted": 2,
        "skipped": 0,
        "image_text_frames": 4,
        "image_text_causes": ["3: boom"],
    }
    assert documented == [dict(result)]
