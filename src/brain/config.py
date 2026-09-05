"""Runtime configuration — environment-driven, import-safe (no live DB needed)."""

from __future__ import annotations

import os

DEFAULT_DATABASE_URL = "postgresql://brain:brain@localhost:5433/brain"

DATABASE_URL: str = os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


def get_database_url() -> str:
    """Return the DATABASE_URL currently in effect (reads env fresh)."""
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
