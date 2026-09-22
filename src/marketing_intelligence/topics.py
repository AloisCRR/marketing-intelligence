"""Controlled topic vocabulary (Ticket 20) — canonical Document tags.

Domain contract (stable): every Document can be tagged with canonical Topic
slugs drawn from the in-code :data:`TOPICS` registry (pillar / region /
content-type axes). ``set_document_topics(identifier, topics, reporter)``
canonicalizes caller synonyms server-side (case-, whitespace- and
separator-insensitive), rejects anything unknown (never stores it as-is) and
appends the resulting assignment to ``document_topics``. The latest row per
``(document_id, topic_slug)`` wins — ``ORDER BY created_at DESC, id DESC`` —
so a later row with ``assigned = FALSE`` retires a tag; nothing is ever
deleted, so the full tagging history is retained. The function returns the
updated article dict (as produced by
``marketing_intelligence.article.get_article``, with ``topics`` attached:
a sorted list of canonical slugs, ``[]`` when unannotated).

The vocabulary is additive by construction: retire a slug by adding a
``retired_alias_of`` entry pointing at its replacement (never by deleting
it), so old spellings keep canonicalizing and callers can still discover
them through :func:`list_vocabulary`.

Error contract: this lane raises ``ValueError``/``TypeError``/``LookupError``
only (unknown URL or unknown tag -> ``ValueError``); the service adapter maps
those to ``InvalidRequest`` (HTTP 422).
"""

from __future__ import annotations

from typing import Any

try:  # foundation seam (preferred)
    from marketing_intelligence.db import get_connection  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - defensive fallback when absent

    def get_connection() -> Any:  # type: ignore[misc]
        raise RuntimeError(
            "No database connection available: marketing_intelligence.db.get_connection "
            "is missing and no fallback is configured."
        )


from marketing_intelligence import article as _article
from marketing_intelligence.normalize import canonicalize_url

REPORTER_MAX_LENGTH = 100

#: Controlled Topic vocabulary: canonical slug -> registry entry.
#:
#: ``kind`` is one of the three controlled axes (pillar / region /
#: content-type) so callers can tell a theme from a geography from a format.
#: ``synonyms`` are accepted spellings canonicalized to this slug. Entries
#: carrying ``retired_alias_of`` are no longer canonical themselves (their
#: synonyms and slug canonicalize to the target) but stay listed forever:
#: vocabulary changes are additive — retire by aliasing, never by deleting.
TOPICS: dict[str, dict[str, Any]] = {
    # --- Pillars (themes) --------------------------------------------------
    "gen-z": {
        "kind": "pillar",
        "label": "Gen Z",
        "synonyms": ("gen z", "genz", "generation z", "zoomers"),
    },
    "gen-z-self-purchase": {
        "kind": "pillar",
        "label": "Gen Z self-purchase",
        "synonyms": (
            "self purchase",
            "self-purchase",
            "gen z self purchase",
            "genz self purchase",
        ),
    },
    "consumer-behavior": {
        "kind": "pillar",
        "label": "Consumer behavior",
        "synonyms": (
            "consumer behaviour",
            "shopper behavior",
            "buying behavior",
            "consumption patterns",
        ),
    },
    # Retired alias: kept discoverable, canonicalizes to consumer-behavior.
    "consumer-trends": {
        "kind": "pillar",
        "label": "Consumer trends",
        "synonyms": ("consumer trends", "trends"),
        "retired_alias_of": "consumer-behavior",
    },
    "luxury": {
        "kind": "pillar",
        "label": "Luxury",
        "synonyms": ("luxury goods", "luxury sector", "high-end", "premium"),
    },
    "jewelry": {
        "kind": "pillar",
        "label": "Jewelry",
        "synonyms": ("jewellery", "fine jewelry", "jewelry industry"),
    },
    "fashion": {
        "kind": "pillar",
        "label": "Fashion",
        "synonyms": ("apparel", "fashion industry", "style"),
    },
    "beauty": {
        "kind": "pillar",
        "label": "Beauty",
        "synonyms": ("beauty industry", "cosmetics", "skincare"),
    },
    "retail": {
        "kind": "pillar",
        "label": "Retail",
        "synonyms": ("retail industry", "retailing", "brick-and-mortar"),
    },
    "e-commerce": {
        "kind": "pillar",
        "label": "E-commerce",
        "synonyms": (
            "ecommerce",
            "online retail",
            "digital commerce",
            "dtc",
            "direct-to-consumer",
        ),
    },
    "social-media": {
        "kind": "pillar",
        "label": "Social media",
        "synonyms": ("social platforms", "social networks", "social"),
    },
    "social-commerce": {
        "kind": "pillar",
        "label": "Social commerce",
        "synonyms": (
            "social shopping",
            "shoppable content",
            "live commerce",
            "livestream commerce",
        ),
    },
    "creator-economy": {
        "kind": "pillar",
        "label": "Creator economy",
        "synonyms": ("creators", "influencers", "influencer economy"),
    },
    # Retired alias: kept discoverable, canonicalizes to creator-economy.
    "influencer-marketing": {
        "kind": "pillar",
        "label": "Influencer marketing",
        "synonyms": ("influencer marketing", "influencer campaigns"),
        "retired_alias_of": "creator-economy",
    },
    "marketing": {
        "kind": "pillar",
        "label": "Marketing",
        "synonyms": ("advertising", "brand marketing", "marketing industry"),
    },
    "brand-strategy": {
        "kind": "pillar",
        "label": "Brand strategy",
        "synonyms": ("branding", "brand positioning", "brand purpose"),
    },
    "technology": {
        "kind": "pillar",
        "label": "Technology",
        "synonyms": ("tech", "innovation"),
    },
    "ai": {
        "kind": "pillar",
        "label": "AI",
        "synonyms": ("artificial intelligence", "machine learning", "genai", "generative ai"),
    },
    "culture": {
        "kind": "pillar",
        "label": "Culture",
        "synonyms": ("cultural trends", "pop culture"),
    },
    "sustainability": {
        "kind": "pillar",
        "label": "Sustainability",
        "synonyms": ("esg", "responsible sourcing", "ethical sourcing", "circular economy"),
    },
    # --- Regions -----------------------------------------------------------
    "global": {
        "kind": "region",
        "label": "Global",
        "synonyms": ("worldwide", "international"),
    },
    "latam": {
        "kind": "region",
        "label": "Latin America",
        "synonyms": ("latin america", "south america", "central america"),
    },
    "usa": {
        "kind": "region",
        "label": "United States",
        "synonyms": ("us", "u.s.", "united states", "america"),
    },
    "uk": {
        "kind": "region",
        "label": "United Kingdom",
        "synonyms": ("united kingdom", "great britain", "britain", "england"),
    },
    "europe": {
        "kind": "region",
        "label": "Europe",
        "synonyms": ("eu", "european union"),
    },
    "asia": {
        "kind": "region",
        "label": "Asia",
        "synonyms": ("asia-pacific", "apac"),
    },
    "china": {
        "kind": "region",
        "label": "China",
        "synonyms": ("mainland china", "chinese market"),
    },
    "brazil": {
        "kind": "region",
        "label": "Brazil",
        "synonyms": ("brasil",),
    },
    "mexico": {
        "kind": "region",
        "label": "Mexico",
        "synonyms": ("méxico",),
    },
    "spain": {
        "kind": "region",
        "label": "Spain",
        "synonyms": ("españa",),
    },
    # ADR-0016: the annotate lane judges Latin-American desks directly instead
    # of lumping them under `latam`; additive regions only, nothing retired.
    "colombia": {
        "kind": "region",
        "label": "Colombia",
        "synonyms": ("colombia", "republic of colombia"),
    },
    "argentina": {
        "kind": "region",
        "label": "Argentina",
        "synonyms": ("argentina", "argentine republic"),
    },
    # --- Content types -----------------------------------------------------
    "news": {
        "kind": "content-type",
        "label": "News",
        "synonyms": ("news article", "breaking news"),
    },
    "analysis": {
        "kind": "content-type",
        "label": "Analysis",
        "synonyms": ("deep dive", "deep-dive", "insight", "insights"),
    },
    "opinion": {
        "kind": "content-type",
        "label": "Opinion",
        "synonyms": ("op-ed", "editorial", "column", "commentary"),
    },
    "interview": {
        "kind": "content-type",
        "label": "Interview",
        "synonyms": ("q&a", "qa"),
    },
    "report": {
        "kind": "content-type",
        "label": "Report",
        "synonyms": ("research report", "whitepaper", "study", "research"),
    },
    "press-release": {
        "kind": "content-type",
        "label": "Press release",
        "synonyms": ("press release", "pr", "announcement", "newswire"),
    },
    "product-launch": {
        "kind": "content-type",
        "label": "Product launch",
        "synonyms": ("launch", "new product", "collection launch", "drop"),
    },
    "campaign": {
        "kind": "content-type",
        "label": "Campaign",
        "synonyms": ("ad campaign", "marketing campaign", "advertising campaign"),
    },
    "earnings": {
        "kind": "content-type",
        "label": "Earnings",
        "synonyms": ("financial results", "results", "trading update", "quarterly results"),
    },
    "event": {
        "kind": "content-type",
        "label": "Event",
        "synonyms": ("trade show", "conference", "expo", "trade fair"),
    },
}

_DOCUMENT_SQL = """\
SELECT d.id
  FROM documents d
 WHERE d.url = %s OR d.canonical_url = %s
 LIMIT 1\
"""

#: Effective (latest-assigned) slugs for one Document, alphabetical. Per slug
#: the newest row wins; ``assigned = FALSE`` tombstones retire a tag without
#: deleting history.
_EFFECTIVE_SQL = """\
SELECT t.topic_slug
  FROM (
        SELECT DISTINCT ON (dt.topic_slug) dt.topic_slug, dt.assigned
          FROM document_topics dt
         WHERE dt.document_id = %s
         ORDER BY dt.topic_slug, dt.created_at DESC, dt.id DESC
       ) t
 WHERE t.assigned
 ORDER BY t.topic_slug\
"""

_INSERT_SQL = """\
INSERT INTO document_topics (document_id, topic_slug, assigned, reporter, created_at)
VALUES (%s, %s, %s, %s, now())\
"""


def _lookup_key(tag: str) -> str:
    """Normalize a caller tag: case/whitespace/separator-insensitive key."""
    spaced = tag.strip().lower().replace("_", " ").replace("-", " ")
    return "-".join(spaced.split())


def _build_index() -> dict[str, str]:
    """Map every known spelling (slug/label/synonym) to its canonical slug."""
    index: dict[str, str] = {}
    for slug, entry in TOPICS.items():
        target = entry.get("retired_alias_of") or slug
        aliases = (slug, entry["label"], *entry["synonyms"])
        for alias in aliases:
            index.setdefault(_lookup_key(str(alias)), target)
    return index


_SYNONYM_INDEX = _build_index()


def list_vocabulary() -> list[dict[str, Any]]:
    """Return the controlled Topic vocabulary in registry order (read-only).

    Every entry is ``{"slug", "kind", "label", "synonyms", "retired_alias_of"}``:
    the canonical slug, its axis (``pillar``/``region``/``content-type``), the
    human label, the accepted spellings, and — for retired aliases — the
    canonical slug they now canonicalize to (``None`` for live slugs).
    Retired aliases stay listed forever so vocabulary changes remain additive.
    """
    return [
        {
            "slug": slug,
            "kind": entry["kind"],
            "label": entry["label"],
            "synonyms": list(entry["synonyms"]),
            "retired_alias_of": entry.get("retired_alias_of"),
        }
        for slug, entry in TOPICS.items()
    ]


def canonicalize_topic(tag: Any) -> str:
    """Canonicalize one caller tag to a registry slug.

    Matching is case-, whitespace- and separator-insensitive
    (``"Gen Z self-purchase"``, ``"genz self purchase"`` and
    ``"gen_z_self_purchase"`` all canonicalize to ``"gen-z-self-purchase"``).
    Synonyms and retired aliases resolve to their canonical target.

    Raises:
        TypeError: non-string tag.
        ValueError: blank or unknown tag (never returns it as-is).
    """
    if not isinstance(tag, str):
        raise TypeError(f"topic tags must be strings, got {type(tag).__name__}")
    if not tag.strip():
        raise ValueError("topic tags must be non-empty strings")
    key = _lookup_key(tag)
    try:
        return _SYNONYM_INDEX[key]
    except KeyError:
        raise ValueError(
            f"unknown topic: {tag!r}; call list_vocabulary() for canonical slugs"
        ) from None


def _validate_topics(topics: Any) -> list[str]:
    """Canonicalize a caller topic list, de-duplicated and order-preserving."""
    if isinstance(topics, (str, bytes)) or not isinstance(topics, (list, tuple)):
        raise TypeError(f"topics must be a list of topic tags, got {type(topics).__name__}")
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in topics:
        slug = canonicalize_topic(raw)
        if slug not in seen:
            seen.add(slug)
            cleaned.append(slug)
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


def _canonical(key: str) -> str:
    try:
        return canonicalize_url(key)
    except Exception:
        return key


def _resolve(conn: Any, key: str, canonical: str) -> Any:
    """Return the Document id for ``key`` or raise ``ValueError``."""
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
        return row.get("id")
    return row[0]


def _fetch_slugs(conn: Any, sql: str, params: tuple) -> list[str]:
    cursor = conn.cursor()
    try:
        cursor.execute(sql, params)
        rows = list(cursor.fetchall() or [])
    finally:
        close = getattr(cursor, "close", None)
        if callable(close):
            close()
    slugs: list[str] = []
    for row in rows:
        value = row.get("topic_slug") if isinstance(row, dict) else row[0]
        if value is not None:
            slugs.append(str(value))
    return slugs


def _insert(conn: Any, document_id: Any, slug: str, assigned: bool, reporter: Any) -> None:
    cursor = conn.cursor()
    try:
        cursor.execute(_INSERT_SQL, (document_id, slug, assigned, reporter))
    finally:
        close = getattr(cursor, "close", None)
        if callable(close):
            close()


def set_document_topics(
    identifier: str,
    topics: list[str],
    reporter: str | None = None,
    conn: Any | None = None,
) -> dict[str, Any]:
    """Set a Document's canonical Topics (latest wins, full history retained).

    Caller tags are canonicalized server-side before anything is written: a
    synonym, a different case, or a retired alias all land on the canonical
    slug, and an unknown tag raises without touching the database (it is
    never stored as-is). The effective set becomes exactly ``topics``: slugs
    newly present are appended as assignments, slugs no longer present are
    retired with ``assigned = FALSE`` tombstones — earlier rows stay, so the
    full tagging history is retained and a later write can restore a tag.

    Args:
        identifier: exact article URL, or anything that canonicalizes to the
            stored canonical URL. Blank/non-string identifiers raise
            ``ValueError``.
        topics: list of topic tags (synonyms accepted, de-duplicated). Unknown
            or non-string tags raise before any DB round-trip. An empty list
            clears the Document's tags.
        reporter: optional reporter tag, max 100 chars.
        conn: optional injected DB-API connection (fake-friendly). When None
            a connection is opened via `get_connection` and closed afterwards;
            an injected connection is never committed or closed here.

    Returns:
        The updated article dict (same keys as
        ``marketing_intelligence.article.get_article``) with ``topics`` — the
        sorted canonical slugs, ``[]`` when cleared/unannotated.

    Raises:
        ValueError: blank identifier, unknown article, unknown/blank tag.
        TypeError: non-list topics, non-string tag/reporter.
    """
    if not isinstance(identifier, str) or not identifier.strip():
        raise ValueError(f"identifier must be a non-empty string, got {identifier!r}")
    key = identifier.strip()
    canonical = _canonical(key)
    clean_topics = _validate_topics(topics)
    clean_reporter = _validate_reporter(reporter)

    owns_connection = conn is None
    if conn is None:
        conn = get_connection()
    assert conn is not None
    try:
        document_id = _resolve(conn, key, canonical)
        current = set(_fetch_slugs(conn, _EFFECTIVE_SQL, (document_id,)))
        wanted = set(clean_topics)
        for slug in sorted(wanted - current):
            _insert(conn, document_id, slug, True, clean_reporter)
        for slug in sorted(current - wanted):
            _insert(conn, document_id, slug, False, clean_reporter)
        return _article.get_article(key, conn=conn)
    finally:
        if owns_connection:
            commit = getattr(conn, "commit", None)
            if callable(commit):
                commit()
            close_conn = getattr(conn, "close", None)
            if callable(close_conn):
                close_conn()
