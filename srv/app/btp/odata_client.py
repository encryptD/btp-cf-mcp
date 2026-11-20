"""Async SAP OData / REST client powered by SAP BTP Destination Service and Principal Propagation."""

from __future__ import annotations

from typing import Any

import httpx

from app.btp.destination import DestinationServiceClient, ResolvedDestination


class SapODataClient:
    """Executes outbound OData/REST calls against SAP systems via BTP Destination Service."""

    def __init__(
        self,
        destination_client: DestinationServiceClient | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.destination_client = destination_client or DestinationServiceClient()
        self._http_client = http_client

    async def get_entity_set(
        self,
        destination_name: str,
        entity_path: str,
        *,
        top: int = 10,
        select: str | None = None,
        filter_expr: str | None = None,
        propagate_principal: bool = True,
    ) -> dict[str, Any]:
        """Query an OData V2/V4 entity set from a named SAP BTP Destination."""
        resolved: ResolvedDestination = await self.destination_client.resolve_destination(
            destination_name,
            propagate_principal=propagate_principal,
        )

        clean_path = entity_path.lstrip("/")
        target_url = f"{resolved.url}/{clean_path}"

        params: dict[str, str] = {
            "$top": str(top),
            "$format": "json",
        }
        if select:
            params["$select"] = select
        if filter_expr:
            params["$filter"] = filter_expr

        headers = {
            "Accept": "application/json",
            **resolved.headers,
        }

        owns_client = self._http_client is None
        client = self._http_client or httpx.AsyncClient(timeout=30.0)
        try:
            response = await client.get(target_url, params=params, headers=headers)
            response.raise_for_status()
            data = response.json()
            return {
                "destination": resolved.name,
                "target_url": str(response.request.url),
                "authentication_type": resolved.authentication_type,
                "is_mock_destination": resolved.is_mock,
                "data": data,
            }
        finally:
            if owns_client:
                await client.aclose()
