"""Dual-mode SAP BTP XSUAA JWT Validator (sap-xssec + PyJWT JWKS/PEM + Local Dev Mock)."""

from __future__ import annotations

import logging
from typing import Any

import jwt
from jwt import PyJWKClient

from app.auth.context import AuthenticationError, UserSecurityContext
from app.config import Settings, XsuaaCredentials

logger = logging.getLogger(__name__)

try:
    from sap import xssec as sap_xssec  # type: ignore[import-untyped]

    HAS_SAP_XSSEC = True
except ImportError:
    sap_xssec = None
    HAS_SAP_XSSEC = False


class XsuaaTokenValidator:
    """Validates Bearer JWT tokens issued by SAP BTP XSUAA and forwarded by Approuter."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._jwks_clients: dict[str, PyJWKClient] = {}

    def validate_request_authorization(self, authorization_header: str | None) -> UserSecurityContext:
        """Validate an HTTP Authorization header and return a UserSecurityContext."""
        token = self._extract_bearer_token(authorization_header)

        if self.settings.effective_auth_mode == "mock":
            return self._build_mock_context(token)

        if not token:
            raise AuthenticationError(
                "Missing Bearer token in Authorization header. Ensure requests are routed via SAP Approuter with forwardAuthToken: true."
            )

        xsuaa_creds = self.settings.xsuaa_credentials
        if xsuaa_creds is None:
            raise AuthenticationError(
                "XSUAA service credentials not found in VCAP_SERVICES while AUTH_MODE='xsuaa'."
            )

        backend = self.settings.xsuaa_validator_backend
        if backend == "xssec" or (backend == "auto" and HAS_SAP_XSSEC):
            return self._validate_with_xssec(token, xsuaa_creds)

        return self._validate_with_pyjwt(token, xsuaa_creds)

    @staticmethod
    def _extract_bearer_token(authorization_header: str | None) -> str | None:
        if not authorization_header:
            return None
        parts = authorization_header.strip().split(" ", 1)
        if len(parts) != 2 or parts[0].lower() != "bearer":
            raise AuthenticationError("Invalid Authorization header format. Expected 'Bearer <JWT>'.")
        token = parts[1].strip()
        return token or None

    def _validate_with_xssec(
        self, token: str, xsuaa_creds: XsuaaCredentials
    ) -> UserSecurityContext:
        """Validate JWT using official SAP sap-xssec library."""
        if not HAS_SAP_XSSEC or sap_xssec is None:
            raise AuthenticationError(
                "XSUAA_VALIDATOR_BACKEND='xssec' requested, but 'sap-xssec' package is not installed."
            )
        try:
            sec_ctx = sap_xssec.create_security_context(token, xsuaa_creds.raw)
        except Exception as exc:
            logger.warning("sap-xssec token validation failed: %s", exc)
            raise AuthenticationError(f"XSUAA token validation failed (sap-xssec): {exc}") from exc

        # Also decode unverified payload to extract full scope list and attributes for inspection
        claims = jwt.decode(token, options={"verify_signature": False})
        scopes = list(claims.get("scope", []))
        ext_attr = claims.get("ext_attr", {}) or {}
        xs_user_attrs = claims.get("xs.user.attributes", {}) or {}

        return UserSecurityContext(
            raw_token=token,
            user_name=getattr(sec_ctx, "get_logon_name", lambda: None)()
            or claims.get("user_name")
            or claims.get("sub", "unknown"),
            email=getattr(sec_ctx, "get_email", lambda: None)() or claims.get("email"),
            given_name=getattr(sec_ctx, "get_given_name", lambda: None)() or claims.get("given_name"),
            family_name=getattr(sec_ctx, "get_family_name", lambda: None)() or claims.get("family_name"),
            tenant_id=getattr(sec_ctx, "get_zone_id", lambda: None)() or claims.get("zid", ""),
            subdomain= getattr(sec_ctx, "get_subdomain", lambda: None)() or ext_attr.get("zdn"),
            scopes=scopes,
            user_attributes=xs_user_attrs,
            client_id=claims.get("cid", claims.get("client_id", "")),
            grant_type= getattr(sec_ctx, "get_grant_type", lambda: None)() or claims.get("grant_type", ""),
            is_mock=False,
            claims=claims,
        )

    def _validate_with_pyjwt(
        self, token: str, xsuaa_creds: XsuaaCredentials
    ) -> UserSecurityContext:
        """Validate JWT using PyJWT against XSUAA verificationkey (PEM) or JWKS endpoint."""
        try:
            signing_key = self._resolve_signing_key(token, xsuaa_creds)
            claims: dict[str, Any] = jwt.decode(
                token,
                key=signing_key,
                algorithms=["RS256"],
                options={
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_iat": True,
                    "verify_aud": False,  # Verified explicitly below for XSUAA aud/cid rules
                },
            )
        except Exception as exc:
            logger.warning("PyJWT XSUAA token validation failed: %s", exc)
            raise AuthenticationError(f"Invalid or expired XSUAA JWT token: {exc}") from exc

        self._verify_xsuaa_audience(claims, xsuaa_creds)
        return self._claims_to_context(token, claims, is_mock=False)

    def _resolve_signing_key(self, token: str, xsuaa_creds: XsuaaCredentials) -> Any:
        """Resolve public key from bound XSUAA verificationkey or remote JWKS /token_keys."""
        if xsuaa_creds.verificationkey.strip():
            return xsuaa_creds.verificationkey.strip()

        if not xsuaa_creds.url:
            raise AuthenticationError(
                "XSUAA credentials contain neither 'verificationkey' nor 'url' for JWKS discovery."
            )

        jwks_uri = f"{xsuaa_creds.url.rstrip('/')}/token_keys"
        if jwks_uri not in self._jwks_clients:
            self._jwks_clients[jwks_uri] = PyJWKClient(jwks_uri)

        jwk_client = self._jwks_clients[jwks_uri]
        return jwk_client.get_signing_key_from_jwt(token).key

    @staticmethod
    def _verify_xsuaa_audience(claims: dict[str, Any], xsuaa_creds: XsuaaCredentials) -> None:
        """Verify XSUAA token audience (aud) or client_id (cid) matches bound XSUAA service."""
        raw_aud = claims.get("aud", [])
        audiences: set[str] = {raw_aud} if isinstance(raw_aud, str) else set(raw_aud or [])
        cid = claims.get("cid") or claims.get("client_id")
        if cid:
            audiences.add(cid)

        # XSUAA often emits aud values like "btp-cf-mcp!t1234.user" -> strip dot suffixes as well
        normalized_audiences = set(audiences)
        for aud in audiences:
            if "." in aud:
                normalized_audiences.add(aud.split(".", 1)[0])

        allowed = {
            v
            for v in (xsuaa_creds.xsappname, xsuaa_creds.clientid)
            if v
        }
        if allowed and not (normalized_audiences & allowed):
            raise AuthenticationError(
                f"JWT audience mismatch. Token aud/cid {sorted(audiences)} does not match bound XSUAA {sorted(allowed)}."
            )

    def _build_mock_context(self, token: str | None) -> UserSecurityContext:
        """Construct a synthetic UserSecurityContext for local development (AUTH_MODE=mock)."""
        if token and token.count(".") == 2:
            try:
                claims = jwt.decode(token, options={"verify_signature": False})
                return self._claims_to_context(token, claims, is_mock=True)
            except Exception:  # noqa: BLE001
                logger.debug("Mock mode token was not a decodable JWT; using configured mock profile.")

        scopes = [s.strip() for s in self.settings.mock_scopes.split(",") if s.strip()]
        raw_token = token or "mock-local-dev-jwt-token"
        return UserSecurityContext(
            raw_token=raw_token,
            user_name=self.settings.mock_user_id,
            email=self.settings.mock_user_id,
            given_name=self.settings.mock_user_given_name,
            family_name=self.settings.mock_user_family_name,
            tenant_id=self.settings.mock_tenant_id,
            subdomain="local-dev",
            scopes=scopes,
            user_attributes={"CompanyCode": ["1000"]},
            client_id="sb-btp-cf-mcp-mock",
            grant_type="mock_local",
            is_mock=True,
            claims={
                "user_name": self.settings.mock_user_id,
                "email": self.settings.mock_user_id,
                "zid": self.settings.mock_tenant_id,
                "scope": scopes,
            },
        )

    @staticmethod
    def _claims_to_context(
        raw_token: str, claims: dict[str, Any], *, is_mock: bool
    ) -> UserSecurityContext:
        ext_attr = claims.get("ext_attr", {}) or {}
        xs_user_attrs = claims.get("xs.user.attributes", {}) or {}
        scopes = claims.get("scope", [])
        if isinstance(scopes, str):
            scopes = scopes.split()

        return UserSecurityContext(
            raw_token=raw_token,
            user_name=claims.get("user_name") or claims.get("sub", "unknown"),
            email=claims.get("email"),
            given_name=claims.get("given_name"),
            family_name=claims.get("family_name"),
            tenant_id=claims.get("zid", ""),
            subdomain=ext_attr.get("zdn"),
            scopes=list(scopes),
            user_attributes=xs_user_attrs,
            client_id=claims.get("cid", claims.get("client_id", "")),
            grant_type=claims.get("grant_type", "user_token"),
            is_mock=is_mock,
            claims=claims,
        )
