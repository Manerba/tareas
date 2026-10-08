"""Projektverknuepfungen und Fortschrittsweitergabe, gemeinsam fuer REST/MCP.

Aufrufer halten BEGIN IMMEDIATE ueber Pruefung und Schreiben. Berechnete
Prozentwerte werden in derselben Transaktion bis zur Wurzel aktualisiert.
Beim Trennen/Loeschen bleibt der letzte Prozentwert manuell bearbeitbar.
Die Verknuepfung vererbt keine Zugriffsrechte.
"""

from dashboard.errors import ApplicationError
from dashboard.permissions import require_subtask_access, require_task_access


# Mit Alias st verwenden, damit alle Teilaufgaben-Antworten dieselben Metadaten liefern.
SUBTASK_LINK_SELECT = """SELECT st.*,
    (SELECT child.id FROM tasks child WHERE child.parent_subtask_id = st.id) AS child_project_id
    FROM sub_tasks st"""


def ensure_manual_progress(db, subtask_id: int):
    if db.execute("SELECT 1 FROM tasks WHERE parent_subtask_id = ?", (subtask_id,)).fetchone():
        raise ApplicationError(
            409,
            "Der Fortschritt dieser Teilaufgabe wird automatisch aus dem verknuepften "
            "Kind-Projekt berechnet. Aendern Sie dessen Teilaufgaben oder loesen Sie die Verknuepfung.",
            code="linked_project_progress_readonly", field="status_percent",
        )


def set_parent_subtask(db, project_id: int, parent_subtask_id: int | None, user: dict):
    """0/None loest die Verknuepfung; ein Subtask hat hoechstens ein Kind-Projekt."""
    if parent_subtask_id is not None and (
        type(parent_subtask_id) is not int or parent_subtask_id < 0
    ):
        raise ApplicationError(422, "PID muss eine ganze Zahl ab 0 sein",
                               code="invalid_parent_subtask", field="parent_subtask_id")
    parent_subtask_id = parent_subtask_id or None
    project, _ = require_task_access(db, project_id, user, "edit")
    old_parent = project["parent_subtask_id"]
    if old_parent:
        require_subtask_access(db, old_parent, user, "edit")
    if parent_subtask_id:
        parent, _ = require_subtask_access(db, parent_subtask_id, user, "edit")
        owner, _ = require_task_access(db, parent["project_id"], user, None)
        if project["task_type"] != "projekt" or owner["task_type"] != "projekt":
            raise ApplicationError(409, "Nur Projekte koennen mit Teilaufgaben eines Projekts verknuepft werden",
                                   code="project_link_requires_project", field="parent_subtask_id")
        if db.execute(
            "SELECT 1 FROM tasks WHERE parent_subtask_id = ? AND id != ?",
            (parent_subtask_id, project_id),
        ).fetchone():
            raise ApplicationError(409, "Diese Teilaufgabe hat bereits ein Kind-Projekt",
                                   code="parent_subtask_already_linked", field="parent_subtask_id")
        # Vom vorgeschlagenen Elternprojekt aufwaerts laufen. Auch indirekte
        # Rueckverweise auf das Kind wuerden einen Zyklus erzeugen.
        ancestor = parent["project_id"]
        visited = {project_id}
        while ancestor:
            if ancestor in visited:
                raise ApplicationError(409, "Die Projektverknuepfung wuerde einen Zyklus erzeugen",
                                       code="project_link_cycle", field="parent_subtask_id")
            visited.add(ancestor)
            row = db.execute(
                "SELECT st.project_id FROM tasks t JOIN sub_tasks st ON st.id = t.parent_subtask_id WHERE t.id = ?",
                (ancestor,),
            ).fetchone()
            ancestor = row["project_id"] if row else None
    db.execute("UPDATE tasks SET parent_subtask_id = ? WHERE id = ?", (parent_subtask_id, project_id))
    refresh_parent_progress(db, project_id)


def ensure_project_type_change(db, project_id: int, task_type: str):
    if task_type == "projekt":
        return
    linked = db.execute(
        """SELECT 1 FROM tasks WHERE id = ? AND parent_subtask_id IS NOT NULL
           UNION ALL
           SELECT 1 FROM tasks child JOIN sub_tasks st ON st.id = child.parent_subtask_id
           WHERE st.project_id = ? LIMIT 1""", (project_id, project_id),
    ).fetchone()
    if linked:
        raise ApplicationError(409, "Vor dem Typwechsel muessen die Projektverknuepfungen geloest werden",
                               code="project_has_links", field="task_type")


def refresh_parent_progress(db, project_id: int):
    """Anteil vollstaendig erledigter Teilaufgaben; leer = 0, abrunden auf Ganzzahl.

    Abbruch bleibt ein separater Projektstatus und veraendert diesen Fortschritt
    nicht. 100 % werden ausschliesslich bei vollstaendiger Erledigung erreicht.
    """
    visited = set()
    while project_id:
        if project_id in visited:
            raise ApplicationError(409, "Zyklus in Projektverknuepfungen",
                                   code="project_link_cycle", field="parent_subtask_id")
        visited.add(project_id)
        parent = db.execute(
            "SELECT st.id, st.project_id FROM tasks t JOIN sub_tasks st ON st.id = t.parent_subtask_id WHERE t.id = ?",
            (project_id,),
        ).fetchone()
        if not parent:
            return
        progress = db.execute(
            """SELECT COUNT(*) AS total,
                      COALESCE(SUM(CASE WHEN status_percent >= 100 THEN 1 ELSE 0 END), 0) AS done
               FROM sub_tasks WHERE project_id = ?""", (project_id,),
        ).fetchone()
        percent = progress["done"] * 100 // progress["total"] if progress["total"] else 0
        db.execute("UPDATE sub_tasks SET status_percent = ? WHERE id = ?", (percent, parent["id"]))
        project_id = parent["project_id"]
