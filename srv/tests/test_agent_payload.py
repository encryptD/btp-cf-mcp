"""Tests for Shared Agent Payload Store (File & InMemory), MCP Payload Tools, and S/4HANA OData POST."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from mcp.server.fastmcp.exceptions import ToolError

from app.auth.context import (
    AuthorizationError,
    UserSecurityContext,
    reset_current_user,
    set_current_user,
)
from app.btp.destination import DestinationServiceClient
from app.btp.odata_client import SapODataClient
from app.config import Settings
from app.mcp_server.server import create_mcp_server
from app.store.payload_store import (
    FilePayloadStore,
    InMemoryPayloadStore,
    reset_payload_store,
)


@pytest.mark.asyncio
async def test_file_payload_store_round_trip_and_last_write_wins(tmp_path: Path) -> None:
    """Verify FilePayloadStore persists JSON atomically and overwrites on subsequent store calls."""
    store_file = tmp_path / "agent_payloads.json"
    store = FilePayloadStore(store_file)

    assert await store.get_latest(tenant_id="t1", payload_key="latest") is None

    first_payload = {"BusinessPartner": "100001", "BusinessPartnerCategory": "1"}
    await store.store(
        first_payload,
        {"updated_by": "aws-agent", "source": "aws"},
        tenant_id="t1",
        payload_key="latest",
    )

    loaded_1 = await store.get_latest(tenant_id="t1", payload_key="latest")
    assert loaded_1 is not None
    assert loaded_1["payload"] == first_payload
    assert loaded_1["metadata"]["source"] == "aws"

    # Second write overwrites previous payload (last-write-wins)
    second_payload = {"BusinessPartner": "100002", "BusinessPartnerCategory": "2"}
    await store.store(
        second_payload,
        {"updated_by": "aws-agent-v2", "source": "aws"},
        tenant_id="t1",
        payload_key="latest",
    )

    loaded_2 = await store.get_latest(tenant_id="t1", payload_key="latest")
    assert loaded_2 is not None
    assert loaded_2["payload"] == second_payload
    assert loaded_2["metadata"]["updated_by"] == "aws-agent-v2"

    # Other tenants remain isolated
    assert await store.get_latest(tenant_id="other-tenant", payload_key="latest") is None


@pytest.mark.asyncio
async def test_mcp_store_and_get_latest_agent_payload_tools() -> None:
    """Verify AWS -> MCP -> Joule round-trip via store_agent_payload and get_latest_agent_payload."""
    mem_store = InMemoryPayloadStore()
    reset_payload_store(mem_store)
    mcp = create_mcp_server("test-payload-mcp")

    user_ctx = UserSecurityContext(
        raw_token="aws-user-jwt",
        user_name="aws-agent@example.com",
        tenant_id="tenant-9999",
        scopes=["btp-cf-mcp!t9999.mcp.read", "btp-cf-mcp!t9999.mcp.execute"],
        is_mock=False,
    )
    token = set_current_user(user_ctx)
    try:
        sample_bp = {
            "BusinessPartnerCategory": "2",
            "OrganizationBPName1": "Acme Corp",
            "SearchTerm1": "ACME",
        }
        store_res = await mcp.call_tool(
            "store_agent_payload",
            {"payload": sample_bp, "source": "aws"},
        )
        store_blocks = store_res[0] if isinstance(store_res, tuple) else store_res
        store_json = json.loads(store_blocks[0].text)
        assert store_json["status"] == "stored"
        assert store_json["backend"] == "memory"

        get_res = await mcp.call_tool("get_latest_agent_payload", {})
        get_blocks = get_res[0] if isinstance(get_res, tuple) else get_res
        get_json = json.loads(get_blocks[0].text)
        assert get_json["status"] == "available"
        assert get_json["available"] is True
        assert get_json["payload"] == sample_bp
        assert get_json["metadata"]["source"] == "aws"
        assert get_json["metadata"]["updated_by"] == "aws-agent@example.com"
    finally:
        reset_current_user(token)
        reset_payload_store(None)


@pytest.mark.asyncio
async def test_agent_payload_tools_enforce_scopes_and_support_m2m_client_credentials() -> None:
    """Verify scope checks reject unauthorized users and allow bound XSUAA client_credentials."""
    mem_store = InMemoryPayloadStore()
    reset_payload_store(mem_store)
    mcp = create_mcp_server("test-scope-mcp")

    # 1. User token with NO scopes should fail store_agent_payload
    no_scope_user = UserSecurityContext(
        raw_token="unprivileged-jwt",
        user_name="guest@example.com",
        tenant_id="tenant-9999",
        scopes=["openid", "uaa.user"],
        grant_type="password",
        is_mock=False,
    )
    tok1 = set_current_user(no_scope_user)
    try:
        with pytest.raises((AuthorizationError, ToolError)):
            await mcp.call_tool(
                "store_agent_payload",
                {"payload": {"key": "blocked"}, "source": "aws"},
            )
    finally:
        reset_current_user(tok1)

    # 2. Bound XSUAA M2M client_credentials token should succeed
    m2m_client = UserSecurityContext(
        raw_token="m2m-client-jwt",
        user_name="sb-btp-cf-mcp!t9999",
        tenant_id="tenant-9999",
        scopes=[],
        client_id="sb-btp-cf-mcp!t9999",
        grant_type="client_credentials",
        is_mock=False,
    )
    tok2 = set_current_user(m2m_client)
    try:
        res = await mcp.call_tool(
            "store_agent_payload",
            {"payload": {"Material": "MAT-100"}, "source": "aws-m2m"},
        )
        blocks = res[0] if isinstance(res, tuple) else res
        parsed = json.loads(blocks[0].text)
        assert parsed["status"] == "stored"
    finally:
        reset_current_user(tok2)
        reset_payload_store(None)


@pytest.mark.asyncio
async def test_odata_client_post_entity_with_csrf_preflight(
    xsuaa_settings: Settings,
) -> None:
    """Verify SapODataClient.post_entity fetches X-CSRF-Token + cookies and POSTs JSON to S/4HANA."""
    captured_requests: list[httpx.Request] = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        if request.url.path.endswith("/oauth/token"):
            return httpx.Response(200, json={"access_token": "dest-service-tech-token"})
        if "/destination-configuration/v1/destinations/S4HANA_BP" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "destinationConfiguration": {
                        "Name": "S4HANA_BP",
                        "URL": "https://s4hana.example.com/sap/opu/odata/sap/API_BUSINESS_PARTNER",
                        "Authentication": "OAuth2SAMLBearerAssertion",
                        "ProxyType": "Internet",
                        "sap-client": "100",
                    },
                    "authTokens": [
                        {
                            "type": "Bearer",
                            "value": "s4-user-token",
                            "http_header": {
                                "key": "Authorization",
                                "value": "Bearer s4-user-token",
                            },
                        }
                    ],
                },
            )
        if (
            request.method == "GET"
            and request.url.host == "s4hana.example.com"
            and request.headers.get("X-CSRF-Token") == "Fetch"
        ):
            return httpx.Response(
                200,
                headers={
                    "x-csrf-token": "s4-csrf-token-xyz",
                    "set-cookie": "SAP_SESSIONID_100=session123; path=/",
                },
                json={"d": {"EntitySets": ["A_BusinessPartner"]}},
            )
        if (
            request.method == "POST"
            and request.url.host == "s4hana.example.com"
            and request.url.path.endswith("/A_BusinessPartner")
        ):
            assert request.headers.get("Authorization") == "Bearer s4-user-token"
            assert request.headers.get("X-CSRF-Token") == "s4-csrf-token-xyz"
            assert "SAP_SESSIONID_100=session123" in (request.headers.get("Cookie") or "")
            assert request.headers.get("sap-client") == "100"
            body = json.loads(request.content.decode("utf-8"))
            return httpx.Response(
                201,
                headers={
                    "Location": "https://s4hana.example.com/sap/opu/odata/sap/API_BUSINESS_PARTNER/A_BusinessPartner('1000555')"
                },
                json={"d": {"BusinessPartner": "1000555", **body}},
            )
        return httpx.Response(404)

    mock_transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=mock_transport) as http_client:
        dest_client = DestinationServiceClient(
            settings=xsuaa_settings,
            http_client=http_client,
        )
        odata_client = SapODataClient(
            destination_client=dest_client,
            http_client=http_client,
        )
        result = await odata_client.post_entity(
            destination_name="S4HANA_BP",
            entity_path="A_BusinessPartner",
            payload={"BusinessPartnerCategory": "2", "OrganizationBPName1": "Global Trade Ltd"},
        )

    assert result["status_code"] == 201
    assert result["csrf_token_used"] is True
    assert result["data"]["d"]["BusinessPartner"] == "1000555"
    assert "A_BusinessPartner('1000555')" in (result["location"] or "")
