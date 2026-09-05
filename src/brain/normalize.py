"""Normalization contract for ingested documents.

Every source adapter (RSS today, APIs/HTML later) must produce
:class:`NormalizedDocument`: the durable evidence unit stored in Postgres.
"""

from __future__ import annotations

import hashlib
import html as _html
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit, urlunsplit

DEFAULT_LANGUAGE = "en"

_WHITESPACE_RE = re.compile(r"\s+")
_TAG_RE = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class NormalizedDocument:
    """Common document representation across all sources."""

    source: str
    url: str
    canonical_url: str
    title: str
    author: str | None
    published_at: datetime  # tz-aware
    retrieved_at: datetime  # tz-aware
    language: str
    content: str
    content_hash: str  # sha256 of normalized title + content


def canonicalize_url(url: str) -> str:
    """Strip query/fragment and lowercase scheme+host.

    Path, and therefore routing/case semantics, is preserved.
    """
    parts = urlsplit(url.strip())
    netloc = parts.netloc.lower()
    return urlunsplit((parts.scheme.lower(), netloc, parts.path, "", ""))


def normalize_text(value: str) -> str:
    """Collapse whitespace for stable hashing/comparison."""
    return _WHITESPACE_RE.sub(" ", value.strip())


def strip_html(value: str) -> str:
    """Best-effort visible-text extraction from feed HTML bodies."""
    return normalize_text(_html.unescape(_TAG_RE.sub(" ", value)))


def content_hash_for(title: str, content: str) -> str:
    """Stable sha256 fingerprint over normalized title + content."""
    normalized = normalize_text(title) + "\n" + normalize_text(content)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def coerce_tz_aware(value: datetime) -> datetime:
    """Ensure tz-awareness; naive inputs are assumed UTC (never dropped)."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def make_document(
    *,
    source: str,
    url: str,
    title: str,
    content: str,
    published_at: datetime,
    author: str | None = None,
    language: str = DEFAULT_LANGUAGE,
    retrieved_at: datetime | None = None,
) -> NormalizedDocument:
    """Build a NormalizedDocument, deriving canonical URL and content hash."""
    clean_title = normalize_text(title)
    clean_content = content if "<" not in content else strip_html(content)
    retrieved = coerce_tz_aware(retrieved_at or datetime.now(UTC))
    return NormalizedDocument(
        source=source,
        url=url,
        canonical_url=canonicalize_url(url),
        title=clean_title,
        author=author.strip() or None if author else None,
        published_at=coerce_tz_aware(published_at),
        retrieved_at=retrieved,
        language=language,
        content=clean_content.strip(),
        content_hash=content_hash_for(clean_title, clean_content),
    )
