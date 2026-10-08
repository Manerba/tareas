"""Gemeinsame Pruefung und Speicherung von Teilaufgaben-Abhaengigkeiten."""

from collections import deque

from dashboard.errors import ApplicationError


def load_dependency_graph(db, project_id: int) -> dict[int, set[int]]:
    graph = {0: set()}
    for row in db.execute(
        "SELECT id, depends_on_project FROM sub_tasks WHERE project_id = ?", (project_id,)
    ):
        graph[row["id"]] = {0} if row["depends_on_project"] else set()
    for row in db.execute(
        """SELECT d.sub_task_id, d.depends_on_id FROM sub_task_dependencies d
           JOIN sub_tasks st ON st.id = d.sub_task_id WHERE st.project_id = ?""", (project_id,)
    ):
        graph[row["sub_task_id"]].add(row["depends_on_id"])
    return graph


def dependency_ancestors(graph: dict[int, set[int]]) -> dict[int, set[int]]:
    result = {}
    for node, predecessors in graph.items():
        visited = set()
        pending = list(predecessors)
        while pending:
            predecessor = pending.pop()
            if predecessor in visited:
                continue
            visited.add(predecessor)
            pending.extend(graph.get(predecessor, ()))
        result[node] = visited
    return result


def redundant_dependencies(graph: dict[int, set[int]]) -> set[tuple[int, int]]:
    """Direkte Kanten, fuer die auch ein Weg ueber andere Vorgaenger existiert."""
    ancestors = dependency_ancestors(graph)
    return {
        (node, predecessor)
        for node, predecessors in graph.items()
        for predecessor in predecessors
        if any(predecessor in ancestors.get(other, ()) for other in predecessors if other != predecessor)
    }


def dependency_path(graph, node: int, predecessor: int) -> list[int]:
    """Alternativen Weg finden, ohne die direkte Kante zu verwenden."""
    pending = deque([[node]])
    visited = {node}
    while pending:
        path = pending.popleft()
        for parent in sorted(graph.get(path[-1], ())):
            if path[-1] == node and parent == predecessor:
                continue
            if parent == predecessor:
                return [*path, parent]
            if parent not in visited:
                visited.add(parent)
                pending.append([*path, parent])
    return []


def _begin_dependency_write(db):
    # Vor Lesen des Graphen sperren: parallele Requests duerfen nicht jeweils
    # einen veralteten Stand validieren und zusammen eine ungueltige Kante bilden.
    if not db.in_transaction:
        db.execute("BEGIN IMMEDIATE")


def set_subtask_predecessors(db, subtask_id: int, predecessor_ids: list[int]):
    """Vorgaenger atomar ersetzen; die aufrufende Transaktion committet/rollt zurueck."""
    _begin_dependency_write(db)
    task = db.execute("SELECT project_id FROM sub_tasks WHERE id = ?", (subtask_id,)).fetchone()
    if not task:
        raise ApplicationError(404, "Teilaufgabe nicht gefunden", code="subtask_not_found", field="subtask_id")
    graph = load_dependency_graph(db, task["project_id"])
    predecessors = set(predecessor_ids)
    if subtask_id in predecessors:
        raise ApplicationError(400, "Eine Teilaufgabe darf nicht von sich selbst abhaengen",
                               code="dependency_self_reference", field="predecessor_ids")
    if any(predecessor not in graph for predecessor in predecessors):
        raise ApplicationError(400, "Alle Vorgaenger muessen zum selben Projekt gehoeren",
                               code="dependency_wrong_project", field="predecessor_ids")
    if 0 in predecessors and len(predecessors) > 1:
        raise ApplicationError(400, "Der Projektknoten ist nur ohne weitere Vorgaenger erlaubt",
                               code="dependency_project_exclusive", field="predecessor_ids")

    proposed = {**graph, subtask_id: predecessors}
    ancestors = dependency_ancestors(proposed)
    for predecessor in predecessors - graph[subtask_id]:
        if subtask_id in ancestors[predecessor]:
            raise ApplicationError(400, "Zirkulaere Abhaengigkeit nicht erlaubt",
                                   code="dependency_cycle", field="predecessor_ids")

    # Auch pruefen, ob die Aenderung eine bisher notwendige Kante an einer
    # anderen Teilaufgabe redundant macht. Vorhandene Altfehler duerfen weiterhin
    # schrittweise entfernt werden und blockieren keine unveraenderten Felder.
    introduced = redundant_dependencies(proposed) - redundant_dependencies(graph)
    if introduced:
        node, predecessor = sorted(introduced)[0]
        path = dependency_path(proposed, node, predecessor)
        via = ", ".join(f"#{item}" for item in path[1:-1])
        raise ApplicationError(
            status_code=400,
            detail=(f"Transitiv redundante Abhaengigkeit: #{node} waere bereits ueber {via} "
                    f"von #{predecessor} abhaengig. Entferne zuerst die ueberfluessige direkte Verbindung."),
            code="dependency_redundant", field="predecessor_ids",
        )

    db.execute("DELETE FROM sub_task_dependencies WHERE sub_task_id = ?", (subtask_id,))
    db.execute("UPDATE sub_tasks SET depends_on_project = ? WHERE id = ?", (int(0 in predecessors), subtask_id))
    db.executemany(
        "INSERT INTO sub_task_dependencies (sub_task_id, depends_on_id) VALUES (?, ?)",
        [(subtask_id, predecessor) for predecessor in sorted(predecessors) if predecessor != 0],
    )


def add_subtask_dependency(db, subtask_id: int, predecessor_id: int, *, allow_existing: bool = False):
    _begin_dependency_write(db)
    task = db.execute("SELECT depends_on_project FROM sub_tasks WHERE id = ?", (subtask_id,)).fetchone()
    if not task:
        raise ApplicationError(404, "Teilaufgabe nicht gefunden", code="subtask_not_found", field="subtask_id")
    predecessors = [row[0] for row in db.execute(
        "SELECT depends_on_id FROM sub_task_dependencies WHERE sub_task_id = ?", (subtask_id,)
    )]
    if task["depends_on_project"]:
        predecessors.append(0)
    if predecessor_id in predecessors:
        if allow_existing:
            return
        raise ApplicationError(400, "Abhaengigkeit existiert bereits",
                               code="dependency_exists", field="predecessor_ids")
    set_subtask_predecessors(db, subtask_id, [*predecessors, predecessor_id])
