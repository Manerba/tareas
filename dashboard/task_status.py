"""Gemeinsame Statusableitung fuer REST- und MCP-Aufgabenabfragen.

Aufgaben behalten ihren manuellen Status. Bei Projekten hat ein Abbruch
Vorrang; sonst zaehlen ausschliesslich vollstaendig erledigte Teilaufgaben.
Insbesondere bleibt ein Projekt mit 0 % und 50 % Teilaufgaben noch offen.
Der gespeicherte Projektstatus wird durch das Lesen nicht veraendert.
"""


_TASK_STATUS_CTE = """
WITH subtask_progress AS (
    SELECT project_id,
           COUNT(*) AS subtask_total,
           SUM(CASE WHEN status_percent >= 100 THEN 1 ELSE 0 END) AS subtask_done
    FROM sub_tasks
    {subtask_filter}
    GROUP BY project_id
), tasks_with_status AS (
    SELECT t.*,
           (SELECT st.project_id FROM sub_tasks st WHERE st.id = t.parent_subtask_id) AS parent_project_id,
           COALESCE(progress.subtask_total, 0) AS subtask_total,
           COALESCE(progress.subtask_done, 0) AS subtask_done,
           CASE WHEN COALESCE(progress.subtask_total, 0) = 0 THEN 0
                ELSE progress.subtask_done * 100 / progress.subtask_total END AS progress_percent,
           CASE
               WHEN COALESCE(t.task_type, '') != 'projekt' OR t.status = 'abgebrochen' THEN t.status
               WHEN COALESCE(progress.subtask_total, 0) = 0 THEN 'offen'
               WHEN progress.subtask_done >= progress.subtask_total THEN 'erledigt'
               WHEN progress.subtask_done > 0 THEN 'in_arbeit'
               ELSE 'offen'
           END AS effective_status
    FROM tasks t
    LEFT JOIN subtask_progress progress ON progress.project_id = t.id
)
"""

# Listen verwenden diesen CTE mit `FROM tasks_with_status t`. Sichtbarkeit,
# Statusfilter (`t.effective_status`) und Pagination folgen im selben SELECT.
# Die Zaehler werden gemeinsam aggregiert, ohne einzelne Projektabfragen.
TASK_STATUS_CTE = _TASK_STATUS_CTE.format(subtask_filter="")


def task_with_status(db, task_id: int):
    """Eine Aufgabe mit effektiven Status- und Fortschrittswerten lesen."""
    # Einzelabrufe zaehlen nur die Teilaufgaben des betroffenen Projekts.
    return db.execute(
        _TASK_STATUS_CTE.format(subtask_filter="WHERE project_id = ?")
        + "SELECT * FROM tasks_with_status WHERE id = ?",
        (task_id, task_id),
    ).fetchone()
