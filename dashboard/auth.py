"""
Tareas - Authentifizierung
Hashing, Sessions, Cookie-Signing, FastAPI-Dependencies.
"""

import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

import bcrypt
from fastapi import HTTPException, Request, Response
from itsdangerous import URLSafeSerializer, BadSignature

from dashboard.database import get_db

COOKIE_NAME = "tareas_session"
SESSION_MAX_AGE = 365 * 86400  # 1 Jahr in Sekunden
SECRET_KEY_PATH = Path(__file__).parent.parent / "data" / "secret.key"


# ============================================================
# Secret Key
# ============================================================

def get_secret_key() -> str:
    """Liest oder erstellt den Secret Key fuer Cookie-Signing."""
    if SECRET_KEY_PATH.exists():
        # Berechtigungen korrigieren falls noetig
        os.chmod(str(SECRET_KEY_PATH), 0o600)
        return SECRET_KEY_PATH.read_text().strip()
    # Verzeichnis mit restriktiven Rechten erstellen
    SECRET_KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(str(SECRET_KEY_PATH.parent), 0o700)
    # Key-Datei mit 0600 erstellen (nur Owner lesen/schreiben)
    key = secrets.token_hex(32)
    fd = os.open(str(SECRET_KEY_PATH), os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    try:
        os.write(fd, key.encode())
    finally:
        os.close(fd)
    return key


def _get_serializer() -> URLSafeSerializer:
    return URLSafeSerializer(get_secret_key(), salt="tareas-session")


# ============================================================
# Passwort-Hashing
# ============================================================

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


# ============================================================
# Session-Verwaltung
# ============================================================

def create_session(user_id: int) -> str:
    """Erstellt eine neue Session in der DB, gibt den Token zurueck."""
    token = secrets.token_hex(32)
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=SESSION_MAX_AGE)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    db = get_db()
    try:
        db.execute(
            "INSERT INTO sessions (user_id, token, expires_at) VALUES (?, ?, ?)",
            (user_id, token, expires_at),
        )
        db.commit()
    finally:
        db.close()
    return token


def validate_session(token: str) -> dict | None:
    """Validiert einen Session-Token. Gibt User-Dict oder None zurueck."""
    db = get_db()
    try:
        row = db.execute(
            """SELECT s.*, u.id as uid, u.username, u.vorname, u.nachname,
                      u.email, u.is_admin
               FROM sessions s
               JOIN users u ON s.user_id = u.id
               WHERE s.token = ? AND s.expires_at > datetime('now')""",
            (token,),
        ).fetchone()
        if not row:
            return None
        return {
            "id": row["uid"],
            "username": row["username"],
            "vorname": row["vorname"],
            "nachname": row["nachname"],
            "email": row["email"],
            "is_admin": row["is_admin"],
        }
    finally:
        db.close()


def delete_session(token: str):
    """Loescht eine Session aus der DB."""
    db = get_db()
    try:
        db.execute("DELETE FROM sessions WHERE token = ?", (token,))
        db.commit()
    finally:
        db.close()


# ============================================================
# Cookie-Signing
# ============================================================

def sign_token(token: str) -> str:
    return _get_serializer().dumps(token)


def unsign_token(signed: str) -> str | None:
    try:
        return _get_serializer().loads(signed)
    except BadSignature:
        return None


# ============================================================
# Cookie-Helpers
# ============================================================

def set_session_cookie(response: Response, token: str):
    from dashboard.tls_utils import get_tls_config
    tls = get_tls_config()
    use_secure = bool(tls and tls["enabled"])

    signed = sign_token(token)
    response.set_cookie(
        key=COOKIE_NAME,
        value=signed,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        secure=use_secure,
        samesite="lax",
        path="/",
    )


def delete_session_cookie(response: Response):
    response.delete_cookie(key=COOKIE_NAME, path="/")


# ============================================================
# FastAPI Dependencies
# ============================================================

def _extract_user_from_request(request: Request) -> dict | None:
    """Liest Cookie, validiert Session, gibt User-Dict oder None zurueck."""
    signed = request.cookies.get(COOKIE_NAME)
    if not signed:
        return None
    token = unsign_token(signed)
    if not token:
        return None
    return validate_session(token)


async def get_current_user(request: Request) -> dict:
    """FastAPI Dependency: Gibt aktuellen User zurueck oder 401."""
    user = _extract_user_from_request(request)
    if not user:
        raise HTTPException(status_code=401, detail="Nicht angemeldet")
    return user


async def get_admin_user(request: Request) -> dict:
    """FastAPI Dependency: Gibt Admin-User zurueck oder 401/403."""
    user = _extract_user_from_request(request)
    if not user:
        raise HTTPException(status_code=401, detail="Nicht angemeldet")
    if not user["is_admin"]:
        raise HTTPException(status_code=403, detail="Kein Admin-Zugriff")
    return user
