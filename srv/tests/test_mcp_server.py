"""Tests for FastMCP server tools, resources, and prompts registration & execution."""

from __future__ import annotations

import json

import pytest

from app.auth.context import UserSecurityContext, reset_current_user, set_current_user
from app.mcp_server.server import create_mcp_server


@pytest.mark.asyncio
async def test_mcp_server_registers_tools_resources_and_prompts() -> None:
    """Verify all expected SAP BTP tools, resources, and prompts are registered."""
    mcp = create_mcp_server("test-mcp-server")

    tools = await mcp.list_tools()
    tool_names = {t.name for t in tools}
    assert {
        "whoami",
        "check_btp_environment",
        "resolve_sap_destination",
        "query_sap_odata",
        "store_agent_payload",
        "get_latest_agent_payload",
        "create_s4_master_data",
    }.issubset(tool_names)

    resources = await mcp.list_resources()
    resource_uris = {str(r.uri) for r in resources}
    assert "btp://env/service-bindings" in resource_uris
    assert "btp://security/current-user" in resource_uris

    prompts = await mcp.list_prompts()
    prompt_names = {p.name for p in prompts}
    assert "sap_odata_exploration" in prompt_names


@pytest.mark.asyncio
async def test_whoami_tool_returns_current_xsuaa_user() -> None:
    """Verify whoami MCP tool reads UserSecurityContext from ContextVar."""
    mcp = create_mcp_server("test-mcp-server")
    user_ctx = UserSecurityContext(
        raw_token="sample-jwt-token",
        user_name="david.miller@sap.com",
        email="david.miller@sap.com",
        given_name="David",
        family_name="Miller",
        tenant_id="tenant-1234",
        subdomain="subaccount-prod",
        scopes=["btp-cf-mcp!t1234.mcp.read", "btp-cf-mcp!t1234.mcp.execute"],
        user_attributes={"CompanyCode": ["1000"]},
        is_mock=False,
    )

    token = set_current_user(user_ctx)
    try:
        result = await mcp.call_tool("whoami", {})
        # FastMCP call_tool returns a sequence of Content blocks or tuple (content, structured)
        content_blocks = result[0] if isinstance(result, tuple) else result
        text_payload = content_blocks[0].text
        parsed = json.loads(text_payload)

        assert parsed["user_name"] == "david.miller@sap.com"
        assert parsed["tenant_id"] == "tenant-1234"
        assert parsed["permissions"]["mcp_read"] is True
        assert parsed["permissions"]["mcp_execute"] is True
        assert parsed["permissions"]["mcp_admin"] is False
    finally:
        reset_current_user(token)
