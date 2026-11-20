"""Pure ASGI Middleware for SAP BTP XSUAA JWT validation compatible with MCP SSE & Streamable HTTP."""

from __future__ import annotations

import logging
from typing import Iterable

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.auth.context import AuthenticationError, reset_current_user, set_current_user
from app.auth.xsuaa import XsuaaTokenValidator
from app.config import Settings

logger = logging.getLogger(__name__)


class XsuaaAuthASGIMiddleware:
    """Pure ASGI middleware that validates XSUAA Bearer tokens and populates ContextVar.

    NOTE: Pure ASGI middleware is deliberately used instead of Starlette's BaseHTTPMiddleware
    so that Server-Sent Events (/sse) and Streamable HTTP (/mcp) streaming responses are never
    buffered or have their ContextVars lost across streaming chunks.
    """

    def __init__(
        self,
        app: ASGIApp,
        settings: Settings,
        exempt_paths: Iterable[str] = ("/health", "/docs", "/openapi.json"),
    ) -> None:
        self.app = app
        self.settings = settings
        self.validator = XsuaaTokenValidator(settings)
        self.exempt_paths = set(exempt_paths)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path: str = scope.get("path", "")
        if path in self.exempt_paths:
            await self.app(scope, receive, send)
            return

        auth_header = self._get_header(scope, b"authorization")
        try:
            user_ctx = self.validator.validate_request_authorization(auth_header)
        except AuthenticationError as exc:
            logger.warning("Unauthorized request to %s: %s", path, exc)
            response = JSONResponse(
                status_code=401,
                content={
                    "error": "unauthorized",
                    "message": str(exc),
                },
                headers={"WWW-Authenticate": 'Bearer realm="SAP BTP XSUAA"'},
            )
            await response(scope, receive, send)
            return

        # Store security context in ASGI scope state and ContextVar
        scope.setdefault("state", {})["user"] = user_ctx
        token = set_current_user(user_ctx)
        try:
            await self.app(scope, receive, send)
        finally:
            reset_current_user(token)

    @staticmethod
    def _get_header(scope: Scope, header_name: bytes) -> str | None:
        for name, value in scope.get("headers", []):
            if name.lower() == header_name:
                return value.decode("latin-1")
        return None
