"""Reusable MCP Prompts for SAP BTP OData and security diagnostics."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP


def register_sap_prompts(mcp: FastMCP) -> None:
    """Register SAP BTP workflow prompts on the FastMCP instance."""

    @mcp.prompt(
        name="sap_odata_exploration",
        description="Step-by-step workflow to verify XSUAA permissions, inspect a BTP Destination, and query an OData entity set.",
    )
    def sap_odata_exploration(
        destination_name: str = "SAP_NORTHWIND_ODATA",
        entity_path: str = "Products",
    ) -> str:
        return (
            f"Please perform a diagnostic and data exploration workflow for SAP BTP Destination '{destination_name}':\n"
            "1. Call the `whoami` tool first to verify the current user's XSUAA identity, tenant ID, and whether "
            "`mcp_read` and `mcp_execute` permissions are granted.\n"
            f"2. Call `resolve_sap_destination` with `destination_name='{destination_name}'` to confirm connectivity "
            "and authentication configuration.\n"
            f"3. If `mcp_execute` is granted, call `query_sap_odata` with `destination_name='{destination_name}'` "
            f"and `entity_path='{entity_path}'` (`top=5`) and summarize the returned records."
        )
