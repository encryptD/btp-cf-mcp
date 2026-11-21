# Agent Handover & Portability Document (`docs/AGENT_HANDOVER.md`)

> **Document Purpose**: Enable seamless handoff between any AI coding agents (Google Antigravity, Claude Code, Cursor, GitHub Copilot, Windsurf, Cline, OpenAI Codex) and human engineers working on this SAP BTP Cloud Foundry Python MCP Server.

---

## 1. Current Project State & Baseline

| Component | Status | Implementation Path |
| :--- | :--- | :--- |
| **MTA Deployment Descriptor** | Ready | `mta.yaml` (Approuter + Python `srv` + Managed XSUAA + Destination Service) |
| **XSUAA Security Configuration** | Ready | `xs-security.json` (`mcp.read`, `mcp.execute`, `mcp.admin` scopes & role templates) |
| **Standalone Node.js Approuter** | Ready | `approuter/package.json`, `approuter/xs-app.json` (`forwardAuthToken: true`) |
| **Python FastAPI + FastMCP ASGI App** | Ready | `srv/app/main.py`, `srv/app/mcp_server/server.py` (`/mcp`, `/sse`, `/messages`, `/health`) |
| **Dual-Mode XSUAA JWT Validator** | Ready | `srv/app/auth/xsuaa.py` (`sap-xssec` + `PyJWT` PEM/JWKS + local `mock` mode) |
| **Pure ASGI Auth Middleware** | Ready | `srv/app/auth/middleware.py` + `ContextVar` in `srv/app/auth/context.py` |
| **BTP Destination + Principal Propagation** | Ready | `srv/app/btp/destination.py`, `srv/app/btp/odata_client.py` (`get_entity_set`, `post_entity` with CSRF fetch) |
| **Shared Agent Payload Bridge (AWS -> Joule)** | Ready | `srv/app/store/payload_store.py`, `srv/data/agent_payloads.json`, `srv/app/mcp_server/tools/agent_payload_tools.py` |
| **Sample MCP Tools, Resources, Prompts** | Ready | `srv/app/mcp_server/tools/` (7 tools), `resources/`, `prompts/` |
| **Automated Pytest Suite** | Passing | `srv/tests/test_auth.py`, `test_destination.py`, `test_mcp_server.py`, `test_agent_payload.py` |

---

## 2. End-to-End Request & Security Lifecycle

Understanding how a user's identity flows from an MCP Client to an SAP Backend (e.g., S/4HANA or CAP OData service) is critical before modifying any code:

1. **Ingress at SAP Approuter (`btp-cf-mcp-approuter`)**:
   - The MCP client connects to `https://<approuter-host>/mcp` (Streamable HTTP) or `https://<approuter-host>/sse` (SSE) with an OAuth2 Bearer token issued by SAP BTP XSUAA (or session cookie from OIDC login).
   - `approuter/xs-app.json` checks the route (`authenticationType: "xsuaa"`, `scope: ["$XSAPPNAME.mcp.read"]`, `csrfProtection: false`).
   - Approuter routes the request to destination `mcp-server-backend` (defined in `mta.yaml` with `forwardAuthToken: true`), forwarding `Authorization: Bearer <user_xsuaa_jwt>`.
2. **Validation at Python ASGI Middleware (`XsuaaAuthASGIMiddleware`)**:
   - Even though Approuter validates the token at the edge, **Zero-Trust SAP BTP architecture** requires the Python backend (`btp-cf-mcp-srv`) to validate the JWT signature, expiration (`exp`), and audience (`aud`/`cid`) against the bound `VCAP_SERVICES.xsuaa` instance.
   - `XsuaaTokenValidator` (`srv/app/auth/xsuaa.py`) validates the token using either `sap-xssec` (if installed) or `PyJWT` (using `verificationkey` PEM or `/token_keys` JWKS) and builds a `UserSecurityContext`.
   - `XsuaaAuthASGIMiddleware` binds `UserSecurityContext` (including `raw_token`) into a request-scoped `ContextVar` via `set_current_user()`.
3. **Execution inside FastMCP Tools (`srv/app/mcp_server/tools/`)**:
   - Any MCP tool retrieves the caller's identity via `user = require_current_user()`.
   - The tool checks fine-grained scopes via `user.check_scope("$XSAPPNAME.mcp.execute", xsappname=...)`.
4. **Outbound Principal Propagation via BTP Destination Service (`DestinationServiceClient`)**:
   - When the tool queries an SAP backend (`SapODataClient`), `DestinationServiceClient.resolve_destination()` forwards the caller's `user.raw_token` (`X-user-token` header or `urn:ietf:params:oauth:grant-type:jwt-bearer` token exchange) to the BTP Destination Service.
   - The BTP Destination Service exchanges the user token for a target system token (e.g., `OAuth2SAMLBearerAssertion` for S/4HANA) and returns `authTokens[0].http_header`, which `SapODataClient` attaches to the outbound OData request.

---

## 3. Agent Playbooks for Common Tasks

### Playbook A: Adding a New MCP Tool with Scope Enforcement

1. Create or open a tool module in `srv/app/mcp_server/tools/` (for example, `srv/app/mcp_server/tools/business_partner_tools.py`).
2. Follow the standard pattern:
   ```python
   from typing import Any
   from mcp.server.fastmcp import FastMCP
   from app.auth.context import require_current_user
   from app.btp.destination import DestinationServiceClient
   from app.btp.odata_client import SapODataClient
   from app.config import get_settings

   def register_business_partner_tools(mcp: FastMCP) -> None:
       @mcp.tool(
           name="get_business_partners",
           description="Fetch Business Partners from S/4HANA via BTP Destination with principal propagation.",
       )
       async def get_business_partners(top: int = 10) -> dict[str, Any]:
           user = require_current_user()
           settings = get_settings()
           xsappname = settings.xsuaa_credentials.xsappname if settings.xsuaa_credentials else None
           # Enforce XSUAA scope before executing
           user.check_scope("$XSAPPNAME.mcp.execute", xsappname=xsappname)

           odata = SapODataClient(DestinationServiceClient(settings=settings))
           return await odata.get_entity_set(
               destination_name="S4HANA_BP_ODATA",
               entity_path="A_BusinessPartner",
               top=top,
               propagate_principal=True,
           )
   ```
3. Register the module in `srv/app/mcp_server/server.py` inside `create_mcp_server()`.
4. Add unit/integration tests in `srv/tests/test_mcp_server.py`.

---

### Playbook B: Adding a New XSUAA Scope or Role Template

1. Edit `xs-security.json`:
   - Add the scope under `"scopes"` as `{"name": "$XSAPPNAME.mcp.<new_scope>", "description": "..."}`.
   - Reference it in the appropriate `"role-templates"` entry.
2. If an Approuter route itself should require the new scope at the gateway level, update `approuter/xs-app.json`.
3. Check the scope inside your Python MCP tool via:
   ```python
   user.check_scope("$XSAPPNAME.mcp.<new_scope>", xsappname=xsappname)
   ```

---

### Playbook C: Adding a New SAP BTP Managed Service (e.g., HANA Cloud, Connectivity, Redis)

1. **Declare in `mta.yaml`**:
   - Add the service under `resources:` (e.g., `org.cloudfoundry.managed-service`).
   - Add `- name: <service-resource-name>` under `modules -> btp-cf-mcp-srv -> requires`.
2. **Add Typed Credentials in `srv/app/config.py`**:
   - Create a Pydantic model (similar to `XsuaaCredentials` and `DestinationServiceCredentials`) and a `@property` on `Settings` reading from `self.vcap_services.get("<service-label>", [])`.
3. **Add Local Mock Entry in `srv/default-env.json.example`** and test fixture in `srv/tests/conftest.py`.

---

### Playbook D: Using & Extending the Shared Agent Payload Bridge (AWS -> MCP -> SAP Build Joule)

See [`docs/IMPLEMENTATION_PLAN_AGENT_PAYLOAD.md`](IMPLEMENTATION_PLAN_AGENT_PAYLOAD.md) for the design specification:
1. **Storage Architecture (`srv/app/store/payload_store.py`)**:
   - Uses `FilePayloadStore` backed by the project-level shared file `srv/data/agent_payloads.json` by default (with atomic temp-file writes and `asyncio.Lock`), and automatically upgrades to `RedisPayloadStore` if `REDIS_URL` or a `VCAP_SERVICES` Redis binding is present.
   - Payloads are isolated by `agent:payload:{tenant_id}:{payload_key}` (`payload_key` defaults to `"latest"`).
2. **Bridge Tools (`srv/app/mcp_server/tools/agent_payload_tools.py`)**:
   - `store_agent_payload(payload, source="aws", payload_key="latest")`: Overwrites the latest JSON payload for the tenant/key.
   - `get_latest_agent_payload(payload_key="latest", include_metadata=True)`: Retrieves the stored payload + metadata for SAP Build Joule or other downstream agents.
   - `create_s4_master_data(destination_name, entity_path, payload=None, use_stored_payload=False, payload_key="latest", fetch_csrf_token=True, propagate_principal=True)`: Posts a JSON payload (either passed directly or loaded from the shared payload store when `use_stored_payload=True`) to an S/4HANA OData service via `SapODataClient.post_entity()`, automatically handling `X-CSRF-Token: Fetch` and session cookie propagation.

---

## 4. Critical Pitfalls & Troubleshooting Guide

| Symptom | Root Cause | Fix |
| :--- | :--- | :--- |
| `403 Forbidden` (CSRF token validation failed) on `POST /mcp` or `POST /messages` via Approuter | Approuter enables browser CSRF protection on `POST` routes by default | Ensure `"csrfProtection": false` is set on `/mcp`, `/sse`, and `/messages` routes in `approuter/xs-app.json`. |
| `401 Unauthorized` at `btp-cf-mcp-srv` even though Approuter authenticated the user | Destination in `mta.yaml` or `default-env.json` is missing `forwardAuthToken: true` | Verify `forwardAuthToken: true` is set on the `mcp-server-backend` destination in `mta.yaml`. |
| SSE (`/sse`) or Streamable HTTP (`/mcp`) hangs or `require_current_user()` returns `None` mid-stream | Someone introduced `starlette.middleware.base.BaseHTTPMiddleware` | Keep all middleware as pure ASGI classes (like `XsuaaAuthASGIMiddleware` in `srv/app/auth/middleware.py`). |
| Intermittent `404 Session not found` on `/mcp` when scaled to `>1` CF instances | Stateful HTTP session memory not shared across CF containers | Keep `stateless_http=True` in `FastMCP` initialization (`srv/app/mcp_server/server.py`) or back sessions with Redis. |
| `sap-xssec` fails to install on local macOS Apple Silicon | SAP C-extension/wheel availability issues on local dev machines | `requirements.txt` uses `PyJWT[crypto]` by default (`XSUAA_VALIDATOR_BACKEND=auto`), which validates XSUAA PEM/JWKS keys on all platforms without native C dependencies, while supporting `sap-xssec` automatically when installed. |

---

## 5. Pre-Handover Verification Checklist for Coding Agents

Before concluding any task or handing over to another agent/developer, run:

```bash
# 1. Lint & formatting check
make lint

# 2. Full async unit & integration test suite
make test
```

Ensure all tests pass and update Section 1 of this document if new modules, services, or MCP tools were added.
