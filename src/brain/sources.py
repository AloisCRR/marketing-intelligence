"""Source registry — curated origins seeded from curated-sources.json.

Registry lookup is DB-free so adapters and unit tests never need Postgres.
(The foundation lane separately seeds the `sources` table for persistence.)
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

SOURCE_NAME_SMT = "Social Media Today"

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
                "language": (entry.get("language") or "English").lower()[:2]
                if entry.get("language")
                else "en",
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
