"""Gemeinsame Aufgabenrechte fuer REST, MCP, Dateien und UI-Metadaten."""

from fastapi import HTTPException


def task_permissions(db, task, user: dict) -> dict:
    manager = bool(user.get("is_admin")) or task["created_by"] == user["id"]
    legacy = task["created_by"] is None
    assignee = task["assigned_to"] == user["id"]
    member = db.execute(
        "SELECT can_read, can_edit, can_create FROM project_members WHERE project_id = ? AND user_id = ?",
        (task["id"], user["id"]),
    ).fetchone()
    shared_edit = bool(member and member["can_edit"])
    shared_create = bool(member and member["can_create"] and task["task_type"] == "projekt")
    shared_read = bool(member and (member["can_read"] or shared_edit or shared_create))
    structure = manager or legacy or shared_edit
    edit = structure or (assignee and user.get("auth_source") == "mcp")
    return {
        "can_read": manager or legacy or assignee or shared_read,
        "can_edit": edit,
        "can_edit_status": edit or assignee,
        "can_manage": manager,
        "can_create": task["task_type"] == "projekt" and (manager or legacy or shared_create),
        "can_structure": structure,
        "can_contribute": edit or assignee,
    }


def require_task_access(db, task_id: int, user: dict, action: str | None = "read"):
    task = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not task:
        raise HTTPException(status_code=404, detail="Aufgabe nicht gefunden")
    rights = task_permissions(db, task, user)
    if action and not rights[f"can_{action}"]:
        raise HTTPException(status_code=403, detail="Keine Berechtigung fuer diese Aufgabe")
    return task, rights


def subtask_permissions(task, rights: dict, subtask, user: dict) -> dict:
    assigned = subtask["assigned_to"] == user["id"]
    # Projektzuweisung allein vererbt Lesen und eigene Beitraege, kein Bearbeiten.
    return {
        **rights,
        "can_read": rights["can_read"] or assigned,
        "can_edit": rights["can_structure"] or assigned,
        "can_edit_status": rights["can_structure"] or assigned,
        "can_contribute": rights["can_structure"] or task["assigned_to"] == user["id"] or assigned,
    }


def require_subtask_access(db, subtask_id: int, user: dict, action: str | None = "read", *, task_id: int | None = None):
    subtask = db.execute("SELECT * FROM sub_tasks WHERE id = ?", (subtask_id,)).fetchone()
    if not subtask or (task_id is not None and subtask["project_id"] != task_id):
        raise HTTPException(status_code=404, detail="Teilaufgabe nicht gefunden")
    task, rights = require_task_access(db, subtask["project_id"], user, None)
    rights = subtask_permissions(task, rights, subtask, user)
    if action and not rights[f"can_{action}"]:
        raise HTTPException(status_code=403, detail="Keine Berechtigung fuer diese Teilaufgabe")
    return subtask, rights


def task_visibility_sql(user: dict, *, subtask: bool = False) -> tuple[str, list[int]]:
    """Feste Aliase t/st fuer Listen und Suche, gleiche Regeln wie Einzelzugriffe."""
    if user.get("is_admin"):
        return "1=1", []
    own_subtask = " OR st.assigned_to = ?" if subtask else ""
    clause = (
        "(t.created_by IS NULL OR t.created_by = ? OR t.assigned_to = ?"
        + own_subtask +
        " OR EXISTS (SELECT 1 FROM project_members pm WHERE pm.project_id = t.id AND pm.user_id = ?"
        " AND (pm.can_read = 1 OR pm.can_edit = 1 OR (pm.can_create = 1 AND t.task_type = 'projekt'))))"
    )
    return clause, [user["id"]] * (4 if subtask else 3)
