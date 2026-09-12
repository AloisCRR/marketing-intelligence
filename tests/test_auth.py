"""Static bearer-token auth tests (MCP security lane, Option 1).

Covers: unauthenticated -> 401 (+ `WWW-Authenticate: Bearer`), wrong token ->
401, correct token -> 200 passthrough on all 4 HTTP routes, `InvalidRequest`
still 422 when authed, and the MCP `StaticTokenVerifier` + streamable-HTTP app
behaviour. `marketing_intelligence.service` is stubbed (no live Postgres).
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
from pathlib import Path
from typing import Any

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(os.path.dirname(_HERE), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import marketing_intelligence.service as service  # noqa: E402
from api.app import app  # noqa: E402
from marketing_intelligence.auth import StaticTokenVerifier, is_auth_configured  # noqa: E402

TOKEN = "test-bearer-token-" + "x" * 32

SEARCH_PAYLOAD = [{"title": "t", "url": "https://example.test/1/"}]
PERIOD_PAYLOAD = {"period": {}, "recent_articles": []}
ARTICLE_PAYLOAD = {"title": "t", "url": "https://example.test/1/", "content": "body"}
FLAG_PAYLOAD = {"title": "t", "url": "https://example.test/1/", "flag_reason": "thin"}


def _load_mcp_server() -> Any:
    path = Path(_SRC) / "mcp" / "server.py"
    spec = importlib.util.spec_from_file_location("trend_mcp_server_auth", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MCP_SERVER = _load_mcp_server()

ARTICLE_URL = "https://example.test/1/"


@pytest.fixture()
def authed(monkeypatch: pytest.MonkeyPatch) -> str:
    """Set BRAIN_API_TOKEN and stub all 4 service functions."""
    monkeypatch.setenv("BRAIN_API_TOKEN", TOKEN)
    assert is_auth_configured()
    monkeypatch.setattr(
        service,
        "search_articles",
        lambda keyword, limit=20, conn=None, exclude_read=False, min_importance=None, topics=None: (
            SEARCH_PAYLOAD
        ),
    )
    monkeypatch.setattr(
        service, "get_period_context", lambda from_date, to_date, **kw: PERIOD_PAYLOAD
    )
    monkeypatch.setattr(service, "get_article", lambda identifier, conn=None: ARTICLE_PAYLOAD)
    monkeypatch.setattr(
        service,
        "flag_extraction",
        lambda identifier, reason=None, detail=None, flagged_by=None, clear=False, conn=None: (
            FLAG_PAYLOAD
        ),
        raising=False,
    )
    return TOKEN


def _authed_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_http_unauthenticated_returns_401(authed: str) -> None:
    client = TestClient(app)
    responses = [
        client.get("/search", params={"q": "TikTok"}),
        client.post("/period-context", json={"from_date": "2026-09-07", "to_date": "2026-09-13"}),
        client.get("/article", params={"url": ARTICLE_URL}),
        client.post("/flag-extraction", json={"identifier": ARTICLE_URL}),
    ]
    for resp in responses:
        assert resp.status_code == 401
        assert resp.headers.get("www-authenticate") == "Bearer"


def test_http_wrong_token_returns_401(authed: str) -> None:
    client = TestClient(app)
    headers = _authed_headers("wrong-token")
    assert client.get("/search", params={"q": "TikTok"}, headers=headers).status_code == 401
    assert (
        client.post(
            "/period-context",
            json={"from_date": "2026-09-07", "to_date": "2026-09-13"},
            headers=headers,
        ).status_code
        == 401
    )
    assert client.get("/article", params={"url": ARTICLE_URL}, headers=headers).status_code == 401
    assert (
        client.post(
            "/flag-extraction", json={"identifier": ARTICLE_URL}, headers=headers
        ).status_code
        == 401
    )


def test_http_correct_token_passthrough_200(authed: str) -> None:
    client = TestClient(app)
    headers = _authed_headers(authed)
    assert client.get("/search", params={"q": "TikTok"}, headers=headers).json() == {
        "results": SEARCH_PAYLOAD
    }
    assert (
        client.post(
            "/period-context",
            json={"from_date": "2026-09-07", "to_date": "2026-09-13"},
            headers=headers,
        ).json()
        == PERIOD_PAYLOAD
    )
    assert client.get("/article", params={"url": ARTICLE_URL}, headers=headers).json() == (
        ARTICLE_PAYLOAD
    )
    assert client.post(
        "/flag-extraction", json={"identifier": ARTICLE_URL}, headers=headers
    ).json() == (FLAG_PAYLOAD)


def test_http_invalid_request_still_422_when_authed(monkeypatch: pytest.MonkeyPatch) -> None:
    # Real service validation (no service stubs): blank keyword / bad date stay
    # 422 when authed — never 401, never 500.
    monkeypatch.setenv("BRAIN_API_TOKEN", TOKEN)
    live = TestClient(app)
    headers = _authed_headers(TOKEN)
    resp = live.get("/search", params={"q": "   "}, headers=headers)
    assert resp.status_code == 422
    resp = live.post(
        "/period-context",
        json={"from_date": "not-a-date", "to_date": "2026-09-13"},
        headers=headers,
    )
    assert resp.status_code == 422


def test_http_open_mode_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    # Unset token (local dev): existing unauthenticated callers keep working.
    monkeypatch.delenv("BRAIN_API_TOKEN", raising=False)
    assert not is_auth_configured()
    monkeypatch.setattr(
        service,
        "search_articles",
        lambda keyword, limit=20, conn=None, exclude_read=False, min_importance=None, topics=None: (
            SEARCH_PAYLOAD
        ),
    )
    resp = TestClient(app).get("/search", params={"q": "TikTok"})
    assert resp.status_code == 200


def test_verifier_accepts_correct_token(authed: str) -> None:
    access = asyncio.run(StaticTokenVerifier().verify_token(authed))
    assert access is not None
    assert access.token == authed
    assert access.scopes == []


def test_verifier_rejects_wrong_token(authed: str) -> None:
    assert asyncio.run(StaticTokenVerifier().verify_token("wrong-token")) is None
    assert asyncio.run(StaticTokenVerifier().verify_token("")) is None


def test_verifier_rejects_everything_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BRAIN_API_TOKEN", raising=False)
    assert asyncio.run(StaticTokenVerifier().verify_token(TOKEN)) is None


def test_mcp_http_requires_auth(authed: str) -> None:
    http_app = MCP_SERVER.create_http_app()
    with TestClient(http_app) as client:
        denied = client.post("/mcp", json={})
        assert denied.status_code == 401
        assert "Bearer" in denied.headers.get("www-authenticate", "")
        # Correct token passes auth (garbage body fails later, but never 401).
        allowed = client.post("/mcp", json={}, headers=_authed_headers(authed))
        assert allowed.status_code != 401


def test_mcp_allowlists_internal_and_public_hosts(authed: str) -> None:
    """Admitted callers (internal compose DNS, public Traefik domain) pass the host gate.

    Regression test for the 421s: only auth (401) or the MCP-protocol layer may
    answer these hosts — never DNS-rebinding protection (421).
    """
    http_app = MCP_SERVER.create_http_app()
    hosts = [
        "marketing-intelligence-mcp",
        "marketing-intelligence-mcp:8124",
        "marketing-intelligence.services.aloiscrr.dev",
    ]
    with TestClient(http_app) as client:
        for host in hosts:
            headers = {**_authed_headers(authed), "host": host}
            resp = client.post("/mcp", json={}, headers=headers)
            assert resp.status_code not in (401, 421), host


def test_mcp_rejects_unknown_host_with_421(authed: str) -> None:
    """DNS-rebinding protection stays on: unlisted hosts get 421, not a silent pass."""
    http_app = MCP_SERVER.create_http_app()
    with TestClient(http_app) as client:
        headers = {**_authed_headers(authed), "host": "evil.example"}
        assert client.post("/mcp", json={}, headers=headers).status_code == 421


def test_mcp_extra_allowed_hosts_env(authed: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """MCP_ALLOWED_HOSTS admits future callers without a code change."""
    monkeypatch.setenv("MCP_ALLOWED_HOSTS", "future-caller.internal")
    http_app = MCP_SERVER.create_http_app()
    with TestClient(http_app) as client:
        headers = {**_authed_headers(authed), "host": "future-caller.internal"}
        resp = client.post("/mcp", json={}, headers=headers)
        assert resp.status_code not in (401, 421)


def test_mcp_internal_host_still_requires_auth(authed: str) -> None:
    """Allowlist opens the host gate only: no token on the internal host is still 401."""
    http_app = MCP_SERVER.create_http_app()
    with TestClient(http_app) as client:
        resp = client.post("/mcp", json={}, headers={"host": "marketing-intelligence-mcp:8124"})
        assert resp.status_code == 401
