"""
Tareas Admin - Benutzerverwaltung
FastAPI Backend, Port 8505
"""

import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path

# Sicherstellen, dass 'dashboard' als Package importierbar ist
sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI, Depends, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse
import uvicorn

from dashboard.database import init_db
from dashboard.api_auth import router as auth_router
from dashboard.api_admin import router as admin_router
from dashboard.api_ldap import router as ldap_router
from dashboard.api_nextcloud import admin_router as nextcloud_admin_router
from dashboard.api_onlyoffice import admin_router as onlyoffice_admin_router
from dashboard.api_mail import mail_admin_router
from dashboard.api_app import app_config_router
from dashboard.api_tls import router as tls_router
from dashboard.api_admin_mcp import router as mcp_admin_router
from dashboard.auth import _extract_user_from_request, get_admin_user
from dashboard.tls_utils import get_tls_config


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/Shutdown Events."""
    init_db()
    yield


app = FastAPI(title="Tareas Admin", lifespan=lifespan)

# Auth-Router OHNE globale Dependency (login muss oeffentlich sein)
app.include_router(auth_router)

# Admin-Router (hat eigene get_admin_user Dependency pro Endpoint)
app.include_router(admin_router)

# LDAP-Router (hat eigene get_admin_user Dependency pro Endpoint)
app.include_router(ldap_router)

# Nextcloud-Router (hat eigene get_admin_user Dependency pro Endpoint)
app.include_router(nextcloud_admin_router)

# ONLYOFFICE-Router (hat eigene get_admin_user Dependency pro Endpoint)
app.include_router(onlyoffice_admin_router)

# Mail-Router (hat eigene get_admin_user Dependency pro Endpoint)
app.include_router(mail_admin_router)

# App-Config-Router (hat eigene get_admin_user Dependency pro Endpoint)
app.include_router(app_config_router)

# TLS-Router (hat eigene get_admin_user Dependency pro Endpoint)
app.include_router(tls_router)

# MCP-Admin-Router (Token-Verwaltung, Audit-Log, Kill-Switch)
app.include_router(mcp_admin_router)

# Verzeichnisse
static_dir = Path(__file__).parent / "static"
templates_dir = Path(__file__).parent / "templates"

app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.middleware("http")
async def security_headers(request, call_next):
    """Security-Header: CSP, X-Frame-Options, X-Content-Type-Options."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "frame-src 'none';"
    )
    return response


@app.middleware("http")
async def no_cache_static(request, call_next):
    """Statische Dateien: Browser muss immer beim Server revalidieren."""
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


# ============================================================
# API-Routen
# ============================================================

@app.post("/api/dashboard/restart")
async def restart_dashboard(user=Depends(get_admin_user)):
    """Startet den Dashboard-Service neu (nur Admins)."""
    subprocess.Popen(
        ["systemctl", "restart", "tareas.service", "tareas-admin.service"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return {"status": "restarting", "message": "Dashboard und Admin werden neu gestartet..."}


# ============================================================
# HTML-Routen
# ============================================================

@app.get("/login", response_class=HTMLResponse)
async def admin_login_page():
    """Admin-Login-Seite."""
    return FileResponse(templates_dir / "admin_login.html")


@app.get("/", response_class=HTMLResponse)
async def admin_index(request: Request):
    """Admin-Startseite - prueft Login und Admin-Rechte."""
    user = _extract_user_from_request(request)
    if not user or not user["is_admin"]:
        return RedirectResponse(url="/login", status_code=302)
    return FileResponse(templates_dir / "admin.html")


@app.get("/{path:path}", response_class=HTMLResponse)
async def admin_catch_all(path: str, request: Request = None):
    """Catch-All."""
    if request:
        user = _extract_user_from_request(request)
        if not user or not user["is_admin"]:
            return RedirectResponse(url="/login", status_code=302)
    return FileResponse(templates_dir / "admin.html")


# ============================================================
# Start
# ============================================================

def main():
    """Admin-App starten."""
    tls = get_tls_config()
    use_tls = tls and tls["enabled"]
    protocol = "https" if use_tls else "http"

    print("=" * 50)
    print("Tareas Admin - Benutzerverwaltung")
    print("=" * 50)
    print(f"URL: {protocol}://0.0.0.0:8505")
    if use_tls:
        print(f"TLS: {tls['cert_path']}")
    print("Strg+C zum Beenden")
    print("=" * 50)

    kwargs = {
        "host": "0.0.0.0",
        "port": 8505,
        "log_level": "info",
    }
    if use_tls:
        kwargs["ssl_certfile"] = tls["cert_path"]
        kwargs["ssl_keyfile"] = tls["key_path"]

    uvicorn.run(app, **kwargs)


if __name__ == "__main__":
    main()
