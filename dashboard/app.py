"""
Tareas - Aufgabenplanungs-Tool
FastAPI Backend, Port 8504
"""

import logging
import logging.handlers
import sys
from contextlib import asynccontextmanager
from pathlib import Path

# Sicherstellen, dass 'dashboard' als Package importierbar ist
sys.path.insert(0, str(Path(__file__).parent.parent))

# Globales Logging: /var/log/tareas.log (Syslog-Format)
LOG_PATH = "/var/log/tareas.log"
_log_formatter = logging.Formatter(
    fmt="%(asctime)s %(name)s[%(process)d]: %(levelname)s %(message)s",
    datefmt="%b %d %H:%M:%S",
)
try:
    _file_handler = logging.handlers.RotatingFileHandler(
        LOG_PATH, maxBytes=5_000_000, backupCount=3, encoding="utf-8",
    )
    _file_handler.setFormatter(_log_formatter)
except PermissionError:
    # Fallback: stderr wenn /var/log nicht beschreibbar
    _file_handler = logging.StreamHandler(sys.stderr)
    _file_handler.setFormatter(_log_formatter)

# Root-Logger konfigurieren (alle dashboard.* Module erben davon)
_root_logger = logging.getLogger()
_root_logger.setLevel(logging.INFO)
_root_logger.addHandler(_file_handler)

# Uvicorn-Logger auf denselben Handler umleiten
for _uv_name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
    _uv_logger = logging.getLogger(_uv_name)
    _uv_logger.handlers.clear()
    _uv_logger.addHandler(_file_handler)
    _uv_logger.propagate = False

from fastapi import FastAPI, Depends, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse, JSONResponse
import uvicorn

from dashboard.database import init_db
from dashboard.crypto_utils import migrate_plaintext_credentials
from dashboard.logging_config import setup_security_logger
from dashboard.csp_utils import get_onlyoffice_origin
from dashboard.api_tasks import router as tasks_router
from dashboard.api_auth import router as auth_router
from dashboard.api_teams import router as teams_router
from dashboard.api_nextcloud import files_router as nextcloud_files_router
from dashboard.api_onlyoffice import wopi_router, editor_router
from dashboard.api_mail import mail_user_router
from dashboard.auth import get_current_user, _extract_user_from_request, validate_mcp_bearer
from dashboard.mcp_server import mcp, current_mcp_user
from dashboard.tls_utils import get_tls_config


# MCP-ASGI-App bauen. Lifespan muss zwingend in den FastAPI-Lifespan eingebunden
# werden, sonst Runtime-Fehler "Task group is not initialized".
mcp_app = mcp.http_app(path="/", transport="streamable-http")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/Shutdown Events."""
    init_db()
    migrate_plaintext_credentials()
    setup_security_logger()
    async with mcp_app.lifespan(app):
        yield


app = FastAPI(title="Tareas", lifespan=lifespan)

# Auth-Router OHNE globale Dependency (login muss oeffentlich sein)
app.include_router(auth_router)

# Tasks-Router MIT auth Dependency
app.include_router(tasks_router, dependencies=[Depends(get_current_user)])

# Teams-Router MIT auth Dependency
app.include_router(teams_router, dependencies=[Depends(get_current_user)])

# Nextcloud Files-Router MIT auth Dependency
app.include_router(nextcloud_files_router, dependencies=[Depends(get_current_user)])

# ONLYOFFICE WOPI-Router OHNE auth (Token-basiert)
app.include_router(wopi_router)

# ONLYOFFICE Editor-Router MIT auth Dependency
app.include_router(editor_router, dependencies=[Depends(get_current_user)])

# Mail User-Router MIT auth Dependency
app.include_router(mail_user_router, dependencies=[Depends(get_current_user)])

# MCP-Server an /mcp/ mounten. Bearer-Token-Auth via mcp_auth-Middleware (s. unten).
app.mount("/mcp", mcp_app)

# Verzeichnisse
static_dir = Path(__file__).parent / "static"
templates_dir = Path(__file__).parent / "templates"
project_root = Path(__file__).parent.parent

app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.middleware("http")
async def csrf_protection(request: Request, call_next):
    """CSRF-Schutz: Mutierende API-Requests muessen X-Requested-With Header senden."""
    if (
        request.method in ("POST", "PUT", "DELETE", "PATCH")
        and request.url.path.startswith("/api/")
        and not request.url.path.startswith("/api/wopi/")
        and not request.url.path.startswith("/api/onlyoffice/callback")
    ):
        if request.headers.get("X-Requested-With") != "XMLHttpRequest":
            return JSONResponse(
                status_code=403,
                content={"detail": "CSRF-Validierung fehlgeschlagen"},
            )
    return await call_next(request)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Security-Header: CSP, X-Frame-Options, X-Content-Type-Options."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if request.url.path.startswith("/editor"):
        # OnlyOffice Editor: CSP dynamisch aus DB-Konfiguration bauen
        origin = get_onlyoffice_origin()
        if origin:
            response.headers["Content-Security-Policy"] = (
                f"default-src 'self'; "
                f"script-src 'self' 'unsafe-inline' {origin}; "
                f"style-src 'self' 'unsafe-inline' {origin}; "
                f"connect-src 'self' {origin}; "
                f"frame-src {origin}; "
                f"img-src 'self' data: {origin}; "
                f"font-src 'self' {origin} data:;"
            )
        else:
            # Kein OnlyOffice konfiguriert: restriktive CSP
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self'; "
                "style-src 'self' 'unsafe-inline';"
            )
        # Editor braucht iframe vom Document Server -> SAMEORIGIN statt DENY
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
    else:
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; "
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


@app.middleware("http")
async def mcp_auth(request: Request, call_next):
    """Bearer-Token-Auth fuer /mcp/*. Setzt current_mcp_user ContextVar."""
    if not request.url.path.startswith("/mcp"):
        return await call_next(request)
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return JSONResponse({"error": "Bearer-Token fehlt"}, status_code=401)
    token = auth_header[7:].strip()
    user = validate_mcp_bearer(token)
    if not user:
        return JSONResponse(
            {"error": "Ungueltiges/widerrufenes Token oder MCP deaktiviert"},
            status_code=401,
        )
    ctx_token = current_mcp_user.set(user)
    try:
        return await call_next(request)
    finally:
        current_mcp_user.reset(ctx_token)


# ============================================================
# API-Endpunkte (muessen VOR dem Catch-All definiert werden)
# ============================================================

@app.get("/api/dashboard/info")
async def dashboard_info(user=Depends(get_current_user)):
    """
    Template-Informationen und Entwickler-Hilfe.
    """
    # Version aus version.txt lesen
    version = "0.0.0"
    version_file = project_root / "version.txt"
    if version_file.exists():
        version = version_file.read_text().strip()

    return {
        "version": version,
        "title": "Tareas",
        "description": "Aufgabenplanungs-Tool auf Basis von FastAPI + Vanilla JS",
        "components": [
            {
                "name": "ExpandableTable",
                "description": "Sortierbare, filterbare Tabelle mit aufklappbaren Detail-Rows",
                "features": [
                    "Spalten-Konfiguration (Breite, Renderer, Sortierung)",
                    "Filter (Select, Input, Buttons, Wildcard-Suche)",
                    "Pagination (konfigurierbare Seitengroessen)",
                    "Aufklappbare Detail-Rows mit Feldern und Sektionen",
                    "Sticky Header",
                    "CSS-Grid-basiertes Layout",
                ],
            },
            {
                "name": "KPI-Cards / Widget-Dashboard",
                "description": "Dashboard-Widgets mit KPI-Karten und Tabellen",
                "features": [
                    "KPI-Karten mit Icon, Wert, Titel, Farb-Regeln",
                    "KPI-Sektionen (gruppierte Karten)",
                    "KPI-Tabellen (kompakte Darstellung)",
                    "Widget-Dashboard mit parallelem Daten-Laden",
                    "Formate: number, percent, currency, text",
                ],
            },
            {
                "name": "Theme-System",
                "description": "Light/Dark-Theme mit CSS Custom Properties",
                "features": [
                    "Persistenz via localStorage",
                    "CSS-Variablen fuer alle Farben",
                    "Theme-Toggle im Settings-Dropdown",
                    "Custom Event 'themeChanged' fuer Komponenten",
                ],
            },
            {
                "name": "Notification-System",
                "description": "Toast-Benachrichtigungen (info, success, error)",
                "features": [
                    "showNotification(message, type)",
                    "Auto-Hide nach 3 Sekunden",
                    "Typen: info, success, error",
                ],
            },
            {
                "name": "DOM-Caching",
                "description": "Tab-Inhalte werden beim Wechsel im Speicher gehalten",
                "features": [
                    "DocumentFragment-basiertes Caching",
                    "Scroll-Position wird wiederhergestellt",
                    "Filter-Zustaende bleiben erhalten",
                    "Gleicher Tab nochmal = Force-Refresh",
                ],
            },
            {
                "name": "URL-Routing",
                "description": "SPA-Routing mit Browser-History-API",
                "features": [
                    "Clientseitiges Routing mit pushState",
                    "Browser-Zurueck/Vorwaerts funktioniert",
                    "Direkte URLs moeglich (Catch-All im Backend)",
                    "Rechtsklick -> In neuem Tab oeffnen",
                ],
            },
        ],
        "how_to_add_tab": {
            "steps": [
                "1. JS-Datei erstellen: dashboard/static/js/tab_example.js",
                "2. Init-Funktion definieren: async function initExampleTab() { ... }",
                "3. In app_core.js: Tab in switchTab() registrieren",
                "4. In index.html: Tab-Link in <nav class='tabs'> + <script>-Include",
                "5. Optional: API-Endpunkt in app.py hinzufuegen",
                "6. Optional: Tabellen-Config mit ExpandableTable definieren",
            ],
            "example": "Siehe expandable_table.js und kpi_grid.js als Referenz",
        },
        "api_structure": {
            "pattern": "/api/{resource}",
            "examples": [
                "GET /api/dashboard/info - Diese Info-Seite",
                "POST /api/dashboard/restart - Server neu starten",
            ],
            "conventions": [
                "GET fuer Lesen, POST fuer Erstellen, PUT fuer Aendern, DELETE fuer Loeschen",
                "Antwort immer als JSON",
                "Fehler als { 'error': 'Beschreibung' }",
            ],
        },
    }


# ============================================================
# HTML-Routen (Catch-All fuer SPA-Routing, NACH API-Routen)
# ============================================================

@app.get("/login", response_class=HTMLResponse)
async def login_page():
    """Login-Seite."""
    return FileResponse(templates_dir / "login.html")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Startseite - bei fehlendem Login -> Redirect zu /login."""
    user = _extract_user_from_request(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return FileResponse(templates_dir / "index.html")


@app.get("/editor", response_class=HTMLResponse)
async def editor_page(request: Request):
    """ONLYOFFICE Editor-Seite (eigener Tab)."""
    user = _extract_user_from_request(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return FileResponse(templates_dir / "editor.html")


@app.get("/{path:path}", response_class=HTMLResponse)
async def catch_all(path: str, request: Request = None):
    """Catch-All fuer clientseitiges Routing (SPA)."""
    if request:
        user = _extract_user_from_request(request)
        if not user:
            return RedirectResponse(url="/login", status_code=302)
    return FileResponse(templates_dir / "index.html")


# ============================================================
# Start
# ============================================================

def main():
    """Dashboard starten."""
    tls = get_tls_config()
    use_tls = tls and tls["enabled"]
    protocol = "https" if use_tls else "http"

    print("=" * 50)
    print("Tareas - Aufgabenplanung")
    print("=" * 50)
    print(f"URL: {protocol}://0.0.0.0:8504")
    if use_tls:
        print(f"TLS: {tls['cert_path']}")
    print("Strg+C zum Beenden")
    print("=" * 50)

    kwargs = {
        "host": "0.0.0.0",
        "port": 8504,
        "log_config": None,  # Eigenes Logging verwenden (nicht uvicorn-default)
    }
    if use_tls:
        kwargs["ssl_certfile"] = tls["cert_path"]
        kwargs["ssl_keyfile"] = tls["key_path"]

    uvicorn.run(app, **kwargs)


if __name__ == "__main__":
    main()
