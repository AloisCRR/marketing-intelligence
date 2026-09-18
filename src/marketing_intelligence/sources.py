"""Source registry — curated origins seeded from curated-sources.json.

This module owns the Source catalog: which Sources exist, the identity inputs
they are seeded from (name, feed/hub URLs, language), and the membership/seeding
views other lanes consume. The curated JSON is the source of truth; the views
here — `catalog_names()`, `catalog_seed_rows()`, and the `V1_SOURCES` snapshot —
are all derived from it, so onboarding a Source never means editing code.

Registry lookup is DB-free so adapters and unit tests never need Postgres.
(The foundation lane separately seeds the `sources` table for persistence.)
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_LANGUAGE_MAP: dict[str, str] = {
    "english": "en",
    "en": "en",
    "portuguese": "pt",
    "português": "pt",
    "portugues": "pt",
    "pt": "pt",
    "spanish": "es",
    "español": "es",
    "espanol": "es",
    "es": "es",
}


def normalize_language(raw: Any | None) -> str:
    """Map a curated language label to an ISO 639-1 code (en/pt/es)."""
    if raw is None:
        return "en"
    key = str(raw).strip().lower()
    if key in _LANGUAGE_MAP:
        return _LANGUAGE_MAP[key]
    prefix = key.split("-", 1)[0].split("_", 1)[0].split(" ", 1)[0].split("/", 1)[0]
    if prefix in _LANGUAGE_MAP:
        return _LANGUAGE_MAP[prefix]
    return "en"


def _clean_cadence(raw: Any | None) -> str | None:
    """Trim a curated cadence label; missing/blank/non-string values are None."""
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


SOURCE_NAME_SMT = "Social Media Today"

#: Default thin threshold (chars) for the enrichment policy when a source
#: carries no override. Mirrors `marketing_intelligence.enrich.DEFAULT_THIN_THRESHOLD`.
DEFAULT_ENRICHMENT_THRESHOLD = 500

#: Allowed enrichment modes: thin-only, enrich everything, enrich nothing.
ENRICHMENT_MODES: tuple[str, ...] = ("auto", "force_on", "force_off")

#: Policy returned for unknown sources and entries without overrides.
DEFAULT_ENRICHMENT_POLICY: dict[str, Any] = {
    "threshold": DEFAULT_ENRICHMENT_THRESHOLD,
    "mode": "auto",
}

#: Allowed retrieval (discovery) types (ticket 07 config seam; spec 06 §18).
#: ``rss`` is the V1 feed lane; ``sitemap`` traverses declared sitemap
#: index/URL-set/news feeds; ``hub`` scrapes hub-page anchors; ``sitemap+hub``
#: runs sitemaps first with hub-anchor fallback; ``url-set`` ingests a declared
#: URL set; ``url-set+hub`` pairs the set with hub-anchor fallback; and
#: ``instagram`` is the ADR-0013 premium exception — a per-account Instagram
#: lane (Apify actor over declared posts), not a feed surface. Retrieval
#: stanzas are config, not code forks — downstream ingest/flows behavior for
#: RSS sources is unchanged.
RETRIEVAL_TYPES: tuple[str, ...] = (
    "rss",
    "sitemap",
    "hub",
    "sitemap+hub",
    "url-set",
    "url-set+hub",
    "instagram",
)

#: Allowed retrieval policies. ``impersonated-feed`` is the default for every
#: feed/discovery fetch: the stdlib lanes are gone, so such a fetch runs the
#: impersonated chain (curl_cffi Chrome + browser headers → Jina reader →
#: Firecrawl), each leg explicit about its own failure. ``apify-premium`` is
#: the ADR-0013 exception: the fixed-purpose ``apify/instagram-post-scraper``
#: Instagram lane, which never issues an impersonated fetch.
RETRIEVAL_POLICIES: tuple[str, ...] = ("impersonated-feed", "apify-premium")

#: Deprecated back-compat alias: stanzas (or callers) still saying
#: ``"stdlib-only"`` resolve to ``"impersonated-feed"``. It is never a fetch
#: identity — no stdlib traffic is ever issued for it.
RETRIEVAL_POLICY_ALIASES: dict[str, str] = {"stdlib-only": "impersonated-feed"}

#: Allowed article extractor families: generic HTML-to-Markdown default, or
#: JSON-LD ``articleBody``-first (Next.js/Sanity family, e.g. Jing Daily)
#: with generic fallback; still-thin results are kept-aside/flagged.
EXTRACTOR_FAMILIES: tuple[str, ...] = ("generic", "json-ld-first")

#: Default extractor when a retrieval stanza declares none.
DEFAULT_EXTRACTOR = "generic"

#: Allowed Instagram content modes (ADR-0013): v1 reads the caption only.
#: Other postures (image-heavy account, reels transcript) are deferred to v2,
#: so they are not valid values until a lane implements them. Carried by the
#: `instagram` retrieval stanza; anything else drops silently.
CONTENT_MODES: tuple[str, ...] = ("caption_first",)

#: Allowed Instagram ``image_text`` handling (ADR-0013 reserves the key):
#: ``ignore`` reads the caption only, ``extract`` runs the ADR-0014 vision
#: lane over the post's frames instead of OCR'ing them into the caption.
#: Carried by the `instagram` stanza.
IMAGE_TEXT_MODES: tuple[str, ...] = ("ignore", "extract")

#: ``image_text`` modes only the Instagram lane implements: a stanza of any
#: other type drops them silently, exactly like an invalid value.
_INSTAGRAM_ONLY_IMAGE_TEXT_MODES: tuple[str, ...] = ("extract",)

#: Default Instagram content mode / image-text handling when undeclared.
DEFAULT_CONTENT_MODE = "caption_first"
DEFAULT_IMAGE_TEXT = "ignore"

#: Default per-host pacing between requests (ms) when a stanza declares none.
DEFAULT_PACING_MS = 1000

#: Default backfill bound (newest N URLs per Source first) when undeclared.
DEFAULT_MAX_URLS = 50

#: Policy returned for unknown sources and entries without overrides.
DEFAULT_RETRIEVAL_POLICY: dict[str, Any] = {
    "type": "rss",
    "policy": "impersonated-feed",
}

_FALLBACK_SMT: dict[str, Any] = {
    "name": SOURCE_NAME_SMT,
    "rss_url": "https://www.socialmediatoday.com/feeds/news/",
    "hub_url": "https://www.socialmediatoday.com/",
    "language": "en",
}


def _curated_path() -> Path | None:
    # Dev override first: editable .scratch copy (never shipped in the image).
    for parent in Path(__file__).resolve().parents:
        candidate = parent / ".scratch" / "marketing-intelligence" / "curated-sources.json"
        if candidate.is_file():
            return candidate
    # Baked-in fallback: shipped inside the image via `COPY src ./src`.
    baked_in = Path(__file__).resolve().parent / "data" / "curated-sources.json"
    if baked_in.is_file():
        return baked_in
    return None


@lru_cache(maxsize=1)
def _registry() -> dict[str, dict[str, Any]]:
    registry: dict[str, dict[str, Any]] = {}
    path = _curated_path()
    if path is not None:
        try:
            entries = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            entries = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("source_name", "")).strip()
            if not name:
                continue
            registry[name] = {
                "name": name,
                # rss_url is None for no-RSS sources (ticket 07: hub/sitemap
                # lanes); RSS behavior downstream only touches RSS entries.
                "rss_url": entry.get("rss_url"),
                "hub_url": entry.get("hub_url"),
                "language": normalize_language(entry.get("language")),
                # Curated publishing cadence ("Daily"/"Weekly"); None when the
                # entry declares none. Read via `get_cadence`.
                "cadence": _clean_cadence(entry.get("cadence")),
                "raw": entry,
            }
            overrides = entry.get("enrichment")
            if isinstance(overrides, dict):
                # Per-source enrichment policy (ticket 03): file-based
                # {"threshold": int, "mode": "auto"|"force_on"|"force_off"}.
                registry[name]["enrichment"] = dict(overrides)
            retrieval = entry.get("retrieval")
            if isinstance(retrieval, dict):
                # Per-source retrieval stanza: file-based
                # {"type": ..., "policy": ...} plus optional discovery keys
                # (extractor, sitemaps, hub, link_pattern, pacing_ms, max_urls).
                registry[name]["retrieval"] = dict(retrieval)
    registry.setdefault(SOURCE_NAME_SMT, dict(_FALLBACK_SMT))
    return registry


def list_sources() -> list[dict[str, Any]]:
    """Return all curated sources in registry order (RSS + no-RSS)."""
    return [dict(entry) for entry in _registry().values()]


def catalog_names() -> tuple[str, ...]:
    """Return every curated Source name, in registry order.

    Membership truth: a Source exists exactly when curated-sources.json
    declares a stanza for it. Onboarding adds a stanza to that JSON — never an
    edit here.
    """
    return tuple(_registry().keys())


def catalog_seed_rows() -> tuple[tuple[str, str | None, str, str], ...]:
    """Return one seed row per curated Source, in registry order.

    Row shape is ``(name, rss_url, hub_url, language)``: ``rss_url`` is the
    feed URL or None for no-RSS Sources (blank/non-string values included),
    ``hub_url`` is "" when undeclared, and ``language`` is the ISO 639-1 code
    (``en``/``pt``/``es``) `_registry` already normalized. Identity derivation
    from these inputs belongs to the DB seeding lane, not here.
    """
    rows: list[tuple[str, str | None, str, str]] = []
    for name, entry in _registry().items():
        raw_rss = entry.get("rss_url")
        rss_url = raw_rss if isinstance(raw_rss, str) and raw_rss.strip() else None
        rows.append(
            (
                name,
                rss_url,
                str(entry.get("hub_url") or ""),
                str(entry.get("language") or "en"),
            )
        )
    return tuple(rows)


#: Derived snapshot of the catalog at import; do not hand-edit. New code should
#: prefer `catalog_names()` (membership) or `catalog_seed_rows()` (identity
#: inputs) so views cannot drift from the curated JSON.
V1_SOURCES: tuple[str, ...] = catalog_names()


def get_source(name: str) -> dict[str, Any]:
    """Return the registry entry for `name`; raises KeyError when unknown."""
    try:
        return dict(_registry()[name])
    except KeyError:
        raise KeyError(f"Unknown source: {name!r}") from None


def get_cadence(source_name: str | None) -> str | None:
    """Return the curated cadence label for `source_name`.

    Cadence is registry metadata (``"Daily"``/``"Weekly"`` today), not a DB
    column. Unknown or missing sources and entries without a declared cadence
    yield ``None`` — this function never raises.
    """
    if not source_name:
        return None
    try:
        entry = get_source(source_name)
    except KeyError:
        return None
    cadence = entry.get("cadence")
    if isinstance(cadence, str) and cadence:
        return cadence
    return None


def get_enrichment_policy(source_name: str) -> dict[str, Any]:
    """Return the enrichment policy for `source_name`.

    Result shape is ``{"threshold": int, "mode": "auto"|"force_on"|"force_off"}``:
    ``auto`` enriches thin bodies only, ``force_on`` enriches every item,
    ``force_off`` skips all enrichment. Overrides come from the registry
    entry's ``"enrichment"`` mapping (``{"threshold": ..., "mode": ...}``);
    entries without overrides yield the default (threshold 500, auto).
    Unknown sources yield the default — this function never raises.
    """
    try:
        entry = get_source(source_name)
    except KeyError:
        return dict(DEFAULT_ENRICHMENT_POLICY)
    raw = entry.get("enrichment")
    if not isinstance(raw, dict):
        return dict(DEFAULT_ENRICHMENT_POLICY)
    threshold = raw.get("threshold", DEFAULT_ENRICHMENT_THRESHOLD)
    if isinstance(threshold, bool) or not isinstance(threshold, int) or threshold <= 0:
        threshold = DEFAULT_ENRICHMENT_THRESHOLD
    mode = raw.get("mode", "auto")
    if mode not in ENRICHMENT_MODES:
        mode = "auto"
    return {"threshold": threshold, "mode": mode}


def _validated_extras(raw: dict[str, Any], rtype: str) -> dict[str, Any]:
    """Validate optional retrieval-stanza keys; drop invalid ones silently.

    Never raises: unconfigured/invalid extras simply fall back to defaults
    (generic extractor, no sitemaps, registry hub, no link pattern, default
    pacing/backfill, no Instagram account keys). Callers must not rely on
    invalid values surviving. ``rtype`` is the already-normalized stanza type:
    ``image_text`` is type-aware (see below).

    ``sitemap_exclude`` entries are a small path mini-language consumed by
    ``discovery._path_excluded``: an entry is a path *substring* by default
    (e.g. ``/webstories/``), while an entry wrapped in ``^`` and ``$``
    matches that exact path only (``^/digital-general/social-media-marketing$``
    drops the section front but keeps its ``/<slug>`` articles, which no
    substring can express). A bare ``^`` or ``$`` with no matching pair stays
    a literal substring character, so only a ``^…$`` pair changes the
    semantics.

    ``image_text`` carries its ADR-0014 ``extract`` mode only on an
    ``instagram`` stanza — the lane that implements it. Every other stanza
    type (RSS, sitemap, hub) drops it silently, exactly like an invalid value.
    """
    extras: dict[str, Any] = {}
    extractor = raw.get("extractor")
    if extractor in EXTRACTOR_FAMILIES:
        extras["extractor"] = extractor
    sitemaps = raw.get("sitemaps")
    if isinstance(sitemaps, list):
        urls = [u for u in (str(u).strip() for u in sitemaps if u is not None) if u]
        if urls:
            extras["sitemaps"] = urls
    hub_pages = raw.get("hub_pages")
    if isinstance(hub_pages, list):
        pages = [u for u in (str(u).strip() for u in hub_pages if u is not None) if u]
        if pages:
            extras["hub_pages"] = pages
    hub = raw.get("hub")
    if isinstance(hub, str) and hub.strip():
        extras["hub"] = hub.strip()
    link_pattern = raw.get("link_pattern")
    if isinstance(link_pattern, str) and link_pattern.strip():
        extras["link_pattern"] = link_pattern.strip()
    sitemap_pattern = raw.get("sitemap_pattern")
    if isinstance(sitemap_pattern, str) and sitemap_pattern.strip():
        extras["sitemap_pattern"] = sitemap_pattern.strip()
    username = raw.get("username")
    if isinstance(username, str) and username.strip():
        extras["username"] = username.strip()
    hashtag_filter = raw.get("hashtag_filter")
    if isinstance(hashtag_filter, str) and hashtag_filter.strip():
        extras["hashtag_filter"] = hashtag_filter.strip()
    content_mode = raw.get("content_mode")
    if content_mode in CONTENT_MODES:
        extras["content_mode"] = content_mode
    image_text = raw.get("image_text")
    if image_text in IMAGE_TEXT_MODES and (
        rtype == "instagram" or image_text not in _INSTAGRAM_ONLY_IMAGE_TEXT_MODES
    ):
        extras["image_text"] = image_text
    sitemap_exclude = raw.get("sitemap_exclude")
    if isinstance(sitemap_exclude, str):
        sitemap_exclude = [sitemap_exclude]
    if isinstance(sitemap_exclude, list):
        patterns = [p for p in (str(p).strip() for p in sitemap_exclude if p is not None) if p]
        if patterns:
            extras["sitemap_exclude"] = patterns
    id_guard = raw.get("id_guard")
    if id_guard is True:
        extras["id_guard"] = True
    pacing_ms = raw.get("pacing_ms")
    if isinstance(pacing_ms, int) and not isinstance(pacing_ms, bool) and pacing_ms > 0:
        extras["pacing_ms"] = pacing_ms
    max_urls = raw.get("max_urls")
    if isinstance(max_urls, int) and not isinstance(max_urls, bool) and max_urls > 0:
        extras["max_urls"] = max_urls
    return extras


def get_retrieval_policy(source_name: str | None) -> dict[str, Any]:
    """Return the retrieval policy for `source_name`.

    Result shape is ``{"type": ..., "policy": ...}`` — the policy is one of
    ``RETRIEVAL_POLICIES`` (``impersonated-feed`` for every feed/discovery
    lane; ``apify-premium`` for the ADR-0013 Instagram lane) — plus validated
    optional discovery keys (``extractor``, ``sitemaps``,
    ``hub``, ``hub_pages``, ``link_pattern``, ``sitemap_pattern``,
    ``sitemap_exclude``, ``id_guard``, ``pacing_ms``, ``max_urls``) and the
    Instagram stanza keys (``username``, ``hashtag_filter``, ``content_mode``,
    ``image_text``) only when the registry stanza declares them: RSS stanzas
    keep their exact ``{"type", "policy"}`` shape, so RSS ingest behavior is
    unchanged. ``image_text`` is the one type-aware key: ``extract``
    (ADR-0014) survives only on an ``instagram`` stanza and is dropped
    elsewhere like an invalid value. Every feed/discovery fetch runs the
    impersonated chain:
    curl_cffi Chrome under ``BROWSER_HEADERS``, then the Jina reader, then
    Firecrawl; the ADR-0013 ``apify-premium`` lane is the Instagram exception
    and never issues such a fetch.
    The deprecated ``"stdlib-only"`` alias resolves back to
    ``"impersonated-feed"`` (accepted for back-compat, never a fetch
    identity), as do unknown policy values. Overrides come from the registry
    entry's ``"retrieval"`` mapping; entries without overrides yield the
    default (rss, impersonated-feed). Unknown (or missing) sources yield the
    default — never raises.
    """
    if source_name is None:
        return dict(DEFAULT_RETRIEVAL_POLICY)
    try:
        entry = get_source(source_name)
    except KeyError:
        return dict(DEFAULT_RETRIEVAL_POLICY)
    raw = entry.get("retrieval")
    if not isinstance(raw, dict):
        return dict(DEFAULT_RETRIEVAL_POLICY)
    rtype = raw.get("type", "rss")
    if rtype not in RETRIEVAL_TYPES:
        rtype = "rss"
    policy = raw.get("policy", "impersonated-feed")
    if not isinstance(policy, str):
        policy = "impersonated-feed"
    policy = RETRIEVAL_POLICY_ALIASES.get(policy, policy)
    if policy not in RETRIEVAL_POLICIES:
        policy = "impersonated-feed"
    return {"type": rtype, "policy": policy, **_validated_extras(raw, rtype)}


def get_retrieval_config(source_name: str | None) -> dict[str, Any]:
    """Return the full normalized retrieval stanza for `source_name`.

    Shape is ``{"type", "policy", "extractor", "sitemaps", "hub",
    "hub_pages", "link_pattern", "sitemap_pattern", "id_guard", "pacing_ms",
    "max_urls"}`` with every default filled:
    missing extractors yield ``"generic"``, missing sitemaps yield ``[]``,
    a missing hub falls back to the registry ``hub_url`` (None when unknown),
    missing hub_pages yield ``[]`` (single hub listing),
    missing patterns yield None, ``id_guard`` defaults to False,
    pacing yields 1000ms and backfill yields 50 URLs. ``sitemap_exclude``
    is carried only when the stanza declares it (no empty-list default),
    so stanzas without exclusions keep their exact established shape.
    The Instagram keys (``username``, ``hashtag_filter``, ``content_mode``,
    ``image_text``) are carried only for stanzas that declare any of them,
    with ``caption_first``/``ignore`` defaults for an undeclared
    ``content_mode``/``image_text`` — feed stanzas keep their exact shape.
    A declared ``image_text`` reaches the config unchanged (the ADR-0014
    ``extract`` opt-in included); non-Instagram stanzas never carry the key.
    Unknown (or missing) sources yield safe RSS defaults — never raises.
    Discovery consumes this, not ad-hoc dict reads.
    """
    policy = get_retrieval_policy(source_name)
    config: dict[str, Any] = {
        "type": policy["type"],
        "policy": policy["policy"],
        "extractor": str(policy.get("extractor", DEFAULT_EXTRACTOR)),
        "sitemaps": list(policy.get("sitemaps", [])),
        "hub": policy.get("hub"),
        "hub_pages": list(policy.get("hub_pages", [])),
        "link_pattern": policy.get("link_pattern"),
        "sitemap_pattern": policy.get("sitemap_pattern"),
        "id_guard": bool(policy.get("id_guard", False)),
        "pacing_ms": int(policy.get("pacing_ms", DEFAULT_PACING_MS)),
        "max_urls": int(policy.get("max_urls", DEFAULT_MAX_URLS)),
    }
    if "sitemap_exclude" in policy:
        # Declared-only: stanzas without exclusions keep their exact
        # established shape (see test_retrieval_config_fills_defaults_for_ticket_08).
        config["sitemap_exclude"] = list(policy["sitemap_exclude"])
    if policy["type"] == "instagram" or any(
        key in policy for key in ("username", "hashtag_filter", "content_mode", "image_text")
    ):
        # Instagram stanza (ADR-0013): declared-only, like sitemap_exclude, so
        # every feed stanza keeps its exact shape. The account/hashtag keys
        # default to None; the two mode keys carry their v1 defaults.
        config["username"] = policy.get("username")
        config["hashtag_filter"] = policy.get("hashtag_filter")
        config["content_mode"] = str(policy.get("content_mode", DEFAULT_CONTENT_MODE))
        config["image_text"] = str(policy.get("image_text", DEFAULT_IMAGE_TEXT))
    if config["hub"] is None and source_name is not None:
        try:
            hub_url = get_source(source_name).get("hub_url")
            if isinstance(hub_url, str) and hub_url.strip():
                config["hub"] = hub_url.strip()
        except KeyError:
            pass
    return config


def list_v1_sources() -> list[dict[str, Any]]:
    """Return registry entries for the V1 scope, in V1 order.

    V1 covers all curated sources in registry order (RSS plus
    sitemap/hub/url-set lanes, plus the ADR-0013 Instagram accounts);
    explicit ``sources=[...]`` still narrows period/flows queries to a subset.
    """
    return [get_source(name) for name in V1_SOURCES]
