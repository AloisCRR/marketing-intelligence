"""Cover-only image-text backfill for pre-extract Instagram rows (one-shot ops tool).

Context: `ig:sabrikolod` rows were ingested while the stanza said
`image_text: ignore`, so they ran `basicData` (no `childPosts`) and never
entered the ADR-0014 vision stage. Re-running the lane cannot reach them —
the pointer only fetches newer posts — so this module re-derives frame text
from the already-stored `document_payloads` JSON and writes it into
`document_image_texts` via the lane's own
:func:`marketing_intelligence.image_text.image_texts_for_posts`.

Cover-only (default): only frame index 0 (`displayUrl`) is transcribed.
Pre-extract rows have no carousel children in their stored payload anyway,
so `--all-frames` only matters for rows whose payload already carries
`childPosts`. Full-carousel recovery for `basicData` rows needs a
`detailedData` re-pull (billed) and is out of scope here.

Why `--refresh` exists: stored `displayUrl`s are signed CDN URLs (the
`oh`/`oe` query params expire). Weeks-old rows 403 on download — the first
VPS run transcribed 8/14 with 6 `HTTP Error 403: Forbidden` causes.
Refresh re-scrapes the exact post URLs through the Apify actor
(`username: [<post urls>]` — URL mode, which ignores `resultsLimit` and
`onlyPostsNewerThan` per the actor docs) at `detailedData`, rewrites
`document_payloads` with fresh URLs, then transcribes. About $0.0027 per
detailed post on Free; batches of 10 stay inside the $0.035 run envelope.

Idempotent and rerunnable: candidates are documents of the account with
zero `document_image_texts` rows, and the side-table upsert overwrites in
place. This writes no `ingestion_runs` row — it is not an Ingestion Run.

Run where the secrets live (the `worker` compose service carries both keys;
`api`/`mcp` do not)::

    docker compose exec worker python -m marketing_intelligence.backfill_image_text
    docker compose exec worker python -m marketing_intelligence.backfill_image_text --dry-run
    docker compose exec worker python -m marketing_intelligence.backfill_image_text --refresh
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Iterator
from datetime import datetime
from typing import Any

from marketing_intelligence.db import get_connection
from marketing_intelligence.image_text import enumerate_frame_urls, image_texts_for_posts
from marketing_intelligence.instagram import (
    DETAILED_DATA_LEVEL,
    MAX_CHARGE_USD,
    map_post_to_document,
    run_actor,
)
from marketing_intelligence.normalize import (
    NormalizedDocument,
    coerce_tz_aware,
    make_document,
)
from marketing_intelligence.payloads import write_document_payloads

#: Account whose pre-extract rows get cover text. Override via argv.
SOURCE_DEFAULT = "ig:sabrikolod"

#: Safety bound on rows transcribed per invocation (vision calls are billed).
DEFAULT_LIMIT = 200

#: Post URLs per Apify run in refresh mode: 10 detailed posts ≈ $0.027,
#: inside the $0.035 run envelope.
REFRESH_BATCH_SIZE = 10

#: How many per-frame causes to print; the rest are counted, not dumped.
CAUSE_PRINT_CAP = 20

#: Cap (chars) on a collapsed failure detail carried into a cause.
_CAUSE_CAP = 512

#: Documents of the account with no vision rows yet, newest first
#: (a bounded billing budget spends on the freshest posts).
_CANDIDATES_SQL = """\
SELECT d.url, d.canonical_url, d.title, d.content, d.published_at,
       d.author, d.language, p.payload
  FROM documents d
  JOIN sources s ON s.id = d.source_id
  LEFT JOIN document_payloads p ON p.document_id = d.id
 WHERE s.name = %s
   AND NOT EXISTS (
       SELECT 1 FROM document_image_texts it WHERE it.document_id = d.id
   )
 ORDER BY d.published_at DESC\
"""


def _cause(exc: Exception) -> str:
    """One collapsed, capped failure detail; never a secret or frame bytes."""
    return " ".join(str(exc).split())[:_CAUSE_CAP]


def _coerce_payload(value: Any) -> dict[str, Any] | None:
    """Stored payload as a dict (psycopg returns JSONB as dict; tolerate text)."""
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _coerce_published(value: Any) -> datetime:
    """DB timestamp as tz-aware datetime (naive reads as UTC, never dropped)."""
    if isinstance(value, datetime):
        return coerce_tz_aware(value)
    return coerce_tz_aware(datetime.fromisoformat(str(value)))


def _candidate_pairs(
    rows: list[Any], *, source_label: str, cover_only: bool
) -> tuple[list[tuple[NormalizedDocument, list[str]]], int, int]:
    """Map candidate rows onto `(document, frame_urls)` vision pairs.

    Returns `(pairs, no_payload, no_frames)`: rows without a stored payload
    need a re-scrape (out of scope) and rows without frame URLs have nothing
    to transcribe — both are counted, never billed.
    """
    pairs: list[tuple[NormalizedDocument, list[str]]] = []
    no_payload = 0
    no_frames = 0
    for row in rows:
        url, _canonical, title, content, published_at, author, language, payload_raw = tuple(row)[
            :8
        ]
        payload = _coerce_payload(payload_raw)
        if payload is None:
            no_payload += 1
            continue
        urls = enumerate_frame_urls(payload)
        if not urls:
            no_frames += 1
            continue
        doc = make_document(
            source=source_label,
            url=str(url),
            title=str(title or ""),
            content=str(content or ""),
            published_at=_coerce_published(published_at),
            author=str(author) if author else None,
            language=str(language or "es"),
        )
        pairs.append((doc, urls[:1] if cover_only else list(urls)))
    return pairs, no_payload, no_frames


def build_refresh_input(urls: list[str]) -> dict[str, Any]:
    """Actor input re-scraping exact post URLs (URL mode).

    URL mode ignores `resultsLimit`/`onlyPostsNewerThan` per the actor docs,
    so the input carries only the URL list plus `detailedData` (fresh
    `displayUrl` + `childPosts`), `skipPinnedPosts` and the charge envelope.
    """
    return {
        "username": list(urls),
        "dataDetailLevel": DETAILED_DATA_LEVEL,
        "skipPinnedPosts": False,
        "maxTotalChargeUsd": MAX_CHARGE_USD,
    }


def _chunked(items: list[str], size: int) -> Iterator[list[str]]:
    """Yield `items` in batches of at most `size` (one Apify run per batch)."""
    for index in range(0, len(items), size):
        yield items[index : index + size]


def refresh_and_transcribe(
    source_label: str,
    urls: list[str],
    conn: Any | None = None,
    *,
    cover_only: bool = True,
    batch_size: int = REFRESH_BATCH_SIZE,
) -> tuple[int, int, list[str]]:
    """Re-scrape post URLs for fresh payloads, then transcribe. Never raises.

    Each batch is one billed actor run; the payload side table is rewritten
    before the vision stage so the transcribed URLs are the fresh ones.
    Returns `(posts_refreshed, frames, causes)`: per-batch actor failures and
    per-post mapping failures become single `0:` causes, never a traceback.
    """
    refreshed = 0
    frames = 0
    causes: list[str] = []
    if not urls:
        return (0, 0, [])
    for chunk in _chunked(list(urls), batch_size):
        try:
            posts = run_actor(build_refresh_input(chunk))
        except Exception as exc:
            causes.append(f"0: {_cause(exc)}")
            continue
        docs: list[NormalizedDocument] = []
        fresh: list[dict[str, Any]] = []
        for post in posts:
            try:
                docs.append(map_post_to_document(post, source_label))
                fresh.append(post)
            except Exception as exc:
                causes.append(f"0: {_cause(exc)}")
        refreshed += len(docs)
        if not docs:
            continue
        try:
            write_document_payloads(list(zip(docs, fresh, strict=True)), conn=conn)
        except Exception as exc:
            causes.append(f"0: {_cause(exc)}")
            continue
        pairs = [
            (doc, enumerate_frame_urls(post)[:1] if cover_only else enumerate_frame_urls(post))
            for doc, post in zip(docs, fresh, strict=True)
            if enumerate_frame_urls(post)
        ]
        batch_frames, batch_causes = image_texts_for_posts(pairs, conn=conn)
        frames += batch_frames
        causes.extend(batch_causes)
    return (refreshed, frames, causes)


def _parse_argv(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Transcribe cover frames for pre-extract Instagram rows."
    )
    parser.add_argument("source", nargs="?", default=SOURCE_DEFAULT)
    parser.add_argument(
        "--all-frames",
        action="store_true",
        help="transcribe every enumerated frame, not just the cover",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=f"max rows transcribed per run (default {DEFAULT_LIMIT})",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="re-scrape candidate posts via Apify for fresh frame URLs before "
        "transcribing (fixes 403-expired CDN params; billed)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=REFRESH_BATCH_SIZE,
        help=f"post URLs per Apify run in refresh mode (default {REFRESH_BATCH_SIZE})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="list what would be transcribed without calling the vision lane",
    )
    return parser.parse_args(argv)


def _print_causes(causes: list[str]) -> None:
    """Print a capped sample of causes; the rest are counted, not dumped."""
    for cause in causes[:CAUSE_PRINT_CAP]:
        print(f"  {cause}")
    if len(causes) > CAUSE_PRINT_CAP:
        print(f"  ... and {len(causes) - CAUSE_PRINT_CAP} more")


def main(argv: list[str] | None = None) -> int:
    """Entry point: select candidates, transcribe, write side-table rows."""
    args = _parse_argv(argv)
    if args.limit < 1:
        print(f"limit must be a positive int, got {args.limit}", file=sys.stderr)
        return 2
    if args.batch_size < 1:
        print(f"batch-size must be a positive int, got {args.batch_size}", file=sys.stderr)
        return 2
    if not args.dry_run and not os.environ.get("DEEPINFRA_API_KEY"):
        print("DEEPINFRA_API_KEY is not set; refusing to bill the vision lane", file=sys.stderr)
        return 2
    if args.refresh and not args.dry_run and not os.environ.get("APIFY_API_TOKEN"):
        print("APIFY_API_TOKEN is not set; refusing to run the Instagram lane", file=sys.stderr)
        return 2
    conn = get_connection()
    try:
        cursor = conn.execute(_CANDIDATES_SQL, (args.source,))
        rows = list(cursor.fetchall()) if cursor is not None else []
        if args.refresh:
            urls = [str(row[0]) for row in rows][: args.limit]
            print(
                f"source={args.source} refresh_candidates={len(rows)} "
                f"planned={len(urls)} cover_only={not args.all_frames}"
            )
            if args.dry_run:
                for url in urls[:CAUSE_PRINT_CAP]:
                    print(f"  would refresh {url}")
                if len(urls) > CAUSE_PRINT_CAP:
                    print(f"  ... and {len(urls) - CAUSE_PRINT_CAP} more")
                return 0
            refreshed, frames, causes = refresh_and_transcribe(
                args.source,
                urls,
                conn=conn,
                cover_only=not args.all_frames,
                batch_size=args.batch_size,
            )
            print(f"refreshed={refreshed} image_text_frames={frames} causes={len(causes)}")
            _print_causes(causes)
            return 0
        pairs, no_payload, no_frames = _candidate_pairs(
            rows, source_label=args.source, cover_only=not args.all_frames
        )
        todo = pairs[: args.limit]
        print(
            f"source={args.source} candidates={len(rows)} "
            f"transcribable={len(pairs)} planned={len(todo)} "
            f"no_payload={no_payload} no_frames={no_frames} "
            f"cover_only={not args.all_frames}"
        )
        if args.dry_run:
            for doc, urls in todo[:CAUSE_PRINT_CAP]:
                print(f"  would transcribe {doc.url} ({len(urls)} frame(s))")
            if len(todo) > CAUSE_PRINT_CAP:
                print(f"  ... and {len(todo) - CAUSE_PRINT_CAP} more")
            return 0
        frames, causes = image_texts_for_posts(todo, conn=conn)
        print(f"image_text_frames={frames} causes={len(causes)}")
        _print_causes(causes)
        return 0
    finally:
        try:
            conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
