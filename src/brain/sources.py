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


SOURCE_NAME_SMT = "Social Media Today"

#: Default thin threshold (chars) for the enrichment policy when a source
#: carries no override. Mirrors `brain.enrich.DEFAULT_THIN_THRESHOLD`.
DEFAULT_ENRICHMENT_THRESHOLD = 500

#: Allowed enrichment modes: thin-only, enrich everything, enrich nothing.
ENRICHMENT_MODES: tuple[str, ...] = ("auto", "force_on", "force_off")

#: Policy returned for unknown sources and entries without overrides.
DEFAULT_ENRICHMENT_POLICY: dict[str, Any] = {
    "threshold": DEFAULT_ENRICHMENT_THRESHOLD,
    "mode": "auto",
}

V1_SOURCES: tuple[str, ...] = (
    SOURCE_NAME_SMT,
    "MarTech",
    "Professional Jeweller",
    "InfoMoney",
)

_FALLBACK_SMT: dict[str, Any] = {
    "name": SOURCE_NAME_SMT,
    "rss_url": "https://www.socialmediatoday.com/feeds/news/",
    "hub_url": "https://www.socialmediatoday.com/",
    "language": "en",
}


def _curated_path() -> Path | None:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / ".scratch" / "trend-intelligence-brain" / "curated-sources.json"
        if candidate.is_file():
            return candidate
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
            if not isinstance(entry, dict) or not entry.get("rss_url"):
                continue
            name = str(entry.get("source_name", "")).strip()
            if not name:
                continue
            registry[name] = {
                "name": name,
                "rss_url": entry["rss_url"],
                "hub_url": entry.get("hub_url"),
                "language": normalize_language(entry.get("language")),
                "raw": entry,
            }
            overrides = entry.get("enrichment")
            if isinstance(overrides, dict):
                # Per-source enrichment policy (ticket 03): file-based
                # {"threshold": int, "mode": "auto"|"force_on"|"force_off"}.
                registry[name]["enrichment"] = dict(overrides)
    registry.setdefault(SOURCE_NAME_SMT, dict(_FALLBACK_SMT))
    return registry


def list_sources() -> list[dict[str, Any]]:
    """Return all RSS-capable curated sources (registry order)."""
    return [dict(entry) for entry in _registry().values()]


def get_source(name: str) -> dict[str, Any]:
    """Return the registry entry for `name`; raises KeyError when unknown."""
    try:
        return dict(_registry()[name])
    except KeyError:
        raise KeyError(f"Unknown source: {name!r}") from None


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


def list_v1_sources() -> list[dict[str, Any]]:
    """Return registry entries for the V1 scope, in V1 order.

    Extra registry entries (e.g. JCK Online, Swarovski PR Newswire) stay
    ingestible by explicit name; they are simply outside V1 defaults.
    """
    return [get_source(name) for name in V1_SOURCES]
