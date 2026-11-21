"""Request-scoped security context using contextvars for MCP tool execution."""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any

from pydantic import BaseModel, Field


class AuthenticationError(Exception):
    """Raised when an incoming request lacks a valid XSUAA JWT token."""


class AuthorizationError(Exception):
    """Raised when an authenticated user lacks a required XSUAA scope."""


class UserSecurityContext(BaseModel):
    """Represents the validated SAP BTP XSUAA user identity and JWT context."""

    raw_token: str = Field(description="Raw Bearer JWT forwarded by SAP Approuter")
    user_name: str = Field(description="User identifier (user_name or sub claim)")
    email: str | None = None
    given_name: str | None = None
    family_name: str | None = None
    tenant_id: str = Field(default="", description="XSUAA Identity Zone ID (zid)")
    subdomain: str | None = Field(default=None, description="XSUAA tenant subdomain (ext_attr.zdn)")
    scopes: list[str] = Field(default_factory=list, description="Granted OAuth2/XSUAA scopes")
    user_attributes: dict[str, list[str]] = Field(
        default_factory=dict, description="XSUAA SAML/custom user attributes (xs.user.attributes)"
    )
    client_id: str = ""
    grant_type: str = "user_token"
    is_mock: bool = False
    claims: dict[str, Any] = Field(default_factory=dict)

    def has_scope(self, scope: str, xsappname: str | None = None) -> bool:
        """Check if the user has the requested scope.

        Supports checking either:
        - Exact scope string (e.g., 'btp-cf-mcp!t1234.mcp.execute')
        - '$XSAPPNAME.<suffix>' template (e.g., '$XSAPPNAME.mcp.execute')
        - Relative scope suffix (e.g., 'mcp.execute'), matched against xsappname or suffix `.mcp.execute`
        """
        if scope in self.scopes:
            return True

        normalized_suffix = scope.removeprefix("$XSAPPNAME.")
        if xsappname:
            expected = f"{xsappname}.{normalized_suffix}"
            if expected in self.scopes:
                return True

        # Suffix match against `<xsappname>.<suffix>` when xsappname contains service instance suffix
        dot_suffix = f".{normalized_suffix}"
        if any(granted.endswith(dot_suffix) for granted in self.scopes):
            return True

        # Allow bound XSUAA technical M2M tokens (grant_type == "client_credentials")
        # for read/execute operations (e.g. AWS agent calling store_agent_payload / create_s4_master_data)
        if (
            self.grant_type == "client_credentials"
            and normalized_suffix in ("mcp.read", "mcp.execute")
            and self.client_id
        ):
            if not xsappname or xsappname in self.client_id or self.client_id.startswith("sb-"):
                return True

        return False

    def check_scope(self, scope: str, xsappname: str | None = None) -> None:
        """Raise AuthorizationError if the required scope is missing."""
        if not self.has_scope(scope, xsappname=xsappname):
            raise AuthorizationError(
                f"User '{self.user_name}' lacks required XSUAA scope '{scope}'."
            )


_current_security_context: ContextVar[UserSecurityContext | None] = ContextVar(
    "current_security_context", default=None
)


def set_current_user(ctx: UserSecurityContext | None) -> Token[UserSecurityContext | None]:
    """Bind a UserSecurityContext to the current async execution context."""
    return _current_security_context.set(ctx)


def reset_current_user(token: Token[UserSecurityContext | None]) -> None:
    """Restore previous UserSecurityContext in contextvar."""
    _current_security_context.reset(token)


def get_current_user() -> UserSecurityContext | None:
    """Retrieve the current request's UserSecurityContext, or None if not set."""
    return _current_security_context.get()


def require_current_user() -> UserSecurityContext:
    """Retrieve the current request's UserSecurityContext or raise AuthenticationError."""
    ctx = _current_security_context.get()
    if ctx is None:
        raise AuthenticationError("No active XSUAA security context found for this request.")
    return ctx
