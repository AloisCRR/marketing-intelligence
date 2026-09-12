"""Thin MCP adapter over marketing_intelligence.service (Ticket 05).

Same validated payloads as the HTTP API by construction: both tools call the
shared service functions. Run directly (never `python -m mcp.server` — the
local `src/mcp/` dir intentionally has no `__init__.py` so the installed
`mcp` distribution keeps winning plain `import mcp`):

    uv run python src/mcp/server.py                 # stdio (local `make mcp`)
    uv run python src/mcp/server.py --http          # streamable HTTP (local only)

Deployment serves the streamable-HTTP app (`http_app`, or a fresh instance via
`create_http_app()`) as plain HTTP behind Traefik, which terminates TLS. When
`BRAIN_API_TOKEN` is set, HTTP requests must carry
`Authorization: Bearer <token>` (enforced via `StaticTokenVerifier` as
`FastMCP(token_verifier=...)`); stdio is unaffected. When unset, auth is
disabled (local-dev open mode).
"""

from __future__ import annotations

import json
import os
import sys
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from marketing_intelligence import service
from marketing_intelligence.auth import StaticTokenVerifier, is_auth_configured, mcp_auth_settings
from marketing_intelligence.healthcheck import (
    health_payload,  # noqa: F401  (installs access-log filter)
)
from marketing_intelligence.service import DEFAULT_PERIOD_LIMIT, DEFAULT_SEARCH_LIMIT

SERVER_INSTRUCTIONS = (
    "Marketing Intelligence is a persistent marketing/GenZ/culture/tech intelligence platform "
    "(not a newsletter generator): deterministic ingestion feeds a Postgres system of record, "
    "exposed here through a semantic layer for an AI digest consumer. "
    "You are a consumer of this layer — discover evidence with search_articles, read full text "
    "with get_article, and never own or re-run scraping/ingestion yourself. "
    "For any period synthesis, call get_period_context(from_date, to_date) with "
    "ISO dates (YYYY-MM-DD); its recent_articles list is recency-ordered by default "
    "(each item carries its rank position, an ordering signal, not an importance "
    "score). Pass min_importance to keep only Documents whose latest score clears "
    "the floor (the bundle then orders importance-first) and topics to keep only "
    "Documents carrying at least one canonical Topic; per_source_limit spreads the "
    "result across sources, so a pillar-filtered, importance-ranked, source-balanced "
    "week is one call. Annotated filters never touch unannotated Documents unless "
    "you ask: a NULL score or empty Topic set is excluded only when the matching "
    "filter is set. Draft only from what that bundle contains — keep provenance "
    "URLs attached. "
    "A digest is just one usage of a bundle: the caller picks any range and builds "
    "the digest from it — there is no built-in schedule. Record which Documents a "
    "digest selected with record_digest_picks(digest_date, urls, reporter), read "
    "that trace back with get_digest_picks, and capture human edits by re-recording "
    "the edited URL set — its added/removed diff is the re-scoring signal. "
    "clear_digest_picks drops a discarded edition's trace. "
    "Use flag_extraction only to report improperly extracted content (thin body, JS shell, "
    "paywall challenge, truncated text, wrong body, unrecoverable fetch) — never for factual "
    "disagreements. Ingestion files `unrecoverable` itself when the whole article-content "
    "chain fails; clear it once you verify the body. "
    "Use set_importance to record how much a Document matters (0-1 score + rationale + "
    "reporter); flagged or paywalled Documents are capped at 0.3 server-side. "
    "Use list_vocabulary to discover canonical Topics, then set_document_topics to tag "
    "Documents with canonical pillar/region/content-type slugs (synonyms are "
    "canonicalized server-side, unknown tags rejected). "
    "Prefer read-only tools for exploration; flag_extraction, mark_article_read, "
    "set_importance, set_document_topics, record_digest_picks and "
    "clear_digest_picks are the mutating tools."
)


def _transport_security() -> TransportSecuritySettings:
    """DNS-rebinding allowlist: public Traefik domain + internal compose DNS.

    Extra hosts via MCP_ALLOWED_HOSTS (comma-separated, Dokploy env).
    Passed as FastMCP's `transport_security=` constructor setting so both
    `streamable_http_app()` and `mcp.run()` enforce the same allowlist.
    """
    extra = [h.strip() for h in os.environ.get("MCP_ALLOWED_HOSTS", "").split(",") if h.strip()]
    return TransportSecuritySettings(
        allowed_hosts=[
            "marketing-intelligence.services.aloiscrr.dev",
            "marketing-intelligence.services.aloiscrr.dev:*",
            "marketing-intelligence-mcp",
            "marketing-intelligence-mcp:8124",
            "localhost:*",
            "127.0.0.1:*",
            *extra,
        ]
    )


def create_mcp() -> FastMCP:
    """Build the shared FastMCP server.

    Reads `BRAIN_API_TOKEN` at call time so tests/rotation get a fresh
    verdict; pass the result to `create_http_app` or serve `http_app`.
    FastMCP 1.29.1 API: `FastMCP(name, auth=AuthSettings(...),
    token_verifier=...)` — `auth` is required alongside `token_verifier`.
    """
    if is_auth_configured():
        return FastMCP(
            "Marketing Intelligence",
            instructions=SERVER_INSTRUCTIONS,
            auth=mcp_auth_settings(),
            token_verifier=StaticTokenVerifier(),
            transport_security=_transport_security(),
        )
    return FastMCP(
        "Marketing Intelligence",
        instructions=SERVER_INSTRUCTIONS,
        transport_security=_transport_security(),
    )


mcp = create_mcp()


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False))
def search_articles(
    keyword: Annotated[str, "Keyword to search in titles/bodies (non-blank)."],
    limit: Annotated[int, "Max results, 1-100."] = DEFAULT_SEARCH_LIMIT,
    exclude_read: Annotated[bool, "When true, hide read articles."] = False,
    min_importance: Annotated[
        float | None,
        "Importance floor in [0,1]; unannotated articles are excluded only when set.",
    ] = None,
    topics: Annotated[
        list[str] | None,
        "Topic filter (canonical slugs or accepted synonyms); unannotated excluded only when set.",
    ] = None,
) -> list[dict[str, Any]]:
    """Search stored articles by keyword, newest first.

    Use for discovery: find candidate evidence before reading full text with
    get_article. This is the first step of every research workflow. Optional
    annotation filters target deep dives: `min_importance` keeps only articles
    whose latest score clears the floor (ordering becomes importance-first),
    and `topics` keeps only articles carrying at least one of the given tags
    (server-canonicalized; unknown tags are rejected). Unannotated articles
    (NULL score / no topics) are excluded only when the matching filter is
    set — never silently dropped from an unfiltered search and never silently
    top-ranked.

    Args:
        keyword: Non-blank search term matched against titles/bodies.
        limit: Max articles to return, 1-100 (default 20).
        exclude_read: When True, hide read articles (default False annotates only).
        min_importance: Optional floor in [0, 1] on the latest importance score.
        topics: Optional canonical Topic slugs or accepted synonyms; a match
            needs at least one of them.

    Returns:
        List of article dicts (19 keys: 7 base + 4 extraction-flag + 3 read
        + 4 importance + 1 topics),
        ordered newest first (importance-first when a floor is set); empty
        list when nothing matches.

    Raises:
        InvalidRequest: If keyword is blank, limit is outside 1-100,
            min_importance is outside [0, 1], or a topic tag is unknown.
    """
    return service.search_articles(
        keyword,
        limit=limit,
        exclude_read=exclude_read,
        min_importance=min_importance,
        topics=topics,
    )


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False))
def get_period_context(
    from_date: Annotated[str, "Range start, ISO date YYYY-MM-DD (inclusive)."],
    to_date: Annotated[str, "Range end, ISO date YYYY-MM-DD (inclusive)."],
    sources: Annotated[
        list[str] | None, "Optional source filter; unknown names are rejected."
    ] = None,
    limit: Annotated[int, "Max articles in bundle, 1-100."] = DEFAULT_PERIOD_LIMIT,
    exclude_read: Annotated[bool, "When true, hide read articles."] = False,
    per_source_limit: Annotated[
        int | None,
        "Optional cap on items from any one source, 1-100; None means no per-source cap.",
    ] = None,
    min_importance: Annotated[
        float | None,
        "Importance floor in [0,1]; unannotated articles are excluded only when set.",
    ] = None,
    topics: Annotated[
        list[str] | None,
        "Topic filter (canonical slugs or accepted synonyms); unannotated excluded only when set.",
    ] = None,
) -> dict[str, Any]:
    """Fetch the evidence bundle for a date range.

    Use for period synthesis: draft exclusively from the returned
    recent_articles — each item carries a 1-based `rank` (its position in the
    final returned order, not an importance score). Keep URLs/provenance
    attached and separate observations from interpretations. A digest is just
    the case where the caller picks a range (often a week).

    The annotation filters compose in one call: `min_importance` keeps only
    Documents whose latest score clears the floor and orders the bundle
    importance-first (ties by recency); `topics` keeps only Documents carrying
    at least one of the given tags (server-canonicalized; unknown tags are
    rejected); `per_source_limit` spreads the result across sources. Without a
    floor the bundle stays purely recency-ordered. Unannotated Documents (NULL
    score / no topics) are excluded only when the matching filter is set —
    never silently dropped or silently top-ranked.

    Args:
        from_date: Range start as ISO date (YYYY-MM-DD, inclusive).
        to_date: Range end as ISO date (YYYY-MM-DD, inclusive).
        sources: Optional allowlist of source names; None means all sources.
        limit: Max articles in the bundle, 1-100 (default 50).
        exclude_read: When True, hide read articles (default False annotates only).
        per_source_limit: Optional cap of 1-100 on articles any single source
            may contribute; None (default) means no per-source cap. Slots a
            capped source cannot fill go to other sources; composes with
            `sources` and `limit`.
        min_importance: Optional floor in [0, 1] on the latest importance score.
        topics: Optional canonical Topic slugs or accepted synonyms; a match
            needs at least one of them.

    Returns:
        Evidence-bundle dict with `period` and a `recent_articles` list
        (recency-ordered, or importance-ordered when a floor is set); no empty
        analytics placeholders.

    Raises:
        InvalidRequest: If dates are malformed/unordered, sources unknown,
            limit/per_source_limit is outside 1-100, min_importance is outside
            [0, 1], or a topic tag is unknown.
    """
    return service.get_period_context(
        from_date,
        to_date,
        sources=sources,
        limit=limit,
        exclude_read=exclude_read,
        per_source_limit=per_source_limit,
        min_importance=min_importance,
        topics=topics,
    )


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False))
def get_article(
    identifier: Annotated[str, "Article URL or canonical URL of the stored document."],
) -> dict[str, Any]:
    """Read one article's full stored text plus provenance.

    Use after search_articles (or a period bundle) when a snippet is not
    enough and the digest/synthesis needs the full body with citations.

    Args:
        identifier: Article URL or canonical URL.

    Returns:
        Full article dict including body text and provenance fields.

    Raises:
        InvalidRequest: If the identifier is blank, unknown, or ambiguous.
    """
    return service.get_article(identifier)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=False))
def flag_extraction(
    identifier: Annotated[str, "Article URL or canonical URL to flag."],
    reason: Annotated[
        str | None,
        "One of thin, js_shell, paywall_challenge, truncated, wrong_body, unrecoverable, other.",
    ] = None,
    detail: Annotated[
        str | None, "Details (<=2000 chars; required when reason is 'other')."
    ] = None,
    flagged_by: Annotated[str | None, "Reporter label (<=100 chars)."] = None,
    clear: Annotated[bool, "When true, clear the flag instead of setting it."] = False,
) -> dict[str, Any]:
    """Flag (or clear) an extraction problem on one article.

    Use only for improperly extracted content (thin body, JS shell,
    paywall/bot challenge, truncated text, wrong body, unrecoverable fetch) —
    never for factual disputes about an otherwise well-extracted article.
    `unrecoverable` is filed by ingestion when every article-content chain leg
    fails; set it only for the same terminal condition, and prefer `clear` once
    you have verified a body the pipeline gave up on. This is one of the
    mutating tools on this server (with mark_article_read, set_importance,
    set_document_topics, record_digest_picks and clear_digest_picks).

    Args:
        identifier: Article URL or canonical URL.
        reason: One of FLAG_REASONS (thin, js_shell, paywall_challenge,
            truncated, wrong_body, unrecoverable, other).
        detail: Free-text detail, max 2000 chars; required when
            reason is "other".
        flagged_by: Reporter label, max 100 chars.
        clear: When True, clear the flag (NULLs flag columns) instead of
            setting it.

    Returns:
        The updated article dict with flag columns applied or cleared.

    Raises:
        InvalidRequest: If the identifier is unknown or flag fields are
            invalid (bad reason, missing/oversize detail, oversize
            flagged_by).
    """
    return service.flag_extraction(  # type: ignore[attr-defined, no-any-return]
        identifier, reason=reason, detail=detail, flagged_by=flagged_by, clear=clear
    )


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=False))
def mark_article_read(
    identifier: Annotated[str, "Article URL or canonical URL to mark."],
    read_by: Annotated[str | None, "Reader label (<=100 chars)."] = None,
    clear: Annotated[bool, "When true, clear the read mark instead of setting it."] = False,
) -> dict[str, Any]:
    """Mark (or clear) an article as read.

    Use to record consuming a document; idempotent — re-marking updates
    read_at/read_by, clearing NULLs the read columns.

    Args:
        identifier: Article URL or canonical URL.
        read_by: Reader label, max 100 chars.
        clear: When True, clear the read mark instead of setting it.

    Returns:
        The updated article dict with read columns applied or cleared.

    Raises:
        InvalidRequest: If the identifier is unknown/blank or read_by is
            invalid (non-string, overlong).
    """
    return service.mark_article_read(identifier, read_by=read_by, clear=clear)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False))
def list_sources_inventory() -> list[dict[str, Any]]:
    """List every curated Source with stored counts, last ingest, and cadence.

    Use to see coverage and pipeline health at a glance: which Sources are
    empty or have never been successfully ingested report explicit ``0`` and
    ``None`` rather than vanishing from the list. Read-only — never triggers
    ingestion.

    Returns:
        All 20 V1 Sources in registry order; each dict has ``name``,
        ``article_count`` (stored Documents), ``last_ingest_at`` (ISO-8601
        finish time of the most recent successful Ingestion Run, or None),
        and ``cadence`` (curated, or None).
    """
    return service.list_sources_inventory()


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=False))
def set_importance(
    identifier: Annotated[str, "Article URL or canonical URL to annotate."],
    score: Annotated[float, "Importance in [0, 1] (server caps flagged/paywalled at 0.3)."],
    rationale: Annotated[str | None, "Why it matters (<=2000 chars)."] = None,
    reporter: Annotated[str | None, "Reporter label (<=100 chars)."] = None,
) -> dict[str, Any]:
    """Record a 0-1 importance annotation on one article (latest write wins).

    Use to mark evidence the digest consumer should prefer. History is
    retained append-only; flagged or paywalled articles are hard-capped at
    0.3 server-side regardless of the submitted score, so they can never
    outrank clean evidence. This is a mutating tool (with flag_extraction,
    mark_article_read, set_document_topics, record_digest_picks and
    clear_digest_picks).

    Args:
        identifier: Article URL or canonical URL.
        score: Importance in [0, 1]; outside that range is rejected (422).
        rationale: Free-text rationale, max 2000 chars.
        reporter: Reporter label, max 100 chars.

    Returns:
        The updated article dict with the latest importance annotation.

    Raises:
        InvalidRequest: If the identifier is unknown/blank or the score /
            rationale / reporter is invalid.
    """
    return service.set_importance(  # type: ignore[attr-defined, no-any-return]
        identifier, score, rationale=rationale, reporter=reporter
    )


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False))
def get_importance(
    identifier: Annotated[str, "Article URL or canonical URL."],
) -> dict[str, Any]:
    """Read one article's latest importance annotation.

    Use to inspect the current score/rationale/reporter/timestamp without
    re-reading the full article. All four ``importance_*`` keys are ``None``
    when the article has never been annotated.

    Args:
        identifier: Article URL or canonical URL.

    Returns:
        Dict with ``importance_score``, ``importance_rationale``,
        ``importance_reporter``, ``importance_updated_at``.

    Raises:
        InvalidRequest: If the identifier is unknown or blank.
    """
    return service.get_importance(identifier)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False))
def list_vocabulary() -> list[dict[str, Any]]:
    """List the controlled Topic vocabulary (canonical slugs + accepted synonyms).

    Use before tagging to discover canonical pillar/region/content-type slugs
    without leaving the tool surface. Vocabulary changes are additive: retired
    slugs stay listed with `retired_alias_of` pointing at their replacement.

    Returns:
        List of entries, each with `slug`, `kind`
        (pillar/region/content-type), `label`, `synonyms` (accepted
        spellings), and `retired_alias_of` (canonical replacement, or None).
    """
    return service.list_vocabulary()


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=False))
def set_document_topics(
    identifier: Annotated[str, "Article URL or canonical URL to tag."],
    topics: Annotated[list[str], "Canonical slugs or accepted synonyms; [] clears all tags."],
    reporter: Annotated[str | None, "Reporter label (<=100 chars)."] = None,
) -> dict[str, Any]:
    """Set an article's canonical Topics (latest write wins).

    Tags are canonicalized server-side — synonyms, case/whitespace variants
    and retired aliases all land on the canonical slug; unknown tags are
    rejected (422) and never stored as-is. The effective set becomes exactly
    `topics` (an empty list clears it); history is retained append-only, so
    the full tagging trail survives. Use list_vocabulary first to discover
    canonical slugs.

    Args:
        identifier: Article URL or canonical URL.
        topics: Canonical slugs or accepted synonyms; duplicates collapse and
            an empty list clears the article's tags.
        reporter: Reporter label, max 100 chars.

    Returns:
        The updated article dict with `topics` (sorted canonical slugs).

    Raises:
        InvalidRequest: If the identifier is unknown/blank, topics is not a
            list of non-blank strings, a tag is unknown, or reporter is
            invalid.
    """
    return service.set_document_topics(identifier, topics, reporter=reporter)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=True))
def record_digest_picks(
    digest_date: Annotated[str, "Digest edition date, ISO YYYY-MM-DD."],
    identifiers: Annotated[
        list[str],
        "Article URLs picked for that digest; [] reconciles the set to empty.",
    ],
    reporter: Annotated[str | None, "Reporter label (<=100 chars)."] = None,
) -> dict[str, Any]:
    """Record which articles a digest date selected (idempotent per article).

    Use to leave a trace of the digest's pick set, and to capture human edits:
    re-record the edited URL set and the returned ``added``/``removed`` lists
    are the re-scoring signal — revisit those Documents' importance with
    set_importance (added = under-weighted, removed = over-weighted). The
    stored set for the date becomes exactly ``identifiers``: new Documents are
    inserted, missing ones deleted, unchanged ones untouched (their original
    ``picked_at`` survives). Recording the same set twice is a no-op. This is
    a mutating tool (with flag_extraction, mark_article_read, set_importance,
    set_document_topics and clear_digest_picks).

    Args:
        digest_date: Digest edition date, ISO ``YYYY-MM-DD``.
        identifiers: Article URLs (or canonical URLs) the digest picked; an
            empty list reconciles the date's set to empty.
        reporter: Reporter label, max 100 chars.

    Returns:
        ``{digest_date, reporter, picks, added, removed}`` — the read-back
        pick set (``url``/``canonical_url``/``title``/``source``/
        ``published_at``/``reporter``/``picked_at`` per pick) plus the
        canonical identifiers this call added and removed.

    Raises:
        InvalidRequest: If the date is malformed, an identifier is blank,
            unknown, or not a string, or reporter is invalid.
    """
    return service.record_digest_picks(digest_date, identifiers, reporter=reporter)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False))
def get_digest_picks(
    digest_date: Annotated[str, "Digest edition date, ISO YYYY-MM-DD."],
) -> dict[str, Any]:
    """Read back one digest date's pick set.

    Use to review what a digest actually selected (each pick carries its
    Document identity, the ``reporter`` who recorded it and the ``picked_at``
    timestamp). Read-only; a date that never picked anything returns
    ``picks: []``.

    Args:
        digest_date: Digest edition date, ISO ``YYYY-MM-DD``.

    Returns:
        ``{digest_date, picks}`` where each pick has ``url``,
        ``canonical_url``, ``title``, ``source``, ``published_at``,
        ``reporter`` and ``picked_at``.

    Raises:
        InvalidRequest: If the date is malformed.
    """
    return service.get_digest_picks(digest_date)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=True))
def clear_digest_picks(
    digest_date: Annotated[str, "Digest edition date, ISO YYYY-MM-DD."],
) -> dict[str, Any]:
    """Clear one digest date's pick set (for a discarded edition).

    Use when a digest is abandoned rather than edited — unlike re-recording
    an edited set, this drops the trace without a diff. Read-back after a
    clear is always ``picks: []``; ``cleared`` reports how many picks were
    removed (0 when the date had none).

    Args:
        digest_date: Digest edition date, ISO ``YYYY-MM-DD``.

    Returns:
        ``{digest_date, cleared, picks}`` with ``picks: []``.

    Raises:
        InvalidRequest: If the date is malformed.
    """
    return service.clear_digest_picks(digest_date)


@mcp.resource("brain://about", mime_type="application/json")
def read_about() -> str:
    """Static overview: coverage and recommended workflow."""
    return json.dumps(
        {
            "server": "Marketing Intelligence",
            "what": "Persistent marketing/GenZ/culture/tech intelligence platform "
            "(not a newsletter generator): deterministic ingestion into a "
            "Postgres system of record, served to AI digest consumers.",
            "v1_sources": "20 curated V1 sources (RSS + sitemap/hub/url-set lanes).",
            "workflow": "search_articles for discovery -> get_article for full text -> "
            "get_period_context(from_date, to_date) for any period bundle "
            "(a digest is built from a caller-chosen range; filters: min_importance, "
            "topics, per_source_limit) -> set_importance to record "
            "why evidence matters -> list_vocabulary + set_document_topics to tag "
            "evidence canonically -> record_digest_picks(digest_date, urls) to leave "
            "the digest trace (re-record an edited set for its added/removed "
            "re-scoring diff; get_digest_picks reads it, clear_digest_picks drops it) "
            "-> flag_extraction for bad content.",
            "resources": ["brain://about", "article://{identifier}", "period://{from}/{to}"],
            "prompts": ["period_digest", "investigate_topic"],
        }
    )


@mcp.resource("article://{identifier}", mime_type="application/json")
def read_article(identifier: str) -> str:
    """Full stored article as JSON, by URL/canonical URL."""
    return json.dumps(service.get_article(identifier))


@mcp.resource("period://{from_date}/{to_date}", mime_type="application/json")
def read_period_bundle(from_date: str, to_date: str) -> str:
    """Evidence bundle as JSON for an ISO date range."""
    return json.dumps(service.get_period_context(from_date, to_date))


@mcp.prompt()
def period_digest(period: str, focus: str | None = None) -> str:
    """Scaffold for the full digest funnel over a period evidence bundle.

    The funnel is recall -> score -> tag -> select -> summarize -> record. The
    importance rubric is versioned here, in the prompt text, so a digest can
    cite the rubric it was scored against; it is not implemented in server
    code.

    Args:
        period: Human label for the bundle range whose two ISO dates bound the
            funnel (e.g. "2026-08-31 to 2026-09-07").
        focus: Optional theme to emphasize (e.g. "GenZ culture").
    """
    scope = f" with focus on {focus}" if focus else ""
    return (
        f"Draft a digest for {period}{scope} by running the full funnel below "
        "against the stored evidence. Never skip a step; never invent evidence.\n"
        "\n"
        "IMPORTANCE RUBRIC v1 (cite it in the digest; the prompt owns the rubric, "
        "not the server):\n"
        "  0.9-1.0 structural  — a durable shift in consumer behaviour, a major "
        "brand/market move, or proprietary data the business must act on.\n"
        "  0.7-0.8 strategic   — directly on-pillar news with clear commercial "
        "implications for jewellery/fashion/GenZ marketing.\n"
        "  0.4-0.6 context     — useful background, adjacent-market signal, or "
        "early/partial reporting of a strategic story.\n"
        "  0.1-0.3 noise       — routine PR, listicles, rehashed commentary, or "
        "poorly extracted/paywalled bodies (the server caps flagged or "
        "paywalled Documents at 0.3).\n"
        "\n"
        "1. RECALL the week: call get_period_context(from_date, to_date) with "
        f"the range behind {period} (raise limit if needed). This "
        "recent_articles list is the candidate pool — nothing else is evidence.\n"
        "2. SCORE each candidate worth considering: set_importance(identifier, "
        "score, rationale, reporter) using RUBRIC v1 above (0.5-0.6 is a fine "
        "default for on-pillar context; do not inflate).\n"
        "3. TAG the scored candidates: list_vocabulary for the canonical slugs, "
        "then set_document_topics(identifier, [...]) with the pillar/region/"
        "content-type tags that describe them.\n"
        "4. SELECT the source-balanced shortlist: re-call get_period_context "
        "for the same range with min_importance=<floor, e.g. 0.6>, "
        "topics=<canonical slugs covering the focus; omit to keep every topic> "
        "and per_source_limit=2. The returned bundle (recent_articles, rank = "
        "importance order) is the digest's evidence set.\n"
        "5. SUMMARIZE from that shortlist only: cite every claim with its "
        "article URL/canonical_url, separate observations from interpretations, "
        "and flag (do not silently fix) badly extracted content via "
        "flag_extraction.\n"
        "6. RECORD the trace: record_digest_picks(digest_date, [picked urls], "
        "reporter=...). If a human later edits the published digest, call "
        "record_digest_picks again with the edited URL set — its added/removed "
        "lists are re-scoring signal: revisit those Documents with "
        "set_importance (added = under-weighted, removed = over-weighted)."
    )


@mcp.prompt()
def investigate_topic(keyword: str) -> str:
    """Scaffold for a deep dive on one topic from stored evidence.

    Args:
        keyword: Search term to start the investigation.
    """
    return (
        f"Investigate '{keyword}': call search_articles(keyword={keyword!r}), "
        "then get_article for the hits you choose to inspect, then synthesize what the "
        "stored evidence supports with URL citations, "
        "clearly separating observations from interpretations. "
        "For a date-bounded view, get_period_context returns a recency-ordered "
        "recent_articles list by default (rank = position, not importance) and "
        "accepts min_importance/topics/per_source_limit for annotation-aware pulls."
    )


async def health(request: Request) -> JSONResponse:
    """Unauthenticated liveness probe (plain route, bypasses token verifier)."""
    del request
    return JSONResponse(health_payload())


def _attach_health_route(app: Starlette) -> Starlette:
    """Append ``GET /health`` to a streamable-HTTP app (idempotent per app)."""
    for route in app.routes:
        if getattr(route, "path", None) == "/health":
            return app
    app.routes.append(Route("/health", endpoint=health, methods=["GET"]))
    return app


def create_http_app() -> Starlette:
    """Fresh streamable-HTTP Starlette app for this server (FastMCP 1.29.1 API).

    Uses `FastMCP.streamable_http_app(self) -> Starlette` (no args) on a
    freshly built server so the current `BRAIN_API_TOKEN` value applies.
    Serve plain HTTP behind Traefik (TLS terminated at the proxy), e.g.
    `uvicorn mcp.server:http_app --port 8124`.
    """
    return _attach_health_route(create_mcp().streamable_http_app())


# ASGI entrypoint reflecting the env at process start (containers set
# BRAIN_API_TOKEN before import, so this is the enforced app in prod).
http_app = _attach_health_route(mcp.streamable_http_app())


if __name__ == "__main__":
    if "--http" in sys.argv:
        mcp.run(transport="streamable-http")
    else:
        mcp.run()
