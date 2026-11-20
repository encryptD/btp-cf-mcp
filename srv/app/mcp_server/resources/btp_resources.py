"""SAP BTP environment and security context MCP resources."""

from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

from app.auth.context import get_current_user
from app.config import get_settings


def register_btp_resources(mcp: FastMCP) -> None:
    """Register SAP BTP metadata resources on the FastMCP instance."""

    @mcp.resource(
        "btp://env/service-bindings",
        name="btp_service_bindings",
        description="Sanitized summary of bound SAP BTP Cloud Foundry services (XSUAA, Destination).",
        mime_type="application/json",
    )
    async def get_service_bindings_resource() -> str:
        settings = get_settings()
        xsuaa_creds = settings.xsuaa_credentials
        dest_creds = settings.destination_credentials

        payload = {
            "app_name": settings.app_name,
            "app_env": settings.app_env,
            "effective_auth_mode": settings.effective_auth_mode,
            "services": {
                "xsuaa": {
                    "bound": xsuaa_creds is not None,
                    "xsappname": xsuaa_creds.xsappname if xsuaa_creds else None,
                    "uaadomain": xsuaa_creds.uaadomain if xsuaa_creds else None,
                },
                "destination": {
                    "bound": dest_creds is not None,
                    "uri": dest_creds.uri if dest_creds else None,
                },
            },
        }
        return json.dumps(payload, indent=2)

    @mcp.resource(
        "btp://security/current-user",
        name="btp_current_user",
        description="Current request's authenticated SAP BTP XSUAA user identity and scopes.",
        mime_type="application/json",
    )
    async def get_current_user_resource() -> str:
        user = get_current_user()
        if user is None:
            return json.dumps({"authenticated": False}, indent=2)
        return json.dumps(
            {
                "authenticated": True,
                "user_name": user.user_name,
                "email": user.email,
                "tenant_id": user.tenant_id,
                "subdomain": user.subdomain,
                "scopes": user.scopes,
                "user_attributes": user.user_attributes,
                "is_mock": user.is_mock,
            },
            indent=2,
        )
