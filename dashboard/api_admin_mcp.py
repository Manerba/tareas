"""
Tareas - Admin-API fuer MCP (Tokens, Audit-Log, Kill-Switch).

Pfad-Prefix: /api/admin/mcp
Auth: get_admin_user pro Endpoint.
"""

from typing import Optional

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel

from dashboard.auth import (
    get_admin_user,
    create_mcp_token,
    revoke_mcp_token,
    list_mcp_tokens,
    get_mcp_config,
    set_mcp_enabled,
)
from dashboard.audit_log import log_change
from dashboard.db_utils import db_query
from dashboard.logging_config import get_security_logger

security_log = get_security_logger()

router = APIRouter(prefix="/api/admin/mcp", tags=["mcp-admin"])


# Static tool list (Sync mit mcp_server.py). Bewusst hardcoded fuer Robustheit
# und um Import-Reihenfolge-Probleme zu vermeiden.
MCP_TOOLS = [
    "get_agent_guide",
    "list_projects", "get_project", "create_project", "update_project", "delete_project",
    "list_subtasks", "get_subtask", "create_subtask", "update_subtask", "delete_subtask",
    "move_subtask", "set_subtask_position",
    "add_dependency", "remove_dependency",
    "note.list", "note.write", "note.update", "note.delete",
    "handoff.list", "handoff.add", "handoff.update", "handoff.delete",
    "list_users", "list_areas", "search", "assign_self", "whoami",
]


# ============================================================
# Pydantic-Modelle
# ============================================================

class TokenCreate(BaseModel):
    display_name: str


class ConfigUpdate(BaseModel):
    enabled: bool


# ============================================================
# Config (Status, Kill-Switch)
# ============================================================

@router.get("/config")
async def mcp_get_config(user=Depends(get_admin_user)):
    """Liefert MCP-Status + Server-URL + Tool-Liste."""
    cfg = get_mcp_config()

    # Server-URL aus app_config zusammenbauen (best-effort)
    server_url = None
    try:
        with db_query() as db:
            row = db.execute("SELECT server_address FROM app_config WHERE id = 1").fetchone()
            tls = db.execute("SELECT enabled FROM tls_config WHERE id = 1").fetchone()
        protocol = "https" if (tls and tls["enabled"]) else "http"
        addr = (row["server_address"] if row else None) or "localhost:8504"
        server_url = f"{protocol}://{addr}/mcp/"
    except Exception:
        server_url = "http://localhost:8504/mcp/"

    # Aktive Token zaehlen
    active_tokens = 0
    try:
        with db_query() as db:
            r = db.execute(
                "SELECT COUNT(*) AS c FROM mcp_tokens WHERE revoked_at IS NULL"
            ).fetchone()
            active_tokens = r["c"]
    except Exception:
        pass

    return {
        "enabled": cfg["enabled"],
        "server_url": server_url,
        "available_tools": MCP_TOOLS,
        "active_token_count": active_tokens,
    }


@router.put("/config")
async def mcp_update_config(data: ConfigUpdate, user=Depends(get_admin_user)):
    """Schaltet MCP global ein/aus (Kill-Switch)."""
    set_mcp_enabled(data.enabled)
    security_log.info("MCP_CONFIG_CHANGED enabled=%s by=%s", data.enabled, user["username"])
    log_change(user, "mcp_config", None, "update", {"enabled": data.enabled})
    return {"enabled": data.enabled}


# ============================================================
# Token-Verwaltung
# ============================================================

@router.get("/tokens")
async def mcp_list_tokens(user=Depends(get_admin_user)):
    """Listet alle MCP-Tokens (ohne Hash)."""
    return {"items": list_mcp_tokens()}


@router.post("/tokens")
async def mcp_create_token(data: TokenCreate, user=Depends(get_admin_user)):
    """Erstellt einen neuen MCP-Token. Plain-Token wird NUR HIER zurueckgegeben."""
    if not data.display_name.strip():
        raise HTTPException(status_code=400, detail="display_name darf nicht leer sein")
    plain_token, token_id = create_mcp_token(data.display_name.strip(), user["id"])
    security_log.info(
        "MCP_TOKEN_CREATED token_id=%s display_name=%s by=%s",
        token_id, data.display_name.strip(), user["username"],
    )
    log_change(user, "mcp_token", token_id, "create", {"display_name": data.display_name.strip()})
    return {
        "id": token_id,
        "display_name": data.display_name.strip(),
        "token": plain_token,
        "message": "Token wird nur EINMAL angezeigt - bitte sicher speichern.",
    }


@router.delete("/tokens/{token_id}")
async def mcp_revoke_token(token_id: int, user=Depends(get_admin_user)):
    """Widerruft einen Token (setzt revoked_at)."""
    ok = revoke_mcp_token(token_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Token nicht gefunden oder bereits widerrufen")
    security_log.info("MCP_TOKEN_REVOKED token_id=%s by=%s", token_id, user["username"])
    log_change(user, "mcp_token", token_id, "revoke")
    return {"revoked": True, "id": token_id}


# ============================================================
# Audit-Log
# ============================================================

@router.get("/audit")
async def mcp_get_audit(
    user=Depends(get_admin_user),
    actor_user_id: Optional[int] = Query(None),
    actor_type: Optional[str] = Query(None),
    entity_type: Optional[str] = Query(None),
    entity_id: Optional[int] = Query(None),
    action: Optional[str] = Query(None),
    since: Optional[str] = Query(None, description="ISO-Timestamp, inclusive"),
    until: Optional[str] = Query(None, description="ISO-Timestamp, inclusive"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    """Audit-Log paginiert + filterbar."""
    where, params = [], []
    if actor_user_id is not None:
        where.append("a.actor_user_id = ?"); params.append(actor_user_id)
    if actor_type:
        where.append("a.actor_type = ?"); params.append(actor_type)
    if entity_type:
        where.append("a.entity_type = ?"); params.append(entity_type)
    if entity_id is not None:
        where.append("a.entity_id = ?"); params.append(entity_id)
    if action:
        where.append("a.action = ?"); params.append(action)
    if since:
        where.append("a.timestamp >= ?"); params.append(since)
    if until:
        where.append("a.timestamp <= ?"); params.append(until)

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    with db_query() as db:
        total = db.execute(
            f"SELECT COUNT(*) AS c FROM audit_log a {where_sql}", params
        ).fetchone()["c"]
        rows = db.execute(
            f"""SELECT a.id, a.timestamp, a.actor_user_id, a.actor_type,
                       a.entity_type, a.entity_id, a.action, a.changes_json,
                       u.username AS actor_username,
                       (COALESCE(u.vorname,'') || ' ' || COALESCE(u.nachname,'')) AS actor_display
                FROM audit_log a
                LEFT JOIN users u ON a.actor_user_id = u.id
                {where_sql}
                ORDER BY a.timestamp DESC, a.id DESC
                LIMIT ? OFFSET ?""",
            params + [limit, offset],
        ).fetchall()

    items = [
        {
            "id": r["id"],
            "timestamp": r["timestamp"],
            "actor_user_id": r["actor_user_id"],
            "actor_type": r["actor_type"],
            "actor_username": r["actor_username"],
            "actor_display": (r["actor_display"] or "").strip() or r["actor_username"],
            "entity_type": r["entity_type"],
            "entity_id": r["entity_id"],
            "action": r["action"],
            "changes_json": r["changes_json"],
        }
        for r in rows
    ]
    return {"items": items, "total": total, "limit": limit, "offset": offset}
