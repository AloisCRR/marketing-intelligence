"""Shared static bearer-token auth (MCP security lane, Option 1).

One secret (`BRAIN_API_TOKEN`, e.g. `openssl rand -hex 32`, injected as a
Dokploy env var) verified by both caller surfaces:

- HTTP API (`src/api/app.py`): `require_bearer` FastAPI dependency raising
  `HTTPException(401)` (never `InvalidRequest`/422). 422 stays exclusively for
  request validation.
- MCP (`src/mcp/server.py`): `StaticTokenVerifier` implementing
  `mcp.server.auth.provider.TokenVerifier.verify_token`, wired as
  `FastMCP(token_verifier=...)` so the streamable-HTTP app behind Traefik
  enforces it.

Comparison uses `secrets.compare_digest`. The container serves plain HTTP;
TLS terminates at Traefik.

When `BRAIN_API_TOKEN` is unset/blank, auth is disabled (local-dev open mode)
so `make dev` / `make mcp` and the existing contract tests keep working
without a token. Deployed environments must set it (process restart picks up
rotation).
"""

from __future__ import annotations

import os
import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from pydantic import AnyHttpUrl

ENV_VAR = "BRAIN_API_TOKEN"
PUBLIC_URL_VAR = "BRAIN_PUBLIC_URL"
WWW_AUTHENTICATE = "Bearer"

bearer_scheme = HTTPBearer(auto_error=False)


def get_expected_token() -> str:
    """The configured shared secret (empty string when unconfigured)."""
    return os.environ.get(ENV_VAR, "").strip()


def is_auth_configured() -> bool:
    """Whether a bearer token is configured (i.e. auth is enforced)."""
    return bool(get_expected_token())


def verify_token_value(provided: str) -> bool:
    """Constant-time comparison of a presented token against the configured one."""
    expected = get_expected_token()
    if not expected or not provided:
        return False
    return secrets.compare_digest(provided, expected)


def _unauthorized(detail: str = "Not authenticated") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": WWW_AUTHENTICATE},
    )


async def require_bearer(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> None:
    """FastAPI dependency enforcing the shared static bearer token.

    Open mode when `BRAIN_API_TOKEN` is unset; otherwise requires
    `Authorization: Bearer <token>`, failing with 401 +
    `WWW-Authenticate: Bearer` (missing, wrong-scheme, and wrong-token alike).
    """
    if not is_auth_configured():
        return None
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _unauthorized()
    if not verify_token_value(credentials.credentials):
        raise _unauthorized("Invalid token")
    return None


class StaticTokenVerifier:
    """MCP `TokenVerifier` for the shared static bearer token.

    Returns an `AccessToken` for the exact configured secret, else `None`
    (the MCP HTTP middleware turns that into 401 + `WWW-Authenticate`).
    Reads the env var per call so rotation needs only a process restart.
    """

    async def verify_token(self, token: str) -> AccessToken | None:
        if not verify_token_value(token):
            return None
        return AccessToken(token=token, client_id="bearer", scopes=[])


def mcp_auth_settings() -> AuthSettings:
    """`AuthSettings` pairing `StaticTokenVerifier` (FastMCP 1.29.1 API).

    FastMCP 1.29.1 raises `ValueError` when `token_verifier=` is given without
    `auth=AuthSettings(...)`. Static bearer has no OAuth issuer, so both URLs
    point at the resource itself: `BRAIN_PUBLIC_URL` (default
    `http://localhost:8124`) plus the default `streamable_http_path` (`/mcp`).
    Only used for metadata/`WWW-Authenticate` values; no OAuth flow exists.
    """
    public = os.environ.get(PUBLIC_URL_VAR, "http://localhost:8124").strip().rstrip("/")
    if not public:
        public = "http://localhost:8124"
    resource = AnyHttpUrl(f"{public}/mcp")
    return AuthSettings(issuer_url=resource, resource_server_url=resource)
