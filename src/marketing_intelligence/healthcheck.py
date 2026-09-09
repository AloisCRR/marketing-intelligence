"""Unauthenticated liveness probe + quiet uvicorn access logs (healthcheck lane).

Both caller surfaces serve ``GET /health`` without auth so container
``HEALTHCHECK`` and load-balancer probes never need ``BRAIN_API_TOKEN``:

- HTTP API (``src/api/app.py``): ``@app.get("/health")`` (no ``require_bearer``).
- MCP (``src/mcp/server.py``): plain Starlette ``Route("/health")`` appended to
  the ``http_app`` instances (the ``/mcp`` route alone carries the
  ``RequireAuthMiddleware`` wrapper, so ``/health`` bypasses ``StaticTokenVerifier``).

``QuietHealthcheckFilter`` drops ``GET /health`` records from the
``uvicorn.access`` logger at import time (stdlib ``logging`` only, no new
deps). It matches the default uvicorn access format
(``'%s - "%s %s HTTP/%s" %d'`` with args
``(client, method, path, version, status)``) via both the structured args
and the formatted message, so it works for ``uvicorn api.app:app`` and
``uvicorn --app-dir src/mcp server:http_app`` without compose changes.
"""

from __future__ import annotations

import logging
import re

HEALTH_PAYLOAD: dict[str, str] = {"status": "ok"}

# Matches `GET /health` only at a path boundary: covers `GET /health `,
# `GET /health?`, and the HTTP-version suffix, but not `/healthcheck`.
_HEALTH_REQUEST_RE = re.compile(r"GET /health(?=[\s?\"'#]|$)")


def health_payload() -> dict[str, str]:
    """Liveness payload served by ``GET /health`` on both services."""
    return dict(HEALTH_PAYLOAD)


class QuietHealthcheckFilter(logging.Filter):
    """Drop uvicorn access-log records for ``GET /health``; keep everything else."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        try:
            if isinstance(args, (tuple, list)) and len(args) >= 3:
                method, path = args[1], args[2]
                if method == "GET" and isinstance(path, str):
                    if path == "/health" or path.startswith(("/health?", "/health ")):
                        return False
        except Exception:
            pass
        try:
            message = record.getMessage()
        except Exception:
            return True
        return _HEALTH_REQUEST_RE.search(message) is None


def install_quiet_healthcheck_filter(
    logger_name: str = "uvicorn.access",
) -> logging.Logger:
    """Attach ``QuietHealthcheckFilter`` to ``logger_name`` (idempotent)."""
    logger = logging.getLogger(logger_name)
    for existing in logger.filters:
        if isinstance(existing, QuietHealthcheckFilter):
            return logger
    logger.addFilter(QuietHealthcheckFilter())
    return logger


install_quiet_healthcheck_filter()
