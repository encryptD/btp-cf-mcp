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

    async def post_entity(
        self,
        destination_name: str,
        entity_path: str,
        payload: dict[str, Any],
        *,
        propagate_principal: bool = True,
        fetch_csrf_token: bool = True,
    ) -> dict[str, Any]:
        """POST an entity to an SAP OData V2/V4 service via BTP Destination + principal propagation.

        When `fetch_csrf_token=True` (default), automatically executes a preflight GET with
        `X-CSRF-Token: Fetch` against the target OData service root / entity URL and forwards
        the returned `x-csrf-token` header and SAP session cookies on the POST request.
        """
        resolved: ResolvedDestination = await self.destination_client.resolve_destination(
            destination_name,
            propagate_principal=propagate_principal,
        )

        clean_path = entity_path.lstrip("/")
        target_url = f"{resolved.url}/{clean_path}"

        base_headers: dict[str, str] = {
            "Accept": "application/json",
            **resolved.headers,
        }

        owns_client = self._http_client is None
        client = self._http_client or httpx.AsyncClient(timeout=30.0)
        try:
            post_headers: dict[str, str] = {
                **base_headers,
                "Content-Type": "application/json",
            }
            csrf_token_used: str | None = None

            if fetch_csrf_token:
                csrf_headers = {
                    **base_headers,
                    "X-CSRF-Token": "Fetch",
                }
                # Preflight GET against service base URL to obtain X-CSRF-Token & session cookies
                preflight_resp = await client.get(resolved.url, headers=csrf_headers)
                fetched_token = preflight_resp.headers.get("x-csrf-token")
                if fetched_token and fetched_token.lower() != "required":
                    csrf_token_used = fetched_token
                    post_headers["X-CSRF-Token"] = fetched_token

                # Forward any Set-Cookie values explicitly if MockTransport or stateless transport is used
                set_cookies = preflight_resp.headers.get_list("set-cookie")
                if set_cookies and "Cookie" not in post_headers:
                    cookie_pairs = [c.split(";", 1)[0].strip() for c in set_cookies if "=" in c]
                    if cookie_pairs:
                        post_headers["Cookie"] = "; ".join(cookie_pairs)

            response = await client.post(
                target_url,
                json=payload,
                headers=post_headers,
            )
            response.raise_for_status()

            response_data: Any
            if response.content:
                try:
                    response_data = response.json()
                except ValueError:
                    response_data = {"raw_text": response.text}
            else:
                response_data = {}

            return {
                "destination": resolved.name,
                "target_url": str(response.request.url),
                "status_code": response.status_code,
                "location": response.headers.get("location"),
                "csrf_token_used": csrf_token_used is not None,
                "authentication_type": resolved.authentication_type,
                "is_mock_destination": resolved.is_mock,
                "data": response_data,
            }
        finally:
            if owns_client:
                await client.aclose()
