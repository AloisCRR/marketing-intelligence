"""ADR-0013 Instagram premium lane: registry row + declarative stanza.

Observable behavior (not privates):
- the curated stanza resolves through both the dev override and the baked-in
  registry, selecting the ``instagram`` lane with the ``apify-premium`` policy
  (never the RSS/impersonated-feed fallback)
- the four account keys survive ``_validated_extras`` and reach
  ``get_retrieval_config`` with their declared curated defaults
- ``image_text: extract`` (ADR-0014) survives only on an ``instagram``
  stanza; every other stanza type drops it like an invalid value
- enrichment is bypassed (``force_off``): captions are final
- the deterministic uuid5 id and the ``SEED_SOURCES`` row agree, and the name
  is in the curated scope
- no secret/token material appears in either registry copy
"""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any

import pytest

import marketing_intelligence.db as db
import marketing_intelligence.sources as sources
from marketing_intelligence.sources import (
    CURATED_SOURCES,
    RETRIEVAL_POLICIES,
    RETRIEVAL_TYPES,
    get_enrichment_policy,
    get_retrieval_config,
    get_retrieval_policy,
    get_source,
)

ROOT = Path(__file__).resolve().parents[1]
DEV_REGISTRY = ROOT / ".scratch" / "marketing-intelligence" / "curated-sources.json"
SHIPPED_REGISTRY = ROOT / "src" / "marketing_intelligence" / "data" / "curated-sources.json"

IG = "ig:sabrikolod"
IG_HUB = "https://www.instagram.com/sabrikolod/"
IG_STANZA: dict[str, Any] = {
    "type": "instagram",
    "policy": "apify-premium",
    "username": "sabrikolod",
    "hashtag_filter": "ChismecitoMarketinero",
    "content_mode": "caption_first",
    "image_text": "ignore",
}

JORDI = "ig:jordisanildefonso"
JORDI_HUB = "https://www.instagram.com/jordisanildefonso/"
JORDI_STANZA: dict[str, Any] = {
    "type": "instagram",
    "policy": "apify-premium",
    "username": "jordisanildefonso",
    "content_mode": "caption_first",
    "image_text": "extract",
}

_SECRET_RE = re.compile(r"token|secret|api[_-]?key|password|bearer|credential", re.IGNORECASE)


def _entries(path: Path) -> dict[str, dict[str, Any]]:
    return {e["source_name"]: e for e in json.loads(path.read_text(encoding="utf-8"))}


def _staged_stanza(monkeypatch: pytest.MonkeyPatch, name: str, stanza: dict[str, Any]) -> None:
    """Stage `stanza` as `name`'s retrieval stanza; both registry files untouched."""

    def fake_get_source(source_name: str) -> dict[str, Any]:
        return {
            "name": source_name,
            "cadence": "Daily",
            "raw": {},
            "retrieval": dict(stanza) if source_name == name else None,
        }

    monkeypatch.setattr(sources, "get_source", fake_get_source)


# --- curated stanza + registry resolution -------------------------------------


def test_ig_stanza_is_declared_identically_in_both_registry_copies() -> None:
    dev = _entries(DEV_REGISTRY)[IG]
    shipped = _entries(SHIPPED_REGISTRY)[IG]
    assert dev == shipped
    assert dev["retrieval"] == IG_STANZA
    assert dev["rss_url"] is None
    assert dev["hub_url"] == IG_HUB
    assert dev["language"] == "Spanish"
    assert dev["cadence"] == "Daily"
    assert dev["enrichment"] == {"threshold": 500, "mode": "force_off"}


def test_ig_source_loads_from_the_dev_override() -> None:
    src = get_source(IG)
    assert src["name"] == IG
    assert src["language"] == "es"
    assert src["hub_url"] == IG_HUB
    assert src["rss_url"] is None
    assert src["cadence"] == "Daily"


def test_ig_source_loads_from_the_baked_in_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The image ships `data/curated-sources.json`; it must resolve standalone."""
    monkeypatch.setattr(sources, "_curated_path", lambda: SHIPPED_REGISTRY)
    sources._registry.cache_clear()
    try:
        assert get_source(IG)["hub_url"] == IG_HUB
        assert get_retrieval_policy(IG) == IG_STANZA
    finally:
        sources._registry.cache_clear()


# --- retrieval lane: instagram/apify-premium, not the rss fallback -------------


def test_retrieval_type_and_policy_are_registered() -> None:
    assert "instagram" in RETRIEVAL_TYPES
    assert "apify-premium" in RETRIEVAL_POLICIES


def test_policy_is_instagram_apify_premium_not_rss_fallback() -> None:
    policy = get_retrieval_policy(IG)
    assert policy["type"] == "instagram"
    assert policy["policy"] == "apify-premium"
    assert policy != {"type": "rss", "policy": "impersonated-feed"}
    assert policy == IG_STANZA


def test_config_carries_the_four_account_extras() -> None:
    config = get_retrieval_config(IG)
    assert config["type"] == "instagram"
    assert config["policy"] == "apify-premium"
    assert config["username"] == "sabrikolod"
    assert config["hashtag_filter"] == "ChismecitoMarketinero"
    assert config["content_mode"] == "caption_first"
    assert config["image_text"] == "ignore"
    assert config["hub"] == IG_HUB


def test_invalid_extras_drop_and_config_fills_lane_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_get_source = sources.get_source

    def fake_get_source(name: str) -> dict[str, Any]:
        entry = real_get_source(name)
        if name == IG:
            entry["retrieval"] = {
                "type": "instagram",
                "policy": "apify-premium",
                "username": "",
                "hashtag_filter": 7,
                "content_mode": "bogus",
                "image_text": "bogus",
            }
        return entry

    monkeypatch.setattr(sources, "get_source", fake_get_source)
    assert sources.get_retrieval_policy(IG) == {
        "type": "instagram",
        "policy": "apify-premium",
    }
    config = sources.get_retrieval_config(IG)
    assert config["username"] is None
    assert config["hashtag_filter"] is None
    assert config["content_mode"] == "caption_first"
    assert config["image_text"] == "ignore"


def test_image_text_extract_reaches_the_instagram_lane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR-0014 opt-in: the lane switches `dataDetailLevel` on this exact value."""
    _staged_stanza(
        monkeypatch,
        IG,
        {"type": "instagram", "policy": "apify-premium", "image_text": "extract"},
    )
    assert sources.get_retrieval_policy(IG)["image_text"] == "extract"
    config = sources.get_retrieval_config(IG)
    assert config["image_text"] == "extract"
    assert config["type"] == "instagram"


def test_image_text_extract_drops_on_an_rss_stanza(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-Instagram stanzas drop it like any invalid value; shape is untouched."""
    _staged_stanza(
        monkeypatch,
        "Staged Feed",
        {"type": "rss", "policy": "impersonated-feed", "image_text": "extract"},
    )
    assert sources.get_retrieval_policy("Staged Feed") == {
        "type": "rss",
        "policy": "impersonated-feed",
    }
    dropped = sources.get_retrieval_config("Staged Feed")
    assert "image_text" not in dropped
    assert "content_mode" not in dropped
    _staged_stanza(monkeypatch, "Staged Feed", {"type": "rss", "policy": "impersonated-feed"})
    assert dropped == sources.get_retrieval_config("Staged Feed")


def test_undeclared_image_text_still_defaults_to_ignore(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _staged_stanza(monkeypatch, IG, {"type": "instagram", "policy": "apify-premium"})
    config = sources.get_retrieval_config(IG)
    assert config["image_text"] == "ignore"
    assert config["content_mode"] == "caption_first"


def test_enrichment_is_bypassed_for_the_instagram_lane() -> None:
    assert get_enrichment_policy(IG) == {"threshold": 500, "mode": "force_off"}


# --- source row: deterministic id, seed tuple, curated scope -------------------


def test_source_uuid_is_deterministic_and_stable() -> None:
    assert isinstance(db.source_uuid(IG), uuid.UUID)
    # Same input scheme as migration 007 / the seeded `sources.id` column.
    assert db.source_uuid(IG) == uuid.uuid5(
        uuid.NAMESPACE_DNS, f"trend-intelligence-brain:source:{IG}"
    )
    assert db.source_uuid(IG) != db.source_uuid("InfoMoney")


def test_seed_sources_carries_the_ig_row_once() -> None:
    row = (IG, None, IG_HUB, "es")
    assert row in db.SEED_SOURCES
    assert [name for name, _, _, _ in db.SEED_SOURCES].count(IG) == 1


def test_ig_is_in_curated_scope() -> None:
    assert IG in CURATED_SOURCES
    assert [e["name"] for e in sources.list_curated_sources()].count(IG) == 1


def test_jordi_stanza_is_declared_identically_in_both_registry_copies() -> None:
    dev = _entries(DEV_REGISTRY)[JORDI]
    shipped = _entries(SHIPPED_REGISTRY)[JORDI]
    assert dev == shipped
    assert dev["retrieval"] == JORDI_STANZA
    assert dev["rss_url"] is None
    assert dev["hub_url"] == JORDI_HUB
    assert dev["language"] == "Spanish"
    assert dev["cadence"] == "Daily"
    assert dev["enrichment"] == {"threshold": 500, "mode": "force_off"}


def test_seed_sources_carries_the_jordi_row_once() -> None:
    row = (JORDI, None, JORDI_HUB, "es")
    assert row in db.SEED_SOURCES
    assert [name for name, _, _, _ in db.SEED_SOURCES].count(JORDI) == 1


def test_jordi_is_in_curated_scope() -> None:
    assert JORDI in CURATED_SOURCES
    assert [e["name"] for e in sources.list_curated_sources()].count(JORDI) == 1


# --- secret hygiene -----------------------------------------------------------


def test_registry_copies_carry_no_secret() -> None:
    for path in (DEV_REGISTRY, SHIPPED_REGISTRY):
        text = path.read_text(encoding="utf-8")
        assert not _SECRET_RE.search(text), path
    entry = get_source(IG)
    assert not _SECRET_RE.search(json.dumps(entry, ensure_ascii=False))
    assert "APIFY_API_TOKEN" not in json.dumps(entry)
