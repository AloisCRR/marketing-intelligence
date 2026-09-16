"""Instagram premium lane (ADR-0013) — pointer-based caption ingestion.

One `sources` row per account (`ig:<handle>`), ingested through the
fixed-purpose ``apify/instagram-post-scraper`` actor instead of a feed. The
lane is deliberately narrow and deterministic:

- **Pointer, derived, never stored.** The high-water mark is
  ``MAX(documents.published_at)`` for the account row. Run 0 (no pointer) is a
  bounded bootstrap (``resultsLimit: 10``, no date filter); run N asks for
  ``resultsLimit: 15`` newer than ``pointer - 60s``. The 60s overlap re-emits
  one post per run for zero-miss; the date filter is *not* a strict
  newer-than (smoke: 4 items for a 1-post window), so steady runs also re-emit
  filter bleed. Both are absorbed by the documents UNIQUE constraint
  (``ON CONFLICT DO NOTHING``) and surface as `skipped`.
- **Caption is final.** The lane never calls trafilatura / the Jina reader /
  Firecrawl, and `ingest_source_flow` skips enrichment entirely for it: a
  short caption would otherwise read as "thin" and burn the extractor chain on
  data destruction.
- **Raw post JSON is kept.** Every run stores the full post result beside its
  document (``payloads.write_document_payloads``, after the upsert) so the
  deferred comment/metric/image/hashtag work needs no re-scrape. Nothing is
  ever dropped ingest-side: every billed post is upserted, and the
  `hashtag_filter` stanza is a query-time hint only.
- **Image text is a separate, opted-in stage (ADR-0014).** The lane ends with
  fetch → map → upsert → payload write → image-text stage. The stage runs only
  when the stanza says ``image_text: extract`` (which also switches the actor
  run to ``detailedData`` — ``basicData`` returns ``childPosts: None``, hiding
  every carousel frame); it downloads each frame, byte-dedupes, transcribes
  and writes the per-frame side table, and its failures become per-frame
  ``image_text_causes`` in the run result instead of failing the run.
- **Secret discipline.** ``APIFY_API_TOKEN`` is read from the environment at
  call time, sent as a bearer header, and never logged; build/run inputs are
  never logged either. ``DEEPINFRA_API_KEY`` follows the same rule inside
  ``image_text``.
"""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from marketing_intelligence.db import get_connection
from marketing_intelligence.image_text import (
    IMAGE_TEXT_EXTRACT,
    enumerate_frame_urls,
    image_texts_for_posts,
)
from marketing_intelligence.ingest import upsert_documents
from marketing_intelligence.normalize import (
    NormalizedDocument,
    canonicalize_url,
    coerce_tz_aware,
    content_hash_for,
    normalize_text,
)
from marketing_intelligence.payloads import write_document_payloads
from marketing_intelligence.sources import get_retrieval_config

#: Apify actor identity (owner/name); the REST path spells it `owner~name`.
ACTOR_ID = "apify/instagram-post-scraper"

#: Run 0 gets one bounded seed page; every later run is capped at 15 results
#: regardless of how much the account posts between runs.
BOOTSTRAP_LIMIT = 10
STEADY_LIMIT = 15

#: Pointer overlap: steady runs ask for posts newer than `pointer - 60s` so a
#: post published just before the previous run can never be missed. The
#: re-emitted overlap (and the actor's non-strict date filter) dedupes via the
#: documents UNIQUE constraint — visible as `skipped`, never as duplicates.
OVERLAP_SECONDS = 60

#: `basicData` keeps one billed `result` event per post; the actor's
#: `detailedData` default bills an extra `post-details` event on top.
DATA_DETAIL_LEVEL = "basicData"

#: `detailedData` is mandatory for carousels (ADR-0014): `basicData` returns
#: `childPosts: None`, so inner frames are invisible. Only accounts opted into
#: the image-text lane pay the extra detail event.
DETAILED_DATA_LEVEL = "detailedData"

#: Per-run spend envelope. Apify enforces this on the run
#: (`maxTotalChargeUsd`); the actor's platform minimum
#: (`minimalMaxTotalChargeUsd`) is $0.0027, so this sits well above the floor.
MAX_CHARGE_USD = 0.035

#: Apify run-sync endpoints cap at 300 s server-side; match it client-side.
RUN_TIMEOUT_SECONDS = 300.0

_API_BASE = "https://api.apify.com/v2"

#: Post permalink path prefix for a shortcode when the actor omitted `url`.
_PERMALINK = "https://www.instagram.com/p/{code}/"

_REEL_PREFIX_RE = re.compile(r"^/reel/")

_POINTER_SQL = (
    "SELECT MAX(published_at) FROM documents "
    "WHERE source_id = (SELECT id FROM sources WHERE name = %s)"
)


def canonical_instagram_url(raw: str) -> str:
    """Canonical post permalink: ``/p/<code>/``, no query/fragment, lower host.

    Reels share the post namespace, so ``/reel/<code>/`` normalizes onto the
    same ``/p/<code>/`` identity (the ADR's fixed identity rule) — else one
    post could occupy two rows. Scheme and host casing plus any missing
    trailing slash are normalized; the path keeps its case, which is
    significant in a shortcode.
    """
    parts = urlsplit(raw.strip())
    path = _REEL_PREFIX_RE.sub("/p/", parts.path)
    if path and not path.endswith("/"):
        path += "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, "", ""))


def instagram_title_for(caption: str | None, handle: str, shortcode: str) -> str:
    """Title rule: first non-empty caption line, else ``@handle — <shortcode>``.

    Every candidate is whitespace-collapsed (same normalization the hash
    uses), so the same post re-maps to the same title — and therefore the same
    `content_hash` — across re-runs.
    """
    for line in (caption or "").splitlines():
        collapsed = normalize_text(line)
        if collapsed:
            return collapsed
    return normalize_text(f"@{handle} — {shortcode}")


def _parse_ig_timestamp(value: Any) -> datetime:
    """Parse an actor ISO timestamp into a tz-aware UTC datetime."""
    text = str(value or "").strip()
    if not text:
        raise ValueError("Instagram post is missing a timestamp")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"unparseable Instagram timestamp: {text!r}") from exc
    return coerce_tz_aware(parsed)


def map_post_to_document(
    post: dict[str, Any], source_label: str = "ig:sabrikolod"
) -> NormalizedDocument:
    """Map one actor post result onto the shared document contract.

    Identity is the canonical permalink; title follows the ADR rule; caption →
    `content` (as-is, only outer whitespace trimmed — a caption containing
    ``<`` is never treated as HTML), owner handle → `author`, actor ISO
    `timestamp` → `published_at`, language is the account's Spanish. The hash
    is the repo's standard title+content fingerprint, so a re-mapped post
    dedupes identically.
    """
    shortcode = str(post.get("shortCode") or "").strip()
    raw_url = str(post.get("url") or "").strip()
    url = canonical_instagram_url(raw_url or _PERMALINK.format(code=shortcode))
    caption = str(post.get("caption") or "")
    handle = str(post.get("ownerUsername") or "").strip()
    title = instagram_title_for(caption, handle, shortcode)
    content = caption.strip()
    return NormalizedDocument(
        source=source_label,
        url=url,
        canonical_url=canonicalize_url(url),
        title=title,
        author=handle or None,
        published_at=_parse_ig_timestamp(post.get("timestamp")),
        retrieved_at=datetime.now(UTC),
        language="es",
        content=content,
        content_hash=content_hash_for(title, content),
    )


def pointer_for_source(source_label: str, conn: Any | None = None) -> datetime | None:
    """Derived high-water mark: newest `published_at` for the account's rows.

    ``None`` means "first run" (bootstrap). Never a stored cursor: the pointer
    is recomputed from the documents table, so re-runs and partial batches
    can't desync it. An injected connection is left open; otherwise one is
    opened and closed here.
    """
    owns_connection = False
    if conn is None:
        conn = get_connection()
        owns_connection = True
    assert conn is not None
    try:
        cursor = conn.execute(_POINTER_SQL, (source_label,))
        row = cursor.fetchone() if cursor is not None else None
        if row is None or row[0] is None:
            return None
        return coerce_tz_aware(row[0])
    finally:
        if owns_connection:
            try:
                conn.close()
            except Exception:
                pass


def _username_from_label(source_label: str) -> str:
    """`ig:<handle>` → `<handle>`; any other label passes through."""
    _, sep, handle = source_label.partition(":")
    return handle if sep and handle else source_label


def build_actor_input(
    username: str,
    pointer: datetime | None,
    data_detail_level: str = DATA_DETAIL_LEVEL,
) -> dict[str, Any]:
    """Build the actor input for a bootstrap (no pointer) or steady run.

    Every input carries `resultsLimit` (the actor has no documented default),
    the pinned `dataDetailLevel`, an explicit `skipPinnedPosts` and the
    per-run charge envelope. A steady run adds the ISO `onlyPostsNewerThan`
    boundary at ``pointer - OVERLAP_SECONDS``. ``data_detail_level`` defaults
    to the cheap `basicData`; the image-text lane's opted-in accounts pass
    `detailedData`, which is the only level that returns `childPosts`.
    """
    actor_input: dict[str, Any] = {
        "username": [username],
        "resultsLimit": BOOTSTRAP_LIMIT if pointer is None else STEADY_LIMIT,
        "dataDetailLevel": data_detail_level,
        "skipPinnedPosts": False,
        "maxTotalChargeUsd": MAX_CHARGE_USD,
    }
    if pointer is not None:
        since = (pointer - timedelta(seconds=OVERLAP_SECONDS)).astimezone(UTC)
        actor_input["onlyPostsNewerThan"] = since.strftime("%Y-%m-%dT%H:%M:%SZ")
    return actor_input


def run_actor(actor_input: dict[str, Any]) -> list[dict[str, Any]]:
    """Run the actor synchronously and return its dataset items.

    The token is read from the environment *at call time* and sent as a
    bearer header (never a query parameter, so it cannot leak into a URL in an
    error message). Missing token or a non-list payload raises explicitly —
    the lane never degrades to a different transport.
    """
    token = os.environ.get("APIFY_API_TOKEN")
    if not token:
        raise RuntimeError("APIFY_API_TOKEN is not set; refusing to run the Instagram lane")
    body = dict(actor_input)
    charge = body.pop("maxTotalChargeUsd", MAX_CHARGE_USD)
    url = f"{_API_BASE}/acts/{ACTOR_ID.replace('/', '~')}/run-sync-get-dataset-items"
    response = httpx.post(
        url,
        params={"maxTotalChargeUsd": charge},
        headers={"Authorization": f"Bearer {token}"},
        json=body,
        timeout=RUN_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    items = response.json()
    if not isinstance(items, list):
        raise RuntimeError("Apify actor returned a non-list dataset payload")
    return [item for item in items if isinstance(item, dict)]


def _image_text_opted_in(config: dict[str, Any]) -> bool:
    """ADR-0014 opt-in flag: the stanza's ``image_text`` mode.

    One decision point for both halves of the opt-in — the actor's
    `dataDetailLevel` (carousel frames need `detailedData`) and whether the
    post-upsert vision stage runs. Never raises: a stanza without the key, or
    with any other value, is `ignore` (the v1 default).
    """
    return config.get("image_text") == IMAGE_TEXT_EXTRACT


def fetch_instagram_posts(
    source_label: str, conn: Any | None = None
) -> tuple[list[dict[str, Any]], int]:
    """Pointer → actor input → run → ingest-all.

    Every billed post is returned for upsert + payload write; nothing is
    dropped ingest-side. The `hashtag_filter` stanza is a query-time hint
    only (caption holds `#tag` text, payload JSONB holds `hashtags[]`), so
    filtering costs zero extra billing. Returns ``(posts, raw_count)`` where
    `raw_count` is the actor's result count (the billing/observability
    number) — always ``len(posts)`` since nothing is filtered. Accounts opted
    into the image-text lane (``image_text: extract``) run `detailedData`, the
    only level that returns a carousel's `childPosts` frames; everyone else
    keeps the cheaper `basicData` default.
    """
    config = get_retrieval_config(source_label)
    username = str(config.get("username") or "").strip() or _username_from_label(source_label)
    detail_level = DETAILED_DATA_LEVEL if _image_text_opted_in(config) else DATA_DETAIL_LEVEL
    pointer = pointer_for_source(source_label, conn)
    items = run_actor(build_actor_input(username, pointer, detail_level))
    return items, len(items)


#: Cap (chars) on a collapsed failure detail carried into the run result.
_CAUSE_CAP = 512


def _cause_text(exc: Exception) -> str:
    """One collapsed, capped failure detail; never secrets."""
    return " ".join(str(exc).split())[:_CAUSE_CAP]


def _image_text_stage(
    docs: list[NormalizedDocument], posts: list[dict[str, Any]]
) -> dict[str, Any]:
    """ADR-0014 vision stage: per-frame transcription into the side table.

    Runs after the upsert *and* after the payload write, so the documents it
    resolves against already exist and the raw post JSON it reads the frame
    URLs from is already stored. Returns only the nonzero additions —
    ``image_text_frames`` (rows applied) and ``image_text_causes``
    (``"<frame_index>: <detail>"``) — so a clean or non-opted-in run keeps its
    exact ``{inserted, skipped}`` shape. Never raises: a stage failure degrades
    to a single cause, never a failed Ingestion Run.
    """
    try:
        pairs = [(doc, enumerate_frame_urls(post)) for doc, post in zip(docs, posts, strict=True)]
        frames, causes = image_texts_for_posts(pairs)
    except Exception as exc:
        frames, causes = 0, [f"0: {_cause_text(exc)}"]
    stage: dict[str, Any] = {}
    if frames:
        stage["image_text_frames"] = frames
    if causes:
        stage["image_text_causes"] = list(causes)
    return stage


def ingest_instagram_source(source_label: str) -> dict[str, Any]:
    """Ingest one Instagram account: fetch → map → upsert → payload → image text.

    Returns ``{"inserted": n, "skipped": n}`` (both from the documents upsert;
    payload writes are idempotent overwrites) and is rerunnable — the pointer
    is derived from stored documents and duplicates dedupe. Accounts opted into
    the image-text lane add the nonzero-only ``image_text_frames`` /
    ``image_text_causes``; that stage runs outside the fetch/upsert guard, so a
    vision failure can only add causes and never costs the run its
    ``{inserted, skipped}`` accounting. Any fetch/map/upsert failure returns
    the explicit ``{"inserted": 0, "skipped": 0, "error": ...}`` shape so the
    flow can finish the run without raising a red task.
    """
    try:
        posts, _raw_count = fetch_instagram_posts(source_label)
        docs = [map_post_to_document(post, source_label) for post in posts]
        inserted, skipped = upsert_documents(docs)
        write_document_payloads(list(zip(docs, posts, strict=True)))
    except Exception as exc:
        return {"inserted": 0, "skipped": 0, "error": _cause_text(exc)}
    result: dict[str, Any] = {"inserted": inserted, "skipped": skipped}
    if _image_text_opted_in(get_retrieval_config(source_label)):
        result.update(_image_text_stage(docs, posts))
    return result
