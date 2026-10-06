"""Gemeinsamer HTTP-Transport und Auth fuer beide Tareas-MCP-Zugaenge."""

import re
from contextlib import asynccontextmanager

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from dashboard.agent_guide import current_agent_base_url
from dashboard.auth import _extract_bearer_token, get_mcp_user, validate_mcp_bearer
from dashboard.crypto_utils import migrate_plaintext_credentials
from dashboard.database import init_db
from dashboard.logging_config import setup_security_logger
from dashboard.mcp_server import mcp


class MCPAuthMiddleware:
    """Prueft MCP und dedizierte Datei-REST vor dem Einlesen des Request-Bodys."""

    def __init__(self, app, *, dedicated: bool = False):
        self.app = app
        self.dedicated = dedicated

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        if self.dedicated and re.match(r"/api/tasks/[^/]+/files(?:/|$)", path):
            # REST-Dependencies greifen sonst erst nach dem Multipart-Parser.
            # Die Dependency prueft nach langen Uploads weiterhin erneut.
            try:
                await get_mcp_user(Request(scope))
            except HTTPException as exc:
                response = JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)
                return await response(scope, receive, send)
            return await self.app(scope, receive, send)
        if not (path == "/mcp" or path.startswith("/mcp/")):
            return await self.app(scope, receive, send)
        request = Request(scope)
        token = _extract_bearer_token(request)
        if not token:
            response = JSONResponse({"error": "Bearer-Token fehlt"}, status_code=401)
            return await response(scope, receive, send)
        user = validate_mcp_bearer(token)
        if not user:
            response = JSONResponse(
                {"error": "Ungueltiges/widerrufenes Token oder MCP deaktiviert"}, status_code=401,
            )
            return await response(scope, receive, send)
        request.state.mcp_user = user
        base_context = current_agent_base_url.set(str(request.base_url).rstrip("/") if self.dedicated else None)
        try:
            await self.app(scope, receive, send)
        finally:
            current_agent_base_url.reset(base_context)


def install_mcp(app, *, dedicated: bool = False):
    """Eigene Transport-Sessions pro Dienst, eine gemeinsame Werkzeugdefinition."""
    transport = mcp.http_app(path="/", transport="streamable-http")
    app.state.mcp_transport = transport
    app.mount("/mcp", transport)
    app.add_middleware(MCPAuthMiddleware, dedicated=dedicated)


@asynccontextmanager
async def mcp_lifespan(app):
    init_db()
    migrate_plaintext_credentials()
    setup_security_logger()
    async with app.state.mcp_transport.lifespan(app):
        yield


async def api_csrf_protection(request: Request, call_next):
    """Einheitlicher Schreibschutz fuer UI- und Agenten-Datei-REST."""
    if (
        request.method in ("POST", "PUT", "DELETE", "PATCH")
        and request.url.path.startswith("/api/")
        and not request.url.path.startswith("/api/wopi/")
        and not request.url.path.startswith("/api/onlyoffice/callback")
        and request.headers.get("X-Requested-With") != "XMLHttpRequest"
    ):
        return JSONResponse(status_code=403, content={"detail": "CSRF-Validierung fehlgeschlagen"})
    return await call_next(request)
