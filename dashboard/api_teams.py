"""
Tareas - API-Router fuer Freigaben von Aufgaben und Projekten.
"""

import hashlib
import json

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from dashboard.db_utils import db_query, db_transaction
from dashboard.auth import get_current_user
from dashboard.audit_log import log_change
from dashboard.permissions import require_task_access

router = APIRouter()


# ============================================================
# Pydantic-Modelle
# ============================================================

class MemberAdd(BaseModel):
    user_id: int
    can_read: bool = True
    can_edit: bool = False
    can_create: bool = False


class MemberUpdate(BaseModel):
    can_read: bool
    can_edit: bool
    can_create: bool



# ============================================================
# Hilfsfunktionen
# ============================================================

def _check_project_creator(db, task_id: int, user: dict):
    return require_task_access(db, task_id, user, "manage")[0]


def _member_values(member, task):
    if member.can_create and task["task_type"] != "projekt":
        raise HTTPException(status_code=422, detail="Nur Projekte erlauben das Erstellen von Teilaufgaben")
    return (int(member.can_read or member.can_edit or member.can_create), int(member.can_edit), int(member.can_create))


def _members_revision(db, task_id):
    rows = db.execute(
        "SELECT user_id, can_read, can_edit, can_create FROM project_members WHERE project_id = ? ORDER BY user_id",
        (task_id,),
    ).fetchall()
    return hashlib.sha256(json.dumps([list(row) for row in rows]).encode()).hexdigest()


class MembersReplace(BaseModel):
    members: list[MemberAdd]
    revision: str


# ============================================================
# Berechtigungen fuer Aufgaben und Projekte (Ersteller und Admins)
# ============================================================

@router.get("/api/tasks/{task_id}/members")
async def get_members(task_id: int, user=Depends(get_current_user)):
    """Freigaben und Benutzer fuer den Einstellungsdialog einer Aufgabe/eines Projekts."""
    with db_query() as db:
        db.execute("BEGIN")
        task = _check_project_creator(db, task_id, user)
        rows = db.execute(
            """SELECT pm.*, u.vorname, u.nachname, u.email
               FROM project_members pm
               JOIN users u ON pm.user_id = u.id
               WHERE pm.project_id = ?
               ORDER BY u.nachname, u.vorname""",
            (task_id,),
        ).fetchall()
        return {
            "task": {"id": task["id"], "name": task["name"], "task_type": task["task_type"],
                     "created_by": task["created_by"], "assigned_to": task["assigned_to"]},
            "revision": _members_revision(db, task_id),
            "users": [dict(row) for row in db.execute(
                "SELECT id, username, vorname, nachname, email, is_admin, is_active FROM users "
                "WHERE is_active = 1 OR id IN (SELECT user_id FROM project_members WHERE project_id = ?) "
                "OR id = ? ORDER BY nachname, vorname, username", (task_id, task["created_by"]),
            )],
            "items": [
                {
                    "user_id": r["user_id"],
                    "vorname": r["vorname"] or "",
                    "nachname": r["nachname"] or "",
                    "email": r["email"] or "",
                    "can_read": bool(r["can_read"]),
                    "can_edit": bool(r["can_edit"]),
                    "can_create": bool(r["can_create"]),
                    "added_at": r["added_at"],
                }
                for r in rows
            ]
        }


@router.post("/api/tasks/{task_id}/members")
async def add_member(task_id: int, member: MemberAdd, user=Depends(get_current_user)):
    """Zusaetzlichen Zugriff auf eine Aufgabe/ein Projekt vergeben."""
    with db_transaction() as db:
        task = _check_project_creator(db, task_id, user)

        # User existiert?
        target = db.execute("SELECT id FROM users WHERE id = ?", (member.user_id,)).fetchone()
        if not target:
            raise HTTPException(status_code=404, detail="Benutzer nicht gefunden")

        # Der Ersteller braucht keine Mitgliedschaft, auch wenn ein Admin verwaltet.
        if member.user_id == task["created_by"]:
            raise HTTPException(status_code=400, detail="Ersteller kann sich nicht selbst als Mitglied hinzufuegen")

        # Duplikat?
        existing = db.execute(
            "SELECT id FROM project_members WHERE project_id = ? AND user_id = ?",
            (task_id, member.user_id),
        ).fetchone()
        if existing:
            raise HTTPException(status_code=400, detail="Benutzer ist bereits Teammitglied")

        db.execute(
            """INSERT INTO project_members (project_id, user_id, can_read, can_edit, can_create)
               VALUES (?, ?, ?, ?, ?)""",
            (task_id, member.user_id, *_member_values(member, task)),
        )
    log_change(user, "project_member", task_id, "create", member.model_dump())
    return {"message": "Mitglied hinzugefuegt"}


@router.put("/api/tasks/{task_id}/members")
async def replace_members(task_id: int, payload: MembersReplace, user=Depends(get_current_user)):
    """Dialog-Entwurf atomar speichern; parallele Aenderungen nicht ueberschreiben."""
    with db_transaction() as db:
        db.execute("BEGIN IMMEDIATE")
        task = _check_project_creator(db, task_id, user)
        if payload.revision != _members_revision(db, task_id):
            raise HTTPException(status_code=409, detail="Berechtigungen wurden inzwischen geaendert")
        existing_ids = {r[0] for r in db.execute(
            "SELECT user_id FROM project_members WHERE project_id = ?", (task_id,),
        )}
        seen = set()
        values = []
        for member in payload.members:
            if member.user_id in seen or member.user_id == task["created_by"]:
                raise HTTPException(status_code=422, detail="Benutzer doppelt angegeben oder bereits Ersteller")
            target = db.execute("SELECT is_active FROM users WHERE id = ?", (member.user_id,)).fetchone()
            if not target or (not target["is_active"] and member.user_id not in existing_ids):
                raise HTTPException(status_code=422, detail="Benutzer nicht verfuegbar")
            seen.add(member.user_id)
            values.append((task_id, member.user_id, *_member_values(member, task)))
        db.executemany(
            "DELETE FROM project_members WHERE project_id = ? AND user_id = ?",
            [(task_id, uid) for uid in existing_ids - seen],
        )
        db.executemany(
            """INSERT INTO project_members (project_id, user_id, can_read, can_edit, can_create)
               VALUES (?, ?, ?, ?, ?) ON CONFLICT(project_id, user_id) DO UPDATE SET
               can_read = excluded.can_read, can_edit = excluded.can_edit, can_create = excluded.can_create""",
            values,
        )
        revision = _members_revision(db, task_id)
    log_change(user, "project_member", task_id, "update", {
        "members": [{"user_id": uid, "can_read": bool(read), "can_edit": bool(edit), "can_create": bool(create)}
                    for _, uid, read, edit, create in values],
        "removed_user_ids": sorted(existing_ids - seen),
    })
    return {"message": "Berechtigungen gespeichert", "revision": revision}


@router.put("/api/tasks/{task_id}/members/{member_user_id}")
async def update_member(task_id: int, member_user_id: int, member: MemberUpdate, user=Depends(get_current_user)):
    """Berechtigungen eines Teammitglieds aendern."""
    with db_transaction() as db:
        task = _check_project_creator(db, task_id, user)

        existing = db.execute(
            "SELECT id FROM project_members WHERE project_id = ? AND user_id = ?",
            (task_id, member_user_id),
        ).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Mitglied nicht gefunden")

        db.execute(
            """UPDATE project_members SET can_read = ?, can_edit = ?, can_create = ?
               WHERE project_id = ? AND user_id = ?""",
            (*_member_values(member, task), task_id, member_user_id),
        )
    log_change(user, "project_member", task_id, "update", {"user_id": member_user_id, **member.model_dump()})
    return {"message": "Berechtigungen aktualisiert"}


@router.delete("/api/tasks/{task_id}/members/{member_user_id}")
async def remove_member(task_id: int, member_user_id: int, user=Depends(get_current_user)):
    """Mitglied aus Team entfernen."""
    with db_transaction() as db:
        task = _check_project_creator(db, task_id, user)

        existing = db.execute(
            "SELECT id FROM project_members WHERE project_id = ? AND user_id = ?",
            (task_id, member_user_id),
        ).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Mitglied nicht gefunden")

        db.execute(
            "DELETE FROM project_members WHERE project_id = ? AND user_id = ?",
            (task_id, member_user_id),
        )
    log_change(user, "project_member", task_id, "delete", {"user_id": member_user_id})
    return {"message": "Mitglied entfernt"}
