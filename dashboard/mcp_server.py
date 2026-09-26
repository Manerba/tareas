"""
Tareas - MCP-Server (Model Context Protocol)

Exponiert Tareas-Funktionen als MCP-Tools fuer remote Claude-Code-Instanzen.
Mountet sich an /mcp/ in app.py via FastMCP.http_app().

Authentifizierung: Bearer-Token (Middleware in app.py setzt current_mcp_user
ContextVar). Jeder MCP-User ist ein eigener Eintrag in der users-Tabelle mit
auth_source='mcp'.

Persistenz: direkter Zugriff auf SQLite (selbe DB wie REST-API), Schreib-Ops
laufen via audit_log.log_change(...) ins Audit.
"""

import logging
from contextvars import ContextVar

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastapi import HTTPException

from dashboard.agent_guide import build_agent_guide_markdown, build_agent_metadata
from dashboard.audit_log import log_change, diff_fields
from dashboard.db_utils import db_query, db_transaction
from dashboard.task_types import normalize_task_type
from dashboard.note_service import update_note_as_admin

logger = logging.getLogger(__name__)

# Wird von der MCP-Auth-Middleware in app.py pro Request gesetzt.
current_mcp_user: ContextVar[dict | None] = ContextVar("current_mcp_user", default=None)


def _user() -> dict:
    """Aktuellen MCP-User aus dem Request-Kontext holen."""
    u = current_mcp_user.get()
    if not u:
        raise ToolError("MCP-User-Kontext fehlt (Bearer-Token nicht akzeptiert?)")
    return u


# ============================================================
# Row-Serialisierung
# ============================================================

def _task_to_dict(row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"] or "",
        "description_format": row["description_format"],
        "status": row["status"],
        "priority": row["priority"],
        "task_type": row["task_type"],
        "deadline": row["deadline"],
        "created_at": row["created_at"],
        "created_by": row["created_by"],
        "assigned_to": row["assigned_to"],
        "nextcloud_path": row["nextcloud_path"] if "nextcloud_path" in row.keys() else None,
    }


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
    row = db.execute("SELECT * FROM sub_tasks WHERE id = ?", (subtask_id,)).fetchone()
    if not row:
        raise ToolError(f"Teilaufgabe {subtask_id} nicht gefunden")
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
    instructions=(
        "Tareas-Aufgabenverwaltung. Du bist als eigener MCP-User in der DB "
        "registriert; alle Aenderungen werden im Audit-Log protokolliert. "
        "Aufgaben (Projekte) haben Teilaufgaben (Subtasks) mit optionalen "
        "Abhaengigkeiten. Felder: description = Spec/Anforderung, notes = "
        "aktuelle User-Notiz, handoffs = chronologischer Verlauf/Findings."
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
def list_projects(status: str | None = None) -> list[dict]:
    """Listet alle Projekte (Top-Level-Aufgaben). Statusfilter: offen, in_arbeit, erledigt, abgebrochen."""
    with db_query() as db:
        if status:
            rows = db.execute(
                "SELECT * FROM tasks WHERE status = ? ORDER BY priority DESC, created_at DESC",
                (status,),
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT * FROM tasks ORDER BY priority DESC, created_at DESC"
            ).fetchall()
        return [_task_to_dict(r) for r in rows]


@mcp.tool
def get_project(project_id: int) -> dict:
    """Holt ein Projekt inkl. aller Subtasks und ihrer Abhaengigkeiten in einem Call."""
    with db_query() as db:
        task = db.execute("SELECT * FROM tasks WHERE id = ?", (project_id,)).fetchone()
        if not task:
            raise ToolError(f"Projekt {project_id} nicht gefunden")
        subs = db.execute(
            "SELECT * FROM sub_tasks WHERE project_id = ? ORDER BY position_number ASC, id ASC",
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
    status: str = "offen",
    task_type: str = "projekt",
) -> dict:
    """Legt ein neues Projekt an. Gibt das angelegte Projekt-Dict (mit id) zurueck."""
    user = _user()
    if not name.strip():
        raise ToolError("name darf nicht leer sein")
    try:
        task_type = normalize_task_type(task_type)
    except ValueError as exc:
        raise ToolError(str(exc))
    with db_transaction() as db:
        cursor = db.execute(
            """INSERT INTO tasks (name, description, deadline, priority, status, task_type, created_by, assigned_to)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (name.strip(), description, deadline, priority, status, task_type, user["id"], user["id"]),
        )
        task_id = cursor.lastrowid
        row = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    log_change(user, "task", task_id, "create", {
        "name": name, "description": description, "priority": priority,
        "status": status, "task_type": task_type, "deadline": deadline,
    })
    return _task_to_dict(row)


@mcp.tool
def update_project(
    project_id: int,
    name: str | None = None,
    description: str | None = None,
    deadline: str | None = None,
    priority: int | None = None,
    status: str | None = None,
    assigned_to: int | None = None,
) -> dict:
    """Aktualisiert ein Projekt. Nur uebergebene Felder werden geaendert.

    status='abgebrochen' bricht Aufgaben/Projekte ab, ohne Inhalte zu loeschen.
    Mit status='offen' wird ein abgebrochenes Projekt wieder aufgenommen;
    die Weboberflaeche berechnet seinen Status dann wieder aus den Teilaufgaben.
    """
    user = _user()
    with db_transaction() as db:
        before = db.execute("SELECT * FROM tasks WHERE id = ?", (project_id,)).fetchone()
        if not before:
            raise ToolError(f"Projekt {project_id} nicht gefunden")
        updates, values = [], []
        new_vals = {}
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
        if not updates:
            raise ToolError("Keine Felder zum Aktualisieren angegeben")
        values.append(project_id)
        db.execute(f"UPDATE tasks SET {', '.join(updates)} WHERE id = ?", values)
        after = db.execute("SELECT * FROM tasks WHERE id = ?", (project_id,)).fetchone()
    changes = diff_fields(dict(before), {**dict(before), **new_vals}, list(new_vals.keys()))
    log_change(user, "task", project_id, "update", changes)
    return _task_to_dict(after)


@mcp.tool
def delete_project(project_id: int) -> dict:
    """Loescht ein Projekt inkl. aller Subtasks (CASCADE)."""
    user = _user()
    with db_transaction() as db:
        row = db.execute("SELECT id, name FROM tasks WHERE id = ?", (project_id,)).fetchone()
        if not row:
            raise ToolError(f"Projekt {project_id} nicht gefunden")
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
        if not db.execute("SELECT id FROM tasks WHERE id = ?", (project_id,)).fetchone():
            raise ToolError(f"Projekt {project_id} nicht gefunden")
        subs = db.execute(
            "SELECT * FROM sub_tasks WHERE project_id = ? ORDER BY position_number ASC, id ASC",
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
            st = _subtask_to_dict(s)
            st["predecessor_ids"] = dep_map.get(s["id"], [])
            result.append(st)
        return result


@mcp.tool
def get_subtask(subtask_id: int) -> dict:
    """Holt eine einzelne Teilaufgabe inkl. predecessor_ids."""
    with db_query() as db:
        row = db.execute("SELECT * FROM sub_tasks WHERE id = ?", (subtask_id,)).fetchone()
        if not row:
            raise ToolError(f"Teilaufgabe {subtask_id} nicht gefunden")
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
    status_percent: int = 0,
    predecessor_ids: list[int] | None = None,
    assigned_to: int | None = None,
    depends_on_project: bool = False,
) -> dict:
    """Legt eine Teilaufgabe an. predecessor_ids = Liste der Vorgaenger-Subtask-IDs."""
    user = _user()
    if not name.strip():
        raise ToolError("name darf nicht leer sein")
    predecessor_ids = predecessor_ids or []
    with db_transaction() as db:
        if not db.execute("SELECT id FROM tasks WHERE id = ?", (project_id,)).fetchone():
            raise ToolError(f"Projekt {project_id} nicht gefunden")
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
        for pid in predecessor_ids:
            if pid == 0:
                continue
            db.execute(
                "INSERT OR IGNORE INTO sub_task_dependencies (sub_task_id, depends_on_id) VALUES (?, ?)",
                (subtask_id, pid),
            )
        row = db.execute("SELECT * FROM sub_tasks WHERE id = ?", (subtask_id,)).fetchone()
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
    status_percent: int | None = None,
    assigned_to: int | None = None,
    predecessor_ids: list[int] | None = None,
) -> dict:
    """Aktualisiert eine Teilaufgabe. Nur uebergebene Felder werden geaendert.
    predecessor_ids ersetzt KOMPLETT die bestehenden Vorgaenger."""
    user = _user()
    with db_transaction() as db:
        before = db.execute("SELECT * FROM sub_tasks WHERE id = ?", (subtask_id,)).fetchone()
        if not before:
            raise ToolError(f"Teilaufgabe {subtask_id} nicht gefunden")
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
            db.execute("DELETE FROM sub_task_dependencies WHERE sub_task_id = ?", (subtask_id,))
            has_project_dep = 0 in predecessor_ids
            db.execute(
                "UPDATE sub_tasks SET depends_on_project = ? WHERE id = ?",
                (1 if has_project_dep else 0, subtask_id),
            )
            for pid in predecessor_ids:
                if pid == 0:
                    continue
                db.execute(
                    "INSERT OR IGNORE INTO sub_task_dependencies (sub_task_id, depends_on_id) VALUES (?, ?)",
                    (subtask_id, pid),
                )
            new_vals["predecessor_ids"] = predecessor_ids
        after = db.execute("SELECT * FROM sub_tasks WHERE id = ?", (subtask_id,)).fetchone()
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
        row = db.execute("SELECT id, name, project_id FROM sub_tasks WHERE id = ?", (subtask_id,)).fetchone()
        if not row:
            raise ToolError(f"Teilaufgabe {subtask_id} nicht gefunden")
        db.execute("DELETE FROM sub_tasks WHERE id = ?", (subtask_id,))
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
        raise ToolError("direction muss 'up' oder 'down' sein")

    with db_transaction() as db:
        current = db.execute(
            "SELECT id, project_id, position_number FROM sub_tasks WHERE id = ?",
            (subtask_id,),
        ).fetchone()
        if not current:
            raise ToolError(f"Teilaufgabe {subtask_id} nicht gefunden")

        rows = _ordered_subtask_rows(db, current["project_id"])
        ordered_ids = [row["id"] for row in rows]
        try:
            old_index = ordered_ids.index(subtask_id)
        except ValueError:
            raise ToolError(f"Teilaufgabe {subtask_id} nicht gefunden")

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
        raise ToolError("position_number muss groesser oder gleich 1 sein")

    with db_transaction() as db:
        current = db.execute(
            "SELECT id, project_id, position_number FROM sub_tasks WHERE id = ?",
            (subtask_id,),
        ).fetchone()
        if not current:
            raise ToolError(f"Teilaufgabe {subtask_id} nicht gefunden")

        rows = _ordered_subtask_rows(db, current["project_id"])
        ordered_ids = [row["id"] for row in rows]
        try:
            old_index = ordered_ids.index(subtask_id)
        except ValueError:
            raise ToolError(f"Teilaufgabe {subtask_id} nicht gefunden")

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
    depends_on_id=0 bedeutet 'haengt vom Projektknoten ab'."""
    user = _user()
    with db_transaction() as db:
        if not db.execute("SELECT id FROM sub_tasks WHERE id = ?", (subtask_id,)).fetchone():
            raise ToolError(f"Teilaufgabe {subtask_id} nicht gefunden")
        if depends_on_id == 0:
            db.execute(
                "UPDATE sub_tasks SET depends_on_project = 1 WHERE id = ?",
                (subtask_id,),
            )
        else:
            if not db.execute("SELECT id FROM sub_tasks WHERE id = ?", (depends_on_id,)).fetchone():
                raise ToolError(f"Vorgaenger {depends_on_id} nicht gefunden")
            db.execute(
                "INSERT OR IGNORE INTO sub_task_dependencies (sub_task_id, depends_on_id) VALUES (?, ?)",
                (subtask_id, depends_on_id),
            )
    log_change(user, "dependency", subtask_id, "create", {"depends_on_id": depends_on_id})
    return {"added": True, "subtask_id": subtask_id, "depends_on_id": depends_on_id}


@mcp.tool
def remove_dependency(subtask_id: int, depends_on_id: int) -> dict:
    """Entfernt eine Abhaengigkeit. depends_on_id=0 entfernt die Abhaengigkeit vom Projektknoten."""
    user = _user()
    with db_transaction() as db:
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

def _task_readable(db, task, user: dict) -> bool:
    if user.get("is_admin"):
        return True
    if task["created_by"] is None:
        return True
    if task["created_by"] == user["id"] or task["assigned_to"] == user["id"]:
        return True

    membership = db.execute(
        "SELECT can_read FROM project_members WHERE project_id = ? AND user_id = ?",
        (task["id"], user["id"]),
    ).fetchone()
    return bool(membership and membership["can_read"])


def _require_task_read_access(db, task_id: int, user: dict):
    row = db.execute(
        "SELECT id, created_by, assigned_to FROM tasks WHERE id = ?",
        (task_id,),
    ).fetchone()
    if not row:
        raise ToolError(f"Aufgabe/Projekt {task_id} nicht gefunden")
    if not _task_readable(db, row, user):
        raise ToolError(f"Keine Leseberechtigung fuer Aufgabe/Projekt {task_id}")
    return row


def _can_read_task(db, task_id: int, user: dict) -> bool:
    row = db.execute(
        "SELECT id, created_by, assigned_to FROM tasks WHERE id = ?",
        (task_id,),
    ).fetchone()
    return bool(row and _task_readable(db, row, user))


def _subtask_readable(db, row, user: dict) -> bool:
    if user.get("is_admin"):
        return True
    if row["task_created_by"] is None:
        return True
    if row["task_created_by"] == user["id"] or row["subtask_assigned_to"] == user["id"]:
        return True

    membership = db.execute(
        "SELECT can_read FROM project_members WHERE project_id = ? AND user_id = ?",
        (row["project_id"], user["id"]),
    ).fetchone()
    return bool(membership and membership["can_read"])


def _require_subtask_read_access(db, task_id: int, subtask_id: int, user: dict):
    row = db.execute(
        """SELECT st.id, st.project_id, st.assigned_to AS subtask_assigned_to,
                  t.created_by AS task_created_by
           FROM sub_tasks st
           JOIN tasks t ON t.id = st.project_id
           WHERE st.id = ? AND st.project_id = ?""",
        (subtask_id, task_id),
    ).fetchone()
    if not row:
        raise ToolError(f"Teilaufgabe {subtask_id} gehoert nicht zu Projekt {task_id}")
    if not _subtask_readable(db, row, user):
        raise ToolError(f"Keine Leseberechtigung fuer Teilaufgabe {subtask_id}")
    return row


def _can_read_subtask(db, task_id: int, subtask_id: int, user: dict) -> bool:
    row = db.execute(
        """SELECT st.id, st.project_id, st.assigned_to AS subtask_assigned_to,
                  t.created_by AS task_created_by
           FROM sub_tasks st
           JOIN tasks t ON t.id = st.project_id
           WHERE st.id = ? AND st.project_id = ?""",
        (subtask_id, task_id),
    ).fetchone()
    return bool(row and _subtask_readable(db, row, user))


def _task_visibility_sql(task_alias: str, user: dict) -> tuple[str, list[int]]:
    if user.get("is_admin"):
        return "1 = 1", []
    return (
        f"({task_alias}.created_by IS NULL "
        f"OR {task_alias}.created_by = ? "
        f"OR {task_alias}.assigned_to = ? "
        f"OR EXISTS ("
        f"SELECT 1 FROM project_members pm "
        f"WHERE pm.project_id = {task_alias}.id "
        f"AND pm.user_id = ? "
        f"AND pm.can_read = 1"
        f"))",
        [user["id"], user["id"], user["id"]],
    )


def _subtask_visibility_sql(task_alias: str, subtask_alias: str, user: dict) -> tuple[str, list[int]]:
    if user.get("is_admin"):
        return "1 = 1", []
    return (
        f"({task_alias}.created_by IS NULL "
        f"OR {task_alias}.created_by = ? "
        f"OR {subtask_alias}.assigned_to = ? "
        f"OR EXISTS ("
        f"SELECT 1 FROM project_members pm "
        f"WHERE pm.project_id = {subtask_alias}.project_id "
        f"AND pm.user_id = ? "
        f"AND pm.can_read = 1"
        f"))",
        [user["id"], user["id"], user["id"]],
    )


def _make_handoff_id(scope: str, local_id: int) -> str:
    return f"{scope}:{local_id}"


def _parse_handoff_id(handoff_id: str) -> tuple[str, int]:
    value = str(handoff_id).strip()
    if ":" not in value:
        raise ToolError("handoff_id muss typisiert sein, z.B. 'task:123' oder 'subtask:456'")
    scope, raw_id = value.split(":", 1)
    if scope not in {"task", "subtask"}:
        raise ToolError("handoff_id muss mit 'task:' oder 'subtask:' beginnen")
    try:
        local_id = int(raw_id)
    except ValueError as exc:
        raise ToolError("handoff_id enthaelt keine gueltige numerische ID") from exc
    if local_id < 1:
        raise ToolError("handoff_id muss eine positive ID enthalten")
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
            _require_subtask_read_access(db, task_id, subtask_id, user)
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
            _require_task_read_access(db, task_id, user)
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
        raise ToolError(exc.detail) from exc


@mcp.tool(name="note.delete")
def note_delete(task_id: int, subtask_id: int | None = None) -> dict:
    """Loescht die aktuelle eigene User-Notiz des aufrufenden MCP-Users."""
    user = _user()
    with db_transaction() as db:
        if subtask_id is not None:
            _require_subtask_read_access(db, task_id, subtask_id, user)
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
            _require_task_read_access(db, task_id, user)
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
        raise ToolError("content darf nicht leer sein")

    with db_transaction() as db:
        if subtask_id is not None:
            _require_subtask_read_access(db, task_id, subtask_id, user)
            cursor = db.execute(
                """INSERT INTO sub_task_note_entries (sub_task_id, user_id, content)
                   VALUES (?, ?, ?)""",
                (subtask_id, user["id"], content),
            )
            entity_id = subtask_id
            entity_type = "sub_task_handoff"
            scope = "subtask"
        else:
            _require_task_read_access(db, task_id, user)
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
        raise ToolError("Nur Admins duerfen Handoffs bearbeiten")
    scope, local_id = _parse_handoff_id(handoff_id)
    if scope == "task" and subtask_id is not None:
        raise ToolError("task-Handoff darf nicht mit subtask_id bearbeitet werden")
    if scope == "subtask" and subtask_id is None:
        with db_query() as db:
            row = db.execute(
                """SELECT sne.sub_task_id FROM sub_task_note_entries sne
                   JOIN sub_tasks st ON st.id = sne.sub_task_id
                   WHERE sne.id = ? AND st.project_id = ?""", (local_id, task_id),
            ).fetchone()
            if not row:
                raise ToolError("Handoff nicht gefunden")
            subtask_id = row["sub_task_id"]
    try:
        return update_note_as_admin(task_id, content, user, subtask_id=subtask_id, entry_id=local_id)
    except HTTPException as exc:
        raise ToolError(exc.detail) from exc


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
                              t.created_by AS task_created_by
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
                    raise ToolError(f"Keine Leseberechtigung fuer Aufgabe/Projekt {task_id}")
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
            if existing["user_id"] != user["id"] and not user.get("is_admin"):
                raise ToolError("Nur eigene Handoffs koennen geloescht werden")
            db.execute(
                "DELETE FROM sub_task_note_entries WHERE id = ? AND sub_task_id = ?",
                (local_id, subtask_id),
            )
            entity_id = subtask_id
            entity_type = "sub_task_handoff"
        else:
            if subtask_id is not None:
                raise ToolError("task-Handoff darf nicht mit subtask_id geloescht werden")
            _require_task_read_access(db, task_id, user)
            existing = db.execute(
                "SELECT id, user_id, content FROM task_note_entries WHERE id = ? AND task_id = ?",
                (local_id, task_id),
            ).fetchone()
            if not existing:
                return {"deleted": False, "handoff_id": handoff_id, "task_id": task_id, "subtask_id": None}
            if existing["user_id"] != user["id"] and not user.get("is_admin"):
                raise ToolError("Nur eigene Handoffs koennen geloescht werden")
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
        raise ToolError("task_id oder subtask_id erforderlich")
    with db_transaction() as db:
        if subtask_id is not None:
            if not db.execute("SELECT id FROM sub_tasks WHERE id = ?", (subtask_id,)).fetchone():
                raise ToolError(f"Teilaufgabe {subtask_id} nicht gefunden")
            db.execute("UPDATE sub_tasks SET assigned_to = ? WHERE id = ?", (user["id"], subtask_id))
            entity_type, entity_id = "sub_task", subtask_id
        else:
            if not db.execute("SELECT id FROM tasks WHERE id = ?", (task_id,)).fetchone():
                raise ToolError(f"Projekt {task_id} nicht gefunden")
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
