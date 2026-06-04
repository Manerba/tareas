"""
Tareas - API-Router fuer Aufgaben, Teilaufgaben und Bereiche.
"""

import asyncio
import heapq
import logging
from collections import defaultdict
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel

from dashboard.audit_log import log_change
from dashboard.db_utils import db_query, db_transaction
from dashboard.user_utils import get_display_name
from dashboard.auth import get_current_user
from dashboard.components import Column, Filter, ExpandableTable
from dashboard.mail_service import notify_event

logger = logging.getLogger(__name__)

router = APIRouter()


# ============================================================
# Pydantic-Modelle
# ============================================================

class TaskCreate(BaseModel):
    name: str
    deadline: Optional[str] = None
    priority: int = 50
    task_type: str = "aufgabe"
    status: str = "offen"
    description: str = ""


class TaskUpdate(BaseModel):
    name: Optional[str] = None
    deadline: Optional[str] = None
    priority: Optional[int] = None
    task_type: Optional[str] = None
    status: Optional[str] = None
    description: Optional[str] = None
    assigned_to: Optional[int] = None
    nextcloud_path: Optional[str] = None


class SubTaskCreate(BaseModel):
    name: str
    area_id: Optional[int] = None
    deadline: Optional[str] = None
    priority: int = 50
    status_percent: int = 0
    predecessor_ids: list[int] = []
    description: str = ""


class SubTaskUpdate(BaseModel):
    name: Optional[str] = None
    area_id: Optional[int] = None
    deadline: Optional[str] = None
    priority: Optional[int] = None
    status_percent: Optional[int] = None
    position_number: Optional[int] = None
    predecessor_ids: Optional[list[int]] = None
    description: Optional[str] = None
    assigned_to: Optional[int] = None


class SubTaskMove(BaseModel):
    direction: str  # "up" oder "down"


class DependencyAction(BaseModel):
    from_id: int
    to_id: int


class NetzplanPositionItem(BaseModel):
    id: int
    x: float
    y: float


class NetzplanPositionsSave(BaseModel):
    positions: list[NetzplanPositionItem]


class AreaCreate(BaseModel):
    name: str


class NoteUpdate(BaseModel):
    content: str


# ============================================================
# Hilfsfunktionen
# ============================================================

def _format_date(iso_str: Optional[str]) -> str:
    """Konvertiert ISO-Datum zu DD.MM.YYYY."""
    if not iso_str:
        return ""
    try:
        # Versuche verschiedene Formate
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(iso_str.strip(), fmt)
                return dt.strftime("%d.%m.%Y")
            except ValueError:
                continue
        # Bereits im DD.MM.YYYY Format?
        if "." in iso_str and len(iso_str.split(".")) == 3:
            return iso_str.strip()
        return iso_str.strip()
    except Exception:
        return iso_str or ""


def _project_status(row) -> str:
    """Berechnet den Projektstatus aus Teilaufgaben-Fortschritt."""
    total = row["subtask_total"] or 0
    done = row["subtask_done"] or 0
    if total == 0:
        return "offen"
    if done >= total:
        return "erledigt"
    if done > 0:
        return "in_arbeit"
    return "offen"


def _parse_date_input(date_str: Optional[str]) -> Optional[str]:
    """Konvertiert Benutzereingabe zu ISO-Datum fuer DB."""
    if not date_str or not date_str.strip():
        return None
    date_str = date_str.strip()
    # DD.MM.YYYY -> YYYY-MM-DD
    if "." in date_str:
        parts = date_str.split(".")
        if len(parts) == 3:
            return f"{parts[2]}-{parts[1]}-{parts[0]}"
    # Bereits ISO oder anderes Format -> durchreichen
    return date_str


def _topological_reorder(db, project_id):
    """Topologische Neuordnung der Teilaufgaben anhand der Abhaengigkeiten (Kahn-Algorithmus)."""
    rows = db.execute(
        "SELECT id, position_number FROM sub_tasks WHERE project_id = ?", (project_id,)
    ).fetchall()
    if not rows:
        return {}

    old_pos = {r["id"]: r["position_number"] for r in rows}
    all_ids = set(old_pos.keys())

    deps = db.execute(
        "SELECT d.sub_task_id, d.depends_on_id FROM sub_task_dependencies d "
        "JOIN sub_tasks s ON d.sub_task_id = s.id WHERE s.project_id = ?", (project_id,)
    ).fetchall()

    in_degree = defaultdict(int)
    successors = defaultdict(list)
    for d in deps:
        if d["sub_task_id"] in all_ids and d["depends_on_id"] in all_ids:
            in_degree[d["sub_task_id"]] += 1
            successors[d["depends_on_id"]].append(d["sub_task_id"])

    # Kahn's mit heapq: Tiebreaker = alte position_number
    heap = []
    for nid in all_ids:
        if in_degree[nid] == 0:
            heapq.heappush(heap, (old_pos.get(nid, 0), nid))

    new_pos = {}
    pos = 1
    while heap:
        _, nid = heapq.heappop(heap)
        new_pos[nid] = pos
        pos += 1
        for succ in successors[nid]:
            in_degree[succ] -= 1
            if in_degree[succ] == 0:
                heapq.heappush(heap, (old_pos.get(succ, 0), succ))

    # Nur geaenderte Positionen aktualisieren
    for nid, p in new_pos.items():
        if old_pos.get(nid) != p:
            db.execute("UPDATE sub_tasks SET position_number = ? WHERE id = ?", (p, nid))

    return new_pos


async def _send_assignment_mail(user_id, task_name, actor_name, deadline, priority):
    """Sendet Zuweisungs-Mail (non-blocking)."""
    try:
        notify_event("task_assigned", user_id, {
            "task_name": task_name,
            "actor_name": actor_name,
            "deadline": deadline,
            "priority": priority,
        })
    except Exception as e:
        logger.error(f"Mail-Versand fehlgeschlagen (task_assigned): {e}")


async def _send_status_mail(user_id, task_name, actor_name, new_status):
    """Sendet Status-Aenderungs-Mail (non-blocking)."""
    status_labels = {"offen": "Offen", "in_arbeit": "In Arbeit", "erledigt": "Erledigt"}
    try:
        notify_event("status_change", user_id, {
            "task_name": task_name,
            "actor_name": actor_name,
            "new_status": status_labels.get(new_status, new_status),
        })
    except Exception as e:
        logger.error(f"Mail-Versand fehlgeschlagen (status_change): {e}")


# ============================================================
# Tasks CRUD
# ============================================================

@router.get("/api/tasks")
async def get_tasks(user=Depends(get_current_user)):
    """Aufgaben abrufen (eigene/zugewiesene/Team-Mitglied + Altdaten ohne created_by).
    Admins sehen alle Aufgaben - inkl. MCP-erstellter Projekte (Kategorie 'mcp')."""
    is_admin = bool(user.get("is_admin"))
    with db_query() as db:
        if is_admin:
            rows = db.execute(
                """SELECT t.*,
                          uc.vorname || ' ' || uc.nachname AS created_by_name,
                          uc.auth_source AS created_by_auth_source,
                          ua.vorname || ' ' || ua.nachname AS assigned_to_name,
                          (SELECT 1 FROM project_members pm WHERE pm.project_id = t.id AND pm.user_id = ?) AS is_team_member,
                          (SELECT COUNT(*) FROM sub_tasks st WHERE st.project_id = t.id) AS subtask_total,
                          (SELECT COUNT(*) FROM sub_tasks st WHERE st.project_id = t.id AND st.status_percent >= 100) AS subtask_done
                   FROM tasks t
                   LEFT JOIN users uc ON t.created_by = uc.id
                   LEFT JOIN users ua ON t.assigned_to = ua.id
                   ORDER BY t.priority ASC, t.created_at DESC""",
                (user["id"],),
            ).fetchall()
        else:
            rows = db.execute(
                """SELECT t.*,
                          uc.vorname || ' ' || uc.nachname AS created_by_name,
                          uc.auth_source AS created_by_auth_source,
                          ua.vorname || ' ' || ua.nachname AS assigned_to_name,
                          (SELECT 1 FROM project_members pm WHERE pm.project_id = t.id AND pm.user_id = ?) AS is_team_member,
                          (SELECT COUNT(*) FROM sub_tasks st WHERE st.project_id = t.id) AS subtask_total,
                          (SELECT COUNT(*) FROM sub_tasks st WHERE st.project_id = t.id AND st.status_percent >= 100) AS subtask_done
                   FROM tasks t
                   LEFT JOIN users uc ON t.created_by = uc.id
                   LEFT JOIN users ua ON t.assigned_to = ua.id
                   WHERE t.created_by = ? OR t.assigned_to = ? OR t.created_by IS NULL
                      OR t.id IN (SELECT project_id FROM project_members WHERE user_id = ? AND can_read = 1)
                   ORDER BY t.priority ASC, t.created_at DESC""",
                (user["id"], user["id"], user["id"], user["id"]),
            ).fetchall()

        items = []
        for row in rows:
            # Kategorie berechnen
            created_by = row["created_by"]
            assigned_to = row["assigned_to"]
            is_team = bool(row["is_team_member"])
            creator_is_mcp = row["created_by_auth_source"] == "mcp"

            if not created_by:
                category = "eigene"
            elif created_by == user["id"] and (not assigned_to or assigned_to == user["id"]):
                category = "eigene"
            elif created_by == user["id"] and assigned_to != user["id"]:
                category = "vergebene"
            elif assigned_to == user["id"] and created_by != user["id"]:
                category = "zugewiesene"
            elif is_team and created_by != user["id"] and assigned_to != user["id"]:
                category = "team"
            elif creator_is_mcp:
                # Von MCP-User angelegt, weder eigene noch zugewiesene noch Team-Sichtbarkeit
                # -> Admin-Sicht auf autonome MCP-Arbeit
                category = "mcp"
            else:
                category = "eigene"

            items.append({
                "id": row["id"],
                "_type": "task",
                "_category": category,
                "name": row["name"],
                "created_at": _format_date(row["created_at"]),
                "deadline": _format_date(row["deadline"]),
                "priority": row["priority"],
                "task_type": row["task_type"],
                "status": _project_status(row) if row["task_type"] == "projekt" else row["status"],
                "description": row["description"] or "",
                "created_by": row["created_by"],
                "assigned_to": row["assigned_to"],
                "created_by_name": (row["created_by_name"] or "").strip(),
                "assigned_to_name": (row["assigned_to_name"] or "").strip(),
                "is_team_member": bool(row["is_team_member"]),
                "nextcloud_path": row["nextcloud_path"] or "",
                "subtask_total": row["subtask_total"] or 0,
                "subtask_done": row["subtask_done"] or 0,
            })

        # Zugewiesene Teilaufgaben als Aufgaben-Eintraege
        st_rows = db.execute(
            """SELECT st.*, t.name AS project_name,
                      uc.vorname || ' ' || uc.nachname AS created_by_name,
                      ua.vorname || ' ' || ua.nachname AS assigned_to_name
               FROM sub_tasks st
               JOIN tasks t ON st.project_id = t.id
               LEFT JOIN users uc ON st.created_by = uc.id
               LEFT JOIN users ua ON st.assigned_to = ua.id
               WHERE st.assigned_to = ?
                 AND (t.created_by IS NULL OR t.created_by != ?)""",
            (user["id"], user["id"]),
        ).fetchall()

        for st in st_rows:
            pct = st["status_percent"] or 0
            if pct == 0:
                st_status = "offen"
            elif pct >= 100:
                st_status = "erledigt"
            else:
                st_status = "in_arbeit"

            items.append({
                "id": -st["id"],
                "_type": "assigned_subtask",
                "_category": "zugewiesene",
                "_subtask_id": st["id"],
                "_project_id": st["project_id"],
                "_project_name": st["project_name"],
                "_status_percent": pct,
                "name": st["name"],
                "created_at": _format_date(st["created_at"]),
                "deadline": _format_date(st["deadline"]),
                "priority": st["priority"],
                "task_type": "aufgabe",
                "status": st_status,
                "description": st["description"] or "",
                "created_by": st["created_by"],
                "assigned_to": st["assigned_to"],
                "created_by_name": (st["created_by_name"] or "").strip(),
                "assigned_to_name": (st["assigned_to_name"] or "").strip(),
                "is_team_member": False,
            })

        return {"items": items, "total": len(items)}


@router.post("/api/tasks")
async def create_task(task: TaskCreate, user=Depends(get_current_user)):
    """Neue Aufgabe anlegen."""
    with db_transaction() as db:
        deadline = _parse_date_input(task.deadline)
        cursor = db.execute(
            """INSERT INTO tasks (name, deadline, priority, task_type, status, description, created_by)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (task.name, deadline, task.priority, task.task_type, task.status, task.description, user["id"]),
        )
        new_id = cursor.lastrowid
    log_change(user, "task", new_id, "create", {
        "name": task.name, "priority": task.priority, "deadline": deadline,
        "task_type": task.task_type, "status": task.status,
    })
    return {"id": new_id, "message": "Aufgabe erstellt"}


@router.put("/api/tasks/{task_id}")
async def update_task(task_id: int, task: TaskUpdate, user=Depends(get_current_user)):
    """Aufgabe aktualisieren."""
    with db_transaction() as db:
        existing = db.execute(
            "SELECT id, created_by, assigned_to FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Aufgabe nicht gefunden")

        # Berechtigung pruefen
        is_creator = existing["created_by"] == user["id"]
        is_legacy = existing["created_by"] is None
        is_assignee = existing["assigned_to"] == user["id"]
        has_team_edit = False
        if not is_creator and not is_legacy:
            membership = db.execute(
                "SELECT can_edit FROM project_members WHERE project_id = ? AND user_id = ?",
                (task_id, user["id"]),
            ).fetchone()
            has_team_edit = bool(membership and membership["can_edit"])

        # MCP-Assignees haben volle Edit-Rechte (im Gegensatz zu menschlichen Assignees)
        mcp_assignee_full = (user.get("auth_source") == "mcp") and is_assignee
        can_full_edit = is_creator or is_legacy or has_team_edit or mcp_assignee_full
        if not can_full_edit and not is_assignee:
            raise HTTPException(status_code=403, detail="Keine Berechtigung zum Bearbeiten")

        updates = []
        values = []
        changes_diff: dict = {}

        # Zugewiesene duerfen nur Status aendern
        if can_full_edit:
            if task.name is not None:
                updates.append("name = ?")
                values.append(task.name)
                changes_diff["name"] = task.name
            if task.deadline is not None:
                updates.append("deadline = ?")
                values.append(_parse_date_input(task.deadline))
                changes_diff["deadline"] = task.deadline
            if task.priority is not None:
                updates.append("priority = ?")
                values.append(task.priority)
                changes_diff["priority"] = task.priority
            if task.task_type is not None:
                updates.append("task_type = ?")
                values.append(task.task_type)
                changes_diff["task_type"] = task.task_type
            if task.description is not None:
                updates.append("description = ?")
                values.append(task.description)
                changes_diff["description_len"] = len(task.description)
            if task.assigned_to is not None:
                if task.assigned_to == 0:
                    updates.append("assigned_to = NULL")
                    changes_diff["assigned_to"] = None
                else:
                    updates.append("assigned_to = ?")
                    values.append(task.assigned_to)
                    changes_diff["assigned_to"] = task.assigned_to
            if task.nextcloud_path is not None:
                if task.nextcloud_path == "":
                    updates.append("nextcloud_path = NULL")
                    changes_diff["nextcloud_path"] = None
                else:
                    updates.append("nextcloud_path = ?")
                    values.append(task.nextcloud_path)
                    changes_diff["nextcloud_path"] = task.nextcloud_path

        # Status darf auch der Zugewiesene aendern
        if task.status is not None:
            updates.append("status = ?")
            values.append(task.status)
            changes_diff["status"] = task.status

        if updates:
            values.append(task_id)
            db.execute(
                f"UPDATE tasks SET {', '.join(updates)} WHERE id = ?",
                values,
            )

        # Mail-Benachrichtigungen (non-blocking)
        actor_name = get_display_name(user)
        task_row = db.execute("SELECT name, deadline, priority, assigned_to, status FROM tasks WHERE id = ?", (task_id,)).fetchone()

    # Mail-Versand ausserhalb der Transaktion (non-blocking)
    if task_row:
        # Bei Zuweisung: Mail an neuen Zugewiesenen
        if task.assigned_to is not None and task.assigned_to > 0:
            old_assigned = existing["assigned_to"]
            if old_assigned != task.assigned_to:
                asyncio.create_task(_send_assignment_mail(
                    task.assigned_to, task_row["name"], actor_name,
                    task_row["deadline"] or "", str(task_row["priority"])
                ))

        # Bei Status-Aenderung: Mail an Zugewiesenen
        if task.status is not None and task_row["assigned_to"] and task_row["assigned_to"] != user["id"]:
            asyncio.create_task(_send_status_mail(
                task_row["assigned_to"], task_row["name"], actor_name, task.status
            ))

    if changes_diff:
        action = "status_change" if list(changes_diff.keys()) == ["status"] else "update"
        log_change(user, "task", task_id, action, changes_diff)
    return {"message": "Aufgabe aktualisiert"}


@router.delete("/api/tasks/{task_id}")
async def delete_task(task_id: int, user=Depends(get_current_user)):
    """Aufgabe loeschen (inkl. SubTasks durch CASCADE)."""
    with db_transaction() as db:
        existing = db.execute("SELECT id, name, created_by FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Aufgabe nicht gefunden")

        # Nur Ersteller, Legacy oder Admin duerfen loeschen
        is_creator = existing["created_by"] == user["id"]
        is_legacy = existing["created_by"] is None
        if not is_creator and not is_legacy and not user.get("is_admin"):
            raise HTTPException(status_code=403, detail="Keine Berechtigung zum Loeschen")

        task_name = existing["name"]
        db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    log_change(user, "task", task_id, "delete", {"name": task_name})
    return {"message": "Aufgabe geloescht"}


# ============================================================
# SubTasks CRUD
# ============================================================

@router.get("/api/tasks/{task_id}/subtasks")
async def get_subtasks(task_id: int, user=Depends(get_current_user)):
    """Teilaufgaben eines Projekts abrufen (mit Berechtigungsfilter)."""
    with db_query() as db:
        # Projekt pruefen
        task = db.execute(
            "SELECT id, created_by, netzplan_project_x, netzplan_project_y FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        if not task:
            raise HTTPException(status_code=404, detail="Aufgabe nicht gefunden")

        is_creator = task["created_by"] == user["id"]
        is_legacy = task["created_by"] is None
        is_admin = bool(user.get("is_admin"))

        # Teammitgliedschaft pruefen
        membership = None
        if not is_creator and not is_legacy and not is_admin:
            membership = db.execute(
                "SELECT can_read, can_edit, can_create FROM project_members WHERE project_id = ? AND user_id = ?",
                (task_id, user["id"]),
            ).fetchone()

        rows = db.execute(
            """SELECT st.*, a.name as area_name,
                      uc.vorname || ' ' || uc.nachname AS created_by_name,
                      ua.vorname || ' ' || ua.nachname AS assigned_to_name
               FROM sub_tasks st
               LEFT JOIN areas a ON st.area_id = a.id
               LEFT JOIN users uc ON st.created_by = uc.id
               LEFT JOIN users ua ON st.assigned_to = ua.id
               WHERE st.project_id = ?
               ORDER BY st.position_number ASC, st.created_at ASC""",
            (task_id,),
        ).fetchall()

        # Alle Dependencies fuer dieses Projekt laden
        sub_ids = [row["id"] for row in rows]
        deps = {}
        if sub_ids:
            placeholders = ",".join("?" * len(sub_ids))
            dep_rows = db.execute(
                f"SELECT sub_task_id, depends_on_id FROM sub_task_dependencies WHERE sub_task_id IN ({placeholders})",
                sub_ids,
            ).fetchall()
            for dr in dep_rows:
                deps.setdefault(dr["sub_task_id"], []).append(dr["depends_on_id"])

        # Mapping: sub_task id -> position_number (fuer predecessors_display)
        id_to_pos = {row["id"]: row["position_number"] for row in rows}

        items = []
        for row in rows:
            # Effektive Berechtigungen berechnen
            if is_creator or is_legacy:
                permissions = {"can_read": True, "can_edit": True, "can_create": True}
            elif is_admin:
                # Admins sehen alle Subtasks (read), Edits bleiben dem Creator vorbehalten
                permissions = {"can_read": True, "can_edit": False, "can_create": False}
            elif membership:
                permissions = {
                    "can_read": bool(membership["can_read"]),
                    "can_edit": bool(membership["can_edit"]),
                    "can_create": bool(membership["can_create"]),
                }
                if not permissions["can_read"]:
                    continue
            else:
                # Kein Zugriff (weder Ersteller, Legacy, noch Teammitglied)
                # Zugewiesene sehen nur ihre zugewiesene Subtask
                if row["assigned_to"] == user["id"]:
                    permissions = {"can_read": True, "can_edit": True, "can_create": False}
                else:
                    continue

            pred_ids = deps.get(row["id"], [])
            if row["depends_on_project"]:
                pred_ids = [0] + pred_ids
            pred_positions = sorted(id_to_pos.get(pid, 0) or 0 for pid in pred_ids)
            items.append({
                "id": row["id"],
                "project_id": row["project_id"],
                "position_number": row["position_number"],
                "name": row["name"],
                "area_id": row["area_id"],
                "area_name": row["area_name"] or "",
                "created_at": _format_date(row["created_at"]),
                "deadline": _format_date(row["deadline"]),
                "priority": row["priority"],
                "status_percent": row["status_percent"],
                "predecessor_ids": pred_ids,
                "predecessors_display": ", ".join(str(p) for p in pred_positions if p),
                "description": row["description"] or "",
                "created_by": row["created_by"],
                "assigned_to": row["assigned_to"],
                "created_by_name": (row["created_by_name"] or "").strip(),
                "assigned_to_name": (row["assigned_to_name"] or "").strip(),
                "permissions": permissions,
                "netzplan_x": row["netzplan_x"],
                "netzplan_y": row["netzplan_y"],
                "depends_on_project": bool(row["depends_on_project"]),
            })

        return {
            "items": items,
            "total": len(items),
            "netzplan_project_x": task["netzplan_project_x"],
            "netzplan_project_y": task["netzplan_project_y"],
        }


@router.post("/api/tasks/{task_id}/subtasks")
async def create_subtask(task_id: int, subtask: SubTaskCreate, user=Depends(get_current_user)):
    """Teilaufgabe anlegen."""
    with db_transaction() as db:
        existing = db.execute("SELECT id, created_by FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Aufgabe nicht gefunden")

        # Berechtigung pruefen: Ersteller, Legacy, oder Teammitglied mit can_create
        is_creator = existing["created_by"] == user["id"]
        is_legacy = existing["created_by"] is None
        if not is_creator and not is_legacy:
            membership = db.execute(
                "SELECT can_create FROM project_members WHERE project_id = ? AND user_id = ?",
                (task_id, user["id"]),
            ).fetchone()
            if not membership or not membership["can_create"]:
                raise HTTPException(status_code=403, detail="Keine Berechtigung zum Erstellen von Teilaufgaben")

        # Naechste position_number ermitteln
        row = db.execute(
            "SELECT COALESCE(MAX(position_number), 0) + 1 as next_pos FROM sub_tasks WHERE project_id = ?",
            (task_id,),
        ).fetchone()
        next_pos = row["next_pos"]

        deadline = _parse_date_input(subtask.deadline)
        cursor = db.execute(
            """INSERT INTO sub_tasks (project_id, name, area_id, deadline, priority, status_percent, position_number, description, created_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (task_id, subtask.name, subtask.area_id, deadline, subtask.priority, subtask.status_percent, next_pos, subtask.description, user["id"]),
        )
        new_id = cursor.lastrowid

        # Vorgaenger eintragen
        for pred_id in subtask.predecessor_ids:
            db.execute(
                "INSERT OR IGNORE INTO sub_task_dependencies (sub_task_id, depends_on_id) VALUES (?, ?)",
                (new_id, pred_id),
            )

    log_change(user, "sub_task", new_id, "create", {
        "project_id": task_id, "name": subtask.name, "priority": subtask.priority,
        "deadline": deadline, "predecessor_ids": subtask.predecessor_ids,
    })
    return {"id": new_id, "position_number": next_pos, "message": "Teilaufgabe erstellt"}


@router.put("/api/subtasks/{subtask_id}")
async def update_subtask(subtask_id: int, subtask: SubTaskUpdate, user=Depends(get_current_user)):
    """Teilaufgabe aktualisieren."""
    with db_transaction() as db:
        existing = db.execute(
            "SELECT id, project_id FROM sub_tasks WHERE id = ?", (subtask_id,)
        ).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Teilaufgabe nicht gefunden")

        # Berechtigung pruefen
        task = db.execute(
            "SELECT created_by FROM tasks WHERE id = ?", (existing["project_id"],)
        ).fetchone()
        is_creator = task and task["created_by"] == user["id"]
        is_legacy = task and task["created_by"] is None
        if not is_creator and not is_legacy:
            membership = db.execute(
                "SELECT can_edit FROM project_members WHERE project_id = ? AND user_id = ?",
                (existing["project_id"], user["id"]),
            ).fetchone()
            if not membership or not membership["can_edit"]:
                # Zugewiesene duerfen ihre Subtask bearbeiten
                st_row = db.execute("SELECT assigned_to FROM sub_tasks WHERE id = ?", (subtask_id,)).fetchone()
                if not st_row or st_row["assigned_to"] != user["id"]:
                    raise HTTPException(status_code=403, detail="Keine Berechtigung zum Bearbeiten")

        updates = []
        values = []
        if subtask.name is not None:
            updates.append("name = ?")
            values.append(subtask.name)
        if subtask.area_id is not None:
            updates.append("area_id = ?")
            values.append(subtask.area_id)
        if subtask.deadline is not None:
            updates.append("deadline = ?")
            values.append(_parse_date_input(subtask.deadline))
        if subtask.priority is not None:
            updates.append("priority = ?")
            values.append(subtask.priority)
        if subtask.status_percent is not None:
            updates.append("status_percent = ?")
            values.append(subtask.status_percent)
        if subtask.position_number is not None:
            # Pruefen ob unique innerhalb des Projekts
            conflict = db.execute(
                "SELECT id FROM sub_tasks WHERE project_id = ? AND position_number = ? AND id != ?",
                (existing["project_id"], subtask.position_number, subtask_id),
            ).fetchone()
            if conflict:
                raise HTTPException(status_code=400, detail="Positionsnummer bereits vergeben")
            updates.append("position_number = ?")
            values.append(subtask.position_number)
        if subtask.description is not None:
            updates.append("description = ?")
            values.append(subtask.description)

        # assigned_to: 0 = unassign, >0 = assign
        if subtask.assigned_to is not None:
            if subtask.assigned_to == 0:
                updates.append("assigned_to = NULL")
            else:
                updates.append("assigned_to = ?")
                values.append(subtask.assigned_to)

        if updates:
            values.append(subtask_id)
            db.execute(
                f"UPDATE sub_tasks SET {', '.join(updates)} WHERE id = ?",
                values,
            )

        # Vorgaenger aktualisieren (komplett ersetzen)
        if subtask.predecessor_ids is not None:
            db.execute(
                "DELETE FROM sub_task_dependencies WHERE sub_task_id = ?", (subtask_id,)
            )
            # depends_on_project aus predecessor_ids extrahieren
            has_project_dep = 0 in subtask.predecessor_ids
            db.execute(
                "UPDATE sub_tasks SET depends_on_project = ? WHERE id = ?",
                (1 if has_project_dep else 0, subtask_id),
            )
            for pred_id in subtask.predecessor_ids:
                if pred_id == 0:
                    continue  # Projektknoten wird ueber depends_on_project behandelt
                db.execute(
                    "INSERT OR IGNORE INTO sub_task_dependencies (sub_task_id, depends_on_id) VALUES (?, ?)",
                    (subtask_id, pred_id),
                )

    sub_changes: dict = {}
    for f in ("name", "area_id", "deadline", "priority", "status_percent",
              "position_number", "assigned_to"):
        v = getattr(subtask, f, None)
        if v is not None:
            sub_changes[f] = v
    if subtask.description is not None:
        sub_changes["description_len"] = len(subtask.description)
    if subtask.predecessor_ids is not None:
        sub_changes["predecessor_ids"] = subtask.predecessor_ids
    if sub_changes:
        log_change(user, "sub_task", subtask_id, "update", sub_changes)
    return {"message": "Teilaufgabe aktualisiert"}


@router.delete("/api/subtasks/{subtask_id}")
async def delete_subtask(subtask_id: int, user=Depends(get_current_user)):
    """Teilaufgabe loeschen (Ersteller, Legacy oder Admin)."""
    with db_transaction() as db:
        existing = db.execute("SELECT id, name, project_id FROM sub_tasks WHERE id = ?", (subtask_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Teilaufgabe nicht gefunden")

        # Ersteller des Projekts, Legacy oder Admin darf loeschen
        task = db.execute(
            "SELECT created_by FROM tasks WHERE id = ?", (existing["project_id"],)
        ).fetchone()
        is_creator = task and task["created_by"] == user["id"]
        is_legacy = task and task["created_by"] is None
        if not is_creator and not is_legacy and not user.get("is_admin"):
            raise HTTPException(status_code=403, detail="Keine Berechtigung zum Loeschen")

        sub_name = existing["name"]
        project_id = existing["project_id"]
        db.execute("DELETE FROM sub_tasks WHERE id = ?", (subtask_id,))
    log_change(user, "sub_task", subtask_id, "delete", {"name": sub_name, "project_id": project_id})
    return {"message": "Teilaufgabe geloescht"}


# ============================================================
# SubTask Move (Position Swap)
# ============================================================

@router.post("/api/subtasks/{subtask_id}/move")
async def move_subtask(subtask_id: int, move: SubTaskMove, user=Depends(get_current_user)):
    """Teilaufgabe nach oben/unten verschieben (Position tauschen)."""
    with db_transaction() as db:
        # Subtask laden
        current = db.execute(
            "SELECT id, project_id, position_number FROM sub_tasks WHERE id = ?",
            (subtask_id,),
        ).fetchone()
        if not current:
            raise HTTPException(status_code=404, detail="Teilaufgabe nicht gefunden")

        # Berechtigung pruefen
        task = db.execute(
            "SELECT created_by FROM tasks WHERE id = ?", (current["project_id"],)
        ).fetchone()
        is_creator = task and task["created_by"] == user["id"]
        is_legacy = task and task["created_by"] is None
        if not is_creator and not is_legacy:
            raise HTTPException(status_code=403, detail="Keine Berechtigung")

        # Nachbar finden
        if move.direction == "up":
            neighbor = db.execute(
                """SELECT id, position_number FROM sub_tasks
                   WHERE project_id = ? AND position_number < ?
                   ORDER BY position_number DESC LIMIT 1""",
                (current["project_id"], current["position_number"]),
            ).fetchone()
        elif move.direction == "down":
            neighbor = db.execute(
                """SELECT id, position_number FROM sub_tasks
                   WHERE project_id = ? AND position_number > ?
                   ORDER BY position_number ASC LIMIT 1""",
                (current["project_id"], current["position_number"]),
            ).fetchone()
        else:
            raise HTTPException(status_code=400, detail="direction muss 'up' oder 'down' sein")

        if not neighbor:
            return {"message": "Bereits am Rand", "removed_dependencies": []}

        # Positionen tauschen
        cur_pos = current["position_number"]
        nb_pos = neighbor["position_number"]
        db.execute(
            "UPDATE sub_tasks SET position_number = ? WHERE id = ?",
            (nb_pos, current["id"]),
        )
        db.execute(
            "UPDATE sub_tasks SET position_number = ? WHERE id = ?",
            (cur_pos, neighbor["id"]),
        )

        # Abhaengigkeiten bereinigen: Alle Dependencies im Projekt laden
        all_subtasks = db.execute(
            "SELECT id, position_number FROM sub_tasks WHERE project_id = ?",
            (current["project_id"],),
        ).fetchall()
        pos_map = {row["id"]: row["position_number"] for row in all_subtasks}

        all_deps = db.execute(
            """SELECT d.sub_task_id, d.depends_on_id
               FROM sub_task_dependencies d
               JOIN sub_tasks s ON d.sub_task_id = s.id
               WHERE s.project_id = ?""",
            (current["project_id"],),
        ).fetchall()

        removed = []
        for dep in all_deps:
            st_pos = pos_map.get(dep["sub_task_id"], 0)
            dep_pos = pos_map.get(dep["depends_on_id"], 0)
            # Vorgaenger muss niedrigere Position haben
            if dep_pos >= st_pos:
                db.execute(
                    "DELETE FROM sub_task_dependencies WHERE sub_task_id = ? AND depends_on_id = ?",
                    (dep["sub_task_id"], dep["depends_on_id"]),
                )
                removed.append({
                    "sub_task_id": dep["sub_task_id"],
                    "depends_on_id": dep["depends_on_id"],
                })

        return {"message": "Position getauscht", "removed_dependencies": removed}


# ============================================================
# SubTask Dependencies (Add / Remove)
# ============================================================

@router.post("/api/tasks/{task_id}/subtasks/add-dependency")
async def add_dependency(task_id: int, dep: DependencyAction, user=Depends(get_current_user)):
    """Abhaengigkeit zwischen Teilaufgaben hinzufuegen (from_id -> to_id)."""
    with db_transaction() as db:
        # Projekt pruefen
        task = db.execute("SELECT id, created_by FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not task:
            raise HTTPException(status_code=404, detail="Aufgabe nicht gefunden")

        # Berechtigung pruefen
        is_creator = task["created_by"] == user["id"]
        is_legacy = task["created_by"] is None
        if not is_creator and not is_legacy:
            raise HTTPException(status_code=403, detail="Keine Berechtigung")

        # Spezialfall: Projektknoten als Vorgaenger
        if dep.from_id == 0:
            to_st = db.execute(
                "SELECT id, depends_on_project FROM sub_tasks WHERE id = ? AND project_id = ?",
                (dep.to_id, task_id),
            ).fetchone()
            if not to_st:
                raise HTTPException(status_code=404, detail="Teilaufgabe nicht gefunden")
            # Nur erlaubt wenn Ziel keine Vorgaenger hat
            has_preds = db.execute(
                "SELECT 1 FROM sub_task_dependencies WHERE sub_task_id = ?", (dep.to_id,)
            ).fetchone()
            if has_preds or to_st["depends_on_project"]:
                raise HTTPException(status_code=400, detail="Nur erlaubt wenn keine Vorgaenger vorhanden")
            db.execute(
                "UPDATE sub_tasks SET depends_on_project = 1 WHERE id = ?", (dep.to_id,)
            )
            return {"message": "Projekt-Abhaengigkeit hinzugefuegt", "new_positions": {}}

        # Beide Subtasks muessen zum Projekt gehoeren
        from_st = db.execute(
            "SELECT id FROM sub_tasks WHERE id = ? AND project_id = ?", (dep.from_id, task_id)
        ).fetchone()
        to_st = db.execute(
            "SELECT id FROM sub_tasks WHERE id = ? AND project_id = ?", (dep.to_id, task_id)
        ).fetchone()
        if not from_st or not to_st:
            raise HTTPException(status_code=404, detail="Teilaufgabe nicht gefunden")

        # Keine Self-Loops
        if dep.from_id == dep.to_id:
            raise HTTPException(status_code=400, detail="Self-Loop nicht erlaubt")

        # Keine Duplikate
        existing = db.execute(
            "SELECT 1 FROM sub_task_dependencies WHERE sub_task_id = ? AND depends_on_id = ?",
            (dep.to_id, dep.from_id),
        ).fetchone()
        if existing:
            raise HTTPException(status_code=400, detail="Abhaengigkeit existiert bereits")

        # Zykluserkennung: from_id darf nicht transitiv von to_id abhaengen
        # BFS von from_id entlang depends_on_id-Kanten
        all_deps = db.execute(
            "SELECT d.sub_task_id, d.depends_on_id FROM sub_task_dependencies d "
            "JOIN sub_tasks s ON d.sub_task_id = s.id WHERE s.project_id = ?", (task_id,)
        ).fetchall()
        adj = defaultdict(set)  # sub_task_id -> set of depends_on_ids (Vorgaenger)
        adj_fwd = defaultdict(set)  # depends_on_id -> set of sub_task_ids (Nachfolger)
        for d in all_deps:
            adj[d["sub_task_id"]].add(d["depends_on_id"])
            adj_fwd[d["depends_on_id"]].add(d["sub_task_id"])

        # Pruefen ob to_id transitiv from_id erreichen kann (= Zyklus)
        visited = set()
        queue = [dep.to_id]
        while queue:
            nid = queue.pop(0)
            if nid == dep.from_id:
                raise HTTPException(status_code=400, detail="Zirkulaere Abhaengigkeit nicht erlaubt")
            if nid in visited:
                continue
            visited.add(nid)
            for succ in adj_fwd.get(nid, set()):
                queue.append(succ)

        # Transitive Redundanz: Pruefen ob from_id bereits transitiver Vorgaenger von to_id ist
        visited_pred = set()
        queue_pred = list(adj.get(dep.to_id, set()))
        while queue_pred:
            nid = queue_pred.pop(0)
            if nid == dep.from_id:
                raise HTTPException(status_code=400, detail="Transitiv redundante Abhaengigkeit")
            if nid in visited_pred:
                continue
            visited_pred.add(nid)
            for pred in adj.get(nid, set()):
                queue_pred.append(pred)

        # Dependency einfuegen
        db.execute(
            "INSERT INTO sub_task_dependencies (sub_task_id, depends_on_id) VALUES (?, ?)",
            (dep.to_id, dep.from_id),
        )

        # Topologische Neuordnung
        new_positions = _topological_reorder(db, task_id)

    log_change(user, "dependency", dep.to_id, "create",
               {"depends_on_id": dep.from_id, "project_id": task_id})
    return {"message": "Abhaengigkeit hinzugefuegt", "new_positions": new_positions}


@router.post("/api/tasks/{task_id}/subtasks/remove-dependency")
async def remove_dependency(task_id: int, dep: DependencyAction, user=Depends(get_current_user)):
    """Abhaengigkeit zwischen Teilaufgaben entfernen."""
    with db_transaction() as db:
        # Projekt pruefen
        task = db.execute("SELECT id, created_by FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not task:
            raise HTTPException(status_code=404, detail="Aufgabe nicht gefunden")

        # Berechtigung pruefen
        is_creator = task["created_by"] == user["id"]
        is_legacy = task["created_by"] is None
        if not is_creator and not is_legacy:
            raise HTTPException(status_code=403, detail="Keine Berechtigung")

        # Spezialfall: Projektknoten-Abhaengigkeit entfernen
        if dep.from_id == 0:
            db.execute(
                "UPDATE sub_tasks SET depends_on_project = 0 WHERE id = ? AND project_id = ?",
                (dep.to_id, task_id),
            )
            removed_project_dep = True
        else:
            removed_project_dep = False
            # Dependency loeschen
            db.execute(
                "DELETE FROM sub_task_dependencies WHERE sub_task_id = ? AND depends_on_id = ?",
                (dep.to_id, dep.from_id),
            )

        # Topologische Neuordnung
        new_positions = _topological_reorder(db, task_id)

    log_change(user, "dependency", dep.to_id, "delete",
               {"depends_on_id": dep.from_id, "project_id": task_id})
    if removed_project_dep:
        return {"message": "Projekt-Abhaengigkeit entfernt", "new_positions": {}}
    return {"message": "Abhaengigkeit entfernt", "new_positions": new_positions}


@router.post("/api/tasks/{task_id}/subtasks/save-netzplan-positions")
async def save_netzplan_positions(task_id: int, payload: NetzplanPositionsSave, user=Depends(get_current_user)):
    """Netzplan-Positionen fuer Teilaufgaben speichern."""
    with db_transaction() as db:
        task = db.execute("SELECT id, created_by FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not task:
            raise HTTPException(status_code=404, detail="Aufgabe nicht gefunden")

        is_creator = task["created_by"] == user["id"]
        is_legacy = task["created_by"] is None
        if not is_creator and not is_legacy:
            membership = db.execute(
                "SELECT can_edit FROM project_members WHERE project_id = ? AND user_id = ?",
                (task_id, user["id"]),
            ).fetchone()
            if not membership or not membership["can_edit"]:
                raise HTTPException(status_code=403, detail="Keine Berechtigung")

        for pos in payload.positions:
            if pos.id == 0:
                db.execute(
                    "UPDATE tasks SET netzplan_project_x = ?, netzplan_project_y = ? WHERE id = ?",
                    (pos.x, pos.y, task_id),
                )
            else:
                db.execute(
                    "UPDATE sub_tasks SET netzplan_x = ?, netzplan_y = ? WHERE id = ? AND project_id = ?",
                    (pos.x, pos.y, pos.id, task_id),
                )
        return {"message": "Positionen gespeichert"}


# ============================================================
# Users List
# ============================================================

@router.get("/api/users/list")
async def get_users_list(user=Depends(get_current_user)):
    """Alle Benutzer als Liste (fuer Zuweisungs-Dropdown)."""
    with db_query() as db:
        rows = db.execute(
            "SELECT id, vorname, nachname FROM users ORDER BY vorname, nachname"
        ).fetchall()
        return {
            "items": [
                {"id": r["id"], "vorname": r["vorname"], "nachname": r["nachname"]}
                for r in rows
            ]
        }


# ============================================================
# Task Notes
# ============================================================

@router.get("/api/tasks/{task_id}/notes")
async def get_task_notes(task_id: int, user=Depends(get_current_user)):
    """Notizen fuer eine Aufgabe abrufen."""
    with db_query() as db:
        rows = db.execute(
            """SELECT tn.*, u.vorname || ' ' || u.nachname AS user_name
               FROM task_notes tn
               JOIN users u ON tn.user_id = u.id
               WHERE tn.task_id = ?
               ORDER BY tn.updated_at DESC""",
            (task_id,),
        ).fetchall()
        return {
            "items": [
                {
                    "user_id": r["user_id"],
                    "user_name": (r["user_name"] or "").strip(),
                    "content": r["content"] or "",
                    "updated_at": _format_date(r["updated_at"]),
                }
                for r in rows
            ]
        }


@router.put("/api/tasks/{task_id}/notes")
async def upsert_task_note(task_id: int, note: NoteUpdate, user=Depends(get_current_user)):
    """Eigene Notiz fuer eine Aufgabe erstellen/aktualisieren."""
    with db_transaction() as db:
        db.execute(
            """INSERT INTO task_notes (task_id, user_id, content, updated_at)
               VALUES (?, ?, ?, datetime('now'))
               ON CONFLICT(task_id, user_id) DO UPDATE SET
                   content = excluded.content,
                   updated_at = datetime('now')""",
            (task_id, user["id"], note.content),
        )
    log_change(user, "task_note", task_id, "update", {"content_len": len(note.content)})
    return {"message": "Notiz gespeichert"}


@router.get("/api/tasks/{task_id}/subtasks/{subtask_id}/notes")
async def get_subtask_notes(task_id: int, subtask_id: int, user=Depends(get_current_user)):
    """Notizen fuer eine Teilaufgabe abrufen."""
    with db_query() as db:
        rows = db.execute(
            """SELECT sn.*, u.vorname || ' ' || u.nachname AS user_name
               FROM sub_task_notes sn
               JOIN users u ON sn.user_id = u.id
               WHERE sn.sub_task_id = ?
               ORDER BY sn.updated_at DESC""",
            (subtask_id,),
        ).fetchall()
        return {
            "items": [
                {
                    "user_id": r["user_id"],
                    "user_name": (r["user_name"] or "").strip(),
                    "content": r["content"] or "",
                    "updated_at": _format_date(r["updated_at"]),
                }
                for r in rows
            ]
        }


@router.put("/api/tasks/{task_id}/subtasks/{subtask_id}/notes")
async def upsert_subtask_note(task_id: int, subtask_id: int, note: NoteUpdate, user=Depends(get_current_user)):
    """Eigene Notiz fuer eine Teilaufgabe erstellen/aktualisieren."""
    with db_transaction() as db:
        db.execute(
            """INSERT INTO sub_task_notes (sub_task_id, user_id, content, updated_at)
               VALUES (?, ?, ?, datetime('now'))
               ON CONFLICT(sub_task_id, user_id) DO UPDATE SET
                   content = excluded.content,
                   updated_at = datetime('now')""",
            (subtask_id, user["id"], note.content),
        )
    log_change(user, "sub_task_note", subtask_id, "update", {"content_len": len(note.content)})
    return {"message": "Notiz gespeichert"}


# ============================================================
# Areas
# ============================================================

@router.get("/api/areas")
async def get_areas(user=Depends(get_current_user)):
    """Alle Bereiche abrufen."""
    with db_query() as db:
        rows = db.execute("SELECT * FROM areas ORDER BY name").fetchall()
        return {"items": [{"id": r["id"], "name": r["name"]} for r in rows]}


@router.post("/api/areas")
async def create_area(area: AreaCreate, user=Depends(get_current_user)):
    """Neuen Bereich anlegen."""
    try:
        with db_transaction() as db:
            cursor = db.execute("INSERT INTO areas (name) VALUES (?)", (area.name,))
            return {"id": cursor.lastrowid, "name": area.name}
    except Exception:
        raise HTTPException(status_code=400, detail="Bereich existiert bereits")


# ============================================================
# ExpandableTable Config
# ============================================================

@router.get("/api/tasks/config")
async def get_tasks_config():
    """ExpandableTable-Konfiguration fuer den Aufgaben-Tab."""
    table = ExpandableTable(
        id="aufgaben",
        api_endpoint="/api/tasks",
        expandable=True,
        columns=[
            Column("Name", "name", width=0, sortable=True, i18n_key="tasks.col.name"),
            Column("Typ", "task_type", width=90, sortable=True, renderer="badge", i18n_key="tasks.col.type"),
            Column("Status", "status", width=90, sortable=True, renderer="badge", i18n_key="tasks.col.status"),
            Column("Prioritaet", "priority", width=80, sortable=True, align="center", i18n_key="tasks.col.priority"),
            Column("Von", "created_by_name", width=120, sortable=True, i18n_key="tasks.col.from"),
            Column("Zugewiesen an", "assigned_to_name", width=140, sortable=True, i18n_key="tasks.col.assignedTo"),
            Column("Erstellt", "created_at", width=100, sortable=True, i18n_key="tasks.col.created"),
            Column("Deadline", "deadline", width=130, sortable=True, i18n_key="tasks.col.deadline"),
            Column("", "_actions", width=36, sortable=False, renderer="deleteAction", align="center"),
        ],
        detail_fields=[],
        default_sort=("priority", "asc"),
        filters=[
            Filter(
                id="typeFilter",
                label="Typ",
                type="select",
                field="task_type",
                options=[
                    {"value": "", "label": "Alle"},
                    {"value": "aufgabe", "label": "Aufgabe"},
                    {"value": "projekt", "label": "Projekt"},
                ],
            ),
            Filter(
                id="statusFilter",
                label="Status",
                type="select",
                field="status",
                options=[
                    {"value": "", "label": "Alle"},
                    {"value": "offen", "label": "Offen"},
                    {"value": "in_arbeit", "label": "In Arbeit"},
                    {"value": "erledigt", "label": "Erledigt"},
                ],
            ),
            Filter(
                id="searchFilter",
                label="Suche",
                type="input",
                field="name",
                placeholder="Suche...",
                wildcard=True,
                wildcard_fields=["description"],
            ),
        ],
    )
    return table.to_dict()
