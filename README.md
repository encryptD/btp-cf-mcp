# SAP BTP Cloud Foundry Python MCP Server (`btp-cf-mcp`)

Production-ready scaffold for a **Python Model Context Protocol (MCP) Server** on **SAP BTP Cloud Foundry**, adhering to the enterprise **SAP Approuter + XSUAA + BTP Destination Service** architecture with **Principal Propagation** and **Cross-Agent Portability**.

---

## Key Features

- **FastMCP + FastAPI/Starlette ASGI Stack (`srv/`)**:
  - Supports both **Streamable HTTP (`/mcp`)** (with `stateless_http=True` for multi-instance Cloud Foundry scaling) and **SSE (`/sse`, `/messages`)** transports.
- **Standalone Node.js SAP Approuter (`approuter/`)**:
  - Routes `/mcp`, `/sse`, `/messages`, and `/api/*` with `authenticationType: "xsuaa"`, `forwardAuthToken: true`, and `csrfProtection: false` for seamless MCP client compatibility.
- **Dual-Mode XSUAA JWT Security (`srv/app/auth/`)**:
  - Supports `sap-xssec` and `PyJWT` (PEM `verificationkey` & `/token_keys` JWKS validation), plus a local development `AUTH_MODE=mock` mode (automatically disabled in Cloud Foundry production).
  - Pure ASGI middleware (`XsuaaAuthASGIMiddleware`) propagates the validated `UserSecurityContext` (user, tenant, scopes, SAML attributes, raw JWT) into MCP tools via `contextvars`.
- **SAP BTP Destination Service & Principal Propagation (`srv/app/btp/`)**:
  - Resolves BTP Destinations using the caller's XSUAA JWT (`X-user-token` header or `urn:ietf:params:oauth:grant-type:jwt-bearer` token exchange) and queries SAP OData V2/V4 services via `SapODataClient`.
- **Coding Agent Handover & Portability**:
  - Includes [`AGENTS.md`](AGENTS.md), [`docs/AGENT_HANDOVER.md`](docs/AGENT_HANDOVER.md), [`.cursorrules`](.cursorrules), [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md), and Architecture Decision Records (`docs/adr/`).

---

## Repository Structure

```text
btp-cf-mcp/
├── AGENTS.md                           # Universal coding agent entrypoint
├── .cursorrules                        # Cursor / Windsurf IDE agent rules
├── mta.yaml                            # SAP BTP Multi-Target Application descriptor
├── xs-security.json                    # XSUAA scopes, attributes, role templates & collections
├── Makefile                            # Developer & agent automation targets
├── approuter/                          # Standalone @sap/approuter Node.js module
│   ├── package.json
│   ├── xs-app.json                     # Routing & XSUAA scope rules
│   └── default-env.json.example
├── srv/                                # Python MCP Server (CF python_buildpack)
│   ├── Procfile
│   ├── runtime.txt
│   ├── requirements.txt
│   ├── pyproject.toml
│   ├── app/
│   │   ├── config.py                   # Settings & VCAP_SERVICES parser
│   │   ├── main.py                     # FastAPI + FastMCP ASGI app factory
│   │   ├── auth/                       # XSUAA validator, ContextVar & pure ASGI middleware
│   │   ├── btp/                        # BTP Destination Service & OData client
│   │   └── mcp_server/                 # FastMCP tools, resources & prompts
│   └── tests/                          # Async pytest suite (auth, destination, MCP tools)
└── docs/
    ├── AGENT_HANDOVER.md               # Portable handover guide & extension playbooks
    ├── ARCHITECTURE.md                 # Mermaid topology & sequence diagrams
    └── adr/                            # Architecture Decision Records
```

---

## Quickstart (Local Development)

### 1. Install Dependencies
```bash
make install
```

### 2. Run the Test Suite & Linter
```bash
make test
make lint
```

### 3. Start Local Server (Mock Auth Mode)
```bash
cp .env.example .env
make dev
```
- **Health Check**: `http://localhost:8080/health`
- **Current User Diagnostic**: `http://localhost:8080/api/me`
- **MCP Streamable HTTP Endpoint**: `http://localhost:8080/mcp`
- **MCP SSE Endpoint**: `http://localhost:8080/sse`

---

## Deploying to SAP BTP Cloud Foundry

### Prerequisites
- [Cloud Foundry CLI (`cf`)](https://github.com/cloudfoundry/cli) with MultiApps plugin (`cf install-plugin multiapps`)
- [Cloud MTA Build Tool (`mbt`)](https://sap.github.io/cloud-mta-build-tool/)

### Build & Deploy
```bash
# 1. Log in to your SAP BTP Cloud Foundry target
cf login -a https://api.cf.<region>.hana.ondemand.com

# 2. Build the MTA archive (.mtar)
make build-mta

# 3. Deploy to Cloud Foundry
make deploy
```

After deployment:
1. Open the **SAP BTP Cockpit** -> **Security** -> **Role Collections** and assign `BTP_MCP_User` or `BTP_MCP_Admin` to your users.
2. Connect your MCP client to `https://<approuter-route>/mcp` (or `/sse`) with an XSUAA Bearer token.
