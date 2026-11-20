"""Tests for SAP BTP XSUAA JWT validation, ASGI middleware, and scope checks."""

from __future__ import annotations

from typing import Callable

import httpx
import pytest

from app.auth.context import AuthorizationError
from app.config import Settings
from app.main import create_app


@pytest.mark.asyncio
async def test_health_endpoint_is_unauthenticated(xsuaa_settings: Settings) -> None:
    """Verify /health is accessible without an Authorization header even when AUTH_MODE=xsuaa."""
    app = create_app(xsuaa_settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "UP"
    assert body["auth_mode"] == "xsuaa"


@pytest.mark.asyncio
async def test_protected_endpoint_rejects_missing_token(xsuaa_settings: Settings) -> None:
    """Verify protected endpoints return 401 when no Bearer token is provided in xsuaa mode."""
    app = create_app(xsuaa_settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/me")

    assert response.status_code == 401
    assert response.headers.get("www-authenticate") == 'Bearer realm="SAP BTP XSUAA"'
    assert response.json()["error"] == "unauthorized"


@pytest.mark.asyncio
async def test_protected_endpoint_accepts_valid_xsuaa_jwt(
    xsuaa_settings: Settings,
    mint_xsuaa_jwt: Callable[..., str],
) -> None:
    """Verify valid RS256-signed XSUAA JWT populates UserSecurityContext accurately."""
    token = mint_xsuaa_jwt(user_name="carol.btp@sap.com", subdomain="customer-subaccount")
    app = create_app(xsuaa_settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/api/me",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["user_name"] == "carol.btp@sap.com"
    assert data["subdomain"] == "customer-subaccount"
    assert data["is_mock"] is False
    assert "btp-cf-mcp!t9999.mcp.execute" in data["scopes"]
    assert data["user_attributes"] == {"CompanyCode": ["1000", "2000"]}


@pytest.mark.asyncio
async def test_protected_endpoint_rejects_wrong_audience_or_expired_jwt(
    xsuaa_settings: Settings,
    mint_xsuaa_jwt: Callable[..., str],
) -> None:
    """Verify XSUAA validator rejects tokens with wrong audience or expired timestamps."""
    wrong_aud_token = mint_xsuaa_jwt(aud=["other-app!t1111"], cid="sb-other-app!t1111")
    expired_token = mint_xsuaa_jwt(exp_offset=-60)

    app = create_app(xsuaa_settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        res_aud = await client.get(
            "/api/me",
            headers={"Authorization": f"Bearer {wrong_aud_token}"},
        )
        res_exp = await client.get(
            "/api/me",
            headers={"Authorization": f"Bearer {expired_token}"},
        )

    assert res_aud.status_code == 401
    assert "audience mismatch" in res_aud.json()["message"].lower()
    assert res_exp.status_code == 401


def test_production_env_never_allows_mock_auth() -> None:
    """Ensure APP_ENV=production forces effective_auth_mode='xsuaa' even if AUTH_MODE='mock'."""
    prod_settings = Settings(APP_ENV="production", AUTH_MODE="mock")
    assert prod_settings.is_production is True
    assert prod_settings.effective_auth_mode == "xsuaa"


def test_user_security_context_scope_matching(
    xsuaa_settings: Settings,
    mint_xsuaa_jwt: Callable[..., str],
) -> None:
    """Verify has_scope and check_scope handle $XSAPPNAME templates and exact scopes."""
    from app.auth.xsuaa import XsuaaTokenValidator

    validator = XsuaaTokenValidator(xsuaa_settings)
    token = mint_xsuaa_jwt(scopes=["btp-cf-mcp!t9999.mcp.read"])
    ctx = validator.validate_request_authorization(f"Bearer {token}")

    assert ctx.has_scope("$XSAPPNAME.mcp.read", xsappname="btp-cf-mcp!t9999") is True
    assert ctx.has_scope("mcp.read") is True
    assert ctx.has_scope("$XSAPPNAME.mcp.execute", xsappname="btp-cf-mcp!t9999") is False

    with pytest.raises(AuthorizationError):
        ctx.check_scope("$XSAPPNAME.mcp.execute", xsappname="btp-cf-mcp!t9999")
