"""
Tareas - MCP-Server (Model Context Protocol)

Exponiert Tareas-Funktionen als MCP-Tools fuer remote Claude-Code-Instanzen.
Wird ueber mcp_transport.py an /mcp/ in app.py und mcp_app.py eingebunden.

Authentifizierung: Bearer-Token (gemeinsame Middleware setzt request.state.mcp_user).
Jeder MCP-User ist ein eigener Eintrag in der users-Tabelle mit
auth_source='mcp'.

Persistenz: direkter Zugriff auf SQLite (selbe DB wie REST-API), Schreib-Ops
laufen via audit_log.log_change(...) ins Audit.
"""

import base64
import binascii
import logging
import mimetypes
from contextvars import ContextVar
from typing import Annotated, Literal

import httpx
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_request
from fastapi import HTTPException
from pydantic import Field

from dashboard.agent_guide import build_agent_guide_markdown, build_agent_metadata
from dashboard.audit_log import log_change, diff_fields
from dashboard.db_utils import db_query, db_transaction
from dashboard.task_types import normalize_task_type
from dashboard.task_status import TASK_STATUS_CTE, task_with_status
from dashboard.task_validation import (
    ProjectStatus, StatusPercent, validate_project_status, validate_status_percent,
)
from dashboard.note_service import update_note_as_admin
from dashboard.permissions import require_task_access, require_subtask_access, task_visibility_sql
from dashboard.dependency_service import add_subtask_dependency, set_subtask_predecessors
from dashboard.project_links import (
    SUBTASK_LINK_SELECT, ensure_manual_progress, refresh_parent_progress, set_parent_subtask,
)
from dashboard.file_storage import get_task_storage, safe_rel_path, storage_type
from dashboard.mcp_errors import MCPToolError, StructuredToolErrors, tool_error_from_http

logger = logging.getLogger(__name__)

# Benutzerkontext fuer direkte Aufrufe ohne HTTP, z.B. in Tests.
current_mcp_user: ContextVar[dict | None] = ContextVar("current_mcp_user", default=None)


def _user() -> dict:
    """Aktuellen MCP-User aus dem Request-Kontext holen."""
    try:
        request = get_http_request()
    except RuntimeError:
        u = current_mcp_user.get()
    else:
        # MCP-Sessions laufen in langlebigen Tasks und erben ContextVars beim
        # Initialisieren. Die Identitaet muss aus dem aktuellen HTTP-Request
        # stammen, damit Token-/Rollenwechsel sofort wirksam werden.
        u = getattr(request.state, "mcp_user", None)
    if not u:
        raise MCPToolError("authentication_required", "MCP-User-Kontext fehlt (Bearer-Token nicht akzeptiert?)")
    return u


def _change_dependencies(operation, *args, **kwargs):
    try:
        return operation(*args, **kwargs)
    except HTTPException as exc:
        raise tool_error_from_http(exc) from exc


# ============================================================
# Row-Serialisierung
# ============================================================

def _task_to_dict(row, *, include_description: bool = True) -> dict:
    result = {
        "id": row["id"],
        "parent_subtask_id": row["parent_subtask_id"],
        "parent_project_id": row["parent_project_id"],
        "progress_percent": row["progress_percent"],
        "name": row["name"],
        "status": row["effective_status"],
        "priority": row["priority"],
        "task_type": row["task_type"],
        "deadline": row["deadline"],
        "created_at": row["created_at"],
        "created_by": row["created_by"],
        "assigned_to": row["assigned_to"],
        "nextcloud_path": row["nextcloud_path"] if "nextcloud_path" in row.keys() else None,
        "file_storage_type": storage_type(row),
    }
    if include_description:
        result["description"] = row["description"] or ""
        result["description_format"] = row["description_format"]
    return result


def _subtask_to_dict(row) -> dict:
    return {
        "id": row["id"],
        "project_id": row["project_id"],
        "name": row["name"],
        "description": row["description"] or "",
        "description_format": row["description_format"],
        "area_id": row["area_id"],
        "deadline": row["deadline"],
        "priority": row["priority"],
        "status_percent": row["status_percent"],
        "child_project_id": row["child_project_id"],
        "progress_automatic": row["child_project_id"] is not None,
        "position_number": row["position_number"],
        "created_at": row["created_at"],
        "created_by": row["created_by"],
        "assigned_to": row["assigned_to"],
        "depends_on_project": bool(row["depends_on_project"]) if row["depends_on_project"] is not None else False,
    }


def _ordered_subtask_rows(db, project_id: int) -> list:
    return db.execute(
        """SELECT id, position_number FROM sub_tasks
           WHERE project_id = ?
           ORDER BY
               CASE WHEN position_number IS NULL THEN 1 ELSE 0 END,
               position_number ASC,
               created_at ASC,
               id ASC""",
        (project_id,),
    ).fetchall()


def _renumber_project_subtasks(db, ordered_ids: list[int]) -> dict[int, int]:
    new_positions: dict[int, int] = {}
    for pos, sid in enumerate(ordered_ids, start=1):
        db.execute("UPDATE sub_tasks SET position_number = ? WHERE id = ?", (pos, sid))
        new_positions[sid] = pos
    return new_positions


def _subtask_with_predecessors(db, subtask_id: int) -> dict:
    row = db.execute(SUBTASK_LINK_SELECT + " WHERE st.id = ?", (subtask_id,)).fetchone()
    if not row:
        raise MCPToolError("subtask_not_found", f"Teilaufgabe {subtask_id} nicht gefunden", field="subtask_id")
    preds = db.execute(
        "SELECT depends_on_id FROM sub_task_dependencies WHERE sub_task_id = ?",
        (subtask_id,),
    ).fetchall()
    result = _subtask_to_dict(row)
    result["predecessor_ids"] = [p["depends_on_id"] for p in preds]
    return result


# ============================================================
# FastMCP-Instanz
# ============================================================

mcp = FastMCP(
    name="Tareas",
    middleware=[StructuredToolErrors()],
    mask_error_details=True,
    # Pydantic validiert dieselben Tooltypen im Middleware-Kontext. Die optionale
    # vorgelagerte JSON-Schema-Pruefung des SDK liefert nur Freitextfehler.
    strict_input_validation=False,
    instructions=(
        "Tareas-Aufgabenverwaltung. Du bist als eigener MCP-User in der DB "
        "registriert; alle Aenderungen werden im Audit-Log protokolliert. "
        "Aufgaben (Projekte) haben Teilaufgaben (Subtasks) mit optionalen "
        "Abhaengigkeiten. Felder: description = Spec/Anforderung, notes = "
        "aktuelle User-Notiz, handoffs = chronologischer Verlauf/Findings. "
        "file.* greift auf die konfigurierte Projektablage zu (lokal oder WebDAV), "
        "mit relativen Pfaden und den aktuellen Projektrechten."
    ),
)


@mcp.tool
def get_agent_guide() -> dict:
    """Liefert die tokenfreie Tareas-Nutzungsanleitung inkl. URLs und AGENTS.md-Snippet."""
    metadata = build_agent_metadata()
    return {
        "metadata": metadata,
        "markdown": build_agent_guide_markdown(metadata),
    }


# ============================================================
# Projekte (Tasks)
# ============================================================

@mcp.tool
def list_projects(status: ProjectStatus | None = None) -> list[dict]:
    """Listet alle Projekte (Top-Level-Aufgaben).

    Statusfilter: offen, in_arbeit, erledigt, abgebrochen. Der Projektstatus
    wird wie in der Weboberflaeche aus erledigten Teilaufgaben berechnet.
    """
    user = _user()
    if status is not None:
        _access(validate_project_status, status)
    with db_query() as db:
        visibility, params = task_visibility_sql(user)
        status_sql = " AND t.effective_status = ?" if status else ""
        rows = db.execute(
            TASK_STATUS_CTE + "SELECT t.* FROM tasks_with_status t WHERE " + visibility + status_sql
            + " ORDER BY t.priority DESC, t.created_at DESC, t.id DESC",
            [*params, *([status] if status else [])],
        ).fetchall()
        return [_task_to_dict(row) for row in rows]


@mcp.tool(annotations={"readOnlyHint": True})
def list_projects_page(
    status: ProjectStatus | None = None,
    offset: Annotated[int, Field(strict=True, ge=0)] = 0,
    limit: Annotated[int, Field(strict=True, ge=1, le=500)] = 50,
    include_description: bool = False,
) -> dict:
    """Listet sichtbare Aufgaben/Projekte kompakt und seitenweise.

    Ohne Beschreibungen; Details bei Bedarf mit get_project nachladen oder
    include_description=true setzen. Sortierung: Prioritaet, Erstellzeit,
    ID absteigend. status filtert den wie im Web berechneten Projektstatus.
    next_offset ist der Start der naechsten Seite oder null am Listenende.
    Bei Aenderungen zwischen Seiten kann sich die Reihenfolge verschieben.
    Das bisherige list_projects liefert weiterhin die vollstaendige Liste.
    """
    user = _user()
    if status is not None:
        _access(validate_project_status, status)
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise MCPToolError("invalid_pagination", "offset muss eine Ganzzahl ab 0 sein", field="offset")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 500:
        raise MCPToolError("invalid_pagination", "limit muss eine Ganzzahl von 1 bis 500 sein", field="limit")

    visibility, params = task_visibility_sql(user)
    where = " FROM tasks_with_status t WHERE " + visibility
    if status is not None:
        where += " AND t.effective_status = ?"
        params.append(status)
    columns = (
        "t.id, t.name, t.effective_status, t.priority, t.task_type, t.deadline, "
        "t.created_at, t.created_by, t.assigned_to, t.nextcloud_path, t.file_storage_type, "
        "t.parent_subtask_id, t.parent_project_id, t.progress_percent"
    )
    if include_description:
        columns += ", t.description, t.description_format"

    with db_query() as db:
        # Beide SELECTs sehen denselben WAL-Snapshot; keine Schreibsperre.
        db.execute("BEGIN")
        total = db.execute(TASK_STATUS_CTE + "SELECT COUNT(*)" + where, params).fetchone()[0]
        # Auch sehr grosse gueltige Offsets bleiben leere Seiten, statt am
        # 64-Bit-Limit von SQLite-Parametern zu scheitern.
        rows = []
        if offset < total:
            rows = db.execute(
                TASK_STATUS_CTE + "SELECT " + columns + where
                + " ORDER BY t.priority DESC, t.created_at DESC, t.id DESC LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
    items = [_task_to_dict(row, include_description=include_description) for row in rows]
    return {
        "items": items,
        "total": total,
        "offset": offset,
        "limit": limit,
        "next_offset": offset + len(items) if offset + len(items) < total else None,
    }


@mcp.tool
def get_project(project_id: int) -> dict:
    """Holt ein Projekt inkl. aller Subtasks und ihrer Abhaengigkeiten in einem Call.

    Der Status folgt erledigten Teilaufgaben: keine = offen, einige =
    in_arbeit, alle = erledigt. Leere Projekte sind offen, abgebrochen hat
    Vorrang. Normale Aufgaben behalten ihren manuell gesetzten Status.
    """
    with db_query() as db:
        task = task_with_status(db, project_id)
        if not task:
            raise MCPToolError("task_not_found", f"Projekt {project_id} nicht gefunden", field="project_id")
        _require_task_read_access(db, project_id, _user())
        subs = db.execute(
            SUBTASK_LINK_SELECT + " WHERE st.project_id = ? ORDER BY st.position_number ASC, st.id ASC",
            (project_id,),
        ).fetchall()
        deps = db.execute(
            """SELECT sub_task_id, depends_on_id FROM sub_task_dependencies
               WHERE sub_task_id IN (SELECT id FROM sub_tasks WHERE project_id = ?)""",
            (project_id,),
        ).fetchall()
        dep_map: dict[int, list[int]] = {}
        for d in deps:
            dep_map.setdefault(d["sub_task_id"], []).append(d["depends_on_id"])

        result = _task_to_dict(task)
        result["subtasks"] = []
        for s in subs:
            st = _subtask_to_dict(s)
            st["predecessor_ids"] = dep_map.get(s["id"], [])
            result["subtasks"].append(st)
        return result


@mcp.tool
def create_project(
    name: str,
    description: str = "",
    deadline: str | None = None,
    priority: int = 50,
    status: ProjectStatus = "offen",
    task_type: str = "projekt",
    parent_subtask_id: Annotated[int, Field(strict=True, ge=0)] | None = None,
) -> dict:
    """Legt ein Projekt an, optional als Kind von parent_subtask_id (PID).
    Ein Kind-Projekt pro Teilaufgabe, keine Zyklen, Schreibrechte auf beiden Seiten.
    Der Eltern-Subtask uebernimmt den Anteil erledigter Teilaufgaben in Prozent;
    sein status_percent ist danach schreibgeschuetzt. Keine Rechtevererbung.
    """
    user = _user()
    status = _access(validate_project_status, status)
    if not name.strip():
        raise MCPToolError("empty_name", "name darf nicht leer sein", field="name")
    try:
        task_type = normalize_task_type(task_type)
    except ValueError as exc:
        raise MCPToolError("invalid_task_type", "task_type muss aufgabe oder projekt sein", field="task_type") from exc
    with db_transaction() as db:
        db.execute("BEGIN IMMEDIATE")
        cursor = db.execute(
            """INSERT INTO tasks (name, description, deadline, priority, status, task_type, created_by, assigned_to)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (name.strip(), description, deadline, priority, status, task_type, user["id"], user["id"]),
        )
        task_id = cursor.lastrowid
        if parent_subtask_id is not None:
            _access(set_parent_subtask, db, task_id, parent_subtask_id, user)
        row = task_with_status(db, task_id)
    log_change(user, "task", task_id, "create", {
        "name": name, "description": description, "priority": priority,
        "status": status, "task_type": task_type, "deadline": deadline,
        "parent_subtask_id": parent_subtask_id or None,
    })
    return _task_to_dict(row)


@mcp.tool
def update_project(
    project_id: int,
    name: str | None = None,
    description: str | None = None,
    deadline: str | None = None,
    priority: int | None = None,
    status: ProjectStatus | None = None,
    assigned_to: int | None = None,
    parent_subtask_id: Annotated[int, Field(strict=True, ge=0)] | None = None,
) -> dict:
    """Aktualisiert ein Projekt. Nur uebergebene Felder werden geaendert.

    status='abgebrochen' bricht Aufgaben/Projekte ab, ohne Inhalte zu loeschen.
    Mit status='offen' wird ein abgebrochenes Projekt wieder aufgenommen;
    MCP und Weboberflaeche berechnen seinen Status dann wieder aus erledigten
    Teilaufgaben. Manuelle Werte offen/in_arbeit/erledigt ueberschreiben bei
    Projekten diese Ableitung nicht; normale Aufgaben behalten diese Werte.
    parent_subtask_id verknuepft mit einer Eltern-Teilaufgabe (PID), 0 trennt.
    Beim Trennen bleibt deren letzter Fortschritt erhalten und wird editierbar.
    Verknuepfung/Trennung brauchen Schreibrechte auf Projekt und Eltern-Subtask.
    """
    user = _user()
    if status is not None:
        status = _access(validate_project_status, status)
    with db_transaction() as db:
        db.execute("BEGIN IMMEDIATE")
        before = db.execute("SELECT * FROM tasks WHERE id = ?", (project_id,)).fetchone()
        if not before:
            raise MCPToolError("task_not_found", f"Projekt {project_id} nicht gefunden", field="project_id")
        _, rights = _access(require_task_access, db, project_id, user, "edit_status")
        if assigned_to is not None and not rights["can_manage"]:
            raise MCPToolError("permission_denied", "Nur Ersteller und Admins duerfen Zuweisungen aendern", field="assigned_to")
        if not rights["can_edit"] and any(v is not None for v in (name, description, deadline, priority)):
            raise MCPToolError("permission_denied", "Nur Statusaenderungen sind erlaubt")
        updates, values = [], []
        new_vals = {}
        if parent_subtask_id is not None:
            _access(set_parent_subtask, db, project_id, parent_subtask_id, user)
            new_vals["parent_subtask_id"] = parent_subtask_id or None
        if name is not None:
            updates.append("name = ?"); values.append(name); new_vals["name"] = name
        if description is not None:
            updates.append("description_format = 'markdown'")
            updates.append("description = ?"); values.append(description); new_vals["description"] = description
        if deadline is not None:
            updates.append("deadline = ?"); values.append(deadline); new_vals["deadline"] = deadline
        if priority is not None:
            updates.append("priority = ?"); values.append(priority); new_vals["priority"] = priority
        if status is not None:
            updates.append("status = ?"); values.append(status); new_vals["status"] = status
        if assigned_to is not None:
            if assigned_to == 0:
                updates.append("assigned_to = NULL"); new_vals["assigned_to"] = None
            else:
                updates.append("assigned_to = ?"); values.append(assigned_to); new_vals["assigned_to"] = assigned_to
        if not new_vals:
            raise MCPToolError("empty_update", "Keine Felder zum Aktualisieren angegeben")
        if updates:
            values.append(project_id)
            db.execute(f"UPDATE tasks SET {', '.join(updates)} WHERE id = ?", values)
        after = task_with_status(db, project_id)
    changes = diff_fields(dict(before), {**dict(before), **new_vals}, list(new_vals.keys()))
    log_change(user, "task", project_id, "update", changes)
    return _task_to_dict(after)


@mcp.tool
def delete_project(project_id: int) -> dict:
    """Loescht ein Projekt inkl. aller Subtasks (CASCADE)."""
    user = _user()
    with db_transaction() as db:
        _access(require_task_access, db, project_id, user, "manage")
        row = db.execute("SELECT id, name FROM tasks WHERE id = ?", (project_id,)).fetchone()
        if not row:
            raise MCPToolError("task_not_found", f"Projekt {project_id} nicht gefunden", field="project_id")
        db.execute("DELETE FROM tasks WHERE id = ?", (project_id,))
    log_change(user, "task", project_id, "delete", {"name": row["name"]})
    return {"deleted": True, "id": project_id}


# ============================================================
# Teilaufgaben (SubTasks)
# ============================================================

@mcp.tool
def list_subtasks(project_id: int) -> list[dict]:
    """Listet alle Teilaufgaben eines Projekts inkl. predecessor_ids."""
    with db_query() as db:
        user = _user()
        _, rights = _access(require_task_access, db, project_id, user, None)
        if not rights["can_read"] and not db.execute(
            "SELECT 1 FROM sub_tasks WHERE project_id = ? AND assigned_to = ?", (project_id, user["id"]),
        ).fetchone():
            raise MCPToolError("permission_denied", "Keine Leseberechtigung", field="project_id")
        if not db.execute("SELECT id FROM tasks WHERE id = ?", (project_id,)).fetchone():
            raise MCPToolError("task_not_found", f"Projekt {project_id} nicht gefunden", field="project_id")
        subs = db.execute(
            SUBTASK_LINK_SELECT + " WHERE st.project_id = ? ORDER BY st.position_number ASC, st.id ASC",
            (project_id,),
        ).fetchall()
        deps = db.execute(
            """SELECT sub_task_id, depends_on_id FROM sub_task_dependencies
               WHERE sub_task_id IN (SELECT id FROM sub_tasks WHERE project_id = ?)""",
            (project_id,),
        ).fetchall()
        dep_map: dict[int, list[int]] = {}
        for d in deps:
            dep_map.setdefault(d["sub_task_id"], []).append(d["depends_on_id"])
        result = []
        for s in subs:
            if not _can_read_subtask(db, project_id, s["id"], _user()):
                continue
            st = _subtask_to_dict(s)
            st["predecessor_ids"] = dep_map.get(s["id"], [])
            result.append(st)
        return result


@mcp.tool
def get_subtask(subtask_id: int) -> dict:
    """Holt eine einzelne Teilaufgabe inkl. predecessor_ids."""
    with db_query() as db:
        _access(require_subtask_access, db, subtask_id, _user())
        row = db.execute(SUBTASK_LINK_SELECT + " WHERE st.id = ?", (subtask_id,)).fetchone()
        if not row:
            raise MCPToolError("subtask_not_found", f"Teilaufgabe {subtask_id} nicht gefunden", field="subtask_id")
        preds = db.execute(
            "SELECT depends_on_id FROM sub_task_dependencies WHERE sub_task_id = ?",
            (subtask_id,),
        ).fetchall()
        st = _subtask_to_dict(row)
        st["predecessor_ids"] = [p["depends_on_id"] for p in preds]
        return st


@mcp.tool
def create_subtask(
    project_id: int,
    name: str,
    description: str = "",
    area_id: int | None = None,
    deadline: str | None = None,
    priority: int = 50,
    status_percent: StatusPercent = 0,
    predecessor_ids: list[int] | None = None,
    assigned_to: int | None = None,
    depends_on_project: bool = False,
) -> dict:
    """Legt eine Teilaufgabe an. Vorgaenger muessen zum Projekt gehoeren.
    Keine Zyklen oder transitiv redundanten Abhaengigkeiten."""
    user = _user()
    status_percent = _access(validate_status_percent, status_percent)
    if not name.strip():
        raise MCPToolError("empty_name", "name darf nicht leer sein", field="name")
    predecessor_ids = list(predecessor_ids or [])
    if depends_on_project and 0 not in predecessor_ids:
        predecessor_ids.append(0)
    with db_transaction() as db:
        db.execute("BEGIN IMMEDIATE")
        _, rights = _access(require_task_access, db, project_id, user, "create")
        if assigned_to not in (None, user["id"]) and not rights["can_manage"]:
            raise MCPToolError("permission_denied", "Nur Ersteller und Admins duerfen Zuweisungen aendern", field="assigned_to")
        if not db.execute("SELECT id FROM tasks WHERE id = ?", (project_id,)).fetchone():
            raise MCPToolError("task_not_found", f"Projekt {project_id} nicht gefunden", field="project_id")
        # naechste position_number ermitteln
        next_pos = db.execute(
            "SELECT COALESCE(MAX(position_number), 0) + 1 AS p FROM sub_tasks WHERE project_id = ?",
            (project_id,),
        ).fetchone()["p"]
        assigned = assigned_to if assigned_to is not None else user["id"]
        cursor = db.execute(
            """INSERT INTO sub_tasks
               (project_id, name, description, area_id, deadline, priority, status_percent,
                position_number, created_by, assigned_to, depends_on_project)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (project_id, name.strip(), description, area_id, deadline, priority, status_percent,
             next_pos, user["id"], assigned, 1 if depends_on_project else 0),
        )
        subtask_id = cursor.lastrowid
        _change_dependencies(set_subtask_predecessors, db, subtask_id, predecessor_ids)
        _access(refresh_parent_progress, db, project_id)
        row = db.execute(SUBTASK_LINK_SELECT + " WHERE st.id = ?", (subtask_id,)).fetchone()
    log_change(user, "sub_task", subtask_id, "create", {
        "project_id": project_id, "name": name, "description": description,
        "priority": priority, "deadline": deadline,
        "predecessor_ids": predecessor_ids, "assigned_to": assigned,
    })
    result = _subtask_to_dict(row)
    result["predecessor_ids"] = predecessor_ids
    return result


@mcp.tool
def update_subtask(
    subtask_id: int,
    name: str | None = None,
    description: str | None = None,
    area_id: int | None = None,
    deadline: str | None = None,
    priority: int | None = None,
    status_percent: StatusPercent | None = None,
    assigned_to: int | None = None,
    predecessor_ids: list[int] | None = None,
) -> dict:
    """Aktualisiert eine Teilaufgabe. Nur uebergebene Felder werden geaendert.
    predecessor_ids ersetzt KOMPLETT die bestehenden Vorgaenger.
    Keine Zyklen oder transitiv redundanten Abhaengigkeiten, auch nicht an anderen Teilaufgaben.
    Bei progress_automatic=true ist status_percent gesperrt (Fehlercode
    linked_project_progress_readonly). Stattdessen das Kind-Projekt bearbeiten."""
    user = _user()
    if status_percent is not None:
        status_percent = _access(validate_status_percent, status_percent)
    with db_transaction() as db:
        db.execute("BEGIN IMMEDIATE")
        before = db.execute(SUBTASK_LINK_SELECT + " WHERE st.id = ?", (subtask_id,)).fetchone()
        if not before:
            raise MCPToolError("subtask_not_found", f"Teilaufgabe {subtask_id} nicht gefunden", field="subtask_id")
        _, rights = _access(require_subtask_access, db, subtask_id, user, "edit")
        if status_percent is not None:
            _access(ensure_manual_progress, db, subtask_id)
        if assigned_to is not None and not rights["can_manage"]:
            raise MCPToolError("permission_denied", "Nur Ersteller und Admins duerfen Zuweisungen aendern", field="assigned_to")
        if predecessor_ids is not None:
            _access(require_task_access, db, before["project_id"], user, "structure")
        updates, values = [], []
        new_vals: dict = {}
        if name is not None:
            updates.append("name = ?"); values.append(name); new_vals["name"] = name
        if description is not None:
            updates.append("description_format = 'markdown'")
            updates.append("description = ?"); values.append(description); new_vals["description"] = description
        if area_id is not None:
            updates.append("area_id = ?"); values.append(area_id); new_vals["area_id"] = area_id
        if deadline is not None:
            updates.append("deadline = ?"); values.append(deadline); new_vals["deadline"] = deadline
        if priority is not None:
            updates.append("priority = ?"); values.append(priority); new_vals["priority"] = priority
        if status_percent is not None:
            updates.append("status_percent = ?"); values.append(status_percent); new_vals["status_percent"] = status_percent
        if assigned_to is not None:
            if assigned_to == 0:
                updates.append("assigned_to = NULL"); new_vals["assigned_to"] = None
            else:
                updates.append("assigned_to = ?"); values.append(assigned_to); new_vals["assigned_to"] = assigned_to
        if updates:
            values.append(subtask_id)
            db.execute(f"UPDATE sub_tasks SET {', '.join(updates)} WHERE id = ?", values)
        if predecessor_ids is not None:
            _change_dependencies(set_subtask_predecessors, db, subtask_id, predecessor_ids)
            new_vals["predecessor_ids"] = predecessor_ids
        if status_percent is not None:
            _access(refresh_parent_progress, db, before["project_id"])
        after = db.execute(SUBTASK_LINK_SELECT + " WHERE st.id = ?", (subtask_id,)).fetchone()
        preds = db.execute(
            "SELECT depends_on_id FROM sub_task_dependencies WHERE sub_task_id = ?",
            (subtask_id,),
        ).fetchall()
    changes = {k: {"old": before[k] if k in before.keys() else None, "new": v} for k, v in new_vals.items()}
    log_change(user, "sub_task", subtask_id, "update", changes)
    result = _subtask_to_dict(after)
    result["predecessor_ids"] = [p["depends_on_id"] for p in preds]
    return result


@mcp.tool
def delete_subtask(subtask_id: int) -> dict:
    """Loescht eine Teilaufgabe."""
    user = _user()
    with db_transaction() as db:
        db.execute("BEGIN IMMEDIATE")
        _access(require_subtask_access, db, subtask_id, user, "manage")
        row = db.execute("SELECT id, name, project_id FROM sub_tasks WHERE id = ?", (subtask_id,)).fetchone()
        if not row:
            raise MCPToolError("subtask_not_found", f"Teilaufgabe {subtask_id} nicht gefunden", field="subtask_id")
        db.execute("DELETE FROM sub_tasks WHERE id = ?", (subtask_id,))
        _access(refresh_parent_progress, db, row["project_id"])
    log_change(user, "sub_task", subtask_id, "delete", {
        "name": row["name"], "project_id": row["project_id"],
    })
    return {"deleted": True, "id": subtask_id}


@mcp.tool
def move_subtask(subtask_id: int, direction: str) -> dict:
    """Verschiebt eine Teilaufgabe um eine Position. direction muss 'up' oder 'down' sein.
    Abhaengigkeiten bleiben dabei unveraendert."""
    user = _user()
    direction = (direction or "").strip().lower()
    if direction not in {"up", "down"}:
        raise MCPToolError("invalid_direction", "direction muss 'up' oder 'down' sein", field="direction")

    with db_transaction() as db:
        subtask, _ = _access(require_subtask_access, db, subtask_id, user)
        _access(require_task_access, db, subtask["project_id"], user, "structure")
        current = db.execute(
            "SELECT id, project_id, position_number FROM sub_tasks WHERE id = ?",
            (subtask_id,),
        ).fetchone()
        if not current:
            raise MCPToolError("subtask_not_found", f"Teilaufgabe {subtask_id} nicht gefunden", field="subtask_id")

        rows = _ordered_subtask_rows(db, current["project_id"])
        ordered_ids = [row["id"] for row in rows]
        try:
            old_index = ordered_ids.index(subtask_id)
        except ValueError:
            raise MCPToolError("subtask_not_found", f"Teilaufgabe {subtask_id} nicht gefunden", field="subtask_id")

        target_index = old_index - 1 if direction == "up" else old_index + 1
        if target_index < 0 or target_index >= len(ordered_ids):
            result = _subtask_with_predecessors(db, subtask_id)
            return {
                "moved": False,
                "message": "Bereits am Rand",
                "subtask": result,
                "old_position": old_index + 1,
                "new_position": old_index + 1,
                "new_positions": {row["id"]: row["position_number"] for row in rows},
                "removed_dependencies": [],
            }

        swapped_with = ordered_ids[target_index]
        ordered_ids[old_index], ordered_ids[target_index] = ordered_ids[target_index], ordered_ids[old_index]
        new_positions = _renumber_project_subtasks(db, ordered_ids)
        result = _subtask_with_predecessors(db, subtask_id)

    log_change(user, "sub_task", subtask_id, "move", {
        "direction": direction,
        "old_position": old_index + 1,
        "new_position": target_index + 1,
        "old_position_number": current["position_number"],
        "swapped_with": swapped_with,
        "new_positions": new_positions,
        "dependencies_preserved": True,
    })
    return {
        "moved": True,
        "message": "Position geaendert",
        "subtask": result,
        "swapped_with": swapped_with,
        "old_position": old_index + 1,
        "new_position": target_index + 1,
        "new_positions": new_positions,
        "removed_dependencies": [],
        "dependencies_preserved": True,
    }


@mcp.tool
def set_subtask_position(subtask_id: int, position_number: int) -> dict:
    """Setzt eine Teilaufgabe auf eine absolute 1-basierte Position innerhalb ihres Projekts.
    Zu grosse Positionswerte werden auf die letzte Position geklemmt. Abhaengigkeiten bleiben unveraendert."""
    user = _user()
    if position_number is None or position_number < 1:
        raise MCPToolError("invalid_position", "position_number muss groesser oder gleich 1 sein", field="position_number")

    with db_transaction() as db:
        subtask, _ = _access(require_subtask_access, db, subtask_id, user)
        _access(require_task_access, db, subtask["project_id"], user, "structure")
        current = db.execute(
            "SELECT id, project_id, position_number FROM sub_tasks WHERE id = ?",
            (subtask_id,),
        ).fetchone()
        if not current:
            raise MCPToolError("subtask_not_found", f"Teilaufgabe {subtask_id} nicht gefunden", field="subtask_id")

        rows = _ordered_subtask_rows(db, current["project_id"])
        ordered_ids = [row["id"] for row in rows]
        try:
            old_index = ordered_ids.index(subtask_id)
        except ValueError:
            raise MCPToolError("subtask_not_found", f"Teilaufgabe {subtask_id} nicht gefunden", field="subtask_id")

        ordered_ids.pop(old_index)
        target_index = min(position_number, len(rows)) - 1
        ordered_ids.insert(target_index, subtask_id)
        new_positions = _renumber_project_subtasks(db, ordered_ids)
        result = _subtask_with_predecessors(db, subtask_id)

    new_position = new_positions[subtask_id]
    moved = old_index + 1 != new_position or current["position_number"] != new_position
    log_change(user, "sub_task", subtask_id, "move", {
        "requested_position": position_number,
        "old_position": old_index + 1,
        "new_position": new_position,
        "old_position_number": current["position_number"],
        "new_positions": new_positions,
        "dependencies_preserved": True,
    })
    return {
        "moved": moved,
        "message": "Position geaendert",
        "subtask": result,
        "requested_position": position_number,
        "old_position": old_index + 1,
        "new_position": new_position,
        "new_positions": new_positions,
        "removed_dependencies": [],
        "dependencies_preserved": True,
    }


# ============================================================
# Abhaengigkeiten
# ============================================================

@mcp.tool
def add_dependency(subtask_id: int, depends_on_id: int) -> dict:
    """Fuegt eine Abhaengigkeit hinzu: subtask_id wartet auf depends_on_id.
    depends_on_id=0 bedeutet 'haengt vom Projektknoten ab'.
    Zyklen und transitiv redundante Verbindungen werden abgewiesen."""
    user = _user()
    with db_transaction() as db:
        subtask, _ = _access(require_subtask_access, db, subtask_id, user)
        _access(require_task_access, db, subtask["project_id"], user, "structure")
        _change_dependencies(add_subtask_dependency, db, subtask_id, depends_on_id, allow_existing=True)
    log_change(user, "dependency", subtask_id, "create", {"depends_on_id": depends_on_id})
    return {"added": True, "subtask_id": subtask_id, "depends_on_id": depends_on_id}


@mcp.tool
def remove_dependency(subtask_id: int, depends_on_id: int) -> dict:
    """Entfernt eine Abhaengigkeit. depends_on_id=0 entfernt die Abhaengigkeit vom Projektknoten."""
    user = _user()
    with db_transaction() as db:
        subtask, _ = _access(require_subtask_access, db, subtask_id, user)
        _access(require_task_access, db, subtask["project_id"], user, "structure")
        if depends_on_id == 0:
            db.execute(
                "UPDATE sub_tasks SET depends_on_project = 0 WHERE id = ?",
                (subtask_id,),
            )
        else:
            db.execute(
                "DELETE FROM sub_task_dependencies WHERE sub_task_id = ? AND depends_on_id = ?",
                (subtask_id, depends_on_id),
            )
    log_change(user, "dependency", subtask_id, "delete", {"depends_on_id": depends_on_id})
    return {"removed": True, "subtask_id": subtask_id, "depends_on_id": depends_on_id}


# ============================================================
# Notes und Handoffs
# ============================================================

def _access(operation, *args, **kwargs):
    try:
        return operation(*args, **kwargs)
    except HTTPException as exc:
        raise tool_error_from_http(exc) from exc


def _task_readable(db, task, user: dict) -> bool:
    return require_task_access(db, task["id"], user, None)[1]["can_read"]


def _require_task_read_access(db, task_id: int, user: dict):
    return _access(require_task_access, db, task_id, user)[0]


def _can_read_task(db, task_id: int, user: dict) -> bool:
    try:
        return require_task_access(db, task_id, user, None)[1]["can_read"]
    except HTTPException:
        return False


def _subtask_readable(db, row, user: dict) -> bool:
    sid = row["subtask_id"] if "subtask_id" in row.keys() else row["id"]
    return _can_read_subtask(db, row["project_id"], sid, user)


def _require_subtask_read_access(db, task_id: int, subtask_id: int, user: dict):
    return _access(require_subtask_access, db, subtask_id, user, task_id=task_id)[0]


def _can_read_subtask(db, task_id: int, subtask_id: int, user: dict) -> bool:
    try:
        return require_subtask_access(db, subtask_id, user, None, task_id=task_id)[1]["can_read"]
    except HTTPException:
        return False


def _task_visibility_sql(task_alias: str, user: dict) -> tuple[str, list[int]]:
    assert task_alias == "t"
    return task_visibility_sql(user)


def _subtask_visibility_sql(task_alias: str, subtask_alias: str, user: dict) -> tuple[str, list[int]]:
    assert (task_alias, subtask_alias) == ("t", "st")
    return task_visibility_sql(user, subtask=True)


def _make_handoff_id(scope: str, local_id: int) -> str:
    return f"{scope}:{local_id}"


def _parse_handoff_id(handoff_id: str) -> tuple[str, int]:
    value = str(handoff_id).strip()
    if ":" not in value:
        raise MCPToolError("invalid_handoff_id", "handoff_id muss typisiert sein, z.B. 'task:123' oder 'subtask:456'", field="handoff_id")
    scope, raw_id = value.split(":", 1)
    if scope not in {"task", "subtask"}:
        raise MCPToolError("invalid_handoff_id", "handoff_id muss mit 'task:' oder 'subtask:' beginnen", field="handoff_id")
    try:
        local_id = int(raw_id)
    except ValueError as exc:
        raise MCPToolError("invalid_handoff_id", "handoff_id enthaelt keine gueltige numerische ID", field="handoff_id") from exc
    if local_id < 1:
        raise MCPToolError("invalid_handoff_id", "handoff_id muss eine positive ID enthalten", field="handoff_id")
    return scope, local_id


@mcp.tool(name="note.list")
def note_list(task_id: int, subtask_id: int | None = None) -> list[dict]:
    """Listet editierbare User-Notizen einer Aufgabe oder Teilaufgabe.

    Wenn subtask_id gesetzt ist, werden die Notizen dieser Teilaufgabe geliefert,
    sonst die der Aufgabe/des Projekts. Neueste Notizen stehen zuerst.
    """
    user = _user()
    with db_query() as db:
        if subtask_id is not None:
            _require_subtask_read_access(db, task_id, subtask_id, user)
            rows = db.execute(
                """SELECT sn.user_id, sn.content, sn.content_format, sn.updated_at,
                          u.vorname || ' ' || u.nachname AS user_name,
                          u.auth_source
                   FROM sub_task_notes sn
                   JOIN users u ON sn.user_id = u.id
                   WHERE sn.sub_task_id = ?
                   ORDER BY sn.updated_at DESC""",
                (subtask_id,),
            ).fetchall()
        else:
            _require_task_read_access(db, task_id, user)
            rows = db.execute(
                """SELECT tn.user_id, tn.content, tn.content_format, tn.updated_at,
                          u.vorname || ' ' || u.nachname AS user_name,
                          u.auth_source
                   FROM task_notes tn
                   JOIN users u ON tn.user_id = u.id
                   WHERE tn.task_id = ?
                   ORDER BY tn.updated_at DESC""",
                (task_id,),
            ).fetchall()
        return [
            {
                "user_id": r["user_id"],
                "user_name": (r["user_name"] or "").strip(),
                "auth_source": r["auth_source"] or "local",
                "content": r["content"] or "",
                "content_format": r["content_format"],
                "updated_at": r["updated_at"],
            }
            for r in rows
        ]


@mcp.tool(name="note.write")
def note_write(task_id: int, content: str, subtask_id: int | None = None) -> dict:
    """Schreibt oder aktualisiert die aktuelle eigene User-Notiz.

    Pro User gibt es je Aufgabe/Teilaufgabe genau eine solche Notiz.
    Wiederholte Aufrufe ueberschreiben diese Notiz.
    """
    user = _user()
    with db_transaction() as db:
        if subtask_id is not None:
            _access(require_subtask_access, db, subtask_id, user, "contribute", task_id=task_id)
            db.execute(
                """INSERT INTO sub_task_notes (sub_task_id, user_id, content, updated_at)
                   VALUES (?, ?, ?, datetime('now'))
                   ON CONFLICT(sub_task_id, user_id) DO UPDATE SET
                       content_format = 'markdown', content = excluded.content, updated_at = datetime('now')""",
                (subtask_id, user["id"], content),
            )
            entity_id = subtask_id
            entity_type = "sub_task_note"
        else:
            _access(require_task_access, db, task_id, user, "contribute")
            db.execute(
                """INSERT INTO task_notes (task_id, user_id, content, updated_at)
                   VALUES (?, ?, ?, datetime('now'))
                   ON CONFLICT(task_id, user_id) DO UPDATE SET
                       content_format = 'markdown', content = excluded.content, updated_at = datetime('now')""",
                (task_id, user["id"], content),
            )
            entity_id = task_id
            entity_type = "task_note"
    log_change(user, entity_type, entity_id, "update", {"content_len": len(content)})
    return {"saved": True, "task_id": task_id, "subtask_id": subtask_id}


@mcp.tool(name="note.update")
def note_update(task_id: int, user_id: int, content: str, subtask_id: int | None = None) -> dict:
    """Admin: bestehende Notiz eines Users bearbeiten, ohne den Autor zu aendern.

    user_id stammt aus note.list. Fuer die eigene Notiz weiterhin note.write nutzen.
    """
    try:
        return update_note_as_admin(task_id, content, _user(), subtask_id=subtask_id, note_user_id=user_id)
    except HTTPException as exc:
        raise tool_error_from_http(exc) from exc


@mcp.tool(name="note.delete")
def note_delete(task_id: int, subtask_id: int | None = None) -> dict:
    """Loescht die aktuelle eigene User-Notiz des aufrufenden MCP-Users."""
    user = _user()
    with db_transaction() as db:
        if subtask_id is not None:
            _access(require_subtask_access, db, subtask_id, user, "contribute", task_id=task_id)
            existing = db.execute(
                "SELECT id, content FROM sub_task_notes WHERE sub_task_id = ? AND user_id = ?",
                (subtask_id, user["id"]),
            ).fetchone()
            if not existing:
                return {"deleted": False, "task_id": task_id, "subtask_id": subtask_id}
            db.execute(
                "DELETE FROM sub_task_notes WHERE sub_task_id = ? AND user_id = ?",
                (subtask_id, user["id"]),
            )
            entity_id = subtask_id
            entity_type = "sub_task_note"
        else:
            _access(require_task_access, db, task_id, user, "contribute")
            existing = db.execute(
                "SELECT id, content FROM task_notes WHERE task_id = ? AND user_id = ?",
                (task_id, user["id"]),
            ).fetchone()
            if not existing:
                return {"deleted": False, "task_id": task_id, "subtask_id": None}
            db.execute(
                "DELETE FROM task_notes WHERE task_id = ? AND user_id = ?",
                (task_id, user["id"]),
            )
            entity_id = task_id
            entity_type = "task_note"

    log_change(user, entity_type, entity_id, "delete", {"content_len": len(existing["content"] or "")})
    return {"deleted": True, "task_id": task_id, "subtask_id": subtask_id}


@mcp.tool(name="handoff.list")
def handoff_list(task_id: int, subtask_id: int | None = None, limit: int = 100) -> list[dict]:
    """Listet Handoffs einer Aufgabe oder Teilaufgabe.

    Fuer Fortschritt, Uebergaben, Entscheidungen, Testergebnisse und
    Audit-Zusammenfassungen verwenden. Neueste Handoffs werden zuerst
    geliefert. Wenn subtask_id gesetzt ist, muss sie zu task_id gehoeren.
    """
    limit = max(1, min(limit, 200))
    user = _user()
    with db_query() as db:
        if subtask_id is not None:
            _require_subtask_read_access(db, task_id, subtask_id, user)
            rows = db.execute(
                """SELECT sne.id, sne.sub_task_id, sne.user_id, sne.content, sne.content_format, sne.created_at,
                          u.vorname || ' ' || u.nachname AS user_name,
                          u.auth_source
                   FROM sub_task_note_entries sne
                   JOIN users u ON sne.user_id = u.id
                   WHERE sne.sub_task_id = ?
                   ORDER BY sne.created_at DESC, sne.id DESC
                   LIMIT ?""",
                (subtask_id, limit),
            ).fetchall()
        else:
            _require_task_read_access(db, task_id, user)
            rows = db.execute(
                """SELECT tne.id, tne.task_id, tne.user_id, tne.content, tne.content_format, tne.created_at,
                          u.vorname || ' ' || u.nachname AS user_name,
                          u.auth_source
                   FROM task_note_entries tne
                   JOIN users u ON tne.user_id = u.id
                   WHERE tne.task_id = ?
                   ORDER BY tne.created_at DESC, tne.id DESC
                   LIMIT ?""",
                (task_id, limit),
            ).fetchall()
        return [
            {
                "handoff_id": _make_handoff_id("subtask" if "sub_task_id" in r.keys() else "task", r["id"]),
                "local_id": r["id"],
                "task_id": r["task_id"] if "task_id" in r.keys() else task_id,
                "subtask_id": r["sub_task_id"] if "sub_task_id" in r.keys() else None,
                "user_id": r["user_id"],
                "user_name": (r["user_name"] or "").strip(),
                "auth_source": r["auth_source"] or "local",
                "content": r["content"] or "",
                "content_format": r["content_format"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]


@mcp.tool(name="handoff.add")
def handoff_add(task_id: int, content: str, subtask_id: int | None = None) -> dict:
    """Fuegt einen neuen Handoff hinzu.

    Fuer Fortschritt, Uebergaben, Entscheidungen, Testergebnisse, Gate-Notizen
    und Audit-Zusammenfassungen verwenden. Jeder Aufruf erzeugt einen eigenen
    Verlaufseintrag und ueberschreibt keine User-Notiz.
    """
    user = _user()
    if not content.strip():
        raise MCPToolError("empty_content", "content darf nicht leer sein", field="content")

    with db_transaction() as db:
        if subtask_id is not None:
            _access(require_subtask_access, db, subtask_id, user, "contribute", task_id=task_id)
            cursor = db.execute(
                """INSERT INTO sub_task_note_entries (sub_task_id, user_id, content)
                   VALUES (?, ?, ?)""",
                (subtask_id, user["id"], content),
            )
            entity_id = subtask_id
            entity_type = "sub_task_handoff"
            scope = "subtask"
        else:
            _access(require_task_access, db, task_id, user, "contribute")
            cursor = db.execute(
                """INSERT INTO task_note_entries (task_id, user_id, content)
                   VALUES (?, ?, ?)""",
                (task_id, user["id"], content),
            )
            entity_id = task_id
            entity_type = "task_handoff"
            scope = "task"
        local_id = cursor.lastrowid
        handoff_id = _make_handoff_id(scope, local_id)

    log_change(user, entity_type, entity_id, "create", {
        "handoff_id": handoff_id,
        "local_id": local_id,
        "owner_user_id": user["id"],
        "content_len": len(content),
    })
    return {
        "saved": True,
        "handoff_id": handoff_id,
        "local_id": local_id,
        "task_id": task_id,
        "subtask_id": subtask_id,
    }


@mcp.tool(name="handoff.update")
def handoff_update(task_id: int, handoff_id: str, content: str, subtask_id: int | None = None) -> dict:
    """Admin: Handoff-Inhalt korrigieren; Autor und Erstellungszeit bleiben erhalten.

    handoff_id stammt aus handoff.list ('task:123' oder 'subtask:456').
    Eine angegebene subtask_id muss zum Handoff und zum Projekt gehoeren.
    """
    user = _user()
    if not user.get("is_admin"):
        raise MCPToolError("permission_denied", "Nur Admins duerfen Handoffs bearbeiten")
    scope, local_id = _parse_handoff_id(handoff_id)
    if scope == "task" and subtask_id is not None:
        raise MCPToolError("invalid_handoff_target", "task-Handoff darf nicht mit subtask_id bearbeitet werden", fields=["handoff_id", "subtask_id"])
    if scope == "subtask" and subtask_id is None:
        with db_query() as db:
            row = db.execute(
                """SELECT sne.sub_task_id FROM sub_task_note_entries sne
                   JOIN sub_tasks st ON st.id = sne.sub_task_id
                   WHERE sne.id = ? AND st.project_id = ?""", (local_id, task_id),
            ).fetchone()
            if not row:
                raise MCPToolError("handoff_not_found", "Handoff nicht gefunden", field="handoff_id")
            subtask_id = row["sub_task_id"]
    try:
        return update_note_as_admin(task_id, content, user, subtask_id=subtask_id, entry_id=local_id)
    except HTTPException as exc:
        raise tool_error_from_http(exc) from exc


@mcp.tool(name="handoff.delete")
def handoff_delete(task_id: int, handoff_id: str, subtask_id: int | None = None) -> dict:
    """Loescht einen Handoff aus dem Verlauf.

    Normale MCP-User duerfen nur eigene Handoffs loeschen. Admin-User duerfen
    auch fremde Handoffs loeschen. Wenn subtask_id gesetzt ist, muss sie zu
    task_id gehoeren.
    """
    user = _user()
    scope, local_id = _parse_handoff_id(handoff_id)
    with db_transaction() as db:
        if scope == "subtask":
            if subtask_id is None:
                row = db.execute(
                    """SELECT sne.id, sne.user_id, sne.content,
                              st.id AS subtask_id,
                              st.project_id,
                              st.assigned_to AS subtask_assigned_to,
                              t.created_by AS task_created_by, t.assigned_to AS task_assigned_to
                       FROM sub_task_note_entries sne
                       JOIN sub_tasks st ON st.id = sne.sub_task_id
                       JOIN tasks t ON t.id = st.project_id
                       WHERE sne.id = ? AND st.project_id = ?""",
                    (local_id, task_id),
                ).fetchone()
                if not row:
                    _require_task_read_access(db, task_id, user)
                    return {"deleted": False, "handoff_id": handoff_id, "task_id": task_id, "subtask_id": None}
                if not _subtask_readable(db, row, user):
                    raise MCPToolError("permission_denied", f"Keine Leseberechtigung fuer Aufgabe/Projekt {task_id}", field="task_id")
                subtask_id = row["subtask_id"]
                existing = row
            else:
                _require_subtask_read_access(db, task_id, subtask_id, user)
                existing = db.execute(
                    "SELECT id, user_id, content FROM sub_task_note_entries WHERE id = ? AND sub_task_id = ?",
                    (local_id, subtask_id),
                ).fetchone()

            if not existing:
                return {"deleted": False, "handoff_id": handoff_id, "task_id": task_id, "subtask_id": subtask_id}
            _access(require_subtask_access, db, subtask_id, user, "contribute", task_id=task_id)
            if existing["user_id"] != user["id"] and not user.get("is_admin"):
                raise MCPToolError("permission_denied", "Nur eigene Handoffs koennen geloescht werden", field="handoff_id")
            db.execute(
                "DELETE FROM sub_task_note_entries WHERE id = ? AND sub_task_id = ?",
                (local_id, subtask_id),
            )
            entity_id = subtask_id
            entity_type = "sub_task_handoff"
        else:
            if subtask_id is not None:
                raise MCPToolError("invalid_handoff_target", "task-Handoff darf nicht mit subtask_id geloescht werden", fields=["handoff_id", "subtask_id"])
            _access(require_task_access, db, task_id, user, "contribute")
            existing = db.execute(
                "SELECT id, user_id, content FROM task_note_entries WHERE id = ? AND task_id = ?",
                (local_id, task_id),
            ).fetchone()
            if not existing:
                return {"deleted": False, "handoff_id": handoff_id, "task_id": task_id, "subtask_id": None}
            if existing["user_id"] != user["id"] and not user.get("is_admin"):
                raise MCPToolError("permission_denied", "Nur eigene Handoffs koennen geloescht werden", field="handoff_id")
            db.execute(
                "DELETE FROM task_note_entries WHERE id = ? AND task_id = ?",
                (local_id, task_id),
            )
            entity_id = task_id
            entity_type = "task_handoff"

    log_change(user, entity_type, entity_id, "delete", {
        "handoff_id": handoff_id,
        "local_id": local_id,
        "owner_user_id": existing["user_id"],
        "deleted_by_admin": bool(user.get("is_admin") and existing["user_id"] != user["id"]),
        "content_len": len(existing["content"] or ""),
    })
    return {"deleted": True, "handoff_id": handoff_id, "task_id": task_id, "subtask_id": subtask_id}


# ============================================================
# Dateien (lokale Ablage und WebDAV)
# ============================================================

MCP_FILE_MAX_BYTES = 1024 * 1024


def _file_operation(operation, *args, **kwargs):
    """Dateifehler ohne interne Serverpfade als MCP-Fehler zurueckgeben."""
    try:
        return operation(*args, **kwargs)
    except HTTPException as exc:
        raise tool_error_from_http(exc) from exc
    except (FileNotFoundError, NotADirectoryError) as exc:
        raise MCPToolError("file_not_found", "Datei oder Verzeichnis nicht gefunden", field="path") from exc
    except (FileExistsError, IsADirectoryError) as exc:
        raise MCPToolError("file_exists", "Zieldatei oder Verzeichnis existiert bereits", field="path") from exc
    except httpx.TimeoutException as exc:
        logger.exception("Zeitueberschreitung bei MCP-Dateioperation")
        raise MCPToolError("storage_timeout", "Zeitueberschreitung beim Zugriff auf die Dateiablage") from exc
    except httpx.RequestError as exc:
        logger.exception("Verbindungsfehler bei MCP-Dateioperation")
        raise MCPToolError("storage_unavailable", "Dateiablage ist momentan nicht erreichbar") from exc
    except (OSError, RuntimeError) as exc:
        logger.exception("MCP-Dateioperation fehlgeschlagen")
        raise MCPToolError("file_operation_failed", "Dateioperation fehlgeschlagen") from exc


def _file_context(task_id: int, path: str, *, write: bool = False, allow_empty: bool = False):
    user = _user()
    try:
        storage = _access(get_task_storage, task_id, user, write=write)
    except MCPToolError as exc:
        if exc.code != "storage_not_configured":
            raise
        # Nur MCP bekommt die Einrichtungsanleitung; REST/UI-detail bleibt
        # unveraendert. get_task_storage hat die aktuellen Rechte schon geprueft.
        raise MCPToolError(
            exc.code,
            f"{exc.message}. In der Tareas-Weboberflaeche die Aufgabe/das Projekt "
            "oeffnen und ueber den Button 'Dateiablage' eine lokale Ablage oder "
            "WebDAV zuordnen. Bei bestehender Ablage liegt die Konfiguration im "
            "Header des Dateibrowsers. WebDAV muss zuvor von einem Admin "
            "eingerichtet sein. Die Einrichtung im Browser braucht "
            "Bearbeitungsrechte (Ersteller, Admin oder Bearbeitungsfreigabe); "
            "eine reine Zuweisung oder Lesefreigabe reicht dort nicht. "
            "Falls diese Rechte fehlen, den Ersteller oder einen Admin um "
            "Einrichtung bitten. MCP-Dateiwerkzeuge legen keine Ablage "
            "automatisch an.",
            field=exc.field,
            fields=exc.fields,
        ) from exc
    path = _access(safe_rel_path, path, allow_empty=allow_empty)
    return user, storage, path


@mcp.tool(name="file.list", annotations={"readOnlyHint": True})
def file_list(task_id: int, path: str = "", offset: int = 0, limit: int = 200) -> dict:
    """Dateien/Ordner der Projektablage auflisten. task_id ist die Aufgaben-/Projekt-ID.

    path ist relativ zur konfigurierten Ablage, leer fuer deren Wurzel.
    Benoetigt Leserechte. Maximal 500 Eintraege pro Seite, next_offset fuer weitere.
    Keine automatische Einrichtung einer Ablage.
    """
    _, storage, path = _file_context(task_id, path, allow_empty=True)
    if offset < 0 or not 1 <= limit <= 500:
        raise MCPToolError("invalid_pagination", "offset muss >= 0 sein, limit zwischen 1 und 500",
                           fields=[field for field, invalid in (("offset", offset < 0), ("limit", not 1 <= limit <= 500)) if invalid])
    entries = _file_operation(storage.list_directory, path)
    items = []
    for entry in entries[offset:offset + limit]:
        item = {key: entry[key] for key in ("name", "type", "size", "last_modified", "mime_type") if key in entry}
        item["path"] = f"{path}/{entry['name']}" if path else entry["name"]
        items.append(item)
    end = offset + len(items)
    return {"task_id": task_id, "path": path, "storage_type": storage.kind,
            "can_write": storage.can_write, "items": items, "total": len(entries),
            "next_offset": end if end < len(entries) else None,
            "max_file_bytes": MCP_FILE_MAX_BYTES}


@mcp.tool(name="file.read", annotations={"readOnlyHint": True})
def file_read(task_id: int, path: str, encoding: Literal["auto", "utf-8", "base64"] = "auto") -> dict:
    """Datei lesen (Leserechte, max. 1 MiB). Pfad relativ zur Projektablage.

    auto liefert UTF-8-Text, bei binaeren/nicht-UTF-8-Inhalten Base64.
    encoding im Ergebnis gibt das Format von content an. base64 erzwingt die
    verlustfreie binaere Uebertragung. Groessere Dateien ueber die Web-UI abrufen.
    """
    _, storage, path = _file_context(task_id, path)
    if encoding not in ("auto", "utf-8", "base64"):
        raise MCPToolError("invalid_encoding", "encoding muss auto, utf-8 oder base64 sein", field="encoding")
    raw, content_type = _file_operation(storage.get_file, path, max_bytes=MCP_FILE_MAX_BYTES)
    text = None
    if encoding != "base64":
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            if encoding == "utf-8":
                raise MCPToolError("invalid_encoding", "Datei ist kein UTF-8-Text. encoding=base64 verwenden", field="encoding") from exc
    if encoding == "auto" and text is not None and any(ord(char) < 32 and char not in "\t\r\n" for char in text):
        text = None
    return {"task_id": task_id, "path": path, "storage_type": storage.kind,
            "content": text if text is not None else base64.b64encode(raw).decode("ascii"),
            "encoding": "utf-8" if text is not None else "base64", "content_type": content_type, "size": len(raw)}


@mcp.tool(name="file.write", annotations={"readOnlyHint": False, "destructiveHint": True})
def file_write(task_id: int, path: str, content: str, encoding: Literal["utf-8", "base64"] = "utf-8") -> dict:
    """Datei anlegen oder vollstaendig ersetzen (Bearbeitungsrechte, max. 1 MiB).

    path ist ein relativer Dateipfad. Elternordner muessen bereits existieren
    (file.mkdir). Text als utf-8, binaere Dateien als strikt kodiertes Base64.
    Vor dem Ersetzen vorhandene Inhalte mit file.read pruefen.
    """
    user, storage, path = _file_context(task_id, path, write=True)
    if encoding not in ("utf-8", "base64"):
        raise MCPToolError("invalid_encoding", "encoding muss utf-8 oder base64 sein", field="encoding")
    max_chars = MCP_FILE_MAX_BYTES if encoding == "utf-8" else 4 * ((MCP_FILE_MAX_BYTES + 2) // 3)
    if len(content) > max_chars:
        raise MCPToolError("file_too_large", "Datei zu gross (max. 1 MiB pro MCP-Aufruf)", field="content")
    try:
        raw = content.encode("utf-8") if encoding == "utf-8" else base64.b64decode(content, validate=True)
    except (UnicodeError, ValueError, binascii.Error) as exc:
        raise MCPToolError("invalid_content", "Ungueltiger Dateiinhalt fuer die angegebene Kodierung", field="content") from exc
    if len(raw) > MCP_FILE_MAX_BYTES:
        raise MCPToolError("file_too_large", "Datei zu gross (max. 1 MiB pro MCP-Aufruf)", field="content")
    content_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
    _file_operation(storage.upload_file, path, raw, content_type)
    log_change(user, "task", task_id, "file_upload", {"path": path, "size": len(raw), "storage_type": storage.kind})
    return {"task_id": task_id, "path": path, "storage_type": storage.kind, "size": len(raw), "written": True}


@mcp.tool(name="file.mkdir", annotations={"readOnlyHint": False, "destructiveHint": False})
def file_mkdir(task_id: int, path: str) -> dict:
    """Ordner anlegen (Bearbeitungsrechte). Relativer Pfad, Elternordner muessen existieren."""
    user, storage, path = _file_context(task_id, path, write=True)
    _file_operation(storage.create_directory, path)
    log_change(user, "task", task_id, "file_mkdir", {"path": path, "storage_type": storage.kind})
    return {"task_id": task_id, "path": path, "created": True}


def _file_argument_path(value: str, field: str) -> str:
    """Pfadvalidierung mit dem tatsaechlichen Argumentnamen des Werkzeugs."""
    try:
        return safe_rel_path(value, allow_empty=False)
    except HTTPException as exc:
        error = tool_error_from_http(exc)
        error.field = field
        raise error from exc


@mcp.tool(name="file.move", annotations={"readOnlyHint": False, "destructiveHint": True})
def file_move(task_id: int, source: str, destination: str) -> dict:
    """Datei/Ordner innerhalb derselben Projektablage verschieben oder umbenennen.

    Benoetigt Bearbeitungsrechte. Beide Pfade relativ, Ziel darf nicht existieren.
    """
    user, storage, _ = _file_context(task_id, "", write=True, allow_empty=True)
    source = _file_argument_path(source, "source")
    destination = _file_argument_path(destination, "destination")
    _file_operation(storage.move_item, source, destination)
    log_change(user, "task", task_id, "file_move", {"source": source, "destination": destination, "storage_type": storage.kind})
    return {"task_id": task_id, "source": source, "destination": destination, "moved": True}


@mcp.tool(name="file.delete", annotations={"readOnlyHint": False, "destructiveHint": True})
def file_delete(task_id: int, path: str) -> dict:
    """Datei oder Ordner samt Inhalt dauerhaft loeschen (Bearbeitungsrechte).

    Relativer Pfad zur Projektablage. Die Wurzel der Ablage kann nicht geloescht werden.
    """
    user, storage, path = _file_context(task_id, path, write=True)
    _file_operation(storage.delete_item, path)
    log_change(user, "task", task_id, "file_delete", {"path": path, "storage_type": storage.kind})
    return {"task_id": task_id, "path": path, "deleted": True}


# ============================================================
# Hilfs-Tools
# ============================================================

@mcp.tool
def list_users() -> list[dict]:
    """Listet alle User (id, name, auth_source) - fuer Zuweisungs-Operationen."""
    with db_query() as db:
        rows = db.execute(
            """SELECT id, username, vorname, nachname, email, auth_source, is_active
               FROM users WHERE is_active = 1 ORDER BY username"""
        ).fetchall()
        return [
            {
                "id": r["id"],
                "username": r["username"],
                "display_name": f"{r['vorname']} {r['nachname']}".strip() or r["username"],
                "email": r["email"] or "",
                "auth_source": r["auth_source"] or "local",
            }
            for r in rows
        ]


@mcp.tool
def list_areas() -> list[dict]:
    """Listet alle Bereiche (Kategorien fuer Teilaufgaben)."""
    with db_query() as db:
        rows = db.execute("SELECT id, name FROM areas ORDER BY name").fetchall()
        return [{"id": r["id"], "name": r["name"]} for r in rows]


@mcp.tool
def search(query: str, limit: int = 20) -> list[dict]:
    """Volltextsuche ueber Name, Description, Notes und Handoffs.

    Liefert Treffer mit type ('task'|'sub_task'|'task_note'|'sub_task_note'|
    'task_handoff'|'sub_task_handoff') und kurzem Snippet.
    """
    if not query.strip():
        return []
    q = f"%{query.strip()}%"
    limit = max(1, min(limit, 200))
    results: list[dict] = []
    user = _user()
    with db_query() as db:
        task_vis_sql, task_vis_params = _task_visibility_sql("t", user)
        for r in db.execute(
            """SELECT id, name, description, created_by, assigned_to
               FROM tasks t
               WHERE (name LIKE ? OR description LIKE ?)
                 AND """ + task_vis_sql + """
               LIMIT ?""",
            (q, q, *task_vis_params, limit),
        ).fetchall():
            results.append({
                "type": "task", "id": r["id"], "name": r["name"],
                "snippet": (r["description"] or "")[:200],
            })
        subtask_vis_sql, subtask_vis_params = _subtask_visibility_sql("t", "st", user)
        for r in db.execute(
            """SELECT st.id, st.project_id, st.name, st.description,
                      st.assigned_to AS subtask_assigned_to,
                      t.created_by AS task_created_by
               FROM sub_tasks st
               JOIN tasks t ON t.id = st.project_id
               WHERE (st.name LIKE ? OR st.description LIKE ?)
                 AND """ + subtask_vis_sql + """
               LIMIT ?""",
            (q, q, *subtask_vis_params, limit),
        ).fetchall():
            results.append({
                "type": "sub_task", "id": r["id"], "project_id": r["project_id"],
                "name": r["name"], "snippet": (r["description"] or "")[:200],
            })
        for r in db.execute(
            """SELECT tn.task_id, tn.user_id, tn.content, u.username
               FROM task_notes tn
               JOIN users u ON tn.user_id = u.id
               JOIN tasks t ON t.id = tn.task_id
               WHERE tn.content LIKE ?
                 AND """ + task_vis_sql + """
               LIMIT ?""",
            (q, *task_vis_params, limit),
        ).fetchall():
            results.append({
                "type": "task_note", "task_id": r["task_id"], "user": r["username"],
                "snippet": (r["content"] or "")[:200],
            })
        for r in db.execute(
            """SELECT sn.sub_task_id, st.project_id, sn.user_id, sn.content, u.username
               FROM sub_task_notes sn
               JOIN users u ON sn.user_id = u.id
               JOIN sub_tasks st ON st.id = sn.sub_task_id
               JOIN tasks t ON t.id = st.project_id
               WHERE sn.content LIKE ?
                 AND """ + subtask_vis_sql + """
               LIMIT ?""",
            (q, *subtask_vis_params, limit),
        ).fetchall():
            results.append({
                "type": "sub_task_note",
                "task_id": r["project_id"],
                "project_id": r["project_id"],
                "subtask_id": r["sub_task_id"],
                "user": r["username"],
                "snippet": (r["content"] or "")[:200],
            })
        for r in db.execute(
            """SELECT tne.id, tne.task_id, tne.user_id, tne.content, u.username
               FROM task_note_entries tne
               JOIN users u ON tne.user_id = u.id
               JOIN tasks t ON t.id = tne.task_id
               WHERE tne.content LIKE ?
                 AND """ + task_vis_sql + """
               LIMIT ?""",
            (q, *task_vis_params, limit),
        ).fetchall():
            results.append({
                "type": "task_handoff",
                "handoff_id": _make_handoff_id("task", r["id"]),
                "local_id": r["id"],
                "task_id": r["task_id"], "user": r["username"],
                "snippet": (r["content"] or "")[:200],
            })
        for r in db.execute(
            """SELECT sne.id, sne.sub_task_id, st.project_id, sne.user_id, sne.content, u.username
               FROM sub_task_note_entries sne
               JOIN users u ON sne.user_id = u.id
               JOIN sub_tasks st ON st.id = sne.sub_task_id
               JOIN tasks t ON t.id = st.project_id
               WHERE sne.content LIKE ?
                 AND """ + subtask_vis_sql + """
               LIMIT ?""",
            (q, *subtask_vis_params, limit),
        ).fetchall():
            results.append({
                "type": "sub_task_handoff",
                "handoff_id": _make_handoff_id("subtask", r["id"]),
                "local_id": r["id"],
                "task_id": r["project_id"],
                "project_id": r["project_id"],
                "subtask_id": r["sub_task_id"],
                "user": r["username"],
                "snippet": (r["content"] or "")[:200],
            })
    return results[:limit]


@mcp.tool
def assign_self(task_id: int | None = None, subtask_id: int | None = None) -> dict:
    """Weist die Aufgabe oder Teilaufgabe dem aufrufenden MCP-User zu."""
    user = _user()
    if task_id is None and subtask_id is None:
        raise MCPToolError("missing_target", "task_id oder subtask_id erforderlich", fields=["task_id", "subtask_id"])
    with db_transaction() as db:
        if subtask_id is not None:
            _access(require_subtask_access, db, subtask_id, user, "manage", task_id=task_id)
            if not db.execute("SELECT id FROM sub_tasks WHERE id = ?", (subtask_id,)).fetchone():
                raise MCPToolError("subtask_not_found", f"Teilaufgabe {subtask_id} nicht gefunden", field="subtask_id")
            db.execute("UPDATE sub_tasks SET assigned_to = ? WHERE id = ?", (user["id"], subtask_id))
            entity_type, entity_id = "sub_task", subtask_id
        else:
            _access(require_task_access, db, task_id, user, "manage")
            if not db.execute("SELECT id FROM tasks WHERE id = ?", (task_id,)).fetchone():
                raise MCPToolError("task_not_found", f"Projekt {task_id} nicht gefunden", field="task_id")
            db.execute("UPDATE tasks SET assigned_to = ? WHERE id = ?", (user["id"], task_id))
            entity_type, entity_id = "task", task_id
    log_change(user, entity_type, entity_id, "update", {"assigned_to": {"new": user["id"]}})
    return {"assigned": True, "entity_type": entity_type, "entity_id": entity_id, "user_id": user["id"]}


@mcp.tool
def whoami() -> dict:
    """Liefert den aktuellen MCP-User (zur Selbst-Diagnose)."""
    user = _user()
    return {
        "id": user["id"],
        "username": user["username"],
        "display_name": user.get("mcp_display_name"),
        "auth_source": user.get("auth_source", "mcp"),
    }
