"""Pytest fixtures for RSA-signed XSUAA JWT tokens and mock BTP VCAP_SERVICES."""

from __future__ import annotations

import json
import time
from typing import Any, Callable

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.config import Settings


@pytest.fixture(scope="session")
def rsa_keypair() -> tuple[str, str]:
    """Generate a 2048-bit RSA private/public PEM keypair for test XSUAA JWT signing."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")

    public_pem = (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("utf-8")
    )
    return private_pem, public_pem


@pytest.fixture
def xsuaa_vcap_env(monkeypatch: pytest.MonkeyPatch, rsa_keypair: tuple[str, str]) -> dict[str, Any]:
    """Configure VCAP_SERVICES in environment with test XSUAA and Destination bindings."""
    _, public_pem = rsa_keypair
    vcap = {
        "xsuaa": [
            {
                "name": "btp-cf-mcp-xsuaa",
                "label": "xsuaa",
                "tags": ["xsuaa"],
                "credentials": {
                    "clientid": "sb-btp-cf-mcp!t9999",
                    "clientsecret": "test-xsuaa-secret",
                    "url": "https://test-tenant.authentication.eu10.hana.ondemand.com",
                    "uaadomain": "authentication.eu10.hana.ondemand.com",
                    "xsappname": "btp-cf-mcp!t9999",
                    "identityzone": "test-tenant",
                    "identityzoneid": "tenant-uuid-9999",
                    "verificationkey": public_pem,
                },
            }
        ],
        "destination": [
            {
                "name": "btp-cf-mcp-destination",
                "label": "destination",
                "tags": ["destination"],
                "credentials": {
                    "clientid": "sb-destination!b9999",
                    "clientsecret": "test-dest-secret",
                    "uri": "https://destination-configuration.cfapps.eu10.hana.ondemand.com",
                    "url": "https://test-tenant.authentication.eu10.hana.ondemand.com",
                    "uaadomain": "authentication.eu10.hana.ondemand.com",
                },
            }
        ],
    }
    monkeypatch.setenv("VCAP_SERVICES", json.dumps(vcap))
    return vcap


@pytest.fixture
def mint_xsuaa_jwt(rsa_keypair: tuple[str, str]) -> Callable[..., str]:
    """Factory fixture that mints RS256-signed XSUAA JWT tokens."""
    private_pem, _ = rsa_keypair

    def _mint(
        *,
        user_name: str = "alice.smith@sap.com",
        email: str = "alice.smith@sap.com",
        zid: str = "tenant-uuid-9999",
        subdomain: str = "test-tenant",
        scopes: list[str] | None = None,
        aud: list[str] | None = None,
        cid: str = "sb-btp-cf-mcp!t9999",
        exp_offset: int = 3600,
        user_attributes: dict[str, list[str]] | None = None,
    ) -> str:
        now = int(time.time())
        payload: dict[str, Any] = {
            "sub": user_name,
            "user_name": user_name,
            "email": email,
            "given_name": "Alice",
            "family_name": "Smith",
            "zid": zid,
            "cid": cid,
            "client_id": cid,
            "aud": aud if aud is not None else ["btp-cf-mcp!t9999", cid],
            "scope": scopes
            if scopes is not None
            else [
                "uaa.user",
                "btp-cf-mcp!t9999.mcp.read",
                "btp-cf-mcp!t9999.mcp.execute",
            ],
            "ext_attr": {"zdn": subdomain},
            "xs.user.attributes": user_attributes or {"CompanyCode": ["1000", "2000"]},
            "grant_type": "authorization_code",
            "iat": now - 10,
            "exp": now + exp_offset,
            "iss": f"https://{subdomain}.authentication.eu10.hana.ondemand.com/oauth/token",
        }
        return jwt.encode(payload, private_pem, algorithm="RS256")

    return _mint


@pytest.fixture
def xsuaa_settings(xsuaa_vcap_env: dict[str, Any]) -> Settings:
    """Return Settings configured for strict XSUAA PyJWT validation."""
    return Settings(
        APP_NAME="btp-cf-mcp-test",
        APP_ENV="test",
        AUTH_MODE="xsuaa",
        XSUAA_VALIDATOR_BACKEND="pyjwt",
    )
