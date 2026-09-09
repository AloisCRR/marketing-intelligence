"""Robots-Disallow filtering in the sitemap/hub discovery pipeline (follow-up 1).

Observable behavior (not privates):
- pure seam: robots_disallowed_paths() + robots_is_disallowed() honor only
  `User-agent: *` groups, prefix-match Disallows, let Allow win on
  longest-prefix, and never raise on garbled input (allow by default)
- harvest: discovered sitemap/hub article URLs under a Disallow are explicit
  skips ("<url>: disallowed by robots.txt") — never fetched or extracted —
  while Allow exceptions still harvest and hub listing pages are never
  filtered themselves

All network is fake-fetch backed; no live HTTP in tests.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from marketing_intelligence.discovery import (
    harvest_sitemap_source,
    robots_disallowed_paths,
    robots_is_disallowed,
)

FIXTURES = Path(__file__).parent / "fixtures"

HOST = "https://example.com"
ROBOTS_URL = f"{HOST}/robots.txt"
SITEMAP_URL = f"{HOST}/sitemap.xml"
HUB_URL = f"{HOST}/"

URL_PUBLIC = f"{HOST}/posts/public-story"
URL_BLOCKED = f"{HOST}/private/secret-story"
URL_EXCEPTION = f"{HOST}/private/allowed-story"


def _robots() -> bytes:
    return b"User-agent: *\nDisallow: /private/\nAllow: /private/allowed-story\n"


def _article_html(title: str) -> bytes:
    body = ("Body sentence about the story under test. " * 30).strip()
    return (
        f"<html><head><title>{title}</title>"
        '<meta property="article:published_time" '
        'content="2026-09-05T10:00:00+00:00">'
        f"</head><body><article><h1>{title}</h1><p>{body}</p>"
        "</article></body></html>"
    ).encode()


def _sitemap_xml(urls: list[str]) -> bytes:
    entries = "".join(
        f"<url><loc>{u}</loc><lastmod>2026-09-05T10:00:00+00:00</lastmod></url>" for u in urls
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{entries}</urlset>"
    ).encode()


def _make_fetch(mapping: dict[str, tuple[str, bytes]], log: list[str] | None = None) -> Any:
    def fake_fetch(url: str) -> tuple[str, bytes]:
        if log is not None:
            log.append(url)
        return mapping[url]

    return fake_fetch


def _sitemap_config(**overrides: Any) -> dict[str, Any]:
    config: dict[str, Any] = {
        "type": "sitemap",
        "policy": "stdlib-only",
        "extractor": "generic",
        "pacing_ms": 1000,
        "max_urls": 50,
        "sitemaps": [SITEMAP_URL],
    }
    config.update(overrides)
    return config


# --- pure-function unit --------------------------------------------------------


def test_allow_all_when_no_rules() -> None:
    assert robots_disallowed_paths("") == []
    assert robots_disallowed_paths("User-agent: *\n") == []
    assert robots_is_disallowed(URL_PUBLIC, "") is False
    assert robots_is_disallowed(URL_PUBLIC, "User-agent: *\nDisallow:\n") is False


def test_disallow_prefix_match() -> None:
    robots = "User-agent: *\nDisallow: /posts/private\n"
    assert robots_disallowed_paths(robots) == ["/posts/private"]
    assert robots_is_disallowed(f"{HOST}/posts/private", robots) is True
    assert robots_is_disallowed(f"{HOST}/posts/privateer-story", robots) is True
    assert robots_is_disallowed(f"{HOST}/posts/private/nested", robots) is True
    assert robots_is_disallowed(f"{HOST}/posts/public", robots) is False


def test_allow_override_beats_disallow_on_longest_prefix() -> None:
    robots = "User-agent: *\nDisallow: /private/\nAllow: /private/allowed-story\n"
    assert robots_is_disallowed(f"{HOST}/private/other", robots) is True
    assert robots_is_disallowed(f"{HOST}/private/allowed-story", robots) is False
    # Full allow-all beats a blanket disallow via longer-prefix Allow: /.
    blanket = "User-agent: *\nDisallow: /\nAllow: /\n"
    assert robots_is_disallowed(f"{HOST}/anything", blanket) is False


def test_specific_user_agent_groups_ignored() -> None:
    robots = "User-agent: Googlebot\nDisallow: /posts/\n"
    assert robots_disallowed_paths(robots) == []
    assert robots_is_disallowed(URL_PUBLIC, robots) is False
    # Consecutive user-agent lines share one block, so `*` still applies here.
    shared = "User-agent: Googlebot\nUser-agent: *\nDisallow: /posts/\n"
    assert robots_disallowed_paths(shared) == ["/posts/"]
    assert robots_is_disallowed(URL_PUBLIC, shared) is True
    # A directive starts a fresh group: the trailing `*` block is empty.
    fresh = "User-agent: *\nDisallow: /posts/\nUser-agent: Googlebot\n"
    assert robots_disallowed_paths(fresh) == ["/posts/"]


def test_garbled_input_never_raises_and_allows() -> None:
    for garbled in ["::::\n\x00\x01", "Disallow", "User-agent", "\udcff", "Allow: /x"]:
        assert robots_disallowed_paths(garbled) == []
        assert robots_is_disallowed("https://example.com/x", garbled) is False
    assert robots_is_disallowed("::not a url::", "User-agent: *\nDisallow: /\n") is False


def test_real_fixture_rules() -> None:
    jd = (FIXTURES / "jd_robots.txt").read_text()
    assert robots_is_disallowed("https://jingdaily.com/search?q=x", jd) is True
    assert robots_is_disallowed("https://jingdaily.com/api/rss/feed", jd) is True
    assert robots_is_disallowed("https://jingdaily.com/posts/some-story", jd) is False
    ri = (FIXTURES / "ri_robots.txt").read_text()
    assert robots_is_disallowed("https://www.richemont.com/our-maisons/net-a-porter/", ri) is True
    assert robots_is_disallowed("https://www.richemont.com/our-maisons/cartier/", ri) is False
    mo = (FIXTURES / "mo_robots.txt").read_text()
    assert robots_is_disallowed("https://www.modaes.com/admin/panel", mo) is True
    assert robots_is_disallowed("https://www.modaes.com/es/actualidad", mo) is False


# --- harvest-level ------------------------------------------------------------


def test_sitemap_url_under_disallow_is_skipped_never_fetched() -> None:
    log: list[str] = []
    fetch = _make_fetch(
        {
            ROBOTS_URL: (ROBOTS_URL, _robots()),
            SITEMAP_URL: (SITEMAP_URL, _sitemap_xml([URL_PUBLIC, URL_BLOCKED])),
            URL_PUBLIC: (URL_PUBLIC, _article_html("Public story")),
            URL_BLOCKED: (URL_BLOCKED, _article_html("Secret story")),
        },
        log=log,
    )
    report = harvest_sitemap_source(
        _sitemap_config(), "Example", "en", fetch=fetch, sleep=lambda _: None
    )
    assert [d.url for d in report.documents] == [URL_PUBLIC]
    assert report.skipped == 1
    assert report.causes == [f"{URL_BLOCKED}: disallowed by robots.txt"]
    assert URL_BLOCKED not in log  # filtered BEFORE fetch/extract


def test_allow_exception_still_harvested() -> None:
    fetch = _make_fetch(
        {
            ROBOTS_URL: (ROBOTS_URL, _robots()),
            SITEMAP_URL: (
                SITEMAP_URL,
                _sitemap_xml([URL_BLOCKED, URL_EXCEPTION, URL_PUBLIC]),
            ),
            URL_PUBLIC: (URL_PUBLIC, _article_html("Public story")),
            URL_EXCEPTION: (URL_EXCEPTION, _article_html("Allowed story")),
            URL_BLOCKED: (URL_BLOCKED, _article_html("Secret story")),
        }
    )
    report = harvest_sitemap_source(
        _sitemap_config(), "Example", "en", fetch=fetch, sleep=lambda _: None
    )
    assert [d.url for d in report.documents] == [URL_EXCEPTION, URL_PUBLIC]
    assert report.skipped == 1
    assert report.causes == [f"{URL_BLOCKED}: disallowed by robots.txt"]


def test_hub_url_under_disallow_skipped_but_hub_page_fetched() -> None:
    log: list[str] = []
    hub_html = (
        "<html><body>"
        f'<a href="{URL_PUBLIC}">Public</a>'
        f'<a href="{URL_BLOCKED}">Secret</a>'
        "</body></html>"
    ).encode()
    fetch = _make_fetch(
        {
            ROBOTS_URL: (ROBOTS_URL, _robots()),
            HUB_URL: (HUB_URL, hub_html),
            URL_PUBLIC: (URL_PUBLIC, _article_html("Public story")),
            URL_BLOCKED: (URL_BLOCKED, _article_html("Secret story")),
        },
        log=log,
    )
    config = _sitemap_config(type="hub", sitemaps=[], hub=HUB_URL, hub_pages=[], link_pattern="/")
    report = harvest_sitemap_source(config, "Example", "en", fetch=fetch, sleep=lambda _: None)
    # The hub listing page itself is fetched (never filtered); only the
    # discovered article URL under the Disallow is skipped.
    assert HUB_URL in log
    assert [d.url for d in report.documents] == [URL_PUBLIC]
    assert report.skipped == 1
    assert report.causes == [f"{URL_BLOCKED}: disallowed by robots.txt"]
    assert URL_BLOCKED not in log
