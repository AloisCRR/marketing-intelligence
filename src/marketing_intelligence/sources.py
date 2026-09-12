"""Source registry — curated origins seeded from curated-sources.json.

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
#: URL set; ``url-set+hub`` pairs the set with hub-anchor fallback. Retrieval
#: stanzas are config, not code forks — downstream ingest/flows behavior for
#: RSS sources is unchanged.
RETRIEVAL_TYPES: tuple[str, ...] = (
    "rss",
    "sitemap",
    "hub",
    "sitemap+hub",
    "url-set",
    "url-set+hub",
)

#: Allowed feed retrieval policies. The stdlib lanes are gone: every fetch
#: runs the impersonated chain (curl_cffi Chrome + browser headers → Jina
#: reader → Firecrawl), each leg explicit about its own failure.
RETRIEVAL_POLICIES: tuple[str, ...] = ("impersonated-feed",)

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

#: Default per-host pacing between requests (ms) when a stanza declares none.
DEFAULT_PACING_MS = 1000

#: Default backfill bound (newest N URLs per Source first) when undeclared.
DEFAULT_MAX_URLS = 50

#: Policy returned for unknown sources and entries without overrides.
DEFAULT_RETRIEVAL_POLICY: dict[str, Any] = {
    "type": "rss",
    "policy": "impersonated-feed",
}

V1_SOURCES: tuple[str, ...] = (
    "JCK Online",
    "National Jeweler",
    "Professional Jeweller",
    "Exame",
    "Modaes",
    "Retail Dive",
    "Jing Daily",
    SOURCE_NAME_SMT,
    "Consumidor Moderno",
    "Meio & Mensagem",
    "Marketing Dive",
    "MarTech",
    "MarketingDirecto",
    "Propmark",
    "Insider Latam",
    "LVMH Press Releases",
    "Richemont Media",
    "Swarovski PR Newswire",
    "InfoMoney",
    "Forbes México",
)

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


def _validated_extras(raw: dict[str, Any]) -> dict[str, Any]:
    """Validate optional retrieval-stanza keys; drop invalid ones silently.

    Never raises: unconfigured/invalid extras simply fall back to defaults
    (generic extractor, no sitemaps, registry hub, no link pattern, default
    pacing/backfill). Callers must not rely on invalid values surviving.
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

    Result shape is ``{"type": ..., "policy": "impersonated-feed"}``
    plus validated optional discovery keys (``extractor``, ``sitemaps``,
    ``hub``, ``hub_pages``, ``link_pattern``, ``sitemap_pattern``,
    ``sitemap_exclude``, ``id_guard``, ``pacing_ms``, ``max_urls``) only when the
    registry stanza declares them: RSS stanzas keep their exact
    ``{"type", "policy"}`` shape, so RSS ingest behavior is unchanged.
    Every fetch runs the impersonated chain: curl_cffi Chrome under
    ``BROWSER_HEADERS``, then the Jina reader, then Firecrawl.
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
    return {"type": rtype, "policy": policy, **_validated_extras(raw)}


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

    V1 covers all 20 curated sources in registry order (RSS plus
    sitemap/hub/url-set lanes); explicit ``sources=[...]`` still narrows
    period/flows queries to a subset.
    """
    return [get_source(name) for name in V1_SOURCES]
