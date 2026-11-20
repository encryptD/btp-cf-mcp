"""SAP BTP Destination Service and OData MCP tools with Principal Propagation."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from app.auth.context import require_current_user
from app.btp.destination import DestinationServiceClient
from app.btp.odata_client import SapODataClient
from app.config import get_settings


def register_sap_odata_tools(mcp: FastMCP) -> None:
    """Register SAP BTP Destination and OData querying tools on the FastMCP instance."""

    @mcp.tool(
        name="resolve_sap_destination",
        description=(
            "Resolve a named SAP BTP Destination from the BTP Destination Service using "
            "the current user's XSUAA security context (principal propagation) and return "
            "its sanitized configuration metadata."
        ),
    )
    async def resolve_sap_destination(
        destination_name: str = "SAP_NORTHWIND_ODATA",
        propagate_principal: bool = True,
    ) -> dict[str, Any]:
        user = require_current_user()
        settings = get_settings()
        xsappname = settings.xsuaa_credentials.xsappname if settings.xsuaa_credentials else None
        user.check_scope("$XSAPPNAME.mcp.read", xsappname=xsappname)

        client = DestinationServiceClient(settings=settings)
        resolved = await client.resolve_destination(
            destination_name=destination_name,
            user_context=user,
            propagate_principal=propagate_principal,
        )

        return {
            "name": resolved.name,
            "url": resolved.url,
            "authentication_type": resolved.authentication_type,
            "proxy_type": resolved.proxy_type,
            "sap_client": resolved.sap_client,
            "is_mock": resolved.is_mock,
            "forwarded_header_keys": sorted(resolved.headers.keys()),
            "resolved_for_user": user.user_name,
        }

    @mcp.tool(
        name="query_sap_odata",
        description=(
            "Query an OData V2/V4 entity set via a configured SAP BTP Destination with "
            "automatic XSUAA user principal propagation. Requires the '$XSAPPNAME.mcp.execute' scope."
        ),
    )
    async def query_sap_odata(
        entity_path: str = "Products",
        destination_name: str = "SAP_NORTHWIND_ODATA",
        top: int = 5,
        select: str | None = None,
        filter_expr: str | None = None,
        propagate_principal: bool = True,
    ) -> dict[str, Any]:
        user = require_current_user()
        settings = get_settings()
        xsappname = settings.xsuaa_credentials.xsappname if settings.xsuaa_credentials else None
        user.check_scope("$XSAPPNAME.mcp.execute", xsappname=xsappname)

        odata_client = SapODataClient(
            destination_client=DestinationServiceClient(settings=settings)
        )
        return await odata_client.get_entity_set(
            destination_name=destination_name,
            entity_path=entity_path,
            top=max(1, min(top, 100)),
            select=select,
            filter_expr=filter_expr,
            propagate_principal=propagate_principal,
        )
