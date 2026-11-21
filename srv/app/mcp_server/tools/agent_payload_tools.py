"""Shared Agent Payload bridge (AWS -> MCP -> SAP Build Joule) and S/4HANA Master Data creation tools."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from app.auth.context import require_current_user
from app.btp.destination import DestinationServiceClient
from app.btp.odata_client import SapODataClient
from app.config import get_settings
from app.store.payload_store import get_payload_store


def register_agent_payload_tools(mcp: FastMCP) -> None:
    """Register shared agent payload bridge and S/4HANA master-data creation tools."""

    @mcp.tool(
        name="store_agent_payload",
        description=(
            "Store (or overwrite) the latest shared JSON payload in the project-level store "
            "so downstream agents (such as SAP Build Joule) or S/4HANA creation tools can retrieve it. "
            "Requires '$XSAPPNAME.mcp.execute' scope (or bound XSUAA client_credentials token)."
        ),
    )
    async def store_agent_payload(
        payload: dict[str, Any],
        source: str = "aws",
        payload_key: str = "latest",
    ) -> dict[str, Any]:
        user = require_current_user()
        settings = get_settings()
        xsappname = settings.xsuaa_credentials.xsappname if settings.xsuaa_credentials else None
        user.check_scope("$XSAPPNAME.mcp.execute", xsappname=xsappname)

        store = get_payload_store(settings)
        tenant_id = user.tenant_id or "default"
        record = await store.store(
            payload=payload,
            metadata={
                "updated_by": user.user_name,
                "source": source,
                "grant_type": user.grant_type,
            },
            tenant_id=tenant_id,
            payload_key=payload_key,
        )

        return {
            "status": "stored",
            "backend": store.backend_name,
            "payload_key": payload_key,
            "tenant_id": tenant_id,
            "updated_by": user.user_name,
            "updated_at": record["metadata"]["updated_at"],
            "source": source,
        }

    @mcp.tool(
        name="get_latest_agent_payload",
        description=(
            "Retrieve the latest shared JSON payload previously stored by an upstream agent "
            "(e.g. AWS agent) for consumption by SAP Build Joule or other agents. "
            "Requires '$XSAPPNAME.mcp.read' or '$XSAPPNAME.mcp.execute' scope."
        ),
    )
    async def get_latest_agent_payload(
        payload_key: str = "latest",
        include_metadata: bool = True,
    ) -> dict[str, Any]:
        user = require_current_user()
        settings = get_settings()
        xsappname = settings.xsuaa_credentials.xsappname if settings.xsuaa_credentials else None

        if not (
            user.has_scope("$XSAPPNAME.mcp.read", xsappname=xsappname)
            or user.has_scope("$XSAPPNAME.mcp.execute", xsappname=xsappname)
        ):
            user.check_scope("$XSAPPNAME.mcp.read", xsappname=xsappname)

        store = get_payload_store(settings)
        tenant_id = user.tenant_id or "default"
        record = await store.get_latest(tenant_id=tenant_id, payload_key=payload_key)

        if record is None:
            return {
                "status": "empty",
                "available": False,
                "backend": store.backend_name,
                "payload_key": payload_key,
                "tenant_id": tenant_id,
                "payload": None,
                "message": f"No agent payload found for key '{payload_key}' in tenant '{tenant_id}'.",
            }

        response: dict[str, Any] = {
            "status": "available",
            "available": True,
            "backend": store.backend_name,
            "payload_key": payload_key,
            "tenant_id": tenant_id,
            "payload": record.get("payload"),
        }
        if include_metadata:
            response["metadata"] = record.get("metadata", {})
        return response

    @mcp.tool(
        name="create_s4_master_data",
        description=(
            "Create master data in SAP S/4HANA by POSTing a JSON payload (either provided directly "
            "or loaded from the shared agent payload store when use_stored_payload=True) to an OData "
            "entity set via SAP BTP Destination Service with automatic X-CSRF-Token preflight and "
            "XSUAA principal propagation. Requires '$XSAPPNAME.mcp.execute' scope."
        ),
    )
    async def create_s4_master_data(
        destination_name: str,
        entity_path: str,
        payload: dict[str, Any] | None = None,
        use_stored_payload: bool = False,
        payload_key: str = "latest",
        fetch_csrf_token: bool = True,
        propagate_principal: bool = True,
    ) -> dict[str, Any]:
        user = require_current_user()
        settings = get_settings()
        xsappname = settings.xsuaa_credentials.xsappname if settings.xsuaa_credentials else None
        user.check_scope("$XSAPPNAME.mcp.execute", xsappname=xsappname)

        effective_payload = payload
        if use_stored_payload or effective_payload is None:
            store = get_payload_store(settings)
            tenant_id = user.tenant_id or "default"
            record = await store.get_latest(tenant_id=tenant_id, payload_key=payload_key)
            if record is None or not isinstance(record.get("payload"), dict):
                raise ValueError(
                    f"No stored payload available under key '{payload_key}' for tenant '{tenant_id}'. "
                    "Pass 'payload' explicitly or call 'store_agent_payload' first."
                )
            effective_payload = record["payload"]

        odata_client = SapODataClient(
            destination_client=DestinationServiceClient(settings=settings)
        )
        return await odata_client.post_entity(
            destination_name=destination_name,
            entity_path=entity_path,
            payload=effective_payload,
            propagate_principal=propagate_principal,
            fetch_csrf_token=fetch_csrf_token,
        )
