"""Quiet healthcheck tests — GET /health on both surfaces + log filter.

Observable contracts only: /health returns 200 + {"status": "ok"} without a
bearer token (even when BRAIN_API_TOKEN is set), and the uvicorn access-log
filter drops GET /health records while keeping other paths.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import sys
from pathlib import Path
from typing import Any

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(os.path.dirname(_HERE), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from fastapi.testclient import TestClient  # noqa: E402

from api.app import app  # noqa: E402
from marketing_intelligence.auth import is_auth_configured  # noqa: E402
from marketing_intelligence.healthcheck import (  # noqa: E402
    QuietHealthcheckFilter,
    install_quiet_healthcheck_filter,
)

TOKEN = "test-bearer-token-" + "y" * 32


def _load_mcp_server() -> Any:
    path = Path(_SRC) / "mcp" / "server.py"
    spec = importlib.util.spec_from_file_location("trend_mcp_server_health", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MCP_SERVER = _load_mcp_server()


def _health_record(path: str, method: str = "GET") -> logging.LogRecord:
    return logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("127.0.0.1:1234", method, path, "1.1", 200),
        exc_info=None,
    )


def test_api_health_unauthenticated_without_token(monkeypatch) -> None:
    monkeypatch.delenv("BRAIN_API_TOKEN", raising=False)
    resp = TestClient(app).get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_api_health_unauthenticated_with_token_configured(monkeypatch) -> None:
    monkeypatch.setenv("BRAIN_API_TOKEN", TOKEN)
    assert is_auth_configured()
    client = TestClient(app)
    # No token and wrong token alike must still get 200 (probe has no secret).
    assert client.get("/health").status_code == 200
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/health", headers={"Authorization": "Bearer wrong"}).status_code == 200


def test_mcp_health_unauthenticated_with_token_configured(monkeypatch) -> None:
    monkeypatch.setenv("BRAIN_API_TOKEN", TOKEN)
    assert is_auth_configured()
    for http_app in (MCP_SERVER.create_http_app(), MCP_SERVER.http_app):
        with TestClient(http_app) as client:
            resp = client.get("/health")
            assert resp.status_code == 200
            assert resp.json() == {"status": "ok"}
            # Query strings stay healthy too.
            assert client.get("/health?probe=1").status_code == 200


def test_mcp_health_open_mode_without_token(monkeypatch) -> None:
    monkeypatch.delenv("BRAIN_API_TOKEN", raising=False)
    with TestClient(MCP_SERVER.create_http_app()) as client:
        assert client.get("/health").json() == {"status": "ok"}


def test_filter_drops_health_keeps_others() -> None:
    filt = QuietHealthcheckFilter()
    assert filt.filter(_health_record("/health")) is False
    assert filt.filter(_health_record("/health?probe=1")) is False
    assert filt.filter(_health_record("/mcp")) is True
    assert filt.filter(_health_record("/search?q=x")) is True
    assert filt.filter(_health_record("/healthcheck")) is True
    assert filt.filter(_health_record("/health", method="POST")) is True


def test_filter_installed_on_uvicorn_access_logger() -> None:
    logger = install_quiet_healthcheck_filter()
    assert any(isinstance(f, QuietHealthcheckFilter) for f in logger.filters)
    # Idempotent: reinstalling adds no duplicate.
    before = len(logger.filters)
    install_quiet_healthcheck_filter()
    assert len(logging.getLogger("uvicorn.access").filters) == before
