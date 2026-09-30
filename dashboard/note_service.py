"""Gemeinsame Admin-Bearbeitung bestehender Notizen und Handoffs fuer REST/MCP."""

import hashlib

from fastapi import HTTPException

from dashboard.audit_log import log_change
from dashboard.db_utils import db_transaction


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
        raise HTTPException(status_code=403, detail="Nur Admins duerfen fremde Notizen und Handoffs bearbeiten")
    if (note_user_id is None) == (entry_id is None):
        raise HTTPException(status_code=422, detail="Notiz-Autor oder Handoff-ID erforderlich")
    is_handoff = entry_id is not None
    if is_handoff and not content.strip():
        raise HTTPException(status_code=422, detail="Handoff darf nicht leer sein")

    with db_transaction() as db:
        db.execute("BEGIN IMMEDIATE")
        if not db.execute("SELECT id FROM tasks WHERE id = ?", (task_id,)).fetchone():
            raise HTTPException(status_code=404, detail="Aufgabe nicht gefunden")
        if subtask_id is not None and not db.execute(
            "SELECT id FROM sub_tasks WHERE id = ? AND project_id = ?", (subtask_id, task_id),
        ).fetchone():
            raise HTTPException(status_code=404, detail="Teilaufgabe nicht gefunden")

        entity_id = subtask_id if subtask_id is not None else task_id
        select, update, entity_type = _STATEMENTS[(subtask_id is not None, is_handoff)]
        existing = db.execute(select, (entity_id, entry_id if is_handoff else note_user_id)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Notiz oder Handoff nicht gefunden")
        if is_handoff and revision is not None and revision != handoff_revision(existing["content"] or "", existing["content_format"]):
            raise HTTPException(status_code=409, detail="handoff.conflict")
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
