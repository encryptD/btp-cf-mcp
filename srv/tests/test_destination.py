"""Tests for SAP BTP Destination Service client and Principal Propagation."""

from __future__ import annotations

import httpx
import pytest

from app.auth.context import UserSecurityContext
from app.btp.destination import DestinationServiceClient
from app.btp.odata_client import SapODataClient
from app.config import Settings


@pytest.mark.asyncio
async def test_destination_resolution_with_principal_propagation(
    xsuaa_settings: Settings,
) -> None:
    """Verify DestinationServiceClient sends X-user-token and parses target authTokens."""
    captured_requests: list[httpx.Request] = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        if request.url.path.endswith("/oauth/token"):
            return httpx.Response(
                200,
                json={"access_token": "dest-service-tech-token", "token_type": "bearer"},
            )
        if "/destination-configuration/v1/destinations/S4HANA_CLOUD" in request.url.path:
            assert request.headers.get("Authorization") == "Bearer dest-service-tech-token"
            assert request.headers.get("X-user-token") == "Bearer incoming-user-xsuaa-jwt"
            return httpx.Response(
                200,
                json={
                    "destinationConfiguration": {
                        "Name": "S4HANA_CLOUD",
                        "URL": "https://my300000-api.s4hana.ondemand.com/sap/opu/odata/sap/API_BUSINESS_PARTNER",
                        "Authentication": "OAuth2SAMLBearerAssertion",
                        "ProxyType": "Internet",
                        "sap-client": "100",
                    },
                    "authTokens": [
                        {
                            "type": "Bearer",
                            "value": "s4-propagated-saml-bearer-token",
                            "http_header": {
                                "key": "Authorization",
                                "value": "Bearer s4-propagated-saml-bearer-token",
                            },
                        }
                    ],
                },
            )
        return httpx.Response(404)

    mock_transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=mock_transport) as http_client:
        dest_client = DestinationServiceClient(
            settings=xsuaa_settings,
            http_client=http_client,
        )
        user_ctx = UserSecurityContext(
            raw_token="incoming-user-xsuaa-jwt",
            user_name="alice.smith@sap.com",
            tenant_id="tenant-uuid-9999",
            scopes=["btp-cf-mcp!t9999.mcp.read", "btp-cf-mcp!t9999.mcp.execute"],
            is_mock=False,
        )
        resolved = await dest_client.resolve_destination(
            "S4HANA_CLOUD",
            user_context=user_ctx,
            propagate_principal=True,
        )

    assert len(captured_requests) == 2
    assert resolved.name == "S4HANA_CLOUD"
    assert resolved.authentication_type == "OAuth2SAMLBearerAssertion"
    assert resolved.headers["Authorization"] == "Bearer s4-propagated-saml-bearer-token"
    assert resolved.headers["sap-client"] == "100"


@pytest.mark.asyncio
async def test_odata_client_queries_entity_set_via_resolved_destination(
    xsuaa_settings: Settings,
) -> None:
    """Verify SapODataClient resolves destination and executes OData GET with propagated headers."""

    def mock_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth/token"):
            return httpx.Response(200, json={"access_token": "dest-service-tech-token"})
        if "/destination-configuration/v1/destinations/SAP_NORTHWIND_ODATA" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "destinationConfiguration": {
                        "Name": "SAP_NORTHWIND_ODATA",
                        "URL": "https://services.odata.org/V4/Northwind/Northwind.svc",
                        "Authentication": "NoAuthentication",
                        "ProxyType": "Internet",
                    },
                    "authTokens": [],
                },
            )
        if request.url.host == "services.odata.org" and request.url.path.endswith("/Products"):
            assert request.url.params.get("$top") == "2"
            assert request.url.params.get("$select") == "ProductID,ProductName"
            return httpx.Response(
                200,
                json={
                    "value": [
                        {"ProductID": 1, "ProductName": "Chai"},
                        {"ProductID": 2, "ProductName": "Chang"},
                    ]
                },
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
        result = await odata_client.get_entity_set(
            destination_name="SAP_NORTHWIND_ODATA",
            entity_path="Products",
            top=2,
            select="ProductID,ProductName",
        )

    assert result["destination"] == "SAP_NORTHWIND_ODATA"
    assert len(result["data"]["value"]) == 2
    assert result["data"]["value"][0]["ProductName"] == "Chai"
