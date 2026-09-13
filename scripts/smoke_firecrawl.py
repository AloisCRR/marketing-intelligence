#!/usr/bin/env python3
"""Throwaway smoke check for the Firecrawl scrape leg (credit-guarded).

Calls :func:`marketing_intelligence.firecrawl.fetch_via_firecrawl` once per URL
with the module default timeout and prints only the URL, the Markdown byte
length, a thin-content verdict, and the first 300 characters. At most two URLs
are accepted: every scrape spends Firecrawl credits, so a third is refused.

Usage:
    FIRECRAWL_API_KEY=... python scripts/smoke_firecrawl.py [URL [URL]]

Without a key it exits 2 before any HTTP request. The key is read from the
environment at call time and is never printed.
"""

from __future__ import annotations

import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(os.path.dirname(_HERE), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from marketing_intelligence.enrich import DEFAULT_THIN_THRESHOLD, is_thin  # noqa: E402
from marketing_intelligence.firecrawl import FirecrawlFailed, fetch_via_firecrawl  # noqa: E402

DEFAULT_URL = "https://martech.org/ftc-puts-personalized-pricing-practices-on-notice/"

#: Credit guard: one scrape per URL, never more than this many per run.
MAX_URLS = 2

SNIPPET_CHARS = 300

#: Error-message cap, so a failure never prints a full body dump.
MESSAGE_CAP = 300

_KEY_ENV = "FIRECRAWL_API_KEY"

#: Credential-shaped tokens scrubbed from any printed failure message.
_SECRET_RE = re.compile(r"(?i)\b(?:bearer\s+\S+|fc-[A-Za-z0-9_-]{8,})")


def _redacted(message: str) -> str:
    """Credential-free, bounded failure text (never the key, never a body dump)."""
    return _SECRET_RE.sub("<redacted>", message)[:MESSAGE_CAP]


def _urls_from_argv(argv: list[str]) -> list[str]:
    """URLs to scrape: non-blank argv entries or the default, capped at ``MAX_URLS``."""
    urls = [arg for arg in argv if arg.strip()] or [DEFAULT_URL]
    if len(urls) > MAX_URLS:
        print(
            f"refusing {len(urls)} URLs: this smoke check caps at {MAX_URLS} "
            "(each scrape spends Firecrawl credits)",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return urls


def _key_configured() -> bool:
    """True when ``FIRECRAWL_API_KEY`` holds a non-blank value right now."""
    return bool((os.environ.get(_KEY_ENV) or "").strip())


def main(argv: list[str] | None = None) -> int:
    urls = _urls_from_argv(sys.argv[1:] if argv is None else argv)
    if not _key_configured():
        print(
            f"{_KEY_ENV} is missing or blank; refusing to make any request.",
            file=sys.stderr,
        )
        return 2
    failures = 0
    for url in urls:
        try:
            raw = fetch_via_firecrawl(url)
        except FirecrawlFailed as exc:
            failures += 1
            print(f"{url}\n  FAILED: {_redacted(str(exc))}")
            continue
        markdown = raw.decode("utf-8", errors="replace")
        thin = is_thin(markdown)
        print(url)
        print(f"  markdown bytes: {len(raw)}")
        print(f"  thin (<{DEFAULT_THIN_THRESHOLD} chars): {'yes' if thin else 'no'}")
        print(f"  first {SNIPPET_CHARS} chars: {markdown[:SNIPPET_CHARS]!r}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
