"""Thin-triggered Markdown enrichment tests (ticket 02).

Contract under test:
- sufficient RSS bodies skip enrichment untouched (zero fetch cost)
- thin/missing bodies get clean Markdown stored, hash over stored text
- every failure mode keeps RSS + records cause; the run completes
- reruns insert nothing new; no model/LLM calls on the enrichment path

All I/O is faked (monkeypatch, no network, no Postgres).
"""

from __future__ import annotations

import sys
import urllib.error
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from prefect_harness import no_engine

from marketing_intelligence.normalize import NormalizedDocument, content_hash_for, make_document

THIN_URL = "https://example.com/articles/thin-story"
FULL_URL = "https://example.com/articles/full-story"

NOW = datetime(2026, 9, 4, 12, 0, 0, tzinfo=UTC)


def _thin_doc(url: str = THIN_URL, content: str = "Short summary.") -> NormalizedDocument:
    return make_document(
        source="Social Media Today",
        url=url,
        title="Thin Story",
        content=content,
        published_at=NOW,
        retrieved_at=NOW,
        language="en",
    )


def _full_doc() -> NormalizedDocument:
    return make_document(
        source="Social Media Today",
        url=FULL_URL,
        title="Full Story",
        content="x" * 600,
        published_at=NOW,
        retrieved_at=NOW,
        language="en",
    )


# --- is_thin -----------------------------------------------------------------


def test_is_thin_empty_and_whitespace_only() -> None:
    from marketing_intelligence.enrich import is_thin

    assert is_thin("") is True
    assert is_thin("   \n\t  ") is True


def test_is_thin_threshold_boundary() -> None:
    from marketing_intelligence.enrich import DEFAULT_THIN_THRESHOLD, is_thin

    assert DEFAULT_THIN_THRESHOLD == 500
    assert is_thin("x" * 499) is True
    assert is_thin("x" * 500) is False
    assert is_thin("x" * 501) is False


def test_is_thin_collapses_whitespace_before_measuring() -> None:
    from marketing_intelligence.enrich import is_thin

    assert is_thin("  a  b  ", threshold=10) is True  # collapses to "a b"
    assert is_thin("  a  b  ", threshold=3) is False
    assert is_thin("x" * 400, threshold=500) is True
    assert is_thin("x" * 400, threshold=100) is False


# --- clean_to_markdown --------------------------------------------------------


def test_clean_to_markdown_strips_tags_preserves_paragraphs() -> None:
    from marketing_intelligence.enrich import clean_to_markdown

    html = "<article><h1>Head</h1><p>First &amp; paragraph.</p><p>Second <b>bold</b> one.</p></article>"
    md = clean_to_markdown(html, THIN_URL)
    assert "<" not in md and ">" not in md
    assert "First & paragraph." in md
    assert "Second" in md and "bold" in md and "one." in md  # <b> may stay **bold**
    assert "\n\n" in md  # paragraph breaks preserved


def test_clean_to_markdown_plain_text_passthrough() -> None:
    from marketing_intelligence.enrich import clean_to_markdown

    md = clean_to_markdown("Just some plain text.", THIN_URL)
    assert md == "Just some plain text."


# --- fetch_and_clean failure mapping ------------------------------------------


def test_fetch_and_clean_timeout_raises_fetch_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marketing_intelligence import enrich

    fake = _FakeCurlRequests(error=urllib.error.URLError("timed out"))
    monkeypatch.setattr(enrich, "_curl_cffi_requests", fake)
    with pytest.raises(enrich.FetchFailed, match="(?i)timed out|fetch failed"):
        enrich.fetch_and_clean(THIN_URL, timeout=1)
    assert fake.calls[0]["timeout"] == 1


def test_fetch_and_clean_http_error_raises_fetch_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marketing_intelligence import enrich

    fake = _FakeCurlRequests(403, b"")
    monkeypatch.setattr(enrich, "_curl_cffi_requests", fake)
    with pytest.raises(enrich.FetchFailed, match="(?i)403|forbidden|fetch failed"):
        enrich.fetch_and_clean(THIN_URL)


def test_fetch_and_clean_empty_body_raises_unparseable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marketing_intelligence import enrich

    fake = _FakeCurlRequests(200, b"<html><body>   </body></html>")
    monkeypatch.setattr(enrich, "_curl_cffi_requests", fake)
    with pytest.raises(enrich.UnparseableBody):
        enrich.fetch_and_clean(THIN_URL)


# --- enrich_document ----------------------------------------------------------


def test_sufficient_rss_skips_untouched_zero_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marketing_intelligence import enrich

    calls: list[str] = []

    def _must_not_run(url: str, timeout: int = 30) -> str:
        calls.append(url)
        raise AssertionError("fetch must not run for sufficient RSS bodies")

    monkeypatch.setattr(enrich, "fetch_and_clean", _must_not_run)
    doc = _full_doc()
    new_doc, method, cause = enrich.enrich_document(doc)
    assert calls == []
    assert method == "rss"
    assert cause is None
    assert new_doc is doc  # byte-identical: same object, untouched


def test_thin_item_stores_markdown_hash_over_stored_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marketing_intelligence import enrich

    markdown = "# Thin Story\n\nFull article body with several sentences of real content."
    monkeypatch.setattr(enrich, "fetch_and_clean", lambda url, timeout=30: markdown)
    doc = _thin_doc()
    new_doc, method, cause = enrich.enrich_document(doc)
    assert cause is None
    assert method != "rss"
    assert new_doc.content == markdown
    assert new_doc.content_hash == content_hash_for(new_doc.title, markdown)
    assert new_doc.content_hash != doc.content_hash  # hash follows stored text
    # metadata preserved
    assert new_doc.url == doc.url
    assert new_doc.title == doc.title
    assert new_doc.source == doc.source
    assert new_doc.language == doc.language


@pytest.mark.parametrize(
    "failure",
    [
        Exception("timeout after 30s"),
        Exception("403 paywall / bot protection"),
        Exception("unparseable body"),
    ],
    ids=["timeout", "paywall", "unparseable"],
)
def test_each_failure_mode_keeps_rss_and_records_cause(
    monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    from marketing_intelligence import enrich, firecrawl

    failure_cls: type[enrich.EnrichmentError] = (
        enrich.UnparseableBody if "unparseable" in str(failure) else enrich.FetchFailed
    )

    def _fail(url: str, timeout: int = 30) -> str:
        raise failure_cls(str(failure))

    monkeypatch.setattr(enrich, "fetch_and_clean", _fail)
    # UnparseableBody is a primary-miss signal: the fallback chain runs, so
    # both legs are faked down (no live Jina/Firecrawl traffic in unit tests).
    monkeypatch.setattr(enrich, "try_fallback_reader", _failing(enrich.FetchFailed("reader down")))
    monkeypatch.setattr(
        firecrawl, "fetch_via_firecrawl", _failing(firecrawl.FirecrawlFailed("firecrawl down"))
    )

    doc = _thin_doc()
    # raising API surfaces the typed error for callers that catch ...
    with pytest.raises(enrich.EnrichmentError):
        enrich.enrich_document(doc)
    # ... while the never-raises wrapper keeps RSS + cause.
    kept, method, cause = enrich.enrich_document_or_keep(doc)
    assert kept is doc
    assert method == "rss"
    assert cause is not None and str(failure) in cause


def test_enrich_document_or_keep_never_raises_on_unexpected_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marketing_intelligence import enrich

    def _boom(url: str, timeout: int = 30) -> str:
        raise RuntimeError("something bizarre")

    monkeypatch.setattr(enrich, "fetch_and_clean", _boom)
    kept, method, cause = enrich.enrich_document_or_keep(_thin_doc())
    assert method == "rss"
    assert cause is not None and "bizarre" in cause


# --- flow integration ----------------------------------------------------------

THIN_FEED = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel><title>SMT</title><link>https://example.com</link>
<item><title>Thin Story</title><link>https://example.com/articles/thin-story</link>
<description>Short.</description>
<pubDate>Thu, 03 Sep 2026 09:15:00 -0500</pubDate></item>
<item><title>Full Story</title><link>https://example.com/articles/full-story</link>
<description>{full}</description>
<pubDate>Thu, 03 Sep 2026 10:15:00 -0500</pubDate></item>
</channel></rss>""".format(full="x" * 600).encode()


class _FakeCursor:
    def __init__(self, store: dict) -> None:
        self._store = store
        self.rowcount = 0
        self._row = None

    def execute(self, sql: str, params: tuple | None = None) -> _FakeCursor:
        assert params is not None
        if sql.lstrip().upper().startswith("SELECT"):
            self._row = (1,)
            self.rowcount = 1
            return self
        url, content_hash = params[1], params[-1]
        if url in self._store or content_hash in {p[-1] for p in self._store.values()}:
            self.rowcount = 0
        else:
            self._store[url] = params
            self.rowcount = 1
        return self

    def fetchone(self) -> tuple | None:
        return self._row


class FakeConnection:
    def __init__(self) -> None:
        self.store: dict = {}
        self.statements: list[str] = []

    def execute(self, sql: str, params: tuple | None = None) -> _FakeCursor:
        self.statements.append(sql)
        return _FakeCursor(self.store).execute(sql, params)

    def commit(self) -> None:
        pass


def _wire_flow(monkeypatch: pytest.MonkeyPatch, conn: FakeConnection) -> Any:
    import marketing_intelligence.flows as flows
    from marketing_intelligence.ingest import upsert_documents

    monkeypatch.setattr(flows, "fetch_rss", lambda url, timeout=30: THIN_FEED)
    monkeypatch.setattr(flows, "upsert_documents", lambda docs: upsert_documents(docs, conn=conn))
    return flows


def test_flow_enriches_thin_only_and_skips_sufficient(
    monkeypatch: pytest.MonkeyPatch, no_engine: None
) -> None:
    from marketing_intelligence import enrich as enrich_mod

    flows = _wire_flow(monkeypatch, FakeConnection())
    fetched: list[str] = []

    def _spy(url: str, timeout: int = 30) -> str:
        fetched.append(url)
        return "# Thin Story\n\nEnriched full markdown body for the thin item."

    monkeypatch.setattr(enrich_mod, "fetch_and_clean", _spy)
    result = flows.ingest_source_flow(source_name="Social Media Today")
    assert result["inserted"] == 2
    assert "error" not in result
    assert fetched == ["https://example.com/articles/thin-story"]  # full item: zero fetch
    assert result.get("enrich_skipped", 0) == 0
    assert "enrich_causes" not in result


def test_flow_failure_keeps_rss_records_cause_run_completes(
    monkeypatch: pytest.MonkeyPatch, no_engine: None
) -> None:
    from marketing_intelligence import enrich as enrich_mod

    flows = _wire_flow(monkeypatch, FakeConnection())

    def _timeout(url: str, timeout: int = 30) -> str:
        raise enrich_mod.FetchFailed("timeout after 30s (paywall/bot guard)")

    monkeypatch.setattr(enrich_mod, "fetch_and_clean", _timeout)
    result = flows.ingest_source_flow(source_name="Social Media Today")
    assert "error" not in result  # run completes: explicit partial failure
    assert result["inserted"] == 2  # RSS bodies still persisted
    assert result["enrich_skipped"] == 1
    assert len(result["enrich_causes"]) == 1
    assert result["enrich_causes"][0].startswith("rss:")  # method recorded with cause
    assert "timeout" in result["enrich_causes"][0]


def test_flow_enrich_stage_never_blocks_on_unexpected_exception(
    monkeypatch: pytest.MonkeyPatch, no_engine: None
) -> None:
    flows = _wire_flow(monkeypatch, FakeConnection())

    def _boom(doc: NormalizedDocument, threshold: int = 500) -> Any:
        raise RuntimeError("enrichment exploded")

    monkeypatch.setattr(flows, "enrich_document_or_keep", _boom)
    result = flows.ingest_source_flow(source_name="Social Media Today")
    assert "error" not in result
    assert result["inserted"] == 2
    assert result["enrich_skipped"] == 2  # both items kept their RSS bodies
    assert len(result["enrich_causes"]) == 2
    assert all(c.startswith("rss:") for c in result["enrich_causes"])


def test_flow_rerun_inserts_nothing_new(monkeypatch: pytest.MonkeyPatch, no_engine: None) -> None:
    from marketing_intelligence import enrich as enrich_mod

    conn = FakeConnection()
    flows = _wire_flow(monkeypatch, conn)
    monkeypatch.setattr(
        enrich_mod,
        "fetch_and_clean",
        lambda url, timeout=30: "# Thin Story\n\nStable enriched body.",
    )
    first = flows.ingest_source_flow(source_name="Social Media Today")
    assert first["inserted"] == 2
    second = flows.ingest_source_flow(source_name="Social Media Today")
    assert second["inserted"] == 0
    assert len(conn.store) == 2


# --- no-LLM guard --------------------------------------------------------------


def test_no_model_calls_on_enrichment_path() -> None:
    import re

    from marketing_intelligence import enrich

    source = Path(enrich.__file__).read_text(encoding="utf-8")
    for banned in ("openai", "anthropic", "transformers", "torch", "firecrawl", "crawl4ai"):
        pattern = re.compile(rf"^\s*(import|from)\s+{banned}\b", re.MULTILINE)
        assert not pattern.search(source), f"enrichment must stay deterministic, found {banned!r}"
    for mod in ("openai", "anthropic", "transformers", "torch"):
        assert mod not in sys.modules, f"model library {mod!r} must not be imported"


# --- primary fetch backend: impersonated Chrome (hard dep), then the fallback chain


ARTICLE_HTML = (
    "<html><head><title>Thin Story</title></head><body><article><h1>Thin Story</h1>"
    "<p>" + "Full article body with real content. " * 40 + "</p></article></body></html>"
).encode()


class _CurlResponse:
    def __init__(self, status_code: int, body: bytes) -> None:
        self.status_code = status_code
        self.content = body


class _FakeCurlRequests:
    """curl_cffi.requests double: records calls, replays one canned response."""

    def __init__(
        self, status_code: int = 200, body: bytes = b"", error: Exception | None = None
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self.status_code = status_code
        self.body = body
        self.error = error

    def get(self, url: str, **kwargs: Any) -> _CurlResponse:
        self.calls.append({"url": url, **kwargs})
        if self.error is not None:
            raise self.error
        return _CurlResponse(self.status_code, self.body)


def _failing(exc: Exception) -> Any:
    """Stub that always raises `exc` (fakes one leg of the fetch chain down)."""

    def _raise(*args: Any, **kwargs: Any) -> Any:
        raise exc

    return _raise


def test_impersonation_backend_used_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marketing_intelligence import enrich

    fake = _FakeCurlRequests(200, ARTICLE_HTML)
    monkeypatch.setattr(enrich, "_curl_cffi_requests", fake)

    def _must_not_open(request: object, timeout: object = None) -> object:
        raise AssertionError("primary success must not touch any fallback lane")

    monkeypatch.setattr(enrich.urllib.request, "urlopen", _must_not_open)
    markdown = enrich.fetch_and_clean(THIN_URL)
    assert "Full article body" in markdown
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["url"] == THIN_URL
    assert call["impersonate"] == "chrome"  # Chrome impersonation, never a crawler
    headers = call["headers"]
    assert headers == enrich.BROWSER_HEADERS  # genuine browser identity, verbatim
    ua = headers["User-Agent"]
    assert ua.startswith("Mozilla/5.0") and "Chrome/" in ua
    assert "Googlebot" not in ua and "bot" not in ua.lower()
    assert "Cookie" not in headers and "Authorization" not in headers


def test_missing_curl_cffi_raises_explicit_fetch_failed_no_stdlib_lane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marketing_intelligence import enrich

    monkeypatch.setattr(enrich, "_curl_cffi_requests", None)

    def _must_not_open(request: object, timeout: object = None) -> object:
        raise AssertionError("curl_cffi is a hard dep: no stdlib fetch lane exists")

    monkeypatch.setattr(enrich.urllib.request, "urlopen", _must_not_open)
    with pytest.raises(enrich.FetchFailed, match="(?i)curl_cffi"):
        enrich.fetch_and_clean(THIN_URL)


def test_impersonation_failure_falls_back_reader_then_firecrawl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marketing_intelligence import enrich, firecrawl

    # Challenge evidence in the 403 body is what gates the fallback chain.
    fake = _FakeCurlRequests(403, b"Attention Required! cloudflare captcha challenge")
    monkeypatch.setattr(enrich, "_curl_cffi_requests", fake)

    reader_calls: list[str] = []
    monkeypatch.setattr(
        enrich,
        "try_fallback_reader",
        lambda url, timeout=30: (reader_calls.append(url), "Not found.")[1],
    )

    scrape_calls: list[str] = []
    monkeypatch.setattr(
        firecrawl,
        "fetch_via_firecrawl",
        lambda url, timeout=30: (scrape_calls.append(url), ARTICLE_HTML)[1],
    )

    doc = _thin_doc()
    new_doc, method, cause = enrich.enrich_document_or_keep(doc)
    assert cause is None
    assert method == "enriched"
    assert fake.calls[0]["impersonate"] == "chrome"
    assert reader_calls == [THIN_URL]  # Jina reader leg first
    assert scrape_calls == [THIN_URL]  # Firecrawl only after the reader miss
    assert "Full article body" in new_doc.content
    assert new_doc.url == doc.url


def test_reader_success_short_circuits_firecrawl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marketing_intelligence import enrich, firecrawl

    fake = _FakeCurlRequests(403, b"cloudflare captcha challenge")
    monkeypatch.setattr(enrich, "_curl_cffi_requests", fake)
    monkeypatch.setattr(
        enrich, "try_fallback_reader", lambda url, timeout=30: "# Story\n\n" + "body " * 200
    )

    def _must_not_scrape(url: str, timeout: int = 30) -> bytes:
        raise AssertionError("Firecrawl must stay paid-last-resort: reader succeeded")

    monkeypatch.setattr(firecrawl, "fetch_via_firecrawl", _must_not_scrape)
    new_doc, method, cause = enrich.enrich_document_or_keep(_thin_doc())
    assert cause is None
    assert method == "enriched"
    assert "body body" in new_doc.content


# --- clean_to_markdown: trafilatura extraction -----------------------------------


BOILERPLATE_HTML = (
    "<html><head><title>Noisy Page</title>"
    "<style>.nav{color:red}.ad{display:block}</style>"
    "<script>window.NREUM||(NREUM={});var personalization={track:function(){}};</script>"
    "</head><body>"
    "<nav><ul><li>Home</li><li>Sections</li><li>Subscribe</li></ul></nav>"
    '<div class="ad">Sponsored: buy now, limited offer!</div>'
    '<aside class="related"><h2>Related stories</h2><ul>'
    "<li>Story one about platforms and engagement trends</li>"
    "<li>Story two about creators and monetization moves</li>"
    "<li>Story three about advertising budgets shifting</li>"
    "<li>Story four about video formats gaining share</li>"
    "<li>Story five about messaging apps adding payments</li>"
    "<li>Story six about social commerce checkout flows</li>"
    "<li>Story seven about analytics dashboards launching</li>"
    "<li>Story eight about brand safety measurement</li>"
    "</ul></aside>"
    '<div class="newsletter">Sign up for our daily newsletter with marketing news.</div>'
    '<div class="cookie">We use cookies to personalize content and ads.</div>'
    '<section class="comments"><h2>Reader comments</h2>'
    "<p>Commenter one: great analysis of the platform shift this quarter.</p>"
    "<p>Commenter two: our team saw the same engagement pattern last month.</p>"
    "<p>Commenter three: would love a follow-up on measurement methodology.</p>"
    "<p>Commenter four: sharing this with our strategy group today.</p>"
    "<p>Commenter five: the payments angle deserves its own deep dive.</p>"
    "</section>"
    '<aside class="most-read"><h2>Most read</h2><ul>'
    "<li>Most read story about algorithm changes rolling out</li>"
    "<li>Most read story about ad platform pricing updates</li>"
    "<li>Most read story about influencer campaign results</li>"
    "<li>Most read story about short-form video benchmarks</li>"
    "<li>Most read story about retail media network growth</li>"
    "<li>Most read story about cookie deprecation timelines</li>"
    "</ul></aside>"
    '<div class="tags">Tagged: platforms, engagement, advertising, video, messaging</div>'
    "<article><h1>Real Story</h1>"
    "<p>First article paragraph with substance.</p>"
    '<p>Second paragraph linking to <a href="https://example.com/more">more context</a>.</p>'
    "<p>" + "Sustained reporting body sentence. " * 30 + "</p>"
    "</article>"
    "<footer>Copyright 2026. Privacy policy. Terms of service.</footer>"
    '<script>personalization.track("pageview");</script>'
    "</body></html>"
)


def test_cleaner_strips_script_and_style_contents() -> None:
    from marketing_intelligence import enrich

    markdown = enrich.clean_to_markdown(BOILERPLATE_HTML, THIN_URL)
    for cruft in ("NREUM", "personalization", "color:red", "function(", "track("):
        assert cruft not in markdown
    assert "<script" not in markdown and "<style" not in markdown


def test_cleaner_keeps_paragraphs_and_links_as_markdown() -> None:
    from marketing_intelligence import enrich

    markdown = enrich.clean_to_markdown(BOILERPLATE_HTML, THIN_URL)
    assert "First article paragraph with substance." in markdown
    assert "[more context](https://example.com/more)" in markdown
    assert "\n\n" in markdown  # paragraph breaks preserved


def test_cleaner_boilerplate_much_shorter_than_regex_path() -> None:
    from marketing_intelligence import enrich

    markdown = enrich.clean_to_markdown(BOILERPLATE_HTML, THIN_URL)
    legacy = enrich._regex_to_markdown(BOILERPLATE_HTML)
    # Regex fallback strips script/style *contents* (never article text) but
    # keeps visible boilerplate (nav/ads/comments), so it stays much longer.
    assert "NREUM" not in legacy
    assert "Reader comments" in legacy
    assert len(markdown) < len(legacy) / 2


def test_cleaner_falls_back_to_regex_when_trafilatura_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marketing_intelligence import enrich

    class _NoneExtractor:
        @staticmethod
        def extract(*args: Any, **kwargs: Any) -> None:
            return None

    monkeypatch.setattr(enrich, "_trafilatura", _NoneExtractor)
    assert enrich.clean_to_markdown(BOILERPLATE_HTML, THIN_URL) == enrich._regex_to_markdown(
        BOILERPLATE_HTML
    )


def test_cleaner_falls_back_to_regex_when_trafilatura_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marketing_intelligence import enrich

    monkeypatch.setattr(enrich, "_trafilatura", None)
    assert enrich.clean_to_markdown(BOILERPLATE_HTML, THIN_URL) == enrich._regex_to_markdown(
        BOILERPLATE_HTML
    )
