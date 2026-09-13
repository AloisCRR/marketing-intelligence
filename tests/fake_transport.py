"""Shared retrieval-seam fake (deepen the retrieval seam, no src changes).

One FakeTransport behind the existing fetch/sleep seam:

- discovery: ``harvest_sitemap_source(..., fetch=, sleep=)`` via
  :meth:`as_kwargs`
- ``discover_*`` traversal: :meth:`fetch_body` returning bare bytes
- ingest/flows RSS lane: :meth:`fetch_rss` stub returning bytes

Noop-sleep default: :meth:`sleep` records instead of sleeping, so tests
stay fast while pacing assertions keep their shape (gap-recording style
in test_sitemap_discovery.py).
"""

from __future__ import annotations

import urllib.error
from collections.abc import Mapping
from typing import Any

import pytest

from marketing_intelligence import discovery


class FakeTransport:
    """Fixture-backed fetch + recording sleep for the retrieval seam."""

    def __init__(
        self,
        fetch_bytes: dict[str, bytes] | None = None,
        failures: Mapping[str, Exception] | None = None,
        log: list[str] | None = None,
    ) -> None:
        self.fetch_bytes: dict[str, bytes] = dict(fetch_bytes or {})
        self.failures: dict[str, Exception] = dict(failures or {})
        self.log: list[str] | None = log
        self.sleep_recorder: list[float] = []

    @classmethod
    def from_fetch_map(
        cls,
        mapping: Mapping[str, tuple[str, bytes]],
        failures: Mapping[str, Exception] | None = None,
        log: list[str] | None = None,
    ) -> FakeTransport:
        """Build from the ``{url: (final_url, body)}`` shape used in tests."""
        return cls({u: body for u, (_, body) in mapping.items()}, failures=failures, log=log)

    def fetch(self, url: str) -> tuple[str, bytes]:
        """Discovery-seam fetch: (final_url, body); URLError when unmapped."""
        if self.log is not None:
            self.log.append(url)
        if url in self.failures:
            raise self.failures[url]
        if url not in self.fetch_bytes:
            raise urllib.error.URLError(f"FakeTransport: no fixture for {url}")
        return (url, self.fetch_bytes[url])

    def fetch_body(self, url: str) -> bytes:
        """Bare-bytes fetch for ``discover_*`` traversal."""
        return self.fetch(url)[1]

    def fetch_rss(self, url: str, timeout: int = 30) -> bytes:
        """RSS-lane stub: bytes out; URLError when unmapped."""
        return self.fetch(url)[1]

    def sleep(self, seconds: float) -> None:
        """Noop sleep that records pacing gaps for assertions."""
        self.sleep_recorder.append(seconds)

    def as_kwargs(self) -> dict[str, Any]:
        """Injectable ``{fetch, sleep}`` pair for harvest functions."""
        return {"fetch": self.fetch, "sleep": self.sleep}

    def install_sitemap_seam(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Feed sitemap traversal from this transport's fixture map.

        Sitemap XML is fetched impersonated-only
        (:func:`marketing_intelligence.discovery.fetch_sitemap_bytes`), so the
        injectable ``fetch``/``sleep`` pair no longer covers sitemaps: tests put
        the same fixture map behind the impersonated leg here.
        """
        monkeypatch.setattr(discovery, "_impersonated_get", lambda url, timeout=30: self.fetch(url))

    def assert_no_sleep(self) -> None:
        """Prove a lane never paced (RSS lane, unmapped transports)."""
        assert self.sleep_recorder == [], f"expected no pacing, got {self.sleep_recorder}"
