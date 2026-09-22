"""Cover-only image-text backfill tests (one-shot ops module).

Fake-only: no Postgres, no network. Pins the candidate mapping contract —
stored payloads become single-cover vision pairs, payload-less and
frame-less rows are counted rather than billed — and the refresh path —
URL-mode actor input, payload rewrite before transcription, failure
containment.
"""

from __future__ import annotations

import datetime as _dt
import os
import sys
from typing import Any

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_SRC = os.path.join(_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import marketing_intelligence.backfill_image_text as backfill  # noqa: E402
import marketing_intelligence.instagram as instagram  # noqa: E402

SOURCE = "ig:sabrikolod"


def _utc(*args: int) -> _dt.datetime:
    return _dt.datetime(*args, tzinfo=_dt.UTC)


def _row(
    code: str,
    payload: Any,
    published: _dt.datetime | None = None,
) -> tuple[Any, ...]:
    return (
        f"https://www.instagram.com/p/{code}/",
        f"https://www.instagram.com/p/{code}/",
        f"Title {code}",
        f"caption {code}",
        published or _utc(2026, 9, 1, 12, 0),
        "sabrikolod",
        "es",
        payload,
    )


def _post(
    code: str,
    caption: str = "caption",
    ts: str = "2026-09-01T12:00:00.000Z",
    display: str = "https://cdn.example/fresh.jpg",
) -> dict[str, Any]:
    return {
        "url": f"https://www.instagram.com/p/{code}/",
        "shortCode": code,
        "ownerUsername": "sabrikolod",
        "caption": caption,
        "timestamp": ts,
        "displayUrl": display,
    }


def test_cover_only_pair_uses_first_frame_only() -> None:
    payload = {
        "displayUrl": "https://cdn.example/cover.jpg",
        "childPosts": [
            {"displayUrl": "https://cdn.example/inner1.jpg"},
            {"displayUrl": "https://cdn.example/inner2.jpg"},
        ],
    }
    pairs, no_payload, no_frames = backfill._candidate_pairs(
        [_row("AAA", payload)], source_label=SOURCE, cover_only=True
    )
    assert no_payload == 0
    assert no_frames == 0
    assert len(pairs) == 1
    doc, urls = pairs[0]
    assert urls == ["https://cdn.example/cover.jpg"]
    assert doc.url == "https://www.instagram.com/p/AAA/"


def test_all_frames_pair_keeps_every_enumerated_frame() -> None:
    payload = {
        "displayUrl": "https://cdn.example/cover.jpg",
        "childPosts": [{"displayUrl": "https://cdn.example/inner1.jpg"}],
    }
    pairs, _, _ = backfill._candidate_pairs(
        [_row("BBB", payload)], source_label=SOURCE, cover_only=False
    )
    assert len(pairs) == 1
    assert pairs[0][1] == [
        "https://cdn.example/cover.jpg",
        "https://cdn.example/inner1.jpg",
    ]


def test_missing_payload_and_frameless_rows_are_counted_not_billed() -> None:
    rows = [
        _row("NOPAY", None),
        _row("NOFRAMES", {"caption": "no media keys"}),
    ]
    pairs, no_payload, no_frames = backfill._candidate_pairs(
        rows, source_label=SOURCE, cover_only=True
    )
    assert pairs == []
    assert no_payload == 1
    assert no_frames == 1


def test_text_payload_coerces_and_garbage_drops() -> None:
    good = _row("TXT", '{"displayUrl": "https://cdn.example/c.jpg"}')
    bad = _row("BAD", "{not json")
    pairs, no_payload, _ = backfill._candidate_pairs(
        [good, bad], source_label=SOURCE, cover_only=True
    )
    assert len(pairs) == 1
    assert pairs[0][1] == ["https://cdn.example/c.jpg"]
    assert no_payload == 1


def test_refresh_input_is_url_mode_with_detailed_data() -> None:
    urls = ["https://www.instagram.com/p/AAA/", "https://www.instagram.com/p/BBB/"]
    actor_input = backfill.build_refresh_input(urls)
    assert actor_input["username"] == urls
    assert actor_input["dataDetailLevel"] == instagram.DETAILED_DATA_LEVEL
    assert "resultsLimit" not in actor_input
    assert "onlyPostsNewerThan" not in actor_input


def test_refresh_rewrites_payload_then_transcribes(monkeypatch: Any) -> None:
    """One URL-mode run: fresh payload written before the vision stage runs."""
    posts = [_post("AAA", display="https://cdn.example/fresh.jpg")]
    seen_inputs: list[dict[str, Any]] = []
    written: list[list[tuple[Any, dict[str, Any]]]] = []
    transcribed: list[list[tuple[Any, list[str]]]] = []

    def fake_run_actor(actor_input: dict[str, Any]) -> list[dict[str, Any]]:
        seen_inputs.append(dict(actor_input))
        return posts

    def fake_write(pairs: list[tuple[Any, dict[str, Any]]], conn: Any = None) -> tuple[int, int]:
        written.append(list(pairs))
        return (len(pairs), 0)

    def fake_texts(pairs: list[tuple[Any, list[str]]], conn: Any = None) -> tuple[int, list[str]]:
        transcribed.append(list(pairs))
        return (1, [])

    monkeypatch.setattr(backfill, "run_actor", fake_run_actor)
    monkeypatch.setattr(backfill, "write_document_payloads", fake_write)
    monkeypatch.setattr(backfill, "image_texts_for_posts", fake_texts)
    refreshed, frames, causes = backfill.refresh_and_transcribe(
        SOURCE, ["https://www.instagram.com/p/AAA/"], conn=object()
    )
    assert refreshed == 1
    assert frames == 1
    assert causes == []
    assert len(seen_inputs) == 1
    assert seen_inputs[0]["username"] == ["https://www.instagram.com/p/AAA/"]
    # Payload rewrite precedes transcription, on the fresh displayUrl.
    assert len(written) == 1
    assert written[0][0][1]["displayUrl"] == "https://cdn.example/fresh.jpg"
    assert len(transcribed) == 1
    assert transcribed[0][0][1] == ["https://cdn.example/fresh.jpg"]


def test_refresh_failure_is_a_cause_not_a_raise(monkeypatch: Any) -> None:
    """A dead batch degrades to one cause; later batches still run."""

    def fake_run_actor(actor_input: dict[str, Any]) -> list[dict[str, Any]]:
        raise RuntimeError("actor exploded")

    monkeypatch.setattr(backfill, "run_actor", fake_run_actor)
    refreshed, frames, causes = backfill.refresh_and_transcribe(
        SOURCE,
        ["https://www.instagram.com/p/AAA/", "https://www.instagram.com/p/BBB/"],
        conn=object(),
        batch_size=1,
    )
    assert refreshed == 0
    assert frames == 0
    assert len(causes) == 2
    assert all(cause.startswith("0: ") for cause in causes)


def test_refresh_empty_urls_bills_nothing(monkeypatch: Any) -> None:
    def fake_run_actor(actor_input: dict[str, Any]) -> list[dict[str, Any]]:
        raise AssertionError("no actor run expected")

    monkeypatch.setattr(backfill, "run_actor", fake_run_actor)
    assert backfill.refresh_and_transcribe(SOURCE, [], conn=object()) == (0, 0, [])
