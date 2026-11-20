"""FastMCP server instance and capability registration."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from app.mcp_server.prompts.sap_prompts import register_sap_prompts
from app.mcp_server.resources.btp_resources import register_btp_resources
from app.mcp_server.tools.sap_odata_tools import register_sap_odata_tools
from app.mcp_server.tools.system_tools import register_system_tools


def _build_transport_security_kwargs() -> dict[str, Any]:
    """Disable localhost-only Host header rejection when running behind Cloud Foundry GoRouter / Approuter."""
    try:
        from mcp.server.transport_security import TransportSecuritySettings

        return {
            "transport_security": TransportSecuritySettings(
                enable_dns_rebinding_protection=False
            )
        }
    except ImportError:
        return {}


def create_mcp_server(name: str = "btp-cf-mcp-server") -> FastMCP:
    """Create and configure the FastMCP server with SAP BTP tools, resources, and prompts."""
    server = FastMCP(
        name,
        stateless_http=True,
        json_response=True,
        **_build_transport_security_kwargs(),
    )
    register_system_tools(server)
    register_sap_odata_tools(server)
    register_btp_resources(server)
    register_sap_prompts(server)
    return server


mcp = create_mcp_server()
