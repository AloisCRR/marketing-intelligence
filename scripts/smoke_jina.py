#!/usr/bin/env python3
"""Throwaway smoke check for the Jina reader fallback leg (free, no key).

Calls :func:`marketing_intelligence.enrich.try_fallback_reader` once per URL
with the module default timeout and prints the URL, the Markdown character
length, and the first 300 characters. A failure prints the redacted exception
message only — never a full body dump.

Usage:
    python scripts/smoke_jina.py [URL [URL [URL]]]

With no arguments it checks the Meio & Mensagem ``/patrocinado/`` host and the
MarTech article. Up to three URLs are read from argv; extras are ignored.
"""

from __future__ import annotations

import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(os.path.dirname(_HERE), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from marketing_intelligence.enrich import EnrichmentError, try_fallback_reader  # noqa: E402

DEFAULT_URLS = [
    "https://www.meioemensagem.com.br/patrocinado/spark/",
    "https://martech.org/ftc-puts-personalized-pricing-practices-on-notice/",
]

#: The reader leg is free; only the argv slice below is capped.
MAX_URLS = 3

SNIPPET_CHARS = 300

#: Error-message cap, so a failure never prints a full body dump.
MESSAGE_CAP = 300

#: Credential-shaped tokens scrubbed from any printed failure message.
_SECRET_RE = re.compile(r"(?i)\b(?:bearer\s+\S+|fc-[A-Za-z0-9_-]{8,})")


def _redacted(message: str) -> str:
    """Credential-free, bounded failure text (never a full body dump)."""
    return _SECRET_RE.sub("<redacted>", message)[:MESSAGE_CAP]


def _urls_from_argv(argv: list[str]) -> list[str]:
    """First ``MAX_URLS`` non-blank argv entries, or the two curated defaults."""
    urls = [arg for arg in argv if arg.strip()]
    if not urls:
        return list(DEFAULT_URLS)
    if len(urls) > MAX_URLS:
        print(
            f"ignoring {len(urls) - MAX_URLS} extra URL(s): this smoke check reads at most "
            f"{MAX_URLS}",
            file=sys.stderr,
        )
    return urls[:MAX_URLS]


def main(argv: list[str] | None = None) -> int:
    urls = _urls_from_argv(sys.argv[1:] if argv is None else argv)
    failures = 0
    for url in urls:
        try:
            markdown = try_fallback_reader(url)
        except EnrichmentError as exc:
            failures += 1
            print(f"{url}\n  FAILED: {_redacted(str(exc))}")
            continue
        print(url)
        print(f"  markdown chars: {len(markdown)}")
        print(f"  first {SNIPPET_CHARS} chars: {markdown[:SNIPPET_CHARS]!r}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
