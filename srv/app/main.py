"""FastAPI / Starlette ASGI entrypoint mounting FastMCP (Streamable HTTP + SSE) and XSUAA Auth Middleware."""

from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncIterator
from typing import Any

from fastapi import FastAPI
from starlette.routing import Mount

from app.auth.context import require_current_user
from app.auth.middleware import XsuaaAuthASGIMiddleware
from app.config import Settings, get_settings
from app.mcp_server.server import create_mcp_server


def create_app(settings: Settings | None = None) -> Any:
    """Create the ASGI application with XSUAA authentication and mounted MCP transports."""
    cfg = settings or get_settings()
    logging.basicConfig(
        level=getattr(logging, cfg.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    mcp_server = create_mcp_server(cfg.app_name)

    # Build Streamable HTTP (/mcp) and SSE (/sse, /messages) ASGI sub-apps from FastMCP
    streamable_app = mcp_server.streamable_http_app()
    sse_app = mcp_server.sse_app()

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        session_manager = getattr(mcp_server, "session_manager", None)
        if session_manager is not None and hasattr(session_manager, "run"):
            async with session_manager.run():
                yield
        else:
            yield

    fastapi_app = FastAPI(
        title="SAP BTP Cloud Foundry Python MCP Server",
        version="0.1.0",
        description=(
            "Model Context Protocol (MCP) server for SAP BTP Cloud Foundry, secured via "
            "SAP Approuter and XSUAA with Destination Service principal propagation."
        ),
        lifespan=lifespan,
    )

    @fastapi_app.get("/health", tags=["system"])
    async def health_check() -> dict[str, Any]:
        """Unauthenticated Cloud Foundry health check endpoint."""
        return {
            "status": "UP",
            "app_name": cfg.app_name,
            "app_env": cfg.app_env,
            "auth_mode": cfg.effective_auth_mode,
            "transports": {
                "streamable_http": "/mcp",
                "sse": "/sse",
            },
        }

    @fastapi_app.get("/api/me", tags=["security"])
    async def get_authenticated_user() -> dict[str, Any]:
        """Diagnostic REST endpoint returning the validated XSUAA user security context."""
        user = require_current_user()
        return {
            "user_name": user.user_name,
            "email": user.email,
            "tenant_id": user.tenant_id,
            "subdomain": user.subdomain,
            "scopes": user.scopes,
            "user_attributes": user.user_attributes,
            "is_mock": user.is_mock,
        }

    # Mount FastMCP transports onto the root router:
    # - Streamable HTTP transport exposes /mcp
    # - SSE transport exposes /sse and /messages
    fastapi_app.router.routes.append(Mount("/", app=streamable_app))
    fastapi_app.router.routes.append(Mount("/", app=sse_app))

    # Wrap entire ASGI stack in XSUAA JWT Authentication Middleware
    return XsuaaAuthASGIMiddleware(
        app=fastapi_app,
        settings=cfg,
        exempt_paths=("/health", "/docs", "/openapi.json"),
    )


app = create_app()
