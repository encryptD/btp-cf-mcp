# ADR 0001: Standalone SAP Approuter + FastMCP Streamable HTTP & SSE Transport

- **Status**: Accepted
- **Date**: 2026-11-13

## Context
Model Context Protocol (MCP) servers deployed on SAP BTP Cloud Foundry must support enterprise OAuth2/OIDC authentication via SAP XSUAA, work seamlessly with Cloud Foundry's GoRouter load balancer across multiple instances, and support both modern MCP **Streamable HTTP (`/mcp`)** and legacy **Server-Sent Events (`/sse`, `/messages`)** clients.

## Decision
1. **Standalone `@sap/approuter` (`approuter/`)**:
   - Acts as the single external entry point for MCP clients.
   - Enforces `"authenticationType": "xsuaa"` and base scope `"$XSAPPNAME.mcp.read"` at the edge.
   - Disables browser-specific CSRF token checks (`"csrfProtection": false`) on `/mcp`, `/sse`, and `/messages` routes so programmatic MCP clients authenticating via `Authorization: Bearer <jwt>` can issue `POST` JSON-RPC calls without `403 Forbidden` CSRF errors.
   - Forwards the caller's XSUAA JWT to the Python backend (`forwardAuthToken: true`).
2. **Stateless Streamable HTTP (`stateless_http=True`) + Pure ASGI Middleware (`srv/`)**:
   - FastMCP is initialized with `stateless_http=True` so any Cloud Foundry container instance can serve `POST /mcp` requests without sticky sessions.
   - Authentication middleware (`XsuaaAuthASGIMiddleware`) is implemented as a pure ASGI middleware rather than Starlette's `BaseHTTPMiddleware` to prevent response buffering and `ContextVar` loss on streaming transports.

## Consequences
- Horizontal scaling (`cf scale btp-cf-mcp-srv -i 3`) works out-of-the-box for Streamable HTTP (`/mcp`).
- Both browser-authenticated sessions and programmatic OAuth2 Bearer MCP clients are supported through the same Approuter gateway.
