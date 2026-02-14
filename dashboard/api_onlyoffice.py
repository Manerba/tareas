"""
Tareas - ONLYOFFICE API-Router
WOPI-Host, Admin-Config, Editor-Open API, Token-Management.
"""

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlparse

import httpx
from fastapi import APIRouter, HTTPException, Depends, Request, Query, Body
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from dashboard.db_utils import db_query, db_transaction, upsert_singleton_config
from dashboard.crypto_utils import encrypt, decrypt
from dashboard.user_utils import get_display_name
from dashboard.auth import get_admin_user, get_current_user
from dashboard import webdav
from dashboard.tls_utils import get_tls_verify_config
from dashboard.csp_utils import invalidate_onlyoffice_cache
from dashboard.logging_config import get_security_logger

logger = logging.getLogger(__name__)
security_log = get_security_logger()

# ============================================================
# Router
# ============================================================

admin_router = APIRouter(prefix="/api/admin/onlyoffice", tags=["onlyoffice-admin"])
wopi_router = APIRouter(tags=["wopi"])
editor_router = APIRouter(tags=["onlyoffice-editor"])

# ============================================================
# Format-Mappings
# ============================================================

EDITABLE_EXTENSIONS = {
    "docx", "doc", "odt", "txt", "rtf", "md", "html",
    "xlsx", "xls", "ods", "csv",
    "pptx", "ppt", "odp",
}

DOCUMENT_TYPE_MAP = {
    # Word
    "docx": "word", "doc": "word", "odt": "word", "txt": "word",
    "rtf": "word", "md": "word", "html": "word",
    # Excel
    "xlsx": "cell", "xls": "cell", "ods": "cell", "csv": "cell",
    # PowerPoint
    "pptx": "slide", "ppt": "slide", "odp": "slide",
}

# ============================================================
# Pydantic-Modelle
# ============================================================

class OnlyOfficeConfigRequest(BaseModel):
    server_url: str
    jwt_secret: str


# ============================================================
# Helper-Funktionen
# ============================================================

def _get_onlyoffice_config() -> dict | None:
    """Config aus DB laden."""
    with db_query() as db:
        row = db.execute("SELECT * FROM onlyoffice_config WHERE id = 1").fetchone()
        if not row:
            return None
        verify_cfg = get_tls_verify_config()
        return {
            "server_url": row["server_url"].rstrip("/"),
            "jwt_secret": decrypt(row["jwt_secret"]),
            "verify": verify_cfg["verify_onlyoffice"],
        }


def _encode_file_id(task_id: int, file_path: str) -> str:
    """Base64-Kodierung von task_id:file_path."""
    raw = f"{task_id}:{file_path}"
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _decode_file_id(file_id: str) -> tuple[int, str]:
    """Base64-Dekodierung zurueck zu (task_id, file_path)."""
    # Padding wiederherstellen
    padding = 4 - len(file_id) % 4
    if padding != 4:
        file_id += "=" * padding
    raw = base64.urlsafe_b64decode(file_id).decode()
    parts = raw.split(":", 1)
    if len(parts) != 2:
        raise ValueError("Ungueltiges file_id Format")
    return int(parts[0]), parts[1]


def _validate_wopi_token(token: str) -> dict | None:
    """Token validieren + User-Daten joinen. Gibt dict oder None zurueck."""
    with db_query() as db:
        row = db.execute(
            """SELECT wt.*, u.username, u.vorname, u.nachname
               FROM wopi_tokens wt
               JOIN users u ON wt.user_id = u.id
               WHERE wt.token = ? AND wt.expires_at > datetime('now')""",
            (token,),
        ).fetchone()
        if not row:
            return None
        return {
            "token_id": row["id"],
            "user_id": row["user_id"],
            "task_id": row["task_id"],
            "file_path": row["file_path"],
            "permissions": row["permissions"],
            "username": row["username"],
            "vorname": row["vorname"],
            "nachname": row["nachname"],
        }


def _generate_wopi_token(user_id: int, task_id: int, file_path: str, permissions: str = "edit") -> tuple[str, str]:
    """
    Token erzeugen (8h Expiry) + Cleanup abgelaufener Tokens.
    Gibt (token, expires_at_epoch_ms) zurueck.
    """
    token = secrets.token_hex(32)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=8)
    expires_str = expires_at.strftime("%Y-%m-%d %H:%M:%S")
    expires_epoch_ms = str(int(expires_at.timestamp() * 1000))

    with db_transaction() as db:
        # Cleanup abgelaufener Tokens
        db.execute("DELETE FROM wopi_tokens WHERE expires_at < datetime('now')")

        db.execute(
            """INSERT INTO wopi_tokens (token, user_id, task_id, file_path, permissions, expires_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (token, user_id, task_id, file_path, permissions, expires_str),
        )

    return token, expires_epoch_ms


def _sign_jwt(payload: dict, secret: str) -> str:
    """Manuelle HMAC-SHA256 JWT-Signierung (kein PyJWT noetig)."""
    header = {"alg": "HS256", "typ": "JWT"}

    def _b64url(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    header_b64 = _b64url(json.dumps(header, separators=(",", ":")).encode())
    payload_b64 = _b64url(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{header_b64}.{payload_b64}"

    signature = hmac.new(
        secret.encode(), signing_input.encode(), hashlib.sha256
    ).digest()
    signature_b64 = _b64url(signature)

    return f"{signing_input}.{signature_b64}"


def _verify_jwt(token: str, secret: str) -> dict | None:
    """JWT-Signatur verifizieren, Payload zurueckgeben. None bei Fehler."""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None

        header_b64, payload_b64, sig_b64 = parts
        signing_input = f"{header_b64}.{payload_b64}"

        expected_sig = hmac.new(
            secret.encode(), signing_input.encode(), hashlib.sha256
        ).digest()

        # Base64url-Padding wiederherstellen
        actual_sig = base64.urlsafe_b64decode(sig_b64 + "=" * (-len(sig_b64) % 4))

        if not hmac.compare_digest(expected_sig, actual_sig):
            return None

        payload_json = base64.urlsafe_b64decode(payload_b64 + "=" * (-len(payload_b64) % 4))
        return json.loads(payload_json)
    except Exception:
        return None


def _validate_callback_url(url: str) -> bool:
    """Download-URL gegen SSRF pruefen. Nur http(s), keine Metadata-Endpoints."""
    try:
        parsed = urlparse(url)
    except Exception:
        return False

    if parsed.scheme not in ("http", "https"):
        return False

    hostname = (parsed.hostname or "").lower()
    if not hostname:
        return False

    # Cloud-Metadata-Endpoints blocken
    blocked_hosts = {"169.254.169.254", "metadata.google.internal"}
    if hostname in blocked_hosts:
        return False

    # Link-local IPv6 Metadata blocken
    if hostname.startswith("fd00:") or hostname.startswith("fe80:"):
        return False

    return True


def _check_user_can_edit(task_id: int, user: dict) -> bool:
    """Schreibrecht pruefen (Ersteller/Legacy/Teammitglied mit can_edit)."""
    with db_query() as db:
        task = db.execute(
            "SELECT created_by FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        if not task:
            return False

        # Ersteller oder Legacy (kein created_by)
        if task["created_by"] == user["id"] or task["created_by"] is None:
            return True

        # Teammitglied mit Edit-Recht
        membership = db.execute(
            "SELECT can_edit FROM project_members WHERE project_id = ? AND user_id = ?",
            (task_id, user["id"]),
        ).fetchone()
        return bool(membership and membership["can_edit"])


def _get_task_nc_path(task_id: int) -> str | None:
    """Nextcloud-Pfad fuer eine Aufgabe ermitteln."""
    with db_query() as db:
        task = db.execute(
            "SELECT nextcloud_path FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        if not task or not task["nextcloud_path"]:
            return None
        return task["nextcloud_path"]


# ============================================================
# Admin-Endpoints (nur Admins)
# ============================================================

@admin_router.get("/config")
async def get_config(user=Depends(get_admin_user)):
    """Aktuelle ONLYOFFICE-Konfiguration laden (Secret maskiert)."""
    with db_query() as db:
        row = db.execute("SELECT * FROM onlyoffice_config WHERE id = 1").fetchone()
        if not row:
            return {"config": None}
        return {
            "config": {
                "server_url": row["server_url"],
                "jwt_secret_set": True,
            }
        }


@admin_router.post("/config")
async def save_config(data: OnlyOfficeConfigRequest, user=Depends(get_admin_user)):
    """ONLYOFFICE-Konfiguration speichern (UPSERT id=1)."""
    if not data.server_url.strip():
        raise HTTPException(status_code=400, detail="Server-URL ist Pflichtfeld")
    if not data.jwt_secret.strip():
        raise HTTPException(status_code=400, detail="JWT-Secret ist Pflichtfeld")

    server_url = data.server_url.strip().rstrip("/")
    jwt_secret = data.jwt_secret.strip()

    # JWT Secret ggf. aus DB holen wenn maskiert
    if "****" in jwt_secret:
        with db_query() as db:
            existing = db.execute("SELECT jwt_secret FROM onlyoffice_config WHERE id = 1").fetchone()
            if existing:
                # Bereits verschluesselten Wert beibehalten
                jwt_secret = existing["jwt_secret"]
            else:
                raise HTTPException(status_code=400, detail="Kein gespeichertes Secret vorhanden")
    else:
        # Neues Secret: verschluesseln vor dem Speichern
        jwt_secret = encrypt(jwt_secret)

    # Config speichern (UPSERT)
    with db_transaction() as db:
        upsert_singleton_config(db, "onlyoffice_config", {
            "server_url": server_url,
            "jwt_secret": jwt_secret,
        })

    # CSP-Cache invalidieren (Origin koennte sich geaendert haben)
    invalidate_onlyoffice_cache()

    security_log.info("CONFIG_CHANGED section=onlyoffice by=%s", user["username"])
    return {"message": "ONLYOFFICE-Konfiguration gespeichert"}


@admin_router.delete("/config")
async def delete_config(user=Depends(get_admin_user)):
    """ONLYOFFICE-Konfiguration loeschen."""
    with db_transaction() as db:
        db.execute("DELETE FROM onlyoffice_config WHERE id = 1")

    # CSP-Cache invalidieren (Origin wurde entfernt)
    invalidate_onlyoffice_cache()

    security_log.info("CONFIG_CHANGED section=onlyoffice_deleted by=%s", user["username"])
    return {"message": "ONLYOFFICE-Konfiguration geloescht"}


@admin_router.get("/test")
async def test_connection(user=Depends(get_admin_user)):
    """Healthcheck-Test gegen ONLYOFFICE Server."""
    config = _get_onlyoffice_config()
    if not config:
        raise HTTPException(status_code=400, detail="Keine ONLYOFFICE-Konfiguration vorhanden")

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0), verify=config.get("verify", False)) as client:
            resp = await client.get(f"{config['server_url']}/healthcheck")
            if resp.status_code == 200 and resp.text.strip().lower() == "true":
                return {"status": "ok", "message": "ONLYOFFICE Document Server ist erreichbar"}
            else:
                raise HTTPException(
                    status_code=400,
                    detail=f"Healthcheck fehlgeschlagen: Status {resp.status_code}, Antwort: {resp.text[:200]}",
                )
    except httpx.ConnectError as e:
        logger.exception("Verbindungsfehler bei ONLYOFFICE-Healthcheck")
        raise HTTPException(status_code=400, detail="Verbindung zum ONLYOFFICE-Server fehlgeschlagen")
    except httpx.TimeoutException:
        raise HTTPException(status_code=400, detail="Zeitueberschreitung bei der Verbindung")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Fehler bei ONLYOFFICE-Healthcheck")
        raise HTTPException(status_code=500, detail="Verbindungstest fehlgeschlagen")


# ============================================================
# WOPI-Endpoints (aufgerufen von ONLYOFFICE, Token-Auth)
# ============================================================

@wopi_router.get("/api/wopi/files/{file_id}")
async def wopi_check_file_info(file_id: str, access_token: str = Query(...)):
    """WOPI CheckFileInfo - Datei-Metadaten."""
    token_data = _validate_wopi_token(access_token)
    if not token_data:
        raise HTTPException(status_code=401, detail="Ungueltiger oder abgelaufener Token")

    try:
        task_id, file_path = _decode_file_id(file_id)
    except (ValueError, Exception):
        raise HTTPException(status_code=404, detail="Ungueltige File-ID")

    # Sicherstellen dass Token zu dieser Datei passt
    if token_data["task_id"] != task_id or token_data["file_path"] != file_path:
        raise HTTPException(status_code=403, detail="Token passt nicht zur Datei")

    nc_path = _get_task_nc_path(task_id)
    if not nc_path:
        raise HTTPException(status_code=404, detail="Kein Nextcloud-Verzeichnis zugeordnet")

    full_path = f"{nc_path}/{file_path.strip('/')}"
    filename = file_path.rsplit("/", 1)[-1] if "/" in file_path else file_path

    # Dateigroesse und Hash ermitteln
    try:
        content, content_type = await asyncio.to_thread(webdav.get_file, full_path)
        file_size = len(content)
        sha256_hash = hashlib.sha256(content).digest()
        sha256_b64 = base64.b64encode(sha256_hash).decode()
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Datei nicht gefunden")
    except Exception as e:
        logger.exception("Fehler beim Laden der Datei-Metadaten (WOPI CheckFileInfo)")
        raise HTTPException(status_code=500, detail="Datei-Metadaten konnten nicht geladen werden")

    user_friendly_name = get_display_name(token_data)

    can_write = token_data["permissions"] == "edit"

    return {
        "BaseFileName": filename,
        "Size": file_size,
        "UserId": str(token_data["user_id"]),
        "UserFriendlyName": user_friendly_name,
        "UserCanWrite": can_write,
        "SHA256": sha256_b64,
        "Version": str(int(time.time())),
        "SupportsLocks": True,
        "SupportsUpdate": can_write,
    }


@wopi_router.get("/api/wopi/files/{file_id}/contents")
async def wopi_get_file(file_id: str, access_token: str = Query(...)):
    """WOPI GetFile - Dateiinhalt streamen."""
    token_data = _validate_wopi_token(access_token)
    if not token_data:
        raise HTTPException(status_code=401, detail="Ungueltiger oder abgelaufener Token")

    try:
        task_id, file_path = _decode_file_id(file_id)
    except (ValueError, Exception):
        raise HTTPException(status_code=404, detail="Ungueltige File-ID")

    if token_data["task_id"] != task_id or token_data["file_path"] != file_path:
        raise HTTPException(status_code=403, detail="Token passt nicht zur Datei")

    nc_path = _get_task_nc_path(task_id)
    if not nc_path:
        raise HTTPException(status_code=404, detail="Kein Nextcloud-Verzeichnis zugeordnet")

    full_path = f"{nc_path}/{file_path.strip('/')}"

    try:
        content, content_type = await asyncio.to_thread(webdav.get_file, full_path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Datei nicht gefunden")
    except Exception as e:
        logger.exception("Fehler beim Laden des Dateiinhalts (WOPI GetFile)")
        raise HTTPException(status_code=500, detail="Datei konnte nicht geladen werden")

    return Response(content=content, media_type="application/octet-stream")


@wopi_router.post("/api/wopi/files/{file_id}/contents")
async def wopi_put_file(file_id: str, request: Request, access_token: str = Query(...)):
    """WOPI PutFile - Geaenderten Inhalt via WebDAV zurueckschreiben."""
    token_data = _validate_wopi_token(access_token)
    if not token_data:
        raise HTTPException(status_code=401, detail="Ungueltiger oder abgelaufener Token")

    if token_data["permissions"] != "edit":
        raise HTTPException(status_code=403, detail="Keine Schreibrechte")

    try:
        task_id, file_path = _decode_file_id(file_id)
    except (ValueError, Exception):
        raise HTTPException(status_code=404, detail="Ungueltige File-ID")

    if token_data["task_id"] != task_id or token_data["file_path"] != file_path:
        raise HTTPException(status_code=403, detail="Token passt nicht zur Datei")

    nc_path = _get_task_nc_path(task_id)
    if not nc_path:
        raise HTTPException(status_code=404, detail="Kein Nextcloud-Verzeichnis zugeordnet")

    full_path = f"{nc_path}/{file_path.strip('/')}"

    # Dateiinhalt aus Request-Body lesen
    content = await request.body()

    try:
        await asyncio.to_thread(webdav.upload_file, full_path, content)
    except Exception as e:
        logger.exception("Fehler beim Speichern der Datei (WOPI PutFile)")
        raise HTTPException(status_code=500, detail="Datei konnte nicht gespeichert werden")

    return Response(status_code=200)


@wopi_router.post("/api/wopi/files/{file_id}")
async def wopi_lock(file_id: str, request: Request, access_token: str = Query(...)):
    """WOPI Lock/Unlock/RefreshLock via X-WOPI-Override Header."""
    token_data = _validate_wopi_token(access_token)
    if not token_data:
        raise HTTPException(status_code=401, detail="Ungueltiger oder abgelaufener Token")

    override = request.headers.get("X-WOPI-Override", "").upper()
    lock_id = request.headers.get("X-WOPI-Lock", "")
    old_lock_id = request.headers.get("X-WOPI-OldLock", "")

    # WOPI Lock braucht spezielle Behandlung:
    # - Bedingte commits an verschiedenen Stellen
    # - Cleanup am Ende (immer)
    # Deshalb verwenden wir hier db_transaction() NICHT, sondern manuelles Management
    # mit db_query() fuer die Hauptlogik und separaten Transaktionen fuer Writes
    with db_transaction() as db:
        existing_lock = db.execute(
            "SELECT * FROM wopi_locks WHERE file_id = ?", (file_id,)
        ).fetchone()

        if override == "LOCK":
            if old_lock_id:
                # Unlock-and-Relock
                if existing_lock and existing_lock["lock_id"] == old_lock_id:
                    expires = (datetime.now(timezone.utc) + timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
                    db.execute(
                        "UPDATE wopi_locks SET lock_id = ?, expires_at = ? WHERE file_id = ?",
                        (lock_id, expires, file_id),
                    )
                    # Cleanup abgelaufener Locks
                    db.execute("DELETE FROM wopi_locks WHERE expires_at < datetime('now')")
                    return Response(status_code=200)
                else:
                    resp = Response(status_code=409)
                    if existing_lock:
                        resp.headers["X-WOPI-Lock"] = existing_lock["lock_id"]
                    # Cleanup abgelaufener Locks
                    db.execute("DELETE FROM wopi_locks WHERE expires_at < datetime('now')")
                    return resp

            if existing_lock:
                if existing_lock["lock_id"] == lock_id:
                    # RefreshLock
                    expires = (datetime.now(timezone.utc) + timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
                    db.execute(
                        "UPDATE wopi_locks SET expires_at = ? WHERE file_id = ?",
                        (expires, file_id),
                    )
                    # Cleanup abgelaufener Locks
                    db.execute("DELETE FROM wopi_locks WHERE expires_at < datetime('now')")
                    return Response(status_code=200)
                else:
                    resp = Response(status_code=409)
                    resp.headers["X-WOPI-Lock"] = existing_lock["lock_id"]
                    # Cleanup abgelaufener Locks
                    db.execute("DELETE FROM wopi_locks WHERE expires_at < datetime('now')")
                    return resp

            # Neuen Lock erstellen
            expires = (datetime.now(timezone.utc) + timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
            db.execute(
                "INSERT OR REPLACE INTO wopi_locks (file_id, lock_id, user_id, expires_at) VALUES (?, ?, ?, ?)",
                (file_id, lock_id, token_data["user_id"], expires),
            )
            # Cleanup abgelaufener Locks
            db.execute("DELETE FROM wopi_locks WHERE expires_at < datetime('now')")
            return Response(status_code=200)

        elif override == "UNLOCK":
            if existing_lock and existing_lock["lock_id"] == lock_id:
                db.execute("DELETE FROM wopi_locks WHERE file_id = ?", (file_id,))
                # Cleanup abgelaufener Locks
                db.execute("DELETE FROM wopi_locks WHERE expires_at < datetime('now')")
                return Response(status_code=200)
            elif existing_lock:
                resp = Response(status_code=409)
                resp.headers["X-WOPI-Lock"] = existing_lock["lock_id"]
                # Cleanup abgelaufener Locks
                db.execute("DELETE FROM wopi_locks WHERE expires_at < datetime('now')")
                return resp
            else:
                resp = Response(status_code=409)
                resp.headers["X-WOPI-Lock"] = ""
                # Cleanup abgelaufener Locks
                db.execute("DELETE FROM wopi_locks WHERE expires_at < datetime('now')")
                return resp

        elif override == "REFRESH_LOCK":
            if existing_lock and existing_lock["lock_id"] == lock_id:
                expires = (datetime.now(timezone.utc) + timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
                db.execute(
                    "UPDATE wopi_locks SET expires_at = ? WHERE file_id = ?",
                    (expires, file_id),
                )
                # Cleanup abgelaufener Locks
                db.execute("DELETE FROM wopi_locks WHERE expires_at < datetime('now')")
                return Response(status_code=200)
            elif existing_lock:
                resp = Response(status_code=409)
                resp.headers["X-WOPI-Lock"] = existing_lock["lock_id"]
                # Cleanup abgelaufener Locks
                db.execute("DELETE FROM wopi_locks WHERE expires_at < datetime('now')")
                return resp
            else:
                resp = Response(status_code=409)
                resp.headers["X-WOPI-Lock"] = ""
                # Cleanup abgelaufener Locks
                db.execute("DELETE FROM wopi_locks WHERE expires_at < datetime('now')")
                return resp

        elif override == "GET_LOCK":
            if existing_lock:
                resp = Response(status_code=200)
                resp.headers["X-WOPI-Lock"] = existing_lock["lock_id"]
                # Cleanup abgelaufener Locks
                db.execute("DELETE FROM wopi_locks WHERE expires_at < datetime('now')")
                return resp
            else:
                resp = Response(status_code=200)
                resp.headers["X-WOPI-Lock"] = ""
                # Cleanup abgelaufener Locks
                db.execute("DELETE FROM wopi_locks WHERE expires_at < datetime('now')")
                return resp

        # Cleanup abgelaufener Locks
        db.execute("DELETE FROM wopi_locks WHERE expires_at < datetime('now')")
        return Response(status_code=501)


# ============================================================
# ONLYOFFICE Callback
# ============================================================

@wopi_router.post("/api/onlyoffice/callback")
async def onlyoffice_callback(request: Request):
    """
    Callback von ONLYOFFICE nach Bearbeitungsende.
    Status 2 = Dokument bereit zum Speichern
    Status 6 = Dokument wird gespeichert (Zwischenspeicherung)

    Sicherheit:
    - JWT-Verifizierung: nur signierte Requests von ONLYOFFICE akzeptieren
    - URL-Validierung: nur http(s), keine Metadata-Endpoints (SSRF-Schutz)
    """
    # 1) Config + JWT-Secret laden
    config = _get_onlyoffice_config()
    if not config or not config.get("jwt_secret"):
        logger.error("ONLYOFFICE Callback: Keine Konfiguration/JWT-Secret vorhanden")
        return {"error": 1}

    secret = config["jwt_secret"]

    try:
        body = await request.json()
    except Exception:
        return {"error": 1}

    # 2) JWT-Verifizierung: Token aus Body oder Authorization-Header
    payload = None

    # ONLYOFFICE sendet JWT im Body als {"token": "<jwt>"}
    if "token" in body:
        payload = _verify_jwt(body["token"], secret)

    # Fallback: Authorization-Header
    if payload is None:
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            payload = _verify_jwt(auth_header[7:], secret)

    if payload is None:
        logger.warning("ONLYOFFICE Callback: JWT-Verifizierung fehlgeschlagen")
        return {"error": 1}

    # Ab hier nur noch verifiziertes Payload verwenden
    status = payload.get("status", 0)
    download_url = payload.get("url", "")

    # Status 2 oder 6: Dokument muss gespeichert werden
    if status in (2, 6) and download_url:
        # 3) URL-Validierung (Defense in Depth)
        if not _validate_callback_url(download_url):
            logger.warning(f"ONLYOFFICE Callback: Unerlaubte Download-URL blockiert: {download_url}")
            return {"error": 1}

        file_key = payload.get("key", "")
        # file_key enthaelt die file_id
        if not file_key:
            return {"error": 0}

        # Download-URL kann von ONLYOFFICE-internem Netzwerk kommen
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0), verify=config.get("verify", False)) as client:
                resp = await client.get(download_url)
                if resp.status_code != 200:
                    logger.error(f"ONLYOFFICE Callback: Download fehlgeschlagen: {resp.status_code}")
                    return {"error": 1}
                content = resp.content
        except Exception as e:
            logger.error(f"ONLYOFFICE Callback: Download-Fehler: {e}")
            return {"error": 1}

        # file_key Format: file_id_timestamp - extrahiere file_id
        # Wir verwenden file_id direkt als key (ohne Timestamp)
        try:
            task_id, file_path = _decode_file_id(file_key.rsplit("_", 1)[0] if "_" in file_key else file_key)
        except (ValueError, Exception):
            logger.error(f"ONLYOFFICE Callback: Ungueltige file_key: {file_key}")
            return {"error": 0}

        nc_path = _get_task_nc_path(task_id)
        if nc_path:
            full_path = f"{nc_path}/{file_path.strip('/')}"
            try:
                await asyncio.to_thread(webdav.upload_file, full_path, content)
                logger.info(f"ONLYOFFICE Callback: Datei gespeichert: {full_path}")
            except Exception as e:
                logger.error(f"ONLYOFFICE Callback: Speichern fehlgeschlagen: {e}")
                return {"error": 1}

    return {"error": 0}


# ============================================================
# Editor-Open Endpoint (Session-Auth)
# ============================================================

@editor_router.get("/api/tasks/{task_id}/files/edit")
async def open_editor(
    task_id: int,
    request: Request,
    path: str = Query(..., description="Relativer Pfad zur Datei"),
    user=Depends(get_current_user),
):
    """
    Generiert WOPI-Token und Editor-Config fuer ONLYOFFICE.
    """
    # ONLYOFFICE Config pruefen
    oo_config = _get_onlyoffice_config()
    if not oo_config:
        raise HTTPException(status_code=400, detail="ONLYOFFICE ist nicht konfiguriert")

    # Nextcloud-Pfad pruefen
    nc_path = _get_task_nc_path(task_id)
    if not nc_path:
        raise HTTPException(status_code=400, detail="Kein Nextcloud-Verzeichnis zugeordnet")

    # Dateiname und Extension pruefen
    filename = path.rsplit("/", 1)[-1] if "/" in path else path
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in EDITABLE_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Dateiformat '{ext}' wird nicht unterstuetzt")

    document_type = DOCUMENT_TYPE_MAP.get(ext, "word")

    # Bearbeitungsrechte pruefen
    can_edit = _check_user_can_edit(task_id, user)
    permissions = "edit" if can_edit else "view"

    # WOPI-Token generieren
    token, token_ttl = _generate_wopi_token(user["id"], task_id, path, permissions)

    # File-ID
    file_id = _encode_file_id(task_id, path)

    # WOPI Base URL aus Request ermitteln
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.headers.get("host", ""))
    wopi_base = f"{scheme}://{host}"

    wopi_src = f"{wopi_base}/api/wopi/files/{file_id}"

    # Editor-Config zusammenbauen
    editor_config = {
        "document": {
            "fileType": ext,
            "key": f"{file_id}_{int(time.time())}",
            "title": filename,
            "url": f"{wopi_src}/contents?access_token={token}",
            "permissions": {
                "edit": can_edit,
                "download": True,
                "print": True,
                "review": False,
                "comment": False,
            },
        },
        "documentType": document_type,
        "editorConfig": {
            "mode": "edit" if can_edit else "view",
            "lang": "de",
            "callbackUrl": f"{wopi_base}/api/onlyoffice/callback",
            "user": {
                "id": str(user["id"]),
                "name": get_display_name(user),
            },
        },
    }

    # JWT fuer ONLYOFFICE signieren
    jwt_token = _sign_jwt(editor_config, oo_config["jwt_secret"])

    return {
        "editor_url": oo_config["server_url"],
        "editor_config": editor_config,
        "token": jwt_token,
        "access_token": token,
        "access_token_ttl": token_ttl,
        "can_edit": can_edit,
        "file_id": file_id,
        "wopi_src": wopi_src,
    }
