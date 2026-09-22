"""Cover-only image-text backfill tests (one-shot ops module).

Fake-only: no Postgres, no network. Pins the candidate mapping contract —
stored payloads become single-cover vision pairs, payload-less and
frame-less rows are counted rather than billed.
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
