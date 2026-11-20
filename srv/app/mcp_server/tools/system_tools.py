"""System and XSUAA identity introspection MCP tools."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from app.auth.context import require_current_user
from app.config import get_settings


def register_system_tools(mcp: FastMCP) -> None:
    """Register identity and BTP environment diagnostic tools on the FastMCP instance."""

    @mcp.tool(
        name="whoami",
        description=(
            "Return the authenticated SAP BTP XSUAA user profile, tenant ID, subdomain, "
            "granted OAuth2 scopes, and SAML user attributes forwarded by SAP Approuter."
        ),
    )
    async def whoami() -> dict[str, Any]:
        user = require_current_user()
        settings = get_settings()
        xsappname = settings.xsuaa_credentials.xsappname if settings.xsuaa_credentials else None

        return {
            "user_name": user.user_name,
            "email": user.email,
            "given_name": user.given_name,
            "family_name": user.family_name,
            "tenant_id": user.tenant_id,
            "subdomain": user.subdomain,
            "scopes": user.scopes,
            "permissions": {
                "mcp_read": user.has_scope("$XSAPPNAME.mcp.read", xsappname=xsappname),
                "mcp_execute": user.has_scope("$XSAPPNAME.mcp.execute", xsappname=xsappname),
                "mcp_admin": user.has_scope("$XSAPPNAME.mcp.admin", xsappname=xsappname),
            },
            "user_attributes": user.user_attributes,
            "grant_type": user.grant_type,
            "is_mock": user.is_mock,
        }

    @mcp.tool(
        name="check_btp_environment",
        description=(
            "Inspect the SAP BTP Cloud Foundry runtime status, active authentication mode, "
            "and bound VCAP_SERVICES (XSUAA, Destination Service) without exposing secrets."
        ),
    )
    async def check_btp_environment() -> dict[str, Any]:
        settings = get_settings()
        user = require_current_user()
        xsuaa_creds = settings.xsuaa_credentials
        dest_creds = settings.destination_credentials

        return {
            "app_name": settings.app_name,
            "app_env": settings.app_env,
            "effective_auth_mode": settings.effective_auth_mode,
            "authenticated_as": user.user_name,
            "bindings": {
                "xsuaa_bound": xsuaa_creds is not None,
                "xsuaa_xsappname": xsuaa_creds.xsappname if xsuaa_creds else None,
                "xsuaa_url": xsuaa_creds.url if xsuaa_creds else None,
                "destination_bound": dest_creds is not None,
                "destination_uri": dest_creds.uri if dest_creds else None,
            },
        }
