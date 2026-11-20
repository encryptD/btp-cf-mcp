"""Application configuration and SAP BTP VCAP_SERVICES parser."""

from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class XsuaaCredentials(BaseModel):
    """Typed credentials extracted from VCAP_SERVICES['xsuaa'][0]['credentials']."""

    clientid: str
    clientsecret: str = ""
    url: str
    uaadomain: str = ""
    xsappname: str
    verificationkey: str = ""
    identityzone: str = ""
    identityzoneid: str = ""
    raw: dict[str, Any] = Field(default_factory=dict)


class DestinationServiceCredentials(BaseModel):
    """Typed credentials extracted from VCAP_SERVICES['destination'][0]['credentials']."""

    clientid: str
    clientsecret: str
    uri: str
    url: str
    uaadomain: str = ""
    raw: dict[str, Any] = Field(default_factory=dict)


def _load_vcap_services_dict() -> dict[str, Any]:
    """Load VCAP_SERVICES from environment variable or fallback to local default-env.json."""
    vcap_raw = os.environ.get("VCAP_SERVICES")
    if vcap_raw:
        try:
            return json.loads(vcap_raw)
        except json.JSONDecodeError as exc:
            logger.warning("Failed to parse VCAP_SERVICES environment variable: %s", exc)

    # Check local default-env.json in current directory or srv/ directory
    for candidate in (Path.cwd() / "default-env.json", Path(__file__).resolve().parents[1] / "default-env.json"):
        if candidate.is_file():
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
                vcap = data.get("VCAP_SERVICES", {})
                if isinstance(vcap, dict):
                    logger.info("Loaded VCAP_SERVICES from local %s", candidate)
                    return vcap
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to read %s: %s", candidate, exc)

    return {}


class Settings(BaseSettings):
    """Runtime settings for the SAP BTP Python MCP Server."""

    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = Field(default="btp-cf-mcp-server", alias="APP_NAME")
    app_env: Literal["development", "test", "production"] = Field(
        default="development", alias="APP_ENV"
    )
    port: int = Field(default=8080, alias="PORT")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # Authentication settings
    auth_mode: Literal["xsuaa", "mock"] = Field(default="xsuaa", alias="AUTH_MODE")
    xsuaa_validator_backend: Literal["auto", "xssec", "pyjwt"] = Field(
        default="auto", alias="XSUAA_VALIDATOR_BACKEND"
    )

    # Mock user configuration (used only when auth_mode == "mock" and not in production)
    mock_user_id: str = Field(default="developer@example.com", alias="MOCK_USER_ID")
    mock_user_given_name: str = Field(default="Local", alias="MOCK_USER_GIVEN_NAME")
    mock_user_family_name: str = Field(default="Developer", alias="MOCK_USER_FAMILY_NAME")
    mock_tenant_id: str = Field(default="default-dev-tenant", alias="MOCK_TENANT_ID")
    mock_scopes: str = Field(
        default="btp-cf-mcp!t1234.mcp.read,btp-cf-mcp!t1234.mcp.execute,btp-cf-mcp!t1234.mcp.admin",
        alias="MOCK_SCOPES",
    )

    # Local Destination fallback URL when no BTP Destination Service is bound
    mock_destination_base_url: str = Field(
        default="https://services.odata.org/V4/Northwind/Northwind.svc",
        alias="MOCK_DESTINATION_BASE_URL",
    )

    @property
    def is_production(self) -> bool:
        """Return True when running in Cloud Foundry or APP_ENV=production."""
        return self.app_env == "production" or bool(os.environ.get("VCAP_APPLICATION"))

    @property
    def effective_auth_mode(self) -> Literal["xsuaa", "mock"]:
        """Never allow mock auth when running in Cloud Foundry / production."""
        if self.is_production:
            return "xsuaa"
        return self.auth_mode

    @property
    def vcap_services(self) -> dict[str, Any]:
        """Return parsed VCAP_SERVICES dictionary."""
        return _load_vcap_services_dict()

    @property
    def xsuaa_credentials(self) -> XsuaaCredentials | None:
        """Return bound XSUAA credentials if present in VCAP_SERVICES."""
        xsuaa_list = self.vcap_services.get("xsuaa", [])
        if not xsuaa_list:
            return None
        creds = xsuaa_list[0].get("credentials", {})
        if not creds:
            return None
        return XsuaaCredentials(
            clientid=creds.get("clientid", ""),
            clientsecret=creds.get("clientsecret", ""),
            url=creds.get("url", ""),
            uaadomain=creds.get("uaadomain", ""),
            xsappname=creds.get("xsappname", ""),
            verificationkey=creds.get("verificationkey", ""),
            identityzone=creds.get("identityzone", ""),
            identityzoneid=creds.get("identityzoneid", ""),
            raw=creds,
        )

    @property
    def destination_credentials(self) -> DestinationServiceCredentials | None:
        """Return bound SAP BTP Destination Service credentials if present in VCAP_SERVICES."""
        dest_list = self.vcap_services.get("destination", [])
        if not dest_list:
            return None
        creds = dest_list[0].get("credentials", {})
        if not creds:
            return None
        return DestinationServiceCredentials(
            clientid=creds.get("clientid", ""),
            clientsecret=creds.get("clientsecret", ""),
            uri=creds.get("uri", ""),
            url=creds.get("url", ""),
            uaadomain=creds.get("uaadomain", ""),
            raw=creds,
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached Settings instance."""
    return Settings()
