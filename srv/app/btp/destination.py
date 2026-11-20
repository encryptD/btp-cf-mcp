"""SAP BTP Destination Service client with XSUAA Principal Propagation / JWT Token Exchange."""

from __future__ import annotations

import logging
from typing import Any

import httpx
from pydantic import BaseModel, Field

from app.auth.context import UserSecurityContext, get_current_user
from app.config import DestinationServiceCredentials, Settings, get_settings

logger = logging.getLogger(__name__)


class DestinationResolutionError(Exception):
    """Raised when a SAP BTP Destination cannot be resolved or token exchange fails."""


class ResolvedDestination(BaseModel):
    """Represents a resolved SAP BTP Destination ready for outbound HTTP/OData calls."""

    name: str
    url: str
    authentication_type: str = "NoAuthentication"
    proxy_type: str = "Internet"
    headers: dict[str, str] = Field(default_factory=dict)
    sap_client: str | None = None
    is_mock: bool = False
    raw_configuration: dict[str, Any] = Field(default_factory=dict)


class DestinationServiceClient:
    """Resolves SAP BTP Destinations with support for XSUAA Principal Propagation.

    Architecture Flow (Principal Propagation):
    1. Approuter authenticates the user via XSUAA and forwards the user's JWT (`Authorization: Bearer <user_jwt>`)
       to this Python MCP Server.
    2. When an MCP Tool needs to call an SAP backend (S/4HANA, SuccessFactors, CAP service) via a BTP Destination:
       - Mode A (JWT Bearer Exchange): Exchanges the user's incoming JWT against `{destination.url}/oauth/token`
         using `grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer` with the Destination service's client credentials.
       - Mode B (Client Credentials + X-user-token): Obtains a technical token for the Destination service and passes
         the incoming user's JWT in the `X-user-token` header when calling
         `GET {destination.uri}/destination-configuration/v1/destinations/{name}`.
    3. The BTP Destination Service performs the configured target authentication flow (e.g., OAuth2SAMLBearerAssertion,
       OAuth2UserTokenExchange, BasicAuthentication) and returns `authTokens[0].http_header` ready for the target call.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._http_client = http_client

    async def resolve_destination(
        self,
        destination_name: str,
        *,
        user_context: UserSecurityContext | None = None,
        propagate_principal: bool = True,
        use_jwt_bearer_exchange: bool = False,
    ) -> ResolvedDestination:
        """Resolve a named destination from SAP BTP Destination Service."""
        user_ctx = user_context or get_current_user()
        dest_creds = self.settings.destination_credentials

        if dest_creds is None:
            if self.settings.effective_auth_mode == "mock":
                return self._build_mock_destination(destination_name, user_ctx)
            raise DestinationResolutionError(
                f"Cannot resolve destination '{destination_name}': No SAP BTP 'destination' service binding found in VCAP_SERVICES."
            )

        if use_jwt_bearer_exchange and propagate_principal and user_ctx and not user_ctx.is_mock:
            service_token = await self._exchange_user_jwt_for_destination_token(
                dest_creds, user_ctx.raw_token
            )
            extra_headers: dict[str, str] = {}
        else:
            service_token = await self._fetch_client_credentials_token(dest_creds)
            extra_headers = {}
            if propagate_principal and user_ctx and user_ctx.raw_token and not user_ctx.is_mock:
                extra_headers["X-user-token"] = f"Bearer {user_ctx.raw_token}"

        payload = await self._fetch_destination_configuration(
            dest_creds=dest_creds,
            destination_name=destination_name,
            service_access_token=service_token,
            extra_headers=extra_headers,
        )
        return self._parse_destination_payload(destination_name, payload)

    async def _fetch_client_credentials_token(
        self, dest_creds: DestinationServiceCredentials
    ) -> str:
        """Obtain an OAuth2 client_credentials access token for the BTP Destination Service."""
        token_url = f"{dest_creds.url.rstrip('/')}/oauth/token"
        data = {"grant_type": "client_credentials"}
        return await self._request_oauth_token(
            token_url=token_url,
            client_id=dest_creds.clientid,
            client_secret=dest_creds.clientsecret,
            form_data=data,
        )

    async def _exchange_user_jwt_for_destination_token(
        self,
        dest_creds: DestinationServiceCredentials,
        user_jwt: str,
    ) -> str:
        """Perform OAuth2 JWT Bearer Token Exchange (urn:ietf:params:oauth:grant-type:jwt-bearer)."""
        token_url = f"{dest_creds.url.rstrip('/')}/oauth/token"
        data = {
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": user_jwt,
            "response_type": "token",
        }
        return await self._request_oauth_token(
            token_url=token_url,
            client_id=dest_creds.clientid,
            client_secret=dest_creds.clientsecret,
            form_data=data,
        )

    async def _request_oauth_token(
        self,
        *,
        token_url: str,
        client_id: str,
        client_secret: str,
        form_data: dict[str, str],
    ) -> str:
        """Execute OAuth2 token request against XSUAA."""
        owns_client = self._http_client is None
        client = self._http_client or httpx.AsyncClient(timeout=15.0)
        try:
            response = await client.post(
                token_url,
                data=form_data,
                auth=(client_id, client_secret),
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            token_data = response.json()
            access_token = token_data.get("access_token")
            if not access_token:
                raise DestinationResolutionError(
                    f"XSUAA token response from {token_url} did not contain 'access_token'."
                )
            return str(access_token)
        except httpx.HTTPError as exc:
            raise DestinationResolutionError(
                f"Failed to obtain Destination Service OAuth token from {token_url}: {exc}"
            ) from exc
        finally:
            if owns_client:
                await client.aclose()

    async def _fetch_destination_configuration(
        self,
        *,
        dest_creds: DestinationServiceCredentials,
        destination_name: str,
        service_access_token: str,
        extra_headers: dict[str, str],
    ) -> dict[str, Any]:
        """Call GET /destination-configuration/v1/destinations/{destination_name}."""
        endpoint = (
            f"{dest_creds.uri.rstrip('/')}/destination-configuration/v1/destinations/{destination_name}"
        )
        headers = {
            "Authorization": f"Bearer {service_access_token}",
            "Accept": "application/json",
            **extra_headers,
        }
        owns_client = self._http_client is None
        client = self._http_client or httpx.AsyncClient(timeout=15.0)
        try:
            response = await client.get(endpoint, headers=headers)
            response.raise_for_status()
            return dict(response.json())
        except httpx.HTTPError as exc:
            raise DestinationResolutionError(
                f"Failed to fetch destination '{destination_name}' from {endpoint}: {exc}"
            ) from exc
        finally:
            if owns_client:
                await client.aclose()

    @staticmethod
    def _parse_destination_payload(
        destination_name: str, payload: dict[str, Any]
    ) -> ResolvedDestination:
        """Convert BTP Destination Service response JSON into a ResolvedDestination."""
        config = payload.get("destinationConfiguration", {}) or {}
        url = config.get("URL") or config.get("url")
        if not url:
            raise DestinationResolutionError(
                f"Destination '{destination_name}' response is missing 'destinationConfiguration.URL'."
            )

        headers: dict[str, str] = {}
        auth_tokens = payload.get("authTokens", []) or []
        for token_entry in auth_tokens:
            http_header = token_entry.get("http_header") or {}
            header_key = http_header.get("key")
            header_val = http_header.get("value")
            if header_key and header_val:
                headers[str(header_key)] = str(header_val)
                break

        sap_client = config.get("sap-client")
        if sap_client:
            headers["sap-client"] = str(sap_client)

        return ResolvedDestination(
            name=destination_name,
            url=str(url).rstrip("/"),
            authentication_type=str(config.get("Authentication", "NoAuthentication")),
            proxy_type=str(config.get("ProxyType", "Internet")),
            headers=headers,
            sap_client=str(sap_client) if sap_client else None,
            is_mock=False,
            raw_configuration=config,
        )

    def _build_mock_destination(
        self, destination_name: str, user_ctx: UserSecurityContext | None
    ) -> ResolvedDestination:
        """Return a mock destination for local development without BTP service bindings."""
        headers: dict[str, str] = {}
        if user_ctx and user_ctx.raw_token:
            headers["X-Forwarded-User"] = user_ctx.user_name
        return ResolvedDestination(
            name=destination_name,
            url=self.settings.mock_destination_base_url.rstrip("/"),
            authentication_type="MockLocalAuth",
            proxy_type="Internet",
            headers=headers,
            is_mock=True,
            raw_configuration={
                "Name": destination_name,
                "URL": self.settings.mock_destination_base_url,
                "Authentication": "MockLocalAuth",
            },
        )
