"""Gemeinsame Admin-Bearbeitung bestehender Notizen und Handoffs fuer REST/MCP."""

import hashlib

from dashboard.audit_log import log_change
from dashboard.db_utils import db_transaction
from dashboard.errors import ApplicationError


# Ausschliesslich feste Statements; weder Tabellen noch Spalten kommen vom Aufrufer.
_STATEMENTS = {
    (False, False): (
        "SELECT id, user_id FROM task_notes WHERE task_id = ? AND user_id = ?",
        "UPDATE task_notes SET content = ?, content_format = 'markdown', "
        "updated_at = datetime('now') WHERE id = ?",
        "task_note",
    ),
    (True, False): (
        "SELECT id, user_id FROM sub_task_notes WHERE sub_task_id = ? AND user_id = ?",
        "UPDATE sub_task_notes SET content = ?, content_format = 'markdown', "
        "updated_at = datetime('now') WHERE id = ?",
        "sub_task_note",
    ),
    (False, True): (
        "SELECT id, user_id, content, content_format FROM task_note_entries WHERE task_id = ? AND id = ?",
        "UPDATE task_note_entries SET content = ?, content_format = 'markdown' WHERE id = ?",
        "task_handoff",
    ),
    (True, True): (
        "SELECT id, user_id, content, content_format FROM sub_task_note_entries WHERE sub_task_id = ? AND id = ?",
        "UPDATE sub_task_note_entries SET content = ?, content_format = 'markdown' WHERE id = ?",
        "sub_task_handoff",
    ),
}


def handoff_revision(content: str, content_format: str) -> str:
    return hashlib.sha256(f"{content_format}\0{content}".encode()).hexdigest()


def update_note_as_admin(
    task_id: int, content: str, user: dict, *, subtask_id: int | None = None,
    note_user_id: int | None = None, entry_id: int | None = None, revision: str | None = None,
) -> dict:
    """Aendert Inhalt, bewahrt Autor/Zeitpunkt und protokolliert den Admin als Akteur."""
    if not user.get("is_admin"):
        raise ApplicationError(403, "Nur Admins duerfen fremde Notizen und Handoffs bearbeiten", code="permission_denied")
    if (note_user_id is None) == (entry_id is None):
        raise ApplicationError(422, "Notiz-Autor oder Handoff-ID erforderlich", code="invalid_note_target",
                               fields=["note_user_id", "handoff_id"])
    is_handoff = entry_id is not None
    if is_handoff and not content.strip():
        raise ApplicationError(422, "Handoff darf nicht leer sein", code="empty_content", field="content")

    with db_transaction() as db:
        db.execute("BEGIN IMMEDIATE")
        if not db.execute("SELECT id FROM tasks WHERE id = ?", (task_id,)).fetchone():
            raise ApplicationError(404, "Aufgabe nicht gefunden", code="task_not_found", field="task_id")
        if subtask_id is not None and not db.execute(
            "SELECT id FROM sub_tasks WHERE id = ? AND project_id = ?", (subtask_id, task_id),
        ).fetchone():
            raise ApplicationError(404, "Teilaufgabe nicht gefunden", code="subtask_not_found", field="subtask_id")

        entity_id = subtask_id if subtask_id is not None else task_id
        select, update, entity_type = _STATEMENTS[(subtask_id is not None, is_handoff)]
        existing = db.execute(select, (entity_id, entry_id if is_handoff else note_user_id)).fetchone()
        if not existing:
            raise ApplicationError(404, "Notiz oder Handoff nicht gefunden",
                                   code="handoff_not_found" if is_handoff else "note_not_found",
                                   field="handoff_id" if is_handoff else "note_user_id")
        if is_handoff and revision is not None and revision != handoff_revision(existing["content"] or "", existing["content_format"]):
            raise ApplicationError(409, "handoff.conflict", code="revision_conflict", field="revision")
        db.execute(update, (content, existing["id"]))

    changes = {
        "local_id": existing["id"], "owner_user_id": existing["user_id"],
        "edited_by_admin": True, "content_len": len(content),
    }
    if is_handoff:
        changes["handoff_id"] = f"{'subtask' if subtask_id is not None else 'task'}:{entry_id}"
    log_change(user, entity_type, entity_id, "update", changes)
    result = {"updated": True, "task_id": task_id, "subtask_id": subtask_id, **changes}
    if is_handoff:
        result["revision"] = handoff_revision(content, "markdown")
    return result
