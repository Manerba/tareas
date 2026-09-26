"""
Tareas - Nextcloud API-Router
Admin-Konfiguration und Dateioperationen via WebDAV.
"""

import asyncio
import logging
import posixpath
from urllib.parse import unquote

from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Query
from fastapi.responses import StreamingResponse, Response
from pydantic import BaseModel
from typing import Optional

from dashboard.db_utils import db_query, db_transaction
from dashboard.config_utils import get_masked_config, resolve_masked_password
from dashboard.crypto_utils import decrypt
from dashboard.auth import get_admin_user, get_current_user
from dashboard import webdav
from dashboard.logging_config import get_security_logger

logger = logging.getLogger(__name__)
security_log = get_security_logger()


# ============================================================
# Konstanten
# ============================================================

MAX_UPLOAD_SIZE = 500 * 1024 * 1024  # 500 MB


# ============================================================
# Router
# ============================================================

admin_router = APIRouter(prefix="/api/admin/nextcloud", tags=["nextcloud-admin"])
files_router = APIRouter(tags=["nextcloud-files"])


# ============================================================
# Pydantic-Modelle
# ============================================================

class NextcloudConfigRequest(BaseModel):
    server_url: str
    username: str
    password: str
    base_path: str


class MoveRequest(BaseModel):
    source: str
    destination: str


class MkdirRequest(BaseModel):
    name: str


# ============================================================
# Path-Traversal-Schutz
# ============================================================

def _safe_rel_path(user_path: str) -> str:
    """
    Validiert einen vom Client uebergebenen relativen Pfad.
    Blockiert Path-Traversal-Versuche (z.B. ../../etc/passwd, %2e%2e).
    Gibt den bereinigten Pfad zurueck oder wirft HTTPException 400.
    """
    # URL-Dekodierung (faengt %2e%2e, %2f etc. ab)
    decoded = unquote(unquote(user_path))  # doppelt fuer doppelte Kodierung
    # Normalisieren: loest ./ und ../ auf
    normalized = posixpath.normpath(decoded)
    # Nach normpath: ".." am Anfang oder absoluter Pfad = Traversal
    if normalized.startswith("..") or normalized.startswith("/"):
        raise HTTPException(status_code=400, detail="Ungueltiger Pfad: Verzeichniswechsel nicht erlaubt")
    # normpath("") => "." -- zurueck zu leerem String
    if normalized == ".":
        return ""
    return normalized


def _safe_filename(name: str) -> str:
    """
    Validiert einen Datei-/Ordnernamen (einzelnes Segment, keine Pfadtrenner).
    """
    decoded = unquote(unquote(name))
    if not decoded or "/" in decoded or "\\" in decoded or decoded in (".", ".."):
        raise HTTPException(status_code=400, detail="Ungueltiger Dateiname")
    return decoded


# ============================================================
# Admin-Endpoints (nur Admins)
# ============================================================

@admin_router.get("/config")
async def get_nextcloud_config(user=Depends(get_admin_user)):
    """Aktuelle Nextcloud-Konfiguration laden (Passwort maskiert)."""
    config = get_masked_config("nextcloud_config", ["password"])
    return {"config": config}


@admin_router.post("/config")
async def save_nextcloud_config(data: NextcloudConfigRequest, user=Depends(get_admin_user)):
    """Nextcloud-Konfiguration speichern (nach erfolgreichem Verbindungstest)."""
    if not data.server_url.strip():
        raise HTTPException(status_code=400, detail="Server-URL ist Pflichtfeld")
    if not data.username.strip():
        raise HTTPException(status_code=400, detail="Benutzername ist Pflichtfeld")
    if not data.password.strip():
        raise HTTPException(status_code=400, detail="Passwort ist Pflichtfeld")
    if not data.base_path.strip():
        raise HTTPException(status_code=400, detail="Wurzelverzeichnis ist Pflichtfeld")

    # Passwort ggf. aus DB holen wenn nicht geaendert
    with db_query() as db:
        actual_password = resolve_masked_password(db, "nextcloud_config", "password", data.password)

    # Verbindungstest (in Thread-Pool) - Klartext-Passwort fuer Test
    success, error = await asyncio.to_thread(
        webdav.test_connection,
        data.server_url.strip(),
        data.username.strip(),
        decrypt(actual_password),
        data.base_path.strip(),
    )

    if not success:
        raise HTTPException(status_code=400, detail=f"Verbindungstest fehlgeschlagen: {error}")

    # Config speichern (UPSERT)
    with db_transaction() as db:
        existing = db.execute("SELECT id FROM nextcloud_config WHERE id = 1").fetchone()

        if existing:
            db.execute(
                """UPDATE nextcloud_config SET server_url = ?, username = ?,
                   password = ?, base_path = ? WHERE id = 1""",
                (data.server_url.strip(), data.username.strip(),
                 actual_password, data.base_path.strip()),
            )
        else:
            db.execute(
                """INSERT INTO nextcloud_config (id, server_url, username, password, base_path)
                   VALUES (1, ?, ?, ?, ?)""",
                (data.server_url.strip(), data.username.strip(),
                 actual_password, data.base_path.strip()),
            )

        security_log.info("CONFIG_CHANGED section=nextcloud by=%s", user["username"])
        return {"message": "Nextcloud-Konfiguration gespeichert"}


@admin_router.delete("/config")
async def delete_nextcloud_config(user=Depends(get_admin_user)):
    """Nextcloud-Konfiguration loeschen."""
    with db_transaction() as db:
        db.execute("DELETE FROM nextcloud_config WHERE id = 1")
        # nextcloud_path in allen tasks zuruecksetzen
        db.execute("UPDATE tasks SET nextcloud_path = NULL WHERE nextcloud_path IS NOT NULL")
        security_log.info("CONFIG_CHANGED section=nextcloud_deleted by=%s", user["username"])
        return {"message": "Nextcloud-Konfiguration geloescht"}


# ============================================================
# Verzeichniszuordnung
# ============================================================

@files_router.get("/api/nextcloud/directories")
async def get_nextcloud_directories(
    path: str = "",
    user=Depends(get_current_user),
):
    """Unterordner auflisten (optional ab Unterpfad, fuer Browse-Dialog)."""
    path = _safe_rel_path(path)
    try:
        dirs = await asyncio.to_thread(webdav.list_subdirectories, path)
        return {
            "directories": [
                {"name": d["name"], "path": f"{path}/{d['name']}".strip("/")}
                for d in dirs
            ],
            "current_path": path,
        }
    except RuntimeError as e:
        logger.exception("Fehler beim Auflisten der Nextcloud-Verzeichnisse")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Fehler beim Auflisten der Nextcloud-Verzeichnisse")
        raise HTTPException(status_code=500, detail="Verzeichnisse konnten nicht geladen werden")


@files_router.get("/api/nextcloud/status")
async def get_nextcloud_status(user=Depends(get_current_user)):
    """Pruefen ob Nextcloud konfiguriert ist (fuer Frontend)."""
    with db_query() as db:
        row = db.execute("SELECT id FROM nextcloud_config WHERE id = 1").fetchone()
        return {"configured": row is not None}


# ============================================================
# Datei-Endpoints (authentifizierte Benutzer)
# ============================================================

def _get_task_nextcloud_path(task_id: int, user: dict) -> str:
    """
    Nextcloud-Pfad fuer eine Aufgabe/Projekt ermitteln und Zugriff pruefen.
    Gibt den relativen Pfad zum Verzeichnis zurueck.
    """
    with db_query() as db:
        task = db.execute(
            "SELECT id, task_type, nextcloud_path, created_by FROM tasks WHERE id = ?",
            (task_id,),
        ).fetchone()

        if not task:
            raise HTTPException(status_code=404, detail="Aufgabe nicht gefunden")
        if not task["nextcloud_path"]:
            raise HTTPException(status_code=400, detail="Kein Nextcloud-Verzeichnis zugeordnet")

        # Zugriffspruefung: Admin, Ersteller, Legacy oder Teammitglied
        is_creator = task["created_by"] == user["id"]
        is_legacy = task["created_by"] is None
        if not is_creator and not is_legacy and not user.get("is_admin"):
            membership = db.execute(
                "SELECT can_read FROM project_members WHERE project_id = ? AND user_id = ?",
                (task_id, user["id"]),
            ).fetchone()
            if not membership or not membership["can_read"]:
                raise HTTPException(status_code=403, detail="Kein Zugriff auf dieses Projekt")

        return task["nextcloud_path"]


@files_router.get("/api/tasks/{task_id}/files")
async def list_task_files(
    task_id: int,
    path: str = "",
    user=Depends(get_current_user),
):
    """Verzeichnisinhalt eines Projekt-Verzeichnisses auflisten."""
    nc_path = _get_task_nextcloud_path(task_id, user)
    path = _safe_rel_path(path)

    # Pfad zusammenbauen: nc_path + relativer Unterpfad
    full_path = nc_path
    if path:
        full_path = f"{nc_path}/{path}"

    try:
        entries = await asyncio.to_thread(webdav.list_directory, full_path)

        # Nextcloud-Config fuer Direktlinks
        config = webdav._get_config()

        items = []
        for e in entries:
            item = {
                "name": e["name"],
                "type": e["type"],
                "size": e["size"],
                "last_modified": e["last_modified"],
                "mime_type": e["mime_type"],
            }
            if config and e.get("file_id"):
                item["nextcloud_link"] = webdav.get_nextcloud_link(config, e["file_id"])
            items.append(item)

        # Sortierung: Ordner zuerst, dann alphabetisch
        items.sort(key=lambda x: (0 if x["type"] == "directory" else 1, x["name"].lower()))

        return {"items": items, "path": path}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Verzeichnis nicht gefunden: {path}")
    except RuntimeError as e:
        logger.exception("Fehler beim Auflisten der Dateien")
        raise HTTPException(status_code=500, detail=str(e))


@files_router.get("/api/tasks/{task_id}/files/download")
async def download_task_file(
    task_id: int,
    path: str = Query(..., description="Relativer Pfad zur Datei"),
    inline: bool = Query(False, description="Datei inline anzeigen statt herunterladen"),
    user=Depends(get_current_user),
):
    """Datei aus dem Projektverzeichnis herunterladen oder inline anzeigen."""
    nc_path = _get_task_nextcloud_path(task_id, user)
    path = _safe_rel_path(path)
    full_path = f"{nc_path}/{path}"

    try:
        content, content_type = await asyncio.to_thread(webdav.get_file, full_path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Datei nicht gefunden: {path}")
    except RuntimeError as e:
        logger.exception("Fehler beim Herunterladen der Datei")
        raise HTTPException(status_code=500, detail=str(e))

    # Dateiname aus Pfad extrahieren
    filename = path.rsplit("/", 1)[-1] if "/" in path else path
    disposition = "inline" if inline else "attachment"

    return Response(
        content=content,
        media_type=content_type,
        headers={
            "Content-Disposition": f'{disposition}; filename="{filename}"',
        },
    )


@files_router.post("/api/tasks/{task_id}/files/upload")
async def upload_task_file(
    task_id: int,
    file: UploadFile = File(...),
    path: str = Query("", description="Zielordner (relativ)"),
    user=Depends(get_current_user),
):
    """Datei in das Projektverzeichnis hochladen."""
    nc_path = _get_task_nextcloud_path(task_id, user)
    path = _safe_rel_path(path)
    filename = _safe_filename(file.filename)

    # Ziel-Pfad zusammenbauen
    target = nc_path
    if path:
        target = f"{nc_path}/{path}"
    target = f"{target}/{filename}"

    content = await file.read()
    if len(content) > MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=413, detail="Datei zu gross (max. 500 MB)")
    content_type = file.content_type or "application/octet-stream"

    try:
        await asyncio.to_thread(webdav.upload_file, target, content, content_type)
    except RuntimeError as e:
        logger.exception("Fehler beim Hochladen der Datei")
        raise HTTPException(status_code=500, detail=str(e))

    return {"message": f"Datei '{file.filename}' hochgeladen", "filename": file.filename}


@files_router.post("/api/tasks/{task_id}/files/mkdir")
async def create_task_directory(
    task_id: int,
    body: MkdirRequest,
    path: str = Query("", description="Uebergeordneter Ordner (relativ)"),
    user=Depends(get_current_user),
):
    """Neuen Ordner im Projektverzeichnis erstellen."""
    nc_path = _get_task_nextcloud_path(task_id, user)
    path = _safe_rel_path(path)
    dirname = _safe_filename(body.name)

    target = nc_path
    if path:
        target = f"{nc_path}/{path}"
    target = f"{target}/{dirname}"

    try:
        await asyncio.to_thread(webdav.create_directory, target)
    except FileExistsError:
        raise HTTPException(status_code=409, detail=f"Ordner existiert bereits: {body.name}")
    except RuntimeError as e:
        logger.exception("Fehler beim Erstellen des Ordners")
        raise HTTPException(status_code=500, detail=str(e))

    return {"message": f"Ordner '{body.name}' erstellt"}


@files_router.delete("/api/tasks/{task_id}/files")
async def delete_task_file(
    task_id: int,
    path: str = Query(..., description="Zu loeschender Pfad (relativ)"),
    user=Depends(get_current_user),
):
    """Datei oder Ordner im Projektverzeichnis loeschen."""
    nc_path = _get_task_nextcloud_path(task_id, user)
    path = _safe_rel_path(path)
    full_path = f"{nc_path}/{path}"

    try:
        await asyncio.to_thread(webdav.delete_item, full_path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Nicht gefunden: {path}")
    except RuntimeError as e:
        logger.exception("Fehler beim Loeschen")
        raise HTTPException(status_code=500, detail=str(e))

    return {"message": "Geloescht"}


@files_router.put("/api/tasks/{task_id}/files/move")
async def move_task_file(
    task_id: int,
    body: MoveRequest,
    user=Depends(get_current_user),
):
    """Datei/Ordner im Projektverzeichnis verschieben oder umbenennen."""
    nc_path = _get_task_nextcloud_path(task_id, user)
    source = f"{nc_path}/{_safe_rel_path(body.source)}"
    dest = f"{nc_path}/{_safe_rel_path(body.destination)}"

    try:
        await asyncio.to_thread(webdav.move_item, source, dest)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Quelle nicht gefunden: {body.source}")
    except FileExistsError:
        raise HTTPException(status_code=409, detail=f"Ziel existiert bereits: {body.destination}")
    except RuntimeError as e:
        logger.exception("Fehler beim Verschieben")
        raise HTTPException(status_code=500, detail=str(e))

    return {"message": "Verschoben"}
