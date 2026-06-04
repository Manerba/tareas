"""
Tareas - Authentifizierung
Hashing, Sessions, Cookie-Signing, FastAPI-Dependencies.
"""

import hashlib
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
                      u.email, u.is_admin, u.auth_source
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
            "auth_source": row["auth_source"] or "local",
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


# ============================================================
# MCP-Token-Auth (Bearer-Token fuer den /mcp-Endpoint)
# ============================================================

def _hash_token(plain_token: str) -> str:
    """SHA-256-Hash eines Plain-Tokens (Hex). Nur Hash wird gespeichert."""
    return hashlib.sha256(plain_token.encode()).hexdigest()


def _mcp_globally_enabled() -> bool:
    """Prueft den Kill-Switch (mcp_config.enabled)."""
    db = get_db()
    try:
        row = db.execute("SELECT enabled FROM mcp_config WHERE id = 1").fetchone()
        return bool(row and row["enabled"])
    finally:
        db.close()


def validate_mcp_bearer(token: str) -> dict | None:
    """Validiert einen MCP-Bearer-Token. Gibt User-Dict (auth_source='mcp')
    oder None zurueck. Aktualisiert last_used_at."""
    if not token:
        return None
    if not _mcp_globally_enabled():
        return None
    token_hash = _hash_token(token)
    db = get_db()
    try:
        row = db.execute(
            """SELECT t.id AS token_id, t.display_name,
                      u.id AS uid, u.username, u.vorname, u.nachname,
                      u.email, u.is_admin, u.auth_source
               FROM mcp_tokens t
               JOIN users u ON t.user_id = u.id
               WHERE t.token_hash = ? AND t.revoked_at IS NULL""",
            (token_hash,),
        ).fetchone()
        if not row:
            return None
        db.execute(
            "UPDATE mcp_tokens SET last_used_at = datetime('now') WHERE id = ?",
            (row["token_id"],),
        )
        db.commit()
        return {
            "id": row["uid"],
            "username": row["username"],
            "vorname": row["vorname"],
            "nachname": row["nachname"],
            "email": row["email"],
            "is_admin": row["is_admin"],
            "auth_source": row["auth_source"] or "mcp",
            "mcp_token_id": row["token_id"],
            "mcp_display_name": row["display_name"],
        }
    finally:
        db.close()


def _extract_bearer_token(request: Request) -> str | None:
    """Liest 'Authorization: Bearer <token>' aus dem Request, oder None."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    return auth[7:].strip() or None


async def get_mcp_user(request: Request) -> dict:
    """FastAPI Dependency: Validiert Bearer-Token, gibt MCP-User zurueck oder 401/403."""
    if not _mcp_globally_enabled():
        raise HTTPException(status_code=403, detail="MCP ist deaktiviert")
    token = _extract_bearer_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="Bearer-Token fehlt")
    user = validate_mcp_bearer(token)
    if not user:
        raise HTTPException(status_code=401, detail="Ungueltiges oder widerrufenes Token")
    return user


def create_mcp_token(display_name: str, created_by_user_id: int) -> tuple[str, int]:
    """Legt einen neuen MCP-User samt Token an.

    Returns:
        (plain_token, token_id) - Plain-Token wird nur HIER und im Admin-UI angezeigt.

    Der zugehoerige User hat auth_source='mcp' und einen automatischen Username
    'mcp_<token_id>'. password_hash bleibt leer (Login per Cookie nicht moeglich).
    """
    plain_token = secrets.token_urlsafe(48)
    token_hash = _hash_token(plain_token)
    db = get_db()
    try:
        # 1. User anlegen (Platzhalter-Username, wird gleich aktualisiert)
        placeholder_username = f"mcp_{secrets.token_hex(4)}"
        cursor = db.execute(
            """INSERT INTO users
               (username, password_hash, vorname, nachname, is_admin, auth_source, is_active)
               VALUES (?, '', ?, '', 0, 'mcp', 1)""",
            (placeholder_username, display_name),
        )
        user_id = cursor.lastrowid

        # 2. Token-Eintrag erstellen
        cursor = db.execute(
            """INSERT INTO mcp_tokens
               (user_id, token_hash, display_name, created_by)
               VALUES (?, ?, ?, ?)""",
            (user_id, token_hash, display_name, created_by_user_id),
        )
        token_id = cursor.lastrowid

        # 3. Username auf finales Format aktualisieren ('mcp_<token_id>')
        db.execute(
            "UPDATE users SET username = ? WHERE id = ?",
            (f"mcp_{token_id}", user_id),
        )

        db.commit()
        return plain_token, token_id
    finally:
        db.close()


def revoke_mcp_token(token_id: int) -> bool:
    """Setzt revoked_at auf jetzt. Behaelt Audit-Spur (User wird nicht geloescht).

    Returns True wenn das Token aktiv war und revoked wurde, sonst False.
    """
    db = get_db()
    try:
        cursor = db.execute(
            """UPDATE mcp_tokens
               SET revoked_at = datetime('now')
               WHERE id = ? AND revoked_at IS NULL""",
            (token_id,),
        )
        db.commit()
        return cursor.rowcount > 0
    finally:
        db.close()


def list_mcp_tokens() -> list[dict]:
    """Liste aller MCP-Tokens (ohne Hash) fuer Admin-UI."""
    db = get_db()
    try:
        rows = db.execute(
            """SELECT t.id, t.display_name, t.created_at, t.last_used_at,
                      t.revoked_at, t.user_id,
                      cb.username AS created_by_username
               FROM mcp_tokens t
               LEFT JOIN users cb ON t.created_by = cb.id
               ORDER BY t.id DESC"""
        ).fetchall()
        return [
            {
                "id": r["id"],
                "display_name": r["display_name"],
                "created_at": r["created_at"],
                "last_used_at": r["last_used_at"],
                "revoked_at": r["revoked_at"],
                "user_id": r["user_id"],
                "created_by_username": r["created_by_username"],
                "active": r["revoked_at"] is None,
            }
            for r in rows
        ]
    finally:
        db.close()


def get_mcp_config() -> dict:
    """Liefert die MCP-Config (Kill-Switch + Metadaten)."""
    db = get_db()
    try:
        row = db.execute("SELECT enabled FROM mcp_config WHERE id = 1").fetchone()
        return {"enabled": bool(row["enabled"]) if row else True}
    finally:
        db.close()


def set_mcp_enabled(enabled: bool) -> None:
    """Schaltet MCP global ein/aus (Kill-Switch)."""
    db = get_db()
    try:
        db.execute(
            "INSERT OR REPLACE INTO mcp_config (id, enabled) VALUES (1, ?)",
            (1 if enabled else 0,),
        )
        db.commit()
    finally:
        db.close()
