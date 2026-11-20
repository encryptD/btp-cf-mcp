# ADR 0002: Dual-Mode XSUAA JWT Validation and BTP Destination Principal Propagation

- **Status**: Accepted
- **Date**: 2026-11-13

## Context
SAP BTP Python applications traditionally rely on `sap-xssec` for XSUAA token validation. However:
1. Local developer laptops (e.g., macOS Apple Silicon) and CI containers often need to run and test the MCP server without Cloud Foundry service bindings or private SAP wheel registries.
2. Outbound calls from MCP tools to SAP systems (S/4HANA, SuccessFactors, CAP OData) must execute under the logged-in user's SAP identity (**Principal Propagation**) rather than a shared technical user.

## Decision
1. **Dual-Mode XSUAA Validator (`srv/app/auth/xsuaa.py`)**:
   - Supports `sap-xssec` automatically when installed (`XSUAA_VALIDATOR_BACKEND=auto` or `xssec`).
   - Provides a first-class `PyJWT[crypto]` validator (`XSUAA_VALIDATOR_BACKEND=pyjwt` or fallback in `auto`) that verifies RS256 signatures against `VCAP_SERVICES.xsuaa[0].credentials.verificationkey` (PEM) or `{xsuaa.url}/token_keys` (JWKS), plus `exp`, `iat`, and `aud`/`cid`.
   - Provides a `mock` mode (`AUTH_MODE=mock`) for local development that injects a configurable `UserSecurityContext`, while `Settings.effective_auth_mode` strictly blocks `mock` mode whenever `APP_ENV=production` or `VCAP_APPLICATION` is present.
2. **ContextVar-Propagated Principal Propagation (`srv/app/btp/destination.py`)**:
   - The validated raw JWT is stored on `UserSecurityContext.raw_token` inside a request-scoped `ContextVar`.
   - `DestinationServiceClient.resolve_destination()` forwards the user's JWT to the SAP BTP Destination Service via `X-user-token: Bearer <user_jwt>` (or OAuth2 `urn:ietf:params:oauth:grant-type:jwt-bearer` token exchange) and attaches the returned target `authTokens` header to outbound OData requests.

## Consequences
- Zero-friction local development (`make dev`, `make test`) on any OS/CPU architecture.
- Enterprise-grade Zero-Trust JWT validation and SAP user principal propagation in Cloud Foundry production.
