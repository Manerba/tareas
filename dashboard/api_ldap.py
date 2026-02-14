"""
Tareas - LDAP Admin API-Router
LDAP-Konfiguration, Gruppen, Synchronisierung.
"""

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from dashboard.db_utils import db_query, db_transaction
from dashboard.config_utils import get_masked_config, resolve_masked_password
from dashboard.crypto_utils import decrypt
from dashboard.auth import get_admin_user
from dashboard.ldap_utils import test_connection, list_groups
from dashboard.ldap_sync import sync_ldap_users
from dashboard.tls_utils import get_tls_verify_config
from dashboard.logging_config import get_security_logger

logger = logging.getLogger(__name__)
security_log = get_security_logger()

router = APIRouter(prefix="/api/admin/ldap", tags=["ldap"])


# ============================================================
# Pydantic-Modelle
# ============================================================

class LdapConfigRequest(BaseModel):
    server: str
    port: int = 389
    use_ssl: int = 0
    bind_dn: str
    bind_password: str
    search_base: str
    sync_interval_minutes: int = 60


class GroupSelectRequest(BaseModel):
    group_dn: str
    group_name: str


# ============================================================
# Endpoints
# ============================================================

@router.get("/config")
async def get_ldap_config(user=Depends(get_admin_user)):
    """Aktuelle LDAP-Konfiguration laden (bind_password maskiert)."""
    config = get_masked_config("ldap_config", ["bind_password"])
    return {"config": config}


@router.post("/config")
async def save_ldap_config(data: LdapConfigRequest, user=Depends(get_admin_user)):
    """LDAP-Konfiguration speichern (nach erfolgreichem Verbindungstest)."""
    if not data.server.strip():
        raise HTTPException(status_code=400, detail="Server ist Pflichtfeld")
    if not data.bind_dn.strip():
        raise HTTPException(status_code=400, detail="Bind-DN ist Pflichtfeld")
    if not data.bind_password.strip():
        raise HTTPException(status_code=400, detail="Bind-Passwort ist Pflichtfeld")
    if not data.search_base.strip():
        raise HTTPException(status_code=400, detail="Suchpfad ist Pflichtfeld")

    # Passwort ggf. aus DB holen wenn maskiert
    with db_query() as db:
        actual_password = resolve_masked_password(db, "ldap_config", "bind_password", data.bind_password)

    # Verbindungstest (in Thread-Pool, blockiert sonst den Event-Loop) - Klartext fuer Test
    verify_cfg = get_tls_verify_config()
    success, error = await asyncio.to_thread(
        test_connection,
        data.server.strip(), data.port, data.use_ssl,
        data.bind_dn.strip(), decrypt(actual_password), data.search_base.strip(),
        verify_cfg["verify_ldap"],
    )

    if not success:
        raise HTTPException(status_code=400, detail=f"Verbindungstest fehlgeschlagen: {error}")

    # Config speichern (UPSERT)
    with db_transaction() as db:
        existing = db.execute("SELECT id FROM ldap_config WHERE id = 1").fetchone()

        if existing:
            db.execute(
                """UPDATE ldap_config SET server = ?, port = ?, use_ssl = ?,
                   bind_dn = ?, bind_password = ?, search_base = ?,
                   sync_interval_minutes = ? WHERE id = 1""",
                (data.server.strip(), data.port, data.use_ssl,
                 data.bind_dn.strip(), actual_password,
                 data.search_base.strip(), data.sync_interval_minutes),
            )
        else:
            db.execute(
                """INSERT INTO ldap_config (id, server, port, use_ssl, bind_dn, bind_password,
                   search_base, sync_interval_minutes) VALUES (1, ?, ?, ?, ?, ?, ?, ?)""",
                (data.server.strip(), data.port, data.use_ssl,
                 data.bind_dn.strip(), actual_password,
                 data.search_base.strip(), data.sync_interval_minutes),
            )

        security_log.info("CONFIG_CHANGED section=ldap by=%s", user["username"])
        return {"message": "LDAP-Konfiguration gespeichert"}


@router.delete("/config")
async def delete_ldap_config(user=Depends(get_admin_user)):
    """LDAP-Konfiguration loeschen und alle LDAP-Benutzer deaktivieren."""
    with db_transaction() as db:
        db.execute("DELETE FROM ldap_config WHERE id = 1")
        db.execute("UPDATE users SET is_active = 0 WHERE auth_source = 'ldap'")
        security_log.info("CONFIG_CHANGED section=ldap_deleted by=%s", user["username"])
        return {"message": "LDAP-Konfiguration geloescht, LDAP-Benutzer deaktiviert"}


@router.get("/groups")
async def get_ldap_groups(user=Depends(get_admin_user)):
    """Verfuegbare AD-Gruppen im search_base auflisten."""
    with db_query() as db:
        row = db.execute("SELECT * FROM ldap_config WHERE id = 1").fetchone()
        if not row:
            raise HTTPException(status_code=400, detail="Keine LDAP-Konfiguration vorhanden")

        verify_cfg = get_tls_verify_config()
        config = {
            "server": row["server"],
            "port": row["port"],
            "use_ssl": row["use_ssl"],
            "bind_dn": row["bind_dn"],
            "bind_password": decrypt(row["bind_password"]),
            "search_base": row["search_base"],
            "verify_cert": verify_cfg["verify_ldap"],
        }

    try:
        groups = await asyncio.to_thread(list_groups, config)
        return {"groups": groups}
    except Exception as e:
        logger.exception("Fehler beim Laden der LDAP-Gruppen")
        raise HTTPException(status_code=500, detail="LDAP-Gruppen konnten nicht geladen werden")


@router.post("/group")
async def select_group(data: GroupSelectRequest, user=Depends(get_admin_user)):
    """Gruppe auswaehlen und sofort Sync ausfuehren."""
    with db_transaction() as db:
        existing = db.execute("SELECT id FROM ldap_config WHERE id = 1").fetchone()
        if not existing:
            raise HTTPException(status_code=400, detail="Keine LDAP-Konfiguration vorhanden")

        db.execute(
            "UPDATE ldap_config SET group_dn = ?, group_name = ? WHERE id = 1",
            (data.group_dn, data.group_name),
        )

    # Sofort Sync ausfuehren
    result = await asyncio.to_thread(sync_ldap_users)

    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])

    return {
        "message": f"Gruppe '{data.group_name}' ausgewaehlt und synchronisiert",
        "sync": result,
    }


@router.delete("/group")
async def deselect_group(user=Depends(get_admin_user)):
    """Gruppenauswahl aufheben und alle LDAP-Benutzer deaktivieren."""
    with db_transaction() as db:
        db.execute(
            "UPDATE ldap_config SET group_dn = NULL, group_name = NULL WHERE id = 1"
        )
        db.execute("UPDATE users SET is_active = 0 WHERE auth_source = 'ldap'")
        return {"message": "Gruppenauswahl aufgehoben, LDAP-Benutzer deaktiviert"}


@router.post("/sync")
async def manual_sync(user=Depends(get_admin_user)):
    """Manueller Sync ('Jetzt synchronisieren')."""
    result = await asyncio.to_thread(sync_ldap_users)

    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])

    return {
        "message": "Synchronisierung abgeschlossen",
        "sync": result,
    }
