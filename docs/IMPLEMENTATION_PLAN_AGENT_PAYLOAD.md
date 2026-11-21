# Implementation Plan: Shared Agent Payload + S/4 Master Data Creation

> **Audience**: Google Antigravity (or any coding agent) working on `encryptD/btp-cf-mcp`.
>
> **Goal**: Enable an AWS-hosted agent to post a JSON payload to this MCP server, make that payload available to a SAP Build Joule agent (via a different tool) until a newer payload arrives, and allow the AWS agent to optionally post the same (or related) JSON to an S/4HANA OData service for master-data creation.
>
> **Constraint**: Current Joule version has no A2A, so the AWS agent cannot call Joule directly. The shared payload on this MCP server is the bridge.

---

## 1. Current Baseline (already implemented)

- Approuter + XSUAA + pure ASGI middleware (`XsuaaAuthASGIMiddleware`) + `ContextVar` user context.
- FastMCP with `stateless_http=True` (required for multi-instance CF).
- Destination Service client + principal propagation (`DestinationServiceClient`).
- OData read client (`SapODataClient.get_entity_set`).
- Existing tools: `whoami`, `check_btp_environment`, `resolve_sap_destination`, `query_sap_odata`.
- Scopes: `$XSAPPNAME.mcp.read`, `$XSAPPNAME.mcp.execute`, `$XSAPPNAME.mcp.admin`.

**Do not violate the guardrails in `AGENTS.md`** (especially pure ASGI middleware, scope checks via `require_current_user()`, `stateless_http=True`, and `csrfProtection: false` on MCP routes).

---

## 2. Target Architecture

| Caller | Action | MCP Tool | Backend |
|--------|--------|----------|---------|
| AWS agent | Send / overwrite JSON | `store_agent_payload` | Persistent store (Redis recommended) |
| Joule agent | Read latest JSON | `get_latest_agent_payload` | Same store |
| AWS agent | Create master data in S/4 | `create_s4_master_data` | Existing Destination + new OData POST |

Because of CF multi-instance scaling + `stateless_http=True`, the payload store **must be external**. An in-memory dict is not acceptable for production.

---

## 3. Recommended Storage Choice

**Primary recommendation: Redis**

- Single key (e.g. `agent:payload:latest`) holding JSON: `{ "payload": {...}, "metadata": { "updated_at": "...", "updated_by": "...", "source": "aws" } }`.
- Last-write-wins semantics match the requirement (“available until a new JSON is sent”).
- Bind a Redis service (BTP Redis, Hyperscaler Redis, or a small CF Redis) and require it from `btp-cf-mcp-srv` in `mta.yaml`.
- Local / test fallback: simple in-memory dict behind the same interface.

**Acceptable alternatives** (if Redis is not desired):
- Single-row table in HANA Cloud or PostgreSQL Hyperscaler.
- Object Store (single object `latest-payload.json`).

Implement a small abstraction so the tools do not care which backend is used.

---

## 4. Concrete Implementation Steps

### 4.1 Payload Store Abstraction

Create `srv/app/store/payload_store.py` (and `srv/app/store/__init__.py`):

- Define a protocol / ABC with:
  - `async def store(self, payload: dict, metadata: dict) -> None`
  - `async def get_latest(self) -> dict | None`
- Provide:
  - `RedisPayloadStore` (using `redis.asyncio`)
  - `InMemoryPayloadStore` (for local development and unit tests)
- Factory function that returns Redis when credentials are available, otherwise InMemory.

Extend `srv/app/config.py` to surface Redis connection details from `VCAP_SERVICES` (or a simple `REDIS_URL` env var for local).

Add `redis` (or `redis[hiredis]`) to `srv/requirements.txt`.

### 4.2 Extend OData Client for Writes

In `srv/app/btp/odata_client.py` add a method such as:

```python
async def post_entity(
    self,
    destination_name: str,
    entity_path: str,
    payload: dict[str, Any],
    *,
    propagate_principal: bool = True,
) -> dict[str, Any]:
    """POST an entity to an OData service via BTP Destination + principal propagation."""
    # Resolve destination the same way as get_entity_set
    # POST JSON body to resolved.url / entity_path
    # Return status code, response body, and useful headers (e.g. Location)
```

Reuse the existing `DestinationServiceClient` resolution and header logic. Keep principal propagation consistent with the GET path.

### 4.3 New MCP Tools

Create `srv/app/mcp_server/tools/agent_payload_tools.py` and register it in `srv/app/mcp_server/server.py` (inside `create_mcp_server()`).

Follow the exact pattern used in `sap_odata_tools.py` and `system_tools.py`:

```python
def register_agent_payload_tools(mcp: FastMCP) -> None:
    @mcp.tool(
        name="store_agent_payload",
        description="Store (overwrite) the latest agent payload so other agents (e.g. Joule) can retrieve it."
    )
    async def store_agent_payload(payload: dict, source: str = "aws") -> dict:
        user = require_current_user()
        settings = get_settings()
        xsappname = ...
        user.check_scope("$XSAPPNAME.mcp.execute", xsappname=xsappname)
        # store via PayloadStore with metadata (user.user_name, timestamp, source)
        return {"status": "stored", "updated_by": user.user_name}

    @mcp.tool(
        name="get_latest_agent_payload",
        description="Retrieve the latest agent payload previously stored by the AWS agent."
    )
    async def get_latest_agent_payload(include_metadata: bool = True) -> dict:
        user = require_current_user()
        # check_scope read or execute
        # return payload + optional metadata, or clear "none available"

    @mcp.tool(
        name="create_s4_master_data",
        description="Create master data in S/4HANA by POSTing a JSON payload to an OData entity set via BTP Destination."
    )
    async def create_s4_master_data(
        destination_name: str,
        entity_path: str,
        payload: dict,
    ) -> dict:
        user = require_current_user()
        user.check_scope("$XSAPPNAME.mcp.execute", xsappname=xsappname)
        odata = SapODataClient(...)
        return await odata.post_entity(
            destination_name=destination_name,
            entity_path=entity_path,
            payload=payload,
            propagate_principal=True,
        )
```

### 4.4 Security & Deployment Updates

- **Optional tighter scope**: Add `$XSAPPNAME.mcp.payload` (or similar) in `xs-security.json` and reference it in the relevant role templates if you want to separate “share payload” from “execute S/4 writes”. Otherwise reusing `mcp.execute` is acceptable.
- **`mta.yaml`**: Add the Redis (or chosen store) resource and require it from the `btp-cf-mcp-srv` module.
- **Approuter**: No change needed for the new tools (they are invoked through the existing `/mcp` or `/sse` routes).

### 4.5 Documentation & Tests

- Update `docs/AGENT_HANDOVER.md` (Section 1 table + a short new playbook).
- Optionally add a one-line pointer in `AGENTS.md`.
- Add unit tests in `srv/tests/` covering:
  - Store + retrieve round-trip (InMemory store).
  - Scope enforcement on the three tools.
  - OData POST method (mock httpx).

---

## 5. Operational Notes for the Agents

- Both the AWS agent and the Joule agent must present a valid XSUAA Bearer token that carries the required scopes. The existing Approuter + middleware already enforce this.
- Expected flow:
  1. AWS agent calls `store_agent_payload` (and optionally `create_s4_master_data`).
  2. Joule agent later calls `get_latest_agent_payload` and acts on the returned JSON.
- Last-write-wins: document ownership so concurrent AWS calls do not race unexpectedly.
- For S/4 writes, the Destination must be configured with an authentication type that supports principal propagation (typically OAuth2SAMLBearerAssertion or UserTokenExchange).

---

## 6. Suggested Implementation Order for Antigravity

1. Payload store abstraction + Redis (or chosen backend) + config changes.
2. `SapODataClient.post_entity`.
3. Three new tools + registration in `server.py`.
4. `mta.yaml` + `requirements.txt` + optional `xs-security.json` update.
5. Tests + handover doc updates.
6. `make test` / `make lint` then deploy.

---

## 7. Acceptance Criteria

- AWS agent can store a JSON payload; a subsequent call overwrites the previous one.
- Joule agent can retrieve the latest stored payload (including basic metadata).
- AWS agent can POST a JSON payload to a configured S/4 OData entity set via Destination Service with principal propagation.
- All tools enforce XSUAA scopes via `require_current_user()`.
- Behavior is correct under multi-instance CF (no reliance on in-process memory for the shared payload).
- Existing tools, auth middleware, and `stateless_http=True` remain unchanged.

---

*This document is the single source of truth for the Antigravity agent to implement the AWS → MCP → Joule payload bridge and the S/4 master-data creation path.*
