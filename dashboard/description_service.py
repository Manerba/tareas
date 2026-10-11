"""Revisionsgeschuetzte Beschreibungen, gemeinsam fuer REST und MCP.

Revisionen bezeichnen Text und Format, nicht sonstige Aufgabenfelder. Bestehende
Volltext-Updates ohne Revision bleiben kompatible bedingungslose Schreibzugriffe.
Partielle Aenderungen verlangen eine Revision und erhalten das gespeicherte Format.
"""

import hashlib
import re

from dashboard.audit_log import log_change
from dashboard.db_utils import db_transaction
from dashboard.errors import ApplicationError
from dashboard.permissions import require_task_access, require_subtask_access


def description_revision(content: str | None, content_format: str) -> str:
    return hashlib.sha256(f"{content_format}\0{content or ''}".encode("utf-8")).hexdigest()


def check_description_revision(existing, revision: str | None, *, required: bool = False) -> None:
    """Aufrufer muss Lesen, Pruefen und Schreiben unter BEGIN IMMEDIATE halten."""
    if revision is None:
        if required:
            raise ApplicationError(422, "Eine Beschreibungsrevision ist erforderlich",
                                   code="revision_required", field="description_revision")
        return
    if not isinstance(revision, str) or not re.fullmatch(r"[a-f0-9]{64}", revision):
        raise ApplicationError(422, "Ungueltige Beschreibungsrevision",
                               code="invalid_revision", field="description_revision")
    if revision != description_revision(existing["description"], existing["description_format"]):
        raise ApplicationError(409, "description.conflict", code="revision_conflict",
                               field="description_revision")


def patch_description(task_id: int, user: dict, *, revision: str, operation: str,
                      text: str, old_text: str | None = None, subtask_id: int | None = None) -> dict:
    """Exakt anhaengen oder genau eine (auch ueberlappend gezaehlte) Fundstelle ersetzen.

    Keine automatischen Zeilenumbrueche, Markdown-Konvertierung oder Normalisierung.
    Leeres text ist erlaubt, leeres old_text beim Ersetzen nicht.
    """
    if operation not in {"append", "replace"}:
        raise ApplicationError(422, "Operation muss append oder replace sein",
                               code="invalid_description_operation", field="operation")
    if not isinstance(text, str):
        raise ApplicationError(422, "Text erforderlich", code="invalid_text", field="text")
    if operation == "append" and old_text is not None:
        raise ApplicationError(422, "old_text ist nur fuer replace erlaubt",
                               code="invalid_description_target", field="old_text")
    if operation == "replace" and (not isinstance(old_text, str) or not old_text):
        raise ApplicationError(422, "Ein nichtleerer exakter Suchtext ist erforderlich",
                               code="invalid_description_target", field="old_text")
    with db_transaction() as db:
        db.execute("BEGIN IMMEDIATE")
        if subtask_id is None:
            existing, _ = require_task_access(db, task_id, user, "edit")
        else:
            existing, _ = require_subtask_access(db, subtask_id, user, "edit", task_id=task_id)
        check_description_revision(existing, revision, required=True)
        source = existing["description"] or ""
        if operation == "append":
            updated = source + text
        else:
            start = source.find(old_text)
            if start < 0:
                raise ApplicationError(422, "Der exakte Suchtext wurde nicht gefunden",
                                       code="description_target_not_found", field="old_text")
            if source.find(old_text, start + 1) >= 0:
                raise ApplicationError(422, "Der Suchtext kommt mehrfach vor; einen eindeutigen Bereich waehlen",
                                       code="description_target_ambiguous", field="old_text")
            updated = source[:start] + text + source[start + len(old_text):]
        statement = ("UPDATE tasks SET description = ? WHERE id = ?" if subtask_id is None
                     else "UPDATE sub_tasks SET description = ? WHERE id = ?")
        db.execute(statement, (updated, existing["id"]))
        result = {"task_id": task_id, "subtask_id": subtask_id,
                  "description_length": len(updated), "description_format": existing["description_format"],
                  "description_revision": description_revision(updated, existing["description_format"])}
    log_change(user, "task" if subtask_id is None else "sub_task", existing["id"], "update",
               {"description_operation": operation, "description_len": len(updated)})
    return {"updated": True, **result}
