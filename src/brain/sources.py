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


def list_v1_sources() -> list[dict[str, Any]]:
    """Return registry entries for the V1 scope, in V1 order.

    Extra registry entries (e.g. JCK Online, Swarovski PR Newswire) stay
    ingestible by explicit name; they are simply outside V1 defaults.
    """
    return [get_source(name) for name in V1_SOURCES]
