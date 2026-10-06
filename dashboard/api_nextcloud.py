"""
Tareas - Nextcloud API-Router
Nextcloud-Konfiguration und Dateioperationen fuer lokale und WebDAV-Ablagen.
"""

import asyncio
import logging
from urllib.parse import quote

import httpx
from fastapi import APIRouter, HTTPException, Depends, UploadFile, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from python_multipart.exceptions import MultipartParseError
from starlette.datastructures import UploadFile as StarletteUploadFile
from starlette.formparsers import MultiPartException, MultiPartParser
from starlette.requests import ClientDisconnect

from dashboard.db_utils import db_query, db_transaction
from dashboard.config_utils import get_masked_config, resolve_masked_password
from dashboard.crypto_utils import decrypt
from dashboard.auth import get_admin_user, get_current_user, get_file_user
from dashboard import webdav
from dashboard.audit_log import log_change
from dashboard.file_storage import get_task_storage, safe_rel_path as _safe_rel_path, safe_filename as _safe_filename
from dashboard.csp_utils import get_onlyoffice_origin
from dashboard.text_files import MAX_TEXT_BYTES, text_format, read_text_file, write_text_file
from dashboard.logging_config import get_security_logger

logger = logging.getLogger(__name__)
security_log = get_security_logger()


# ============================================================
# Konstanten
# ============================================================

MAX_UPLOAD_SIZE = 500 * 1024 * 1024  # 500 MB
UPLOAD_TOO_LARGE = "Datei zu gross (max. 500 MB)"


class _UploadTooLarge(MultiPartException):
    pass


class _LimitedUploadParser(MultiPartParser):
    """Dateibytes vor dem Schreiben in die temporaere Upload-Datei begrenzen."""

    def __init__(self, headers, stream):
        # Der Endpoint erwartet genau ein Multipart-Dateifeld, Pfade sind Query-Parameter.
        super().__init__(headers, stream, max_files=1, max_fields=0)
        self.file_bytes = 0

    def on_part_data(self, data, start, end):
        if self._current_part.file is not None:
            self.file_bytes += end - start
            if self.file_bytes > MAX_UPLOAD_SIZE:
                raise _UploadTooLarge(UPLOAD_TOO_LARGE)
        super().on_part_data(data, start, end)

    def close(self):
        # Auch bei Verbindungsabbruch oder unvollstaendigen Multipart-Bodies
        # muessen alle angelegten Dateien geschlossen werden, nicht nur FormData.
        for file in self._files_to_close_on_error:
            file.close()


async def _parse_upload(request: Request):
    # Keine Content-Length-Vorabpruefung: Es zaehlen die tatsaechlichen Dateibytes,
    # auch bei Chunked-Uploads. Multipart-Rahmen zaehlen nicht zum Dateilimit.
    parser = _LimitedUploadParser(request.headers, request.stream())
    try:
        try:
            form = await parser.parse()
        except _UploadTooLarge as exc:
            raise HTTPException(status_code=413, detail=UPLOAD_TOO_LARGE) from exc
        except MultiPartException as exc:
            raise HTTPException(status_code=400, detail=exc.message) from exc
        except (MultipartParseError, ClientDisconnect) as exc:
            raise HTTPException(status_code=400, detail="There was an error parsing the body") from exc
        file = form.get("file")
        if not isinstance(file, StarletteUploadFile):
            raise HTTPException(status_code=422, detail=[{
                "type": "missing", "loc": ["body", "file"], "msg": "Field required", "input": None,
            }])
        yield file
    finally:
        parser.close()


# ============================================================
# Router
# ============================================================

admin_router = APIRouter(prefix="/api/admin/nextcloud", tags=["nextcloud-admin"])
files_router = APIRouter(tags=["files"])
file_operations_router = APIRouter(tags=["files"])


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


class TextFileRequest(BaseModel):
    content: str = Field(max_length=MAX_TEXT_BYTES)
    revision: str = Field(pattern=r"^[a-f0-9]{64}$")


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
        # Nur WebDAV-Zuordnungen entfernen; lokale Ablagen bleiben erhalten.
        db.execute("DELETE FROM wopi_tokens WHERE task_id IN (SELECT id FROM tasks WHERE nextcloud_path IS NOT NULL AND COALESCE(file_storage_type, '') != 'local')")
        db.execute("UPDATE tasks SET nextcloud_path = NULL, file_storage_type = NULL WHERE COALESCE(file_storage_type, '') != 'local'")
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
# Datei-Endpoints (Web-Session oder MCP-Bearer)
# ============================================================

def _get_task_nextcloud_path(task_id: int, user: dict) -> str:
    """Kompatibilitaet fuer bestehende Aufrufer der WebDAV-Zugriffspruefung."""
    storage = get_task_storage(task_id, user)
    if storage.kind != "webdav":
        raise HTTPException(status_code=400, detail="Kein Nextcloud-Verzeichnis zugeordnet")
    return storage.base_path


async def _file_operation(operation, *args):
    try:
        return await asyncio.to_thread(operation, *args)
    except httpx.TimeoutException as exc:
        logger.warning("Zeitueberschreitung beim Zugriff auf Nextcloud/WebDAV (%s)", type(exc).__name__, exc_info=True)
        raise HTTPException(status_code=504, detail="files.webdavTimeout") from exc
    except httpx.RequestError as exc:
        logger.warning("Nextcloud/WebDAV nicht erreichbar (%s)", type(exc).__name__, exc_info=True)
        raise HTTPException(status_code=503, detail="files.webdavConnectionError") from exc
    except (FileNotFoundError, NotADirectoryError):
        raise HTTPException(status_code=404, detail="Datei oder Verzeichnis nicht gefunden")
    except (FileExistsError, IsADirectoryError):
        raise HTTPException(status_code=409, detail="Datei oder Verzeichnis existiert bereits")
    except (OSError, RuntimeError):
        logger.exception("Fehler beim Zugriff auf die Dateiablage")
        raise HTTPException(status_code=500, detail="Dateioperation fehlgeschlagen")


@file_operations_router.get("/api/tasks/{task_id}/files")
async def list_task_files(task_id: int, path: str = "", user=Depends(get_file_user)):
    storage = get_task_storage(task_id, user)
    path = _safe_rel_path(path)
    items = await _file_operation(storage.list_directory, path)
    for item in items:
        item["text_format"] = text_format(item["name"]) if item["type"] == "file" else None
    return {"items": items, "path": path, "storage_type": storage.kind, "can_write": storage.can_write,
            "onlyoffice_configured": get_onlyoffice_origin() is not None}


@file_operations_router.get("/api/tasks/{task_id}/files/text")
async def get_task_text_file(task_id: int, path: str = Query(...), user=Depends(get_file_user)):
    storage = get_task_storage(task_id, user)
    path = _safe_rel_path(path, allow_empty=False)
    return await _file_operation(read_text_file, storage, path)


@file_operations_router.put("/api/tasks/{task_id}/files/text")
async def save_task_text_file(task_id: int, body: TextFileRequest, path: str = Query(...), user=Depends(get_file_user)):
    storage = get_task_storage(task_id, user, write=True)
    path = _safe_rel_path(path, allow_empty=False)
    result = await _file_operation(write_text_file, storage, path, body.content, body.revision)
    log_change(user, "task", task_id, "file_edit", {"path": path, "storage_type": storage.kind})
    return result


@file_operations_router.get("/api/tasks/{task_id}/files/download")
async def download_task_file(
    task_id: int,
    path: str = Query(..., description="Relativer Pfad zur Datei"),
    inline: bool = Query(False, description="Datei inline anzeigen statt herunterladen"),
    user=Depends(get_file_user),
):
    storage = get_task_storage(task_id, user)
    path = _safe_rel_path(path, allow_empty=False)
    content, content_type = await _file_operation(storage.get_file, path)
    filename = path.rsplit("/", 1)[-1]
    disposition = "inline" if inline else "attachment"
    return Response(content=content, media_type=content_type, headers={
        "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(filename, safe='')}",
        "Content-Security-Policy": "sandbox",
        "X-Content-Type-Options": "nosniff",
    })


@file_operations_router.post("/api/tasks/{task_id}/files/upload", openapi_extra={
    "requestBody": {"required": True, "content": {"multipart/form-data": {"schema": {
        "type": "object", "required": ["file"],
        "properties": {"file": {"type": "string", "format": "binary"}},
    }}}},
})
async def upload_task_file(
    task_id: int, file: UploadFile = Depends(_parse_upload),
    path: str = Query("", description="Zielordner (relativ)"),
    user=Depends(get_file_user),
):
    storage = get_task_storage(task_id, user, write=True)
    path = _safe_rel_path(path)
    filename = _safe_filename(file.filename or "")
    target = f"{path}/{filename}" if path else filename
    content = await file.read(MAX_UPLOAD_SIZE + 1)
    if len(content) > MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=413, detail=UPLOAD_TOO_LARGE)
    await _file_operation(storage.upload_file, target, content, file.content_type or "application/octet-stream")
    log_change(user, "task", task_id, "file_upload", {"path": target, "storage_type": storage.kind})
    return {"message": f"Datei '{filename}' hochgeladen", "filename": filename}


@file_operations_router.post("/api/tasks/{task_id}/files/mkdir")
async def create_task_directory(
    task_id: int, body: MkdirRequest,
    path: str = Query("", description="Uebergeordneter Ordner (relativ)"),
    user=Depends(get_file_user),
):
    storage = get_task_storage(task_id, user, write=True)
    path = _safe_rel_path(path)
    dirname = _safe_filename(body.name)
    target = f"{path}/{dirname}" if path else dirname
    await _file_operation(storage.create_directory, target)
    log_change(user, "task", task_id, "file_mkdir", {"path": target, "storage_type": storage.kind})
    return {"message": f"Ordner '{dirname}' erstellt"}


@file_operations_router.delete("/api/tasks/{task_id}/files")
async def delete_task_file(
    task_id: int, path: str = Query(..., description="Zu loeschender Pfad (relativ)"),
    user=Depends(get_file_user),
):
    storage = get_task_storage(task_id, user, write=True)
    path = _safe_rel_path(path, allow_empty=False)
    await _file_operation(storage.delete_item, path)
    log_change(user, "task", task_id, "file_delete", {"path": path, "storage_type": storage.kind})
    return {"message": "Geloescht"}


@file_operations_router.put("/api/tasks/{task_id}/files/move")
async def move_task_file(task_id: int, body: MoveRequest, user=Depends(get_file_user)):
    storage = get_task_storage(task_id, user, write=True)
    source = _safe_rel_path(body.source, allow_empty=False)
    destination = _safe_rel_path(body.destination, allow_empty=False)
    await _file_operation(storage.move_item, source, destination)
    log_change(user, "task", task_id, "file_move", {"source": source, "destination": destination, "storage_type": storage.kind})
    return {"message": "Verschoben"}


files_router.include_router(file_operations_router)
