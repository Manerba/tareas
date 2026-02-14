"""
Tareas - Admin API-Router
Benutzerverwaltung (CRUD).
"""

from typing import Optional

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from dashboard.db_utils import db_query, db_transaction
from dashboard.auth import hash_password, get_admin_user
from dashboard.logging_config import get_security_logger

security_log = get_security_logger()

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ============================================================
# Pydantic-Modelle
# ============================================================

class UserCreate(BaseModel):
    username: str
    password: str
    vorname: str = ""
    nachname: str = ""
    email: str = ""
    is_admin: int = 0


class UserUpdate(BaseModel):
    vorname: Optional[str] = None
    nachname: Optional[str] = None
    email: Optional[str] = None
    is_admin: Optional[int] = None
    password: Optional[str] = None


# ============================================================
# Endpoints
# ============================================================

@router.get("/users")
async def get_users(user=Depends(get_admin_user)):
    """Alle Benutzer auflisten."""
    with db_query() as db:
        rows = db.execute(
            """SELECT id, username, vorname, nachname, email, is_admin, created_at,
                      auth_source, is_active
               FROM users ORDER BY username"""
        ).fetchall()
        return {
            "items": [
                {
                    "id": r["id"],
                    "username": r["username"],
                    "vorname": r["vorname"],
                    "nachname": r["nachname"],
                    "email": r["email"],
                    "is_admin": r["is_admin"],
                    "created_at": r["created_at"],
                    "auth_source": r["auth_source"] or "local",
                    "is_active": r["is_active"] if r["is_active"] is not None else 1,
                }
                for r in rows
            ],
            "total": len(rows),
        }


@router.post("/users")
async def create_user(data: UserCreate, user=Depends(get_admin_user)):
    """Neuen Benutzer anlegen."""
    if not data.username.strip():
        raise HTTPException(status_code=400, detail="Benutzername ist Pflichtfeld")
    if not data.password or len(data.password) < 4:
        raise HTTPException(status_code=400, detail="Passwort muss mindestens 4 Zeichen haben")

    with db_transaction() as db:
        existing = db.execute(
            "SELECT id FROM users WHERE username = ?", (data.username.strip(),)
        ).fetchone()
        if existing:
            raise HTTPException(status_code=400, detail="Benutzername existiert bereits")

        hashed = hash_password(data.password)
        cursor = db.execute(
            """INSERT INTO users (username, password_hash, vorname, nachname, email, is_admin)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (data.username.strip(), hashed, data.vorname, data.nachname, data.email, data.is_admin),
        )
        security_log.info("USER_CREATED user=%s by=%s", data.username.strip(), user["username"])
        return {"id": cursor.lastrowid, "message": "Benutzer erstellt"}


@router.put("/users/{user_id}")
async def update_user(user_id: int, data: UserUpdate, user=Depends(get_admin_user)):
    """Benutzer aktualisieren."""
    with db_transaction() as db:
        existing = db.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Benutzer nicht gefunden")

        updates = []
        values = []

        if data.vorname is not None:
            updates.append("vorname = ?")
            values.append(data.vorname)
        if data.nachname is not None:
            updates.append("nachname = ?")
            values.append(data.nachname)
        if data.email is not None:
            updates.append("email = ?")
            values.append(data.email)
        if data.is_admin is not None:
            updates.append("is_admin = ?")
            values.append(data.is_admin)
        if data.password is not None and data.password.strip():
            if len(data.password) < 4:
                raise HTTPException(status_code=400, detail="Passwort muss mindestens 4 Zeichen haben")
            updates.append("password_hash = ?")
            values.append(hash_password(data.password))

        if updates:
            values.append(user_id)
            db.execute(
                f"UPDATE users SET {', '.join(updates)} WHERE id = ?",
                values,
            )

        return {"message": "Benutzer aktualisiert"}


@router.delete("/users/{user_id}")
async def delete_user(user_id: int, user=Depends(get_admin_user)):
    """Benutzer loeschen."""
    if user_id == user["id"]:
        raise HTTPException(status_code=400, detail="Eigenen Account kann man nicht loeschen")

    with db_transaction() as db:
        existing = db.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Benutzer nicht gefunden")

        # Sessions mitloeschen (CASCADE sollte das tun, aber sicherheitshalber)
        db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        db.execute("DELETE FROM users WHERE id = ?", (user_id,))
        security_log.info("USER_DELETED user_id=%s by=%s", user_id, user["username"])
        return {"message": "Benutzer geloescht"}
