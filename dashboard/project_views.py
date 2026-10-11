"""Kompakte Projektansichten und rein abgeleitete Arbeitsbereitschaft.

IDs sind stabile Referenzen, position_number ist die veraenderliche Reihenfolge.
Namen bleiben fachliche Titel, auch wenn sie sinnvolle Zahlen enthalten.
"""

from collections import Counter

from dashboard.errors import ApplicationError
from dashboard.permissions import task_permissions
from dashboard.task_status import task_with_status


HANDOFF_PREVIEW_LIMIT = 200
READINESS_STATES = ("completed", "ready", "blocked", "delegated", "cancelled", "restricted")


def without_descriptions(payload):
    """Kopiert eine Antwort ohne Volltexte; kurze Format-/Revisionsmetadaten bleiben."""
    if isinstance(payload, list):
        return [without_descriptions(item) for item in payload]
    if isinstance(payload, dict):
        return {key: without_descriptions(value) for key, value in payload.items() if key != "description"}
    return payload


def handoff_preview(content: str | None) -> str:
    """Erste nichtleere Zeile, maximal 200 Zeichen; keine HTML-Ausgabe."""
    return next((line.strip()[:HANDOFF_PREVIEW_LIMIT] for line in (content or "").splitlines() if line.strip()), "")


def derive_readiness(subtasks: list[dict], predecessors: dict[int, list[int]], *, cancelled: bool = False) -> dict[int, dict]:
    """Wertet nur sichtbare Teilaufgaben aus; unsichtbare Vorgaenger bleiben unbekannt.

    100 Prozent ist erledigt. Abbruch sperrt jede offene Arbeit. Verknuepfte
    Kindprojekte sind delegierte Arbeit, nicht direkt startbereit. Projektknoten
    0 ist ein Startanker ohne Fortschrittsblockade. Unsichtbare Vorgaenger werden
    weder mit ID noch Fortschritt offengelegt; 'restricted' vermeidet auch eine
    indirekte Offenlegung ihres Erledigungsstatus ueber 'ready'/'blocked'.
    """
    visible = {item["id"]: item for item in subtasks}
    result = {}
    for item in subtasks:
        sid = item["id"]
        deps = set(predecessors.get(sid, [])) - {0}
        blockers = sorted(pid for pid in deps if pid in visible and visible[pid]["status_percent"] < 100)
        restricted = bool(deps - visible.keys())
        if item["status_percent"] >= 100:
            state, blockers = "completed", []
        elif cancelled:
            state = "cancelled"
        elif item.get("child_project_id") is not None:
            state = "delegated"
        elif restricted:
            state = "restricted"
        elif blockers:
            state = "blocked"
        else:
            state = "ready"
        result[sid] = {"readiness": state, "blocker_ids": blockers}
    return result


def get_project_status(db, project_id: int, user: dict) -> dict:
    """Liest Uebersicht, letzte Handoffs und Graph in einem Snapshot (vier SELECTs).

    Der Aufrufer besitzt die Verbindung. Eine vorhandene Transaktion bleibt
    erhalten; sonst wird ein eigener reiner Lese-Snapshot begonnen/beendet.
    Projektmetadaten und Zaehler enthalten bei Teilaufgaben-Zuweisungen keine
    fremden Inhalte. Handoffs werden in einer Mengenabfrage gelesen.
    """
    own_transaction = not db.in_transaction
    if own_transaction:
        db.execute("BEGIN")
    try:
        task = task_with_status(db, project_id)
        if not task:
            raise ApplicationError(404, "Projekt nicht gefunden", code="task_not_found", field="project_id")
        rights = task_permissions(db, task, user)
        visible_args = (project_id, bool(rights["can_read"]), user["id"])
        rows = db.execute(
            """SELECT st.id, st.project_id, st.position_number, st.name,
                      st.area_id, a.name AS area_name, st.status_percent,
                      st.depends_on_project, st.assigned_to,
                      child.id AS child_project_id,
                      latest.id AS handoff_id, latest.user_id AS handoff_user_id,
                      latest.created_at AS handoff_created_at, latest.content AS handoff_content,
                      COALESCE(NULLIF(TRIM(COALESCE(u.vorname, '') || ' ' || COALESCE(u.nachname, '')), ''),
                               u.username) AS handoff_user_name
               FROM sub_tasks st
               LEFT JOIN areas a ON a.id = st.area_id
               LEFT JOIN tasks child ON child.parent_subtask_id = st.id
               LEFT JOIN sub_task_note_entries latest ON latest.id = (
                   SELECT e.id FROM sub_task_note_entries e WHERE e.sub_task_id = st.id
                   ORDER BY e.created_at DESC, e.id DESC LIMIT 1
               )
               LEFT JOIN users u ON u.id = latest.user_id
               WHERE st.project_id = ? AND (? OR st.assigned_to = ?)
               ORDER BY st.position_number ASC, st.id ASC""",
            visible_args,
        ).fetchall()
        if not rights["can_read"] and not rows:
            raise ApplicationError(403, "Keine Leseberechtigung", code="permission_denied", field="project_id")
        dependencies = db.execute(
            """SELECT d.sub_task_id, d.depends_on_id
               FROM sub_task_dependencies d JOIN sub_tasks st ON st.id = d.sub_task_id
               WHERE st.project_id = ? AND (? OR st.assigned_to = ?)
               ORDER BY d.sub_task_id, d.depends_on_id""",
            visible_args,
        ).fetchall()
        predecessors = {}
        for dependency in dependencies:
            predecessors.setdefault(dependency["sub_task_id"], []).append(dependency["depends_on_id"])
        subtasks = []
        for row in rows:
            item = {key: row[key] for key in (
                "id", "project_id", "position_number", "name", "area_id", "area_name",
                "status_percent", "assigned_to", "child_project_id",
            )}
            item["progress_automatic"] = row["child_project_id"] is not None
            item["depends_on_project"] = bool(row["depends_on_project"])
            item["latest_handoff"] = None if row["handoff_id"] is None else {
                "handoff_id": f"subtask:{row['handoff_id']}",
                "user_id": row["handoff_user_id"], "user_name": row["handoff_user_name"],
                "created_at": row["handoff_created_at"], "preview": handoff_preview(row["handoff_content"]),
            }
            subtasks.append(item)
        readiness = derive_readiness(subtasks, predecessors, cancelled=task["status"] == "abgebrochen")
        for item in subtasks:
            item.update(readiness[item["id"]])
        counts = Counter(item["readiness"] for item in subtasks)
        summary = {"total": len(subtasks), **{state: counts[state] for state in READINESS_STATES}}
        if rights["can_read"]:
            project = {key: task[key] for key in (
                "id", "name", "task_type", "priority", "deadline", "parent_subtask_id", "parent_project_id", "progress_percent",
            )}
            project["status"] = task["effective_status"]
        else:
            project = {"id": project_id, "limited": True}
        return {"project": project, "counts": summary, "subtasks": subtasks}
    finally:
        if own_transaction:
            db.rollback()
