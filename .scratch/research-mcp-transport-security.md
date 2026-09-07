# Research: MCP transport (stdio vs HTTP) + internet-exposure security

Date: 2026-09-05. Lane: /research. Read-only; no code changes.
Repo: Trend Intelligence Brain V1 — `brain.service` (`MAX_LIMIT=100`, `InvalidRequest`), thin HTTP (`GET /search`, `POST /weekly-context`, 422 on `InvalidRequest`) + MCP 2-tool parity (`src/mcp/server.py` → `FastMCP("trend-intelligence-brain")`, `search_articles` / `get_weekly_context`, currently `mcp.run()` = stdio default).

## TL;DR

- **Yes — current MCP is stdio.** `src/mcp/server.py:41` calls `mcp.run()` with no `transport=` arg; Python SDK default is `stdio` (`run()` dispatches to `run_stdio_async` when `transport="stdio"`, the default).
- **For internet exposure to autonomous agents, stdio is wrong; use Streamable HTTP.** Spec: stdio = client launches server as subprocess over stdin/stdout; Streamable HTTP = server is an independent process handling multiple clients via POST+GET on a single MCP endpoint. Old HTTP+SSE transport is deprecated (superseded in protocol 2025-03-26); do not build on it.
- **Secure Streamable HTTP as an OAuth 2.1 resource server + hardened HTTP surface:** bearer-token auth with audience binding, HTTPS/TLS termination, `Origin` validation (DNS-rebinding), localhost-bind by default, least-privilege tools/scopes, validation already in `brain.service` is necessary but not sufficient (need network auth, rate limiting, logging upstream of tools).

## 1. stdio vs Streamable HTTP (primary sources)

| | stdio | Streamable HTTP (current) | Old HTTP+SSE (deprecated) |
|---|---|---|---|
| Model | Client spawns server subprocess; newline-delimited JSON-RPC over stdin/stdout; logs on stderr; nothing but valid MCP messages on stdout | Independent server process, many clients; single MCP endpoint (e.g. `https://example.com/mcp`) supporting POST (client→server JSON-RPC) + optional GET-opened SSE stream (server→client) | Two endpoints (SSE + POST); superseded 2025-03-26 |
| Use when | Local-first: Claude Desktop / IDE extension / same-host agent harness spawning `uv run python src/mcp/server.py` | Remote / internet / multi-tenant: autonomous agents reach you over the network | Legacy clients only |
| Spec stance | "Clients SHOULD support stdio whenever possible" (transports) | The remote option; single MCP endpoint MUST support POST+GET | Back-compat section only |

Sources:

- Transports overview — stdio vs Streamable HTTP definitions: https://modelcontextprotocol.io/specification/2025-11-25/basic/transports
- stdio rules (subprocess, newline-delimited, stdout purity, stderr for logs): same page, `Transports > stdio`.
- Streamable HTTP rules (single endpoint, POST+GET, optional SSE, `MCP-Session-Id`, `MCP-Protocol-Version`, resumability): same page, `Transports > Streamable HTTP`.
- Backwards-compatibility note (serve old SSE+POST alongside new endpoint only if you need old clients): same page, `Backwards Compatibility`.
- Python SDK "Pick a transport" (stdio = stdin/stdout local; `streamable-http` = port listener; `sse` only for legacy): https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/run/index.md
- `MCPServer.run(transport="stdio"|"sse"|"streamable-http")` default stdio; `run_stdio_async()` via `stdio_server()`: https://github.com/modelcontextprotocol/python-sdk/blob/main/src/mcp/server/mcpserver/server.py
- Minimal `mcp.run()` = stdio (`examples/snippets/servers/direct_execution.py`); `--http --port N [--path /mcp]` → uvicorn on 127.0.0.1 pattern (`examples/stories/_hosting.py`): same repo paths.

### What changed (SSE deprecation)

- Protocol 2025-03-26 replaced HTTP+SSE with Streamable HTTP. SDK keeps `transport="sse"` only for legacy clients; new code uses `transport="streamable-http"`. Server migration = expose one MCP endpoint handling both POST and GET instead of two endpoints.
- Source: SDK `docs/run/index.md` ("older sse transport was superseded by Streamable HTTP in the 2025-03-26 protocol revision") + spec `Backwards Compatibility`.

### Example server setup (Python SDK, FastMCP)

Local (what repo does today):

```python
from mcp.server.fastmcp import FastMCP
mcp = FastMCP("trend-intelligence-brain")
# ... @mcp.tool() defs ...
mcp.run()  # = transport="stdio"
```

Remote (what internet exposure needs — same tools, different transport):

```python
mcp.run(transport="streamable-http", host="127.0.0.1", port=8000, path="/mcp")
# then reverse-proxy with TLS in front; do NOT bind 0.0.0.0 directly unless behind auth/proxy
```

Sources: `MCPServer.run()` signature/overloads in `src/mcp/server/mcpserver/server.py`; transport recap in `docs/run/index.md`. Exact kwargs (`host`, `port`, `streamable_http_path`) are transport options to `run()` per that recap page.

## 2. Securing internet-exposed MCP (what spec REQUIRES vs recommends)

Spec framing: **authorization is OPTIONAL in general, but when you use HTTP transport you SHOULD conform to the authorization spec; stdio servers SHOULD NOT use it (credentials from environment instead).** Source: https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization ("Protocol Requirements").

MUSTs (HTTP remote):

1. **OAuth 2.1 as resource server.** Server validates `Authorization: Bearer <token>` on *every* request; tokens never in query string; invalid/expired → `401`; insufficient scope → `403` with `WWW-Authenticate: Bearer resource_metadata="...", scope="..."`. Sources: authorization spec `Access Token Usage`, `Error Handling`.
2. **Protected Resource Metadata (RFC 9728).** Server MUST serve it (well-known URI and/or `WWW-Authenticate: Bearer resource_metadata=...` on 401); document MUST list `authorization_servers`. Auth servers MUST expose RFC 8414 or OIDC Discovery metadata. Sources: `Authorization Server Discovery`, `Overview` point 4.
3. **Audience binding + no passthrough.** MUST validate token was issued *for this server* (aud / RFC 8707 `resource` parameter); MUST reject foreign-audience tokens; when calling upstream APIs act as separate OAuth client — MUST NOT forward the client token. Sources: `Token Handling`, `Access Token Privilege Restriction`, `Security Best Practices > Token Passthrough`: https://modelcontextprotocol.io/specification/2025-11-25/basic/security_best_practices
4. **HTTPS + safe redirects.** All AS endpoints MUST be HTTPS; redirect URIs MUST be `localhost` or HTTPS; PKCE `S256` required; state validation; exact redirect-URI match. Source: `Security Considerations` (Communication Security, PKCE, Open Redirection).
5. **Origin validation (DNS-rebinding).** Streamable HTTP servers MUST validate `Origin` on all incoming connections → `403` if invalid; SHOULD bind localhost, SHOULD require auth. Source: transports page `Security Warning` (§1–3).
6. **Session hygiene.** `MCP-Session-Id` SHOULD be unguessable; client stores securely; server returns 404 on dead session; DELETE terminates. Source: transports `Session Management` + `Security Best Practices > Session Hijacking`.

SHOULDs / best practice (do these too):

- **Least-privilege tools/scopes:** minimal `scopes_supported`, use 401-challenged `scope=` as authoritative, step-up flow for extra scopes; only 2 read-only tools is already a good posture — keep it. Source: authorization `Scope Selection Strategy`, `Scope Challenge Handling`; best-practices `Scope Minimization`.
- **TLS termination + network boundary:** reverse proxy (TLS, WAF), private subnet for Postgres/Prefect, no direct DB exposure; short-lived tokens, secure storage, rotation for public clients. Sources: OAuth 2.1 §1.5/§7.1 via spec `Security Considerations`; SDK `docs/run/authorization.md` three-party model (MCP server = resource server, IdP = authorization server).
- **Rate limiting / abuse controls:** not in MCP spec — implement at proxy/middleware (per-token/IP limits, `MAX_LIMIT=100` already caps fan-out but is not rate control). No MCP-spec citation; standard HTTP hardening.
- **Logging/observability without secret leakage:** never log bearer tokens; log auth failures distinctly from `InvalidRequest→422` domain errors.

SDK wiring (primary):

- `TokenVerifier.verify_token(token) -> AccessToken | None` in `src/mcp/server/auth/provider.py`; `BearerAuthBackend` + `RequireAuthMiddleware` (401/403 *before* tool dispatch) + `create_protected_resource_routes` in `src/mcp/server/lowlevel/server.py`; conceptual model in `docs/run/authorization.md` ("MCP server = resource server; verifies tokens, does not issue them").
- Community helper (not spec): `mcp-auth/python` plug-and-play OAuth 2.1 — evaluate, don't assume. SDK `TokenVerifier` is the official seam.

## 3. Implications for this repo

- **No change to `brain.service`.** `MAX_LIMIT=100` + `InvalidRequest` validation is transport-agnostic and already shared by construction (`src/brain/service.py:1-9`, `src/api/app.py`, `src/mcp/server.py`). HTTP maps `InvalidRequest→422`; MCP surfaces it as tool error. Remote MCP reuses the same functions — only the transport + auth wrapper changes.
- **What must change for internet agents:** (a) run FastMCP with `transport="streamable-http"` behind TLS proxy; (b) add `TokenVerifier` + `AuthSettings(resource_server_url, issuer_url, required_scopes)` + Protected Resource Metadata; (c) enforce `Origin` check (SDK does not do it for you — verify version behavior), bind localhost, proxy handles TLS/rate-limit; (d) keep `/search` + `/weekly-context` semantics identical so parity holds; (e) document discovery URLs (`/.well-known/oauth-protected-resource...`, `/mcp`) for agent operators.
- **What NOT to do:** expose stdio over ssh/netcat hacks; build on deprecated SSE transport; accept API keys in query strings; forward agent tokens to upstream sources; rely on `limit<=100` as DOS protection; bind `0.0.0.0` without auth during dev.
- **Open decisions (need owner):** which IdP is the authorization server (self-hosted vs managed, e.g. Keycloak/Authentik vs cloud IdP); token lifetime + scopes (`read:search`, `read:weekly`?); whether public HTTP API and MCP share one proxy/auth policy or diverge; per-tenant rate limits.

## Sources (primary only)

- Spec 2025-11-25 Transports: https://modelcontextprotocol.io/specification/2025-11-25/basic/transports
- Spec 2025-11-25 Authorization: https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization
- Spec 2025-11-25 Security Best Practices: https://modelcontextprotocol.io/specification/2025-11-25/basic/security_best_practices
- Python SDK — Running servers (`docs/run/index.md`): https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/run/index.md
- Python SDK — Authorization (`docs/run/authorization.md`): https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/run/authorization.md
- Python SDK — `MCPServer.run()` (`src/mcp/server/mcpserver/server.py`): https://github.com/modelcontextprotocol/python-sdk/blob/main/src/mcp/server/mcpserver/server.py
- Python SDK — `TokenVerifier` (`src/mcp/server/auth/provider.py`), auth middleware + metadata routes (`src/mcp/server/lowlevel/server.py`, `src/mcp/server/auth/middleware/bearer_auth.py`): same repo paths.
- Repo evidence: `CONTEXT.md`, `src/brain/service.py`, `src/api/app.py`, `src/mcp/server.py`.
