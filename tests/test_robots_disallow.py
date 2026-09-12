"""Robots-Disallow URLs are harvested like any other (follow-up 1 removal).

Observable behavior (not privates):
- planning: a source whose robots.txt Disallows article paths still plans
  those URLs — no "disallowed by robots.txt" skip, no pre-fetch drop — and
  they are fetched/extracted alongside their allowed siblings
- politeness: robots crawl-delay still floors the pacing gap
  (max(stanza pacing, crawl-delay) plus bounded jitter) and newest-first
  `max_urls` budgeting still keeps the newest URLs, Disallowed or not

All network is fake-fetch backed; no live HTTP in tests.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from marketing_intelligence.discovery import (
    harvest_sitemap_source,
    plan_harvest,
)

FIXTURES = Path(__file__).parent / "fixtures"

HOST = "https://example.com"
ROBOTS_URL = f"{HOST}/robots.txt"
SITEMAP_URL = f"{HOST}/sitemap.xml"
HUB_URL = f"{HOST}/"

URL_PUBLIC = f"{HOST}/posts/public-story"
URL_BLOCKED = f"{HOST}/private/secret-story"

# Real affected source: tests/fixtures/ri_robots.txt Disallows Net-a-Porter.
RI_HOST = "https://www.richemont.com"
RI_ROBOTS_URL = f"{RI_HOST}/robots.txt"
RI_SITEMAP_URL = f"{RI_HOST}/sitemap.xml"
RI_BLOCKED = f"{RI_HOST}/our-maisons/net-a-porter/"
RI_PUBLIC = f"{RI_HOST}/our-maisons/cartier/"

#: Bounded jitter: pacing gap * (1 + uniform(0, 0.25)).
_MAX_JITTER = 1.25


def _robots(crawl_delay: float | None = None) -> bytes:
    lines = "User-agent: *\nDisallow: /private/\n"
    if crawl_delay is not None:
        lines += f"Crawl-delay: {crawl_delay:g}\n"
    return lines.encode()


def _article_html(title: str) -> bytes:
    body = ("Body sentence about the story under test. " * 30).strip()
    return (
        f"<html><head><title>{title}</title>"
        '<meta property="article:published_time" '
        'content="2026-09-05T10:00:00+00:00">'
        f"</head><body><article><h1>{title}</h1><p>{body}</p>"
        "</article></body></html>"
    ).encode()


def _sitemap_xml(entries: list[tuple[str, str]]) -> bytes:
    urls = "".join(
        f"<url><loc>{loc}</loc><lastmod>{lastmod}</lastmod></url>" for loc, lastmod in entries
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{urls}</urlset>"
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
        "policy": "impersonated-feed",
        "extractor": "generic",
        "pacing_ms": 1000,
        "max_urls": 50,
        "sitemaps": [SITEMAP_URL],
    }
    config.update(overrides)
    return config


# --- planning ----------------------------------------------------------------


def test_disallowed_sitemap_url_is_planned() -> None:
    """A Disallowed article URL is a normal plan entry, never a skip cause."""
    log: list[str] = []
    fetch = _make_fetch(
        {
            ROBOTS_URL: (ROBOTS_URL, _robots()),
            SITEMAP_URL: (
                SITEMAP_URL,
                _sitemap_xml(
                    [
                        (URL_BLOCKED, "2026-09-05T10:00:00+00:00"),
                        (URL_PUBLIC, "2026-09-05T10:00:00+00:00"),
                    ]
                ),
            ),
        },
        log=log,
    )
    plan = plan_harvest(_sitemap_config(), "Example", "en", fetch=fetch, sleep=lambda _: None)
    assert {job.loc for job in plan.jobs} == {URL_BLOCKED, URL_PUBLIC}
    assert plan.skipped == 0
    assert plan.causes == []
    assert ROBOTS_URL in log  # robots.txt still read (crawl-delay)


def test_disallowed_article_is_fetched_and_extracted() -> None:
    log: list[str] = []
    fetch = _make_fetch(
        {
            ROBOTS_URL: (ROBOTS_URL, _robots()),
            SITEMAP_URL: (
                SITEMAP_URL,
                _sitemap_xml(
                    [
                        (URL_PUBLIC, "2026-09-05T10:00:00+00:00"),
                        (URL_BLOCKED, "2026-09-05T10:00:00+00:00"),
                    ]
                ),
            ),
            URL_PUBLIC: (URL_PUBLIC, _article_html("Public story")),
            URL_BLOCKED: (URL_BLOCKED, _article_html("Secret story")),
        },
        log=log,
    )
    report = harvest_sitemap_source(
        _sitemap_config(), "Example", "en", fetch=fetch, sleep=lambda _: None
    )
    assert {d.url for d in report.documents} == {URL_PUBLIC, URL_BLOCKED}
    assert report.skipped == 0
    assert report.causes == []
    assert URL_BLOCKED in log  # fetched, not dropped pre-fetch


def test_disallowed_hub_anchor_is_harvested() -> None:
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
    assert {d.url for d in report.documents} == {URL_PUBLIC, URL_BLOCKED}
    assert report.skipped == 0
    assert report.causes == []
    assert URL_BLOCKED in log


def test_real_fixture_disallowed_url_is_planned() -> None:
    """Real affected source (Richemont robots.txt): Net-a-Porter is planned."""
    robots = (FIXTURES / "ri_robots.txt").read_bytes()
    fetch = _make_fetch(
        {
            RI_ROBOTS_URL: (RI_ROBOTS_URL, robots),
            RI_SITEMAP_URL: (
                RI_SITEMAP_URL,
                _sitemap_xml(
                    [
                        (RI_BLOCKED, "2026-09-04T10:00:00+00:00"),
                        (RI_PUBLIC, "2026-09-05T10:00:00+00:00"),
                    ]
                ),
            ),
            RI_BLOCKED: (RI_BLOCKED, _article_html("Net-a-Porter story")),
            RI_PUBLIC: (RI_PUBLIC, _article_html("Cartier story")),
        }
    )
    report = harvest_sitemap_source(
        _sitemap_config(sitemaps=[RI_SITEMAP_URL]),
        "Richemont",
        "en",
        fetch=fetch,
        sleep=lambda _: None,
    )
    assert RI_BLOCKED in {d.url for d in report.documents}
    assert report.skipped == 0
    assert report.causes == []


# --- politeness unchanged ----------------------------------------------------


def test_crawl_delay_still_floors_the_pacing_gap() -> None:
    sleeps: list[float] = []
    fetch = _make_fetch(
        {
            ROBOTS_URL: (ROBOTS_URL, _robots(crawl_delay=10)),
            SITEMAP_URL: (SITEMAP_URL, _sitemap_xml([(URL_PUBLIC, "2026-09-05T10:00:00+00:00")])),
            URL_PUBLIC: (URL_PUBLIC, _article_html("Public story")),
        }
    )
    plan = plan_harvest(
        _sitemap_config(pacing_ms=1000), "Example", "en", fetch=fetch, sleep=sleeps.append
    )
    assert plan.gap_s == 10.0  # max(stanza 1s, crawl-delay 10s)
    assert [job.gap_s for job in plan.jobs] == [10.0]
    assert sleeps  # planning traversal is paced too
    assert all(10.0 <= s <= 10.0 * _MAX_JITTER for s in sleeps)


def test_stanza_pacing_floor_with_jitter_unchanged() -> None:
    sleeps: list[float] = []
    fetch = _make_fetch(
        {
            ROBOTS_URL: (ROBOTS_URL, _robots()),  # no crawl-delay declared
            SITEMAP_URL: (SITEMAP_URL, _sitemap_xml([(URL_PUBLIC, "2026-09-05T10:00:00+00:00")])),
            URL_PUBLIC: (URL_PUBLIC, _article_html("Public story")),
        }
    )
    config = _sitemap_config(pacing_ms=1000)
    plan = plan_harvest(config, "Example", "en", fetch=fetch, sleep=sleeps.append)
    assert plan.gap_s == 1.0
    assert all(1.0 <= s <= 1.0 * _MAX_JITTER for s in sleeps)

    harvest_sleeps: list[float] = []
    harvest_sitemap_source(config, "Example", "en", fetch=fetch, sleep=harvest_sleeps.append)
    assert harvest_sleeps
    assert all(1.0 <= s <= 1.0 * _MAX_JITTER for s in harvest_sleeps)


def test_newest_first_budget_keeps_disallowed_urls() -> None:
    """max_urls still spends on the newest URLs; Disallow is not a budget sink."""
    oldest = f"{HOST}/posts/oldest-story"
    fetch = _make_fetch(
        {
            ROBOTS_URL: (ROBOTS_URL, _robots()),
            SITEMAP_URL: (
                SITEMAP_URL,
                _sitemap_xml(
                    [
                        (oldest, "2026-09-01T10:00:00+00:00"),
                        (URL_BLOCKED, "2026-09-04T10:00:00+00:00"),
                        (URL_PUBLIC, "2026-09-05T10:00:00+00:00"),
                    ]
                ),
            ),
        }
    )
    plan = plan_harvest(
        _sitemap_config(max_urls=2), "Example", "en", fetch=fetch, sleep=lambda _: None
    )
    assert [job.loc for job in plan.jobs] == [URL_PUBLIC, URL_BLOCKED]
    assert plan.skipped == 0
    assert plan.causes == []
