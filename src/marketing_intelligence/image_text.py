"""Instagram image-text side table (ADR-0014) — vision transcription of frames.

Domain contract (stable): ``write_document_image_texts(pairs, conn=None)``
takes ``(NormalizedDocument, [(frame_index, image_text), ...])`` pairs **after**
the documents exist (call it once ``ingest.upsert_documents`` has run),
resolves each document by `url` then `canonical_url` — the flag/payload-lane
pattern — and upserts one row per ``(document_id, frame_index)`` into
``document_image_texts``. Returns ``(inserted, skipped)``: ``inserted`` counts
applied frame writes (a re-extraction overwrites in place and refreshes
`extracted_at`, never duplicates), ``skipped`` counts frames whose document is
unknown plus per-frame DB failures. An unknown URL never raises — one
unattributable post must not fail an ingest batch — mirroring
:func:`marketing_intelligence.payloads.write_document_payloads`.

``extract_frames(frame_urls)`` is the billing-sensitive half: frames are
downloaded through the shared impersonated fetch and deduped by the SHA-256 of
their **bytes** before any vision call, because the actor's cover
(``displayUrl``) is routinely byte-identical to the first ``childPosts[]``
frame while each vision call is billed. At most :data:`MAX_FRAMES_PER_POST`
unique images are fetched and billed per post — the cap is counted over
deduped images in enumeration order, so the billed set is always the post's
first unique frames and the overflow is reported as per-index causes, never
paid for. Deduped frames all store the same transcription — the side table is
keyed frame by frame, so evidence stays addressable per frame index.
``enumerate_frame_urls(post)`` supplies the frame URLs in display order (cover
first); carousel children exist only in ``detailedData`` actor runs, which the
opted-in lane asks for.

Vision vendor: DeepInfra ``deepseek-ai/DeepSeek-V4.1-Flash``, pinned by id
(never a moving alias), ``reasoning_effort: none``, ``temperature: 0``,
``max_tokens: 1000``, base64 upload (the provider cannot fetch Instagram's CDN
itself) under an exact-transcription prompt whose ``NO_TEXT`` sentinel marks a
frame with nothing to read. ``NO_TEXT`` frames are *stored*, not skipped: the
row is evidence the frame was examined, and only the retrieval
``has_image_text`` flag treats the sentinel as empty. ``documents.content``
stays caption-only — image text is a separate read field.

``DEEPINFRA_API_KEY`` is read from the environment at call time, sent as a
bearer header, and never logged (neither are frame bytes or prompts). Failures
never raise out of this module: :func:`extract_image_text` returns
``(None, cause)`` per call and :func:`image_texts_for_posts` turns each failure
into an ``"<frame_index>: <detail>"`` cause for the lane's run result, so the
vision lane can never fail an Ingestion Run.

Connection handling mirrors ``write_document_payloads``: when ``conn`` is None
a connection is opened via ``marketing_intelligence.db.get_connection`` and
closed here (commit on the way out); an injected connection is committed but
never closed by this module.
"""

from __future__ import annotations

import base64
import hashlib
import os
from typing import Any

import httpx

from marketing_intelligence.db import get_connection
from marketing_intelligence.enrich import fetch_impersonated
from marketing_intelligence.normalize import NormalizedDocument

#: Pinned vision model (DeepInfra). Never a moving alias: a silent swap would
#: change both output and per-frame cost.
MODEL_ID = "deepseek-ai/DeepSeek-V4.1-Flash"

#: Sentinel the model must answer with when a frame carries no readable text.
#: Stored as-is (the row is evidence the frame was examined); only retrieval's
#: ``has_image_text`` treats it as empty.
NO_TEXT = "NO_TEXT"

#: Per-frame budget in seconds for the frame download and for the vision call.
TIMEOUT_SECONDS = 120.0

#: Per-post frame cap (ADR-0014 cost control): at most this many *unique*
#: frames are downloaded and billed per post, in enumeration order (cover
#: first, duplicates free). Every frame past the cap is reported as a cause
#: instead of fetched, so a pathological carousel can never widen the bill.
#: 20 covers the 17-frame pilot (`Dc9PS-SkU6_`) with margin.
MAX_FRAMES_PER_POST = 20

#: Output ceiling per frame: enough for a dense carousel frame, small enough
#: that a degenerate repetition loop (ADR-0014 accuracy floor) stays bounded.
MAX_TOKENS = 1000

#: Deterministic transcription: the model is a transcriber, never a summarizer.
TEMPERATURE = 0

#: Exact-transcription prompt. Anything the model cannot read is left out
#: rather than guessed; a frame with no text at all answers with the sentinel.
PROMPT = (
    "Transcribe every piece of text visible in this image, exactly as it "
    "appears, with nothing added. Preserve the original language, accents, "
    "casing, punctuation, line breaks and emoji. Never translate, summarize, "
    "describe the image, or explain anything. If the image holds no readable "
    f"text at all, reply with exactly {NO_TEXT} and nothing else."
)

#: Stanza value (ADR-0014) that opts an account into the image-text lane:
#: `detailedData` actor runs plus the post-upsert vision stage. Any other value
#: (`ignore`, the v1 default) leaves both off.
IMAGE_TEXT_EXTRACT = "extract"

#: DeepInfra's OpenAI-compatible chat-completions endpoint.
_API_URL = "https://api.deepinfra.com/v1/openai/chat/completions"

#: Environment variable read at call time; never carried in a payload or log.
_API_KEY_ENV = "DEEPINFRA_API_KEY"

#: Cap (chars) on a collapsed failure detail carried into a run result.
_CAUSE_CAP = 512

#: Resolve the stored document id from the pair's URL, exact match first,
#: canonical second (same URL/canonical keying as the flag/payload/read lanes).
_DOCUMENT_ID_SQL = """\
SELECT id FROM documents WHERE url = %s OR canonical_url = %s\
"""

#: One row per frame; a re-extraction overwrites the stored text and refreshes
#: `extracted_at` (013 documents it as the extraction time, so the stamp always
#: describes the text currently stored).
_IMAGE_TEXT_UPSERT_SQL = """\
INSERT INTO document_image_texts (document_id, frame_index, image_text, model)
VALUES (%s, %s, %s, %s)
ON CONFLICT (document_id, frame_index) DO UPDATE
    SET image_text = EXCLUDED.image_text,
        model = EXCLUDED.model,
        extracted_at = now()\
"""


def _cause(exc: Exception) -> str:
    """One collapsed, capped failure detail; never a secret or frame bytes."""
    return " ".join(str(exc).split())[:_CAUSE_CAP]


def _frame_url(value: Any) -> str | None:
    """Trim one actor media URL; blank/missing values yield None."""
    text = str(value or "").strip()
    return text or None


def enumerate_frame_urls(post: dict[str, Any]) -> list[str]:
    """Every frame image URL of one actor post, in display order.

    Index 0 is the post cover (``displayUrl``); the carousel's inner frames
    follow in ``childPosts[].displayUrl`` order. The actor returns `childPosts`
    only for ``detailedData`` runs — the reason the opted-in lane asks for that
    detail level (``basicData`` sends ``childPosts: None`` and hides every
    inner frame). A video frame contributes its ``displayUrl`` poster, never a
    video stream (video decoding is deferred). Malformed entries and blank
    URLs are skipped, and the same image may appear under two URLs (cover and
    first child): the stage's byte dedupe, not this walk, decides what gets
    billed. Never raises — the post payload is untrusted JSON.
    """
    urls: list[str] = []
    cover = _frame_url(post.get("displayUrl"))
    if cover:
        urls.append(cover)
    children = post.get("childPosts")
    if isinstance(children, list):
        for child in children:
            if not isinstance(child, dict):
                continue
            url = _frame_url(child.get("displayUrl"))
            if url:
                urls.append(url)
    return urls


def fetch_frame_bytes(url: str, timeout: float = TIMEOUT_SECONDS) -> bytes:
    """Download one frame through the shared impersonated fetch.

    The vision provider cannot fetch Instagram's CDN itself (ADR-0014 smoke:
    image-URL mode timed out), so the lane downloads the bytes here and uploads
    them base64. Reuses the canonical curl_cffi Chrome identity rather than
    adding a transport; raises the enrich lane's ``FetchFailed`` on
    transport/HTTP failure, which the stage records as a per-frame cause.
    """
    return fetch_impersonated(url, timeout=int(timeout))[1]


def _image_mime(image_bytes: bytes) -> str:
    """Sniff the media type of downloaded frame bytes (JPEG when unknown)."""
    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
        return "image/webp"
    if image_bytes.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if image_bytes[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return "image/jpeg"


def _data_url(image_bytes: bytes) -> str:
    """Base64 data URL for one frame (never the CDN URL: the provider can't fetch it)."""
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{_image_mime(image_bytes)};base64,{encoded}"


def _completion_text(body: Any) -> str | None:
    """Extract the completion text from an OpenAI-shaped envelope, or None."""
    try:
        content = body["choices"][0]["message"]["content"]
    except Exception:
        return None
    if isinstance(content, list):
        content = "".join(str(part.get("text") or "") for part in content if isinstance(part, dict))
    if not isinstance(content, str) or not content.strip():
        return None
    return content.strip()


def _is_no_text(text: str) -> bool:
    """True when the model answered with the sentinel (stray case/punctuation tolerated)."""
    return text.strip(" .\n\t").upper() == NO_TEXT


def extract_image_text(
    image_bytes: bytes, timeout: float = TIMEOUT_SECONDS
) -> tuple[str | None, str | None]:
    """Transcribe one frame through DeepInfra; returns ``(text, cause)``.

    Exactly one of the two is set: a successful call yields
    ``(<transcription>, None)`` — the ``NO_TEXT`` sentinel when the frame holds
    no readable text — while every failure yields ``(None, <cause>)``: a
    missing ``DEEPINFRA_API_KEY``, a transport/HTTP error, a malformed
    envelope, or an empty completion. Never raises, so one bad frame can never
    fail an Ingestion Run; the caller turns each cause into a per-frame entry
    in the run result. The key is read here, at call time, and only ever
    travels in the Authorization header.
    """
    api_key = os.environ.get(_API_KEY_ENV)
    if not api_key:
        return (None, f"{_API_KEY_ENV} is not set; refusing to bill the vision lane")
    payload: dict[str, Any] = {
        "model": MODEL_ID,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": PROMPT},
                    {"type": "image_url", "image_url": {"url": _data_url(image_bytes)}},
                ],
            }
        ],
        "temperature": TEMPERATURE,
        "max_tokens": MAX_TOKENS,
        "reasoning_effort": "none",
    }
    try:
        response = httpx.post(
            _API_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
            timeout=timeout,
        )
        response.raise_for_status()
        body = response.json()
    except Exception as exc:
        return (None, _cause(exc))
    text = _completion_text(body)
    if text is None:
        return (None, "vision model returned no text")
    if _is_no_text(text):
        return (NO_TEXT, None)
    return (text, None)


def extract_frames(frame_urls: list[str]) -> tuple[list[tuple[int, str]], list[str]]:
    """Download and transcribe one post's frames, billing each unique image once.

    Returns ``(rows, causes)``: rows are ``(frame_index, image_text)`` in frame
    order (``NO_TEXT`` frames included — they are stored, not skipped) and
    causes are ``"<frame_index>: <detail>"`` for frames that could not be
    downloaded or transcribed. Frames are deduped by the SHA-256 of their bytes
    *before* any vision call — the cover is routinely byte-identical to the
    first child, and each call is billed — so a duplicate reuses the first
    frame's transcription (or its failure detail) without a second call.

    At most :data:`MAX_FRAMES_PER_POST` unique images are fetched and billed
    per post: the cap is counted over deduped images in enumeration order
    (cover first), so the billed set is always the post's first unique frames.
    Every frame past the cap — duplicates of billed frames included, since
    nothing past the cap is downloaded — is reported as a
    ``"<frame_index>: frame cap (<cap>) exceeded, skipped"`` cause. Never
    raises.
    """
    rows: list[tuple[int, str]] = []
    causes: list[str] = []
    transcribed: dict[str, str] = {}  # digest -> transcription (NO_TEXT included)
    failures: dict[str, str] = {}  # digest -> why that image's call failed
    for frame_index, url in enumerate(frame_urls):
        if len(transcribed) + len(failures) >= MAX_FRAMES_PER_POST:
            causes.append(f"{frame_index}: frame cap ({MAX_FRAMES_PER_POST}) exceeded, skipped")
            continue
        try:
            image_bytes = fetch_frame_bytes(url)
        except Exception as exc:
            causes.append(f"{frame_index}: {_cause(exc)}")
            continue
        digest = hashlib.sha256(image_bytes).hexdigest()
        if digest in failures:
            causes.append(f"{frame_index}: {failures[digest]}")
            continue
        if digest in transcribed:
            rows.append((frame_index, transcribed[digest]))
            continue
        text, cause = extract_image_text(image_bytes)
        if text is None:
            failures[digest] = cause or "vision call failed"
            causes.append(f"{frame_index}: {failures[digest]}")
            continue
        transcribed[digest] = text
        rows.append((frame_index, text))
    return rows, causes


def write_document_image_texts(
    pairs: list[tuple[NormalizedDocument, list[tuple[int, str]]]], conn: Any | None = None
) -> tuple[int, int]:
    """Persist per-frame transcriptions beside their documents. (inserted, skipped).

    Call after ``upsert_documents``: resolution is keyed by the document's
    ``url``/``canonical_url``, so the row must already exist. One row per
    ``(document_id, frame_index)``; a re-extraction overwrites the stored frame
    text in place and refreshes ``extracted_at``. Every frame carries the
    pinned :data:`MODEL_ID`. Unknown documents (and per-frame write failures)
    count as skipped, never raise. When ``conn`` is None a connection is opened
    via ``marketing_intelligence.db.get_connection`` and closed here; an
    injected connection is committed but left open (fake connections welcome in
    tests).
    """
    if not pairs:
        return (0, 0)
    owns_connection = False
    if conn is None:
        conn = get_connection()
        owns_connection = True
    assert conn is not None
    try:
        inserted = 0
        skipped = 0
        for doc, frames in pairs:
            try:
                cursor = conn.execute(_DOCUMENT_ID_SQL, (doc.url, doc.canonical_url))
                row = cursor.fetchone() if cursor is not None else None
                document_id = row[0] if row is not None else None
            except Exception:
                skipped += len(frames)
                continue
            if document_id is None:
                skipped += len(frames)
                continue
            for frame_index, image_text in frames:
                try:
                    conn.execute(
                        _IMAGE_TEXT_UPSERT_SQL,
                        (document_id, frame_index, image_text, MODEL_ID),
                    )
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


def image_texts_for_posts(
    pairs: list[tuple[NormalizedDocument, list[str]]], conn: Any | None = None
) -> tuple[int, list[str]]:
    """Run the whole vision stage for one lane batch. Returns ``(frames, causes)``.

    ``pairs`` is ``(document, frame_urls)`` with URLs straight from
    :func:`enumerate_frame_urls`. Every frame is downloaded (up to
    :data:`MAX_FRAMES_PER_POST` unique frames per post — the overflow is
    reported, never fetched), byte-deduped, transcribed and written;
    ``frames`` counts the side-table rows applied
    (unknown documents reduce it) and ``causes`` holds ``"<frame_index>:
    <detail>"`` entries for frames that could not be transcribed, plus a single
    ``0:`` entry when the stage itself fails (opening a connection, writing) —
    index 0 stands for the frame index the post's payload hangs off. Never
    raises, and a clean all-``NO_TEXT`` post is a result, not a failure.
    """
    if not pairs:
        return (0, [])
    rows: list[tuple[NormalizedDocument, list[tuple[int, str]]]] = []
    causes: list[str] = []
    for doc, frame_urls in pairs:
        try:
            frames, frame_causes = extract_frames(list(frame_urls))
        except Exception as exc:  # defensive: one unreadable post payload
            causes.append(f"0: {_cause(exc)}")
            continue
        causes.extend(frame_causes)
        if frames:
            rows.append((doc, frames))
    if not rows:
        return (0, causes)
    try:
        inserted, _skipped = write_document_image_texts(rows, conn=conn)
    except Exception as exc:
        causes.append(f"0: {_cause(exc)}")
        return (0, causes)
    return (inserted, causes)
