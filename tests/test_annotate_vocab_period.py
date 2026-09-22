"""ADR-0016 vocabulary additions + period default filter — fakes, no live Postgres.

Locked contract:
- `TOPICS` gains exactly two additive regions — `colombia` and `argentina`,
  after `spain` — with their labels/synonyms; case/whitespace/separator
  variants canonicalize server-side and nothing is retired or removed.
- `period.DEFAULT_PERIOD_TOPICS` is the pinned 16-slug priority view and every
  slug in it is canonical.
- `service.get_period_context(topics=None)` substitutes that default and runs it
  through the same vocabulary path as a caller list, so the lane always receives
  canonical slugs; `topics=[]` is the explicit unfiltered bundle and an explicit
  list wins exactly (no merge with the default).
- Search keeps `topics=None` = unfiltered; unknown tags still 422 on both
  caller surfaces, before any query.
- The frozen key sets are byte-identical: SEARCH 21, period headline 13, period
  bundle {period, recent_articles}.

The fake connection mirrors the lane's SQL semantics (array overlap, importance
floor, per-source window cap) and records the SQL/params the lane receives, so
the assertions are about observable behavior: what the lane is asked for and
which Documents the bundle reports.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(os.path.dirname(_HERE), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import marketing_intelligence.period as period_lane  # noqa: E402
import marketing_intelligence.search as search_lane  # noqa: E402
import marketing_intelligence.service as service  # noqa: E402
import marketing_intelligence.topics as topics_lane  # noqa: E402
from api.app import app  # noqa: E402


def _load_mcp_server() -> Any:
    path = Path(_SRC) / "mcp" / "server.py"
    spec = importlib.util.spec_from_file_location("a16_mcp_server", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MCP_SERVER = _load_mcp_server()

#: A bearer token may be configured in the ambient environment, so the HTTP
#: assertions pin their own (auth is read from the environment per request).
TOKEN = "test-bearer-token-" + "v" * 32


@pytest.fixture()
def http(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("BRAIN_API_TOKEN", TOKEN)
    client = TestClient(app)
    client.headers.update({"Authorization": f"Bearer {TOKEN}"})
    return client


WEEK_FROM = date(2026, 9, 7)
WEEK_TO = date(2026, 9, 13)

#: ADR-0016's priority view, transcribed (order included) so a silent reorder or
#: slug rename cannot pass as "the default still applies".
EXPECTED_DEFAULT_TOPICS = (
    "gen-z",
    "consumer-behavior",
    "jewelry",
    "social-media",
    "creator-economy",
    "marketing",
    "brand-strategy",
    "ai",
    "latam",
    "mexico",
    "brazil",
    "colombia",
    "argentina",
    "report",
    "campaign",
    "earnings",
)

#: Frozen public key sets (search 21 / period headline 13), written out because
#: the ADR requires them byte-identical: no lane may reshape a payload.
FROZEN_SEARCH_KEYS = (
    "title",
    "url",
    "canonical_url",
    "source",
    "published_at",
    "author",
    "snippet",
    "flag_reason",
    "flag_detail",
    "flagged_at",
    "flagged_by",
    "read",
    "read_at",
    "read_by",
    "importance_score",
    "importance_rationale",
    "importance_reporter",
    "importance_updated_at",
    "topics",
    "has_image_text",
    "readers",
)
FROZEN_PERIOD_HEADLINE_KEYS = (
    "title",
    "url",
    "canonical_url",
    "published_at",
    "author",
    "read",
    "read_at",
    "read_by",
    "readers",
    "flag_reason",
    "importance_score",
    "topics",
    "has_image_text",
)
FROZEN_PERIOD_KEYS = ("period", "recent_articles")


# --- fake store (mirrors the lane's SQL semantics) -----------------------------


def _utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


def _doc(
    doc_id: int,
    title: str,
    source: str,
    topics: list[str],
    *,
    score: float | None = None,
    published_at: datetime | None = None,
) -> dict[str, Any]:
    url = f"https://example.test/{doc_id}/"
    return {
        "id": doc_id,
        "title": title,
        "url": url,
        "canonical_url": url,
        "source": source,
        "published_at": published_at or _utc(2026, 9, 8, 12),
        "author": "A",
        "content": f"Body about {title}.",
        "flag_reason": None,
        "read_at": None,
        "read_by": None,
        "readers": None,
        "read_ats": None,
        "importance_score": score,
        "topics": list(topics),
    }


#: Four Documents that separate the three states the period default decides:
#: on-default, annotated off-vocabulary, and never annotated at all.
DOCS = [
    _doc(
        1, "Jewelry rebound", "JCK Online", ["jewelry"], score=0.9, published_at=_utc(2026, 9, 8, 9)
    ),
    _doc(
        2,
        "Colombia desk expands",
        "Exame",
        ["colombia"],
        score=0.7,
        published_at=_utc(2026, 9, 9, 9),
    ),
    _doc(
        3,
        "Retail footfall dips",
        "Retail Dive",
        ["retail"],
        score=0.95,
        published_at=_utc(2026, 9, 10, 9),
    ),
    _doc(4, "Untagged launch", "JCK Online", [], score=0.85, published_at=_utc(2026, 9, 11, 9)),
    _doc(
        5,
        "Loyalty shift",
        "MarTech",
        ["consumer-behavior"],
        score=0.6,
        published_at=_utc(2026, 9, 12, 9),
    ),
]

#: Everything the default view keeps (docs 3 and 4 stay out).
ON_DEFAULT_TITLES = {"Jewelry rebound", "Colombia desk expands", "Loyalty shift"}
ALL_TITLES = {doc["title"] for doc in DOCS}


class _BundleCursor:
    """Records the lane's SQL/params and applies the documented filter params."""

    def __init__(self, store: list[dict[str, Any]]) -> None:
        self._store = store
        self.last_sql = ""
        self.last_params: tuple[Any, ...] = ()
        self.topics_param: list[str] | None = None
        self._rows: list[dict[str, Any]] = []

    @property
    def has_topic_predicate(self) -> bool:
        return "tps.topics && %s::text[]" in self.last_sql

    def execute(self, sql: str, params: tuple | None = None) -> _BundleCursor:
        self.last_sql = " ".join(sql.split())
        self.last_params = tuple(params or ())
        index = 3  # bounds, source names, then the optional annotation filters
        floor: float | None = None
        self.topics_param = None
        if "imp.score >= %s" in self.last_sql:
            floor = self.last_params[index]
            index += 1
        if self.has_topic_predicate:
            self.topics_param = list(self.last_params[index])
        per_source = int(self.last_params[-1])
        kept = [doc for doc in self._store if _matches(doc, floor=floor, topics=self.topics_param)]
        kept.sort(key=lambda doc: doc["published_at"], reverse=True)
        seen: dict[str, int] = {}
        capped: list[dict[str, Any]] = []
        for doc in kept:
            rank = seen.get(doc["source"], 0) + 1
            seen[doc["source"]] = rank
            if rank <= per_source:
                capped.append(doc)
        self._rows = capped
        return self

    def fetchall(self) -> list[dict[str, Any]]:
        return list(self._rows)


def _matches(doc: dict[str, Any], *, floor: float | None, topics: list[str] | None) -> bool:
    if floor is not None and not (
        doc["importance_score"] is not None and doc["importance_score"] >= floor
    ):
        return False
    if topics and not (set(doc["topics"]) & set(topics)):
        return False
    return True


class _BundleConnection:
    def __init__(self, store: list[dict[str, Any]]) -> None:
        self.store = list(store)
        self.cursors: list[_BundleCursor] = []

    def execute(self, sql: str, params: tuple | None = None) -> _BundleCursor:
        cursor = _BundleCursor(self.store)
        self.cursors.append(cursor)
        return cursor.execute(sql, params)


def _titles(bundle: dict[str, Any]) -> set[str]:
    return {item["title"] for group in bundle["recent_articles"] for item in group["articles"]}


# --- vocabulary: two additive regions -----------------------------------------


def test_registry_gains_colombia_and_argentina_after_spain() -> None:
    assert len(topics_lane.TOPICS) == 42
    entries = topics_lane.list_vocabulary()
    slugs = [entry["slug"] for entry in entries]
    # Registry order is the reading order: both new regions sit in the region
    # block, after `spain` and before the content types begin.
    assert slugs.index("spain") < slugs.index("colombia") < slugs.index("argentina")
    assert slugs.index("argentina") < slugs.index("news")
    by_slug = {entry["slug"]: entry for entry in entries}
    for slug, label, synonym in (
        ("colombia", "Colombia", "republic of colombia"),
        ("argentina", "Argentina", "argentine republic"),
    ):
        assert by_slug[slug]["kind"] == "region"
        assert by_slug[slug]["label"] == label
        assert synonym in by_slug[slug]["synonyms"]
        assert by_slug[slug]["retired_alias_of"] is None  # additive, nothing retired


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("colombia", "colombia"),
        ("Colombia", "colombia"),
        ("  COLOMBIA  ", "colombia"),
        ("Republic of Colombia", "colombia"),
        ("republic_of_colombia", "colombia"),
        ("argentina", "argentina"),
        ("Argentina", "argentina"),
        ("ARGENTINE REPUBLIC", "argentina"),
        ("Argentine-Republic", "argentina"),
    ],
)
def test_new_region_spellings_canonicalize(raw: str, expected: str) -> None:
    assert topics_lane.canonicalize_topic(raw) == expected


# --- the pinned default --------------------------------------------------------


def test_default_period_topics_is_the_pinned_canonical_view() -> None:
    assert period_lane.DEFAULT_PERIOD_TOPICS == EXPECTED_DEFAULT_TOPICS
    assert len(period_lane.DEFAULT_PERIOD_TOPICS) == 16
    for slug in EXPECTED_DEFAULT_TOPICS:
        entry = topics_lane.TOPICS[slug]
        assert entry.get("retired_alias_of") is None
        assert topics_lane.canonicalize_topic(slug) == slug


def test_default_is_filtered_before_the_lane_runs() -> None:
    """`topics=None` means the priority view, handed over as canonical slugs."""
    conn = _BundleConnection(list(DOCS))
    bundle = service.get_period_context(WEEK_FROM, WEEK_TO, conn=conn)
    cursor = conn.cursors[-1]

    assert cursor.has_topic_predicate
    assert cursor.topics_param == list(period_lane.DEFAULT_PERIOD_TOPICS)
    assert _titles(bundle) == ON_DEFAULT_TITLES
    # The two Documents the prior unfiltered default would have paid tokens for.
    assert "Retail footfall dips" not in _titles(bundle)  # annotated, off the view
    assert "Untagged launch" not in _titles(bundle)  # never annotated


def test_default_runs_through_the_vocabulary_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """A future default is canonicalized exactly like a caller list."""
    conn = _BundleConnection(list(DOCS))
    monkeypatch.setattr(
        period_lane,
        "DEFAULT_PERIOD_TOPICS",
        ("consumer-trends", "jewellery"),  # retired alias + accepted synonym
    )
    bundle = service.get_period_context(WEEK_FROM, WEEK_TO, conn=conn)

    assert conn.cursors[-1].topics_param == ["consumer-behavior", "jewelry"]
    assert _titles(bundle) == {"Loyalty shift", "Jewelry rebound"}


def test_empty_topics_is_the_unfiltered_bundle() -> None:
    conn = _BundleConnection(list(DOCS))
    bundle = service.get_period_context(WEEK_FROM, WEEK_TO, conn=conn, topics=[])
    cursor = conn.cursors[-1]

    assert not cursor.has_topic_predicate  # no predicate, and no filter param
    assert len(cursor.last_params) == 4  # bounds, sources, limit
    assert _titles(bundle) == ALL_TITLES


def test_explicit_topics_win_exactly() -> None:
    conn = _BundleConnection(list(DOCS))
    bundle = service.get_period_context(WEEK_FROM, WEEK_TO, conn=conn, topics=["retail"])
    assert conn.cursors[-1].topics_param == ["retail"]  # never merged with default
    assert _titles(bundle) == {"Retail footfall dips"}

    # A caller spelling goes through the same canonicalization the default does.
    synonym_conn = _BundleConnection(list(DOCS))
    bundle = service.get_period_context(WEEK_FROM, WEEK_TO, conn=synonym_conn, topics=["jewellery"])
    assert synonym_conn.cursors[-1].topics_param == ["jewelry"]
    assert _titles(bundle) == {"Jewelry rebound"}


def test_unknown_region_tag_still_rejected_before_the_lane(http: TestClient) -> None:
    conn = _BundleConnection(list(DOCS))
    with pytest.raises(service.InvalidRequest):
        service.get_period_context(WEEK_FROM, WEEK_TO, conn=conn, topics=["quantum-colombia"])
    assert conn.cursors == []  # rejected before any query

    resp = http.post(
        "/period-context",
        json={
            "from_date": "2026-09-07",
            "to_date": "2026-09-13",
            "topics": ["quantum-colombia"],
        },
    )
    assert resp.status_code == 422
    with pytest.raises(service.InvalidRequest) as excinfo:
        service.get_period_context(
            WEEK_FROM, WEEK_TO, conn=_BundleConnection([]), topics=["quantum-colombia"]
        )
    assert resp.json() == {"detail": str(excinfo.value)}


# --- the default stays on the period lane --------------------------------------


def test_search_keeps_none_unfiltered(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_search(keyword: str, **kwargs: Any) -> list[dict[str, Any]]:
        seen.update(kwargs, keyword=keyword)
        return []

    monkeypatch.setattr(search_lane, "search_articles", fake_search)
    assert service.search_articles("jewelry") == []
    assert seen["topics"] is None  # the ADR default never reaches search
    assert seen["min_importance"] is None

    service.search_articles("jewelry", topics=["jewellery"])
    assert seen["topics"] == ["jewelry"]  # caller list, canonicalized as before


def test_http_and_mcp_apply_the_same_period_default(
    http: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    received: list[list[str] | None] = []

    def fake_lane(from_date: object, to_date: object, **kwargs: Any) -> dict[str, Any]:
        received.append(kwargs["topics"])
        return {"period": {}, "recent_articles": []}

    monkeypatch.setattr(period_lane, "get_period_context", fake_lane)
    body = {"from_date": "2026-09-07", "to_date": "2026-09-13"}
    expected = {"period": {}, "recent_articles": []}

    assert http.post("/period-context", json=body).json() == expected
    assert MCP_SERVER.get_period_context(**body) == expected
    assert received == [list(period_lane.DEFAULT_PERIOD_TOPICS)] * 2


# --- frozen key sets -----------------------------------------------------------


def test_key_sets_are_byte_identical() -> None:
    assert service.SEARCH_RESULT_KEYS == FROZEN_SEARCH_KEYS
    assert service.PERIOD_ARTICLE_KEYS == FROZEN_PERIOD_HEADLINE_KEYS
    assert len(service.SEARCH_RESULT_KEYS) == 21
    assert len(service.PERIOD_ARTICLE_KEYS) == 13

    bundle = service.get_period_context(WEEK_FROM, WEEK_TO, conn=_BundleConnection(list(DOCS)))
    assert tuple(bundle) == FROZEN_PERIOD_KEYS
    assert set(bundle["period"]) == {"from", "to", "timezone"}
    groups = bundle["recent_articles"]
    assert groups
    for group in groups:
        assert set(group) == {"source", "articles"}
        for item in group["articles"]:
            assert tuple(item) == FROZEN_PERIOD_HEADLINE_KEYS
