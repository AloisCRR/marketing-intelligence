"""National Jeweler extraction repair (Ticket 15).

Observable behavior (not privates):
- A National Jeweler Document's stored body is clean Markdown: the lede comes
  first, sentence text stays intact, and the related-articles block, the
  "The Latest" sidebar, and nav/footer chrome never reach the body.
- Inline editorial anchors become plain words (no markdown links, no URLs);
  non-NJ sources keep their links (guarded by existing enrichment tests).
- Extraction keeps full provenance: title, URLs, author, timestamps,
  language, and a content hash over the cleaned text.
- The harvested (keep) body and the enriched fetch path share the same
  cleaning; the pre-repair generic fallback body is pinned as polluted
  before, clean after.

All network is fixture-backed; no live HTTP.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from marketing_intelligence.discovery import extract_article
from marketing_intelligence.enrich import clean_to_markdown, enrich_document_or_keep

FIXTURES = Path(__file__).parent / "fixtures"

NJ = "National Jeweler"
NJ_GENZ = (
    "https://nationaljeweler.com/articles/"
    "15300-gen-z-shoppers-are-buying-fine-jewelry-for-themselves"
)
NJ_CLOSURE = (
    "https://nationaljeweler.com/articles/15301-family-owned-jewelry-store-to-close-after-62-years"
)

#: Chrome markers that must never appear in a cleaned National Jeweler body.
CHROME = ("The Latest", "Related Articles", "Newsletter", "Home", "©")

CASES = [
    pytest.param(
        "nj_article_genz_polluted.html",
        "nj_article_genz_clean.md",
        NJ_GENZ,
        "Gen Z Shoppers Are Buying Fine Jewelry for Themselves",
        "Lenore Fedow",
        "A new survey finds self-purchase is now the leading reason",
        id="gen-z-self-purchase",
    ),
    pytest.param(
        "nj_article_closure_polluted.html",
        "nj_article_closure_clean.md",
        NJ_CLOSURE,
        "Family-Owned Jewelry Store to Close After 62 Years",
        "Sam Reyes",
        "The third-generation retailer in downtown Boise will close in November",
        id="store-closure",
    ),
]


def _text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("html_name, clean_name, url, title, author, lede", CASES)
def test_nj_body_is_clean_and_lede_first(
    html_name: str, clean_name: str, url: str, title: str, author: str, lede: str
) -> None:
    markdown = clean_to_markdown(_text(html_name), url)

    # Pinned before/after: the polluted page cleans to exactly this body.
    assert markdown == _text(clean_name)
    assert markdown.startswith(lede)
    for chrome in CHROME:
        assert chrome not in markdown
    assert "](" not in markdown and "http" not in markdown


@pytest.mark.parametrize("html_name, clean_name, url, title, author, lede", CASES)
def test_nj_extraction_keeps_full_provenance(
    html_name: str, clean_name: str, url: str, title: str, author: str, lede: str
) -> None:
    published = datetime(2026, 9, 8, tzinfo=UTC)
    retrieved = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
    doc = extract_article(
        url=url,
        final_url=url,
        html=_text(html_name),
        source=NJ,
        language="en",
        retrieved_at=retrieved,
        fallback_published=published,
    )

    assert doc.content == _text(clean_name)
    assert doc.content.startswith(lede)
    assert not any(chrome in doc.content for chrome in CHROME)
    assert doc.title == title
    assert doc.author == author
    assert doc.url == url
    assert doc.canonical_url == url
    assert doc.published_at == published
    assert doc.retrieved_at == retrieved
    assert doc.language == "en"
    assert doc.source == NJ
    assert doc.content_hash


@pytest.mark.parametrize("html_name, clean_name, url, title, author, lede", CASES)
def test_nj_reingest_is_deterministic_and_clean(
    html_name: str, clean_name: str, url: str, title: str, author: str, lede: str
) -> None:
    # A previously flagged item re-ingests clean and byte-identical, so the
    # ON CONFLICT DO NOTHING upsert stays a no-op and no chrome is stored.
    kwargs = {
        "url": url,
        "final_url": url,
        "html": _text(html_name),
        "source": NJ,
        "language": "en",
        "retrieved_at": datetime(2026, 9, 10, 12, 0, tzinfo=UTC),
    }
    first = extract_article(**kwargs)
    second = extract_article(**kwargs)
    assert first.content_hash == second.content_hash
    assert first.content == _text(clean_name)


def test_nj_post_processing_flattens_anchors_for_the_family() -> None:
    from marketing_intelligence import enrich

    html = _text("nj_article_genz_polluted.html")

    # Generic (non-family) URL: trafilatura keeps inline editorial anchors as
    # Markdown links and chrome is already dropped by the converter itself.
    generic = enrich.clean_to_markdown(html, "https://example.com/articles/15300")
    assert "](" in generic and "http" in generic
    for chrome in CHROME:
        assert chrome not in generic

    # Family URL: the Markdown-level cleanup flattens anchors to plain words.
    repaired = enrich.clean_to_markdown(html, NJ_GENZ)
    assert repaired != generic
    for chrome in CHROME:
        assert chrome not in repaired
    assert "](" not in repaired and "http" not in repaired
    assert "New York—Self-purchase" in repaired  # sentence text intact


def test_harvest_and_enriched_paths_share_the_clean_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marketing_intelligence import enrich

    html = _text("nj_article_genz_polluted.html")
    expected = _text("nj_article_genz_clean.md")

    # Enriched path: the fetch leg hands the page to the same cleaner.
    monkeypatch.setattr(
        enrich, "_fetch_primary_bytes", lambda url, timeout=15: html.encode("utf-8")
    )
    assert enrich.fetch_and_clean(NJ_GENZ) == expected

    # Harvest/keep path: a substantive cleaned document is kept untouched.
    doc = extract_article(
        url=NJ_GENZ,
        final_url=NJ_GENZ,
        html=html,
        source=NJ,
        language="en",
        retrieved_at=datetime(2026, 9, 10, 12, 0, tzinfo=UTC),
    )
    kept, method, cause = enrich_document_or_keep(doc)
    assert kept is doc
    assert method == "rss" and cause is None
    assert kept.content == expected
