# AGENTS.md — Universal Coding Agent Instructions

> **Portability Notice**: This file is the canonical entrypoint for all AI coding agents (Google Antigravity, Claude Code, Cursor, GitHub Copilot, Cline, Windsurf, OpenAI Codex). Before making structural changes, read [`docs/AGENT_HANDOVER.md`](docs/AGENT_HANDOVER.md).

---

## 1. System Overview

This repository is a **SAP BTP Cloud Foundry Python MCP (Model Context Protocol) Server** built around the enterprise **SAP Approuter + XSUAA + BTP Destination Service** architecture:

- **`approuter/`**: Standalone Node.js `@sap/approuter` gateway. Authenticates users/clients via XSUAA OAuth2/OIDC and forwards the validated JWT (`forwardAuthToken: true`) to the Python backend.
- **`srv/`**: Python 3.11+ ASGI application (`FastAPI` + `FastMCP`) exposing MCP over **Streamable HTTP (`/mcp`)** and **SSE (`/sse`, `/messages`)**, protected by a pure-ASGI XSUAA JWT validator (`srv/app/auth/`).
- **`mta.yaml` & `xs-security.json`**: SAP Multi-Target Application (MTA) deployment descriptor and XSUAA security configuration (`$XSAPPNAME.mcp.read`, `$XSAPPNAME.mcp.execute`, `$XSAPPNAME.mcp.admin`).

---

## 2. Quick Commands for Agents

Run all Python commands from `srv/` (or via root `Makefile`):

```bash
# 1. Create virtualenv and install dependencies (including dev/test tools)
make install

# 2. Run test suite (pytest + asyncio)
make test

# 3. Run linter (ruff)
make lint

# 4. Start local development server in Mock Auth mode on http://localhost:8080
make dev

# 5. Build SAP BTP MTA archive (.mtar)
make build-mta
```

---

## 3. Non-Negotiable Architectural Guardrails

1. **Never Replace Pure ASGI Auth Middleware with `BaseHTTPMiddleware`**:
   - `srv/app/auth/middleware.py` (`XsuaaAuthASGIMiddleware`) is written as a pure ASGI middleware so MCP streaming responses (`/mcp` Streamable HTTP and `/sse` Server-Sent Events) are not buffered and `ContextVar` (`_current_security_context`) remains intact across async tool calls.
2. **Never Hardcode Credentials or Bypass XSUAA in Production**:
   - `Settings.effective_auth_mode` in `srv/app/config.py` forces `"xsuaa"` whenever `APP_ENV=production` or `VCAP_APPLICATION` is present in the environment. Never remove this safeguard.
3. **Preserve `csrfProtection: false` on MCP Routes in `approuter/xs-app.json`**:
   - MCP clients invoke `POST /mcp` and `POST /messages` programmatically with `Authorization: Bearer <jwt>`. Enabling Approuter browser CSRF token checks on those routes breaks non-browser MCP clients with `403 Forbidden`.
4. **Always Propagate User Context (`require_current_user()`) in MCP Tools**:
   - Every MCP tool in `srv/app/mcp_server/tools/` must obtain the caller's `UserSecurityContext` via `require_current_user()`, verify its required XSUAA scope via `user.check_scope("$XSAPPNAME.mcp.<scope>", xsappname=...)`, and pass `user_context=user` when calling `DestinationServiceClient` for principal propagation.
5. **Keep FastMCP `stateless_http=True`**:
   - Cloud Foundry horizontally scales application instances behind GoRouter. `stateless_http=True` in `srv/app/mcp_server/server.py` ensures any instance can serve Streamable HTTP requests without requiring sticky sessions.

---

## 4. Key Files Map

| Path | Responsibility |
| :--- | :--- |
| `mta.yaml` | BTP MTA build & deployment descriptor (`approuter`, `srv`, `xsuaa`, `destination`) |
| `xs-security.json` | XSUAA scopes (`mcp.read`, `mcp.execute`, `mcp.admin`), role templates & collections |
| `approuter/xs-app.json` | Approuter routing rules (`/mcp`, `/sse`, `/messages`, `/health`) |
| `srv/app/config.py` | `Settings` + `VCAP_SERVICES` / `default-env.json` parser |
| `srv/app/auth/context.py` | `UserSecurityContext` model + `ContextVar` accessors (`require_current_user`) |
| `srv/app/auth/xsuaa.py` | Dual-mode XSUAA JWT validator (`sap-xssec`, `PyJWT` PEM/JWKS, local `mock`) |
| `srv/app/auth/middleware.py` | Pure ASGI middleware enforcing Bearer JWT auth on all non-exempt routes |
| `srv/app/btp/destination.py` | SAP BTP Destination Service client + XSUAA Principal Propagation / Token Exchange |
| `srv/app/btp/odata_client.py` | Async OData V2/V4 query (`get_entity_set`) & write (`post_entity` with CSRF preflight) client |
| `srv/app/store/payload_store.py` | Shared Agent Payload Store (`FilePayloadStore` at `srv/data/agent_payloads.json`, `RedisPayloadStore`, `InMemoryPayloadStore`) |
| `srv/app/mcp_server/tools/agent_payload_tools.py` | AWS -> MCP -> SAP Build Joule payload bridge & S/4HANA master data creation tools |
| `srv/app/mcp_server/server.py` | `FastMCP` server factory registering tools, resources, and prompts |
| `srv/app/main.py` | FastAPI + FastMCP ASGI app factory (`create_app`) |
| `docs/AGENT_HANDOVER.md` | Detailed handover guide, playbooks, and extension patterns |
| `docs/ARCHITECTURE.md` | Mermaid sequence & component diagrams for Approuter + XSUAA + MCP |
