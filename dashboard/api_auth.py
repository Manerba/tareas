"""
Tareas - Auth API-Router
Login, Logout, Me, Passwort-Aenderung.
"""

import asyncio
import logging
import time
from fastapi import APIRouter, HTTPException, Request, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from dashboard.db_utils import db_query, db_transaction
from dashboard.crypto_utils import decrypt
from dashboard.logging_config import get_security_logger
from dashboard.auth import (
    verify_password,
    hash_password,
    create_session,
    delete_session,
    set_session_cookie,
    delete_session_cookie,
    get_current_user,
    COOKIE_NAME,
    unsign_token,
)

logger = logging.getLogger(__name__)
security_log = get_security_logger()

router = APIRouter(prefix="/api/auth", tags=["auth"])

LOGIN_FAILED_MSG = "Anmeldung fehlgeschlagen"

# ============================================================
# Rate-Limiting (Fritz!Box-Style)
# Nach 3 Fehlversuchen: 10s Sperre, danach +5s pro Fehlversuch
# ============================================================

_rate_limits: dict[str, dict] = {}  # {key: {"count": int, "last_fail": float}}
_RATE_LIMIT_THRESHOLD = 3
_RATE_LIMIT_BASE_DELAY = 10   # Sekunden nach 3. Fehlversuch
_RATE_LIMIT_INCREMENT = 5     # Sekunden pro weiterem Fehlversuch


def _get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _check_rate_limit(key: str) -> int | None:
    """Gibt verbleibende Wartezeit in Sekunden zurueck, oder None."""
    entry = _rate_limits.get(key)
    if not entry or entry["count"] < _RATE_LIMIT_THRESHOLD:
        return None
    extra = entry["count"] - _RATE_LIMIT_THRESHOLD
    wait = _RATE_LIMIT_BASE_DELAY + extra * _RATE_LIMIT_INCREMENT
    elapsed = time.monotonic() - entry["last_fail"]
    if elapsed < wait:
        return int(wait - elapsed) + 1
    return None


def _record_fail(key: str):
    entry = _rate_limits.get(key)
    if entry:
        entry["count"] += 1
        entry["last_fail"] = time.monotonic()
    else:
        _rate_limits[key] = {"count": 1, "last_fail": time.monotonic()}


def _reset_fails(key: str):
    _rate_limits.pop(key, None)


# ============================================================
# Pydantic-Modelle
# ============================================================

class LoginRequest(BaseModel):
    username: str
    password: str


class PasswordChangeRequest(BaseModel):
    old_password: str
    new_password: str


# ============================================================
# Endpoints
# ============================================================

@router.post("/login")
async def login(data: LoginRequest, request: Request):
    """Benutzer anmelden, Session erstellen, Cookie setzen."""
    client_ip = _get_client_ip(request)
    rate_key = f"login:{client_ip}"

    # Rate-Limit pruefen
    remaining = _check_rate_limit(rate_key)
    if remaining is not None:
        logger.warning("Rate-Limit: IP %s muss noch %ds warten", client_ip, remaining)
        security_log.warning("RATE_LIMITED key=%s", rate_key)
        raise HTTPException(
            status_code=429,
            detail=f"Zu viele Fehlversuche. Bitte {remaining}s warten.",
            headers={"Retry-After": str(remaining)},
        )

    with db_query() as db:
        row = db.execute(
            "SELECT * FROM users WHERE username = ?", (data.username,)
        ).fetchone()

    if not row:
        _record_fail(rate_key)
        logger.warning("Login fehlgeschlagen: Benutzer '%s' nicht gefunden (IP: %s)", data.username, client_ip)
        security_log.warning("LOGIN_FAILED user=%s ip=%s", data.username, client_ip)
        raise HTTPException(status_code=401, detail=LOGIN_FAILED_MSG)

    auth_source = row["auth_source"] if "auth_source" in row.keys() else "local"
    is_active = row["is_active"] if "is_active" in row.keys() else 1

    if auth_source == "ldap":
        # LDAP-Benutzer: Pruefen ob aktiv
        if not is_active:
            logger.warning("Login fehlgeschlagen: LDAP-Benutzer '%s' deaktiviert", data.username)
            security_log.warning("LOGIN_FAILED user=%s ip=%s", data.username, client_ip)
            raise HTTPException(status_code=401, detail=LOGIN_FAILED_MSG)

        # LDAP-Config laden und Authentifizierung
        with db_query() as config_db:
            config_row = config_db.execute("SELECT * FROM ldap_config WHERE id = 1").fetchone()

        if not config_row:
            raise HTTPException(status_code=503, detail="LDAP-Server nicht konfiguriert")

        from dashboard.tls_utils import get_tls_verify_config
        verify_cfg = get_tls_verify_config()
        config = {
            "server": config_row["server"],
            "port": config_row["port"],
            "use_ssl": config_row["use_ssl"],
            "bind_dn": config_row["bind_dn"],
            "bind_password": decrypt(config_row["bind_password"]),
            "search_base": config_row["search_base"],
            "verify_cert": verify_cfg["verify_ldap"],
        }

        try:
            from dashboard.ldap_utils import authenticate_user
            success, error = await asyncio.to_thread(
                authenticate_user, config, data.username, data.password
            )
        except Exception:
            raise HTTPException(status_code=503, detail="LDAP-Server nicht erreichbar")

        if not success:
            _record_fail(rate_key)
            logger.warning("Login fehlgeschlagen: LDAP-Auth fuer '%s': %s (IP: %s)", data.username, error, client_ip)
            security_log.warning("LOGIN_FAILED user=%s ip=%s", data.username, client_ip)
            raise HTTPException(status_code=401, detail=LOGIN_FAILED_MSG)
    else:
        # Lokaler Benutzer: Passwort-Hash pruefen
        if not verify_password(data.password, row["password_hash"]):
            _record_fail(rate_key)
            logger.warning("Login fehlgeschlagen: Falsches Passwort fuer '%s' (IP: %s)", data.username, client_ip)
            security_log.warning("LOGIN_FAILED user=%s ip=%s", data.username, client_ip)
            raise HTTPException(status_code=401, detail=LOGIN_FAILED_MSG)

    # Erfolgreicher Login: Fehlzaehler zuruecksetzen
    _reset_fails(rate_key)
    security_log.info("LOGIN_SUCCESS user=%s ip=%s", data.username, client_ip)
    token = create_session(row["id"])

    user_data = {
        "id": row["id"],
        "username": row["username"],
        "vorname": row["vorname"],
        "nachname": row["nachname"],
        "is_admin": row["is_admin"],
    }

    response = JSONResponse(content={"message": "Angemeldet", "user": user_data})
    set_session_cookie(response, token)
    return response


@router.post("/logout")
async def logout(request: Request, user=Depends(get_current_user)):
    """Abmelden: Session und Cookie loeschen."""
    security_log.info("LOGOUT user=%s", user["username"])
    signed = request.cookies.get(COOKIE_NAME)
    if signed:
        token = unsign_token(signed)
        if token:
            delete_session(token)

    response = JSONResponse(content={"message": "Abgemeldet"})
    delete_session_cookie(response)
    return response


@router.get("/me")
async def me(user=Depends(get_current_user)):
    """Aktuellen Benutzer zurueckgeben."""
    return {"user": user}


@router.put("/password")
async def change_password(data: PasswordChangeRequest, user=Depends(get_current_user)):
    """Passwort aendern."""
    rate_key = f"pwchange:{user['id']}"

    # Rate-Limit pruefen
    remaining = _check_rate_limit(rate_key)
    if remaining is not None:
        logger.warning("Rate-Limit: Passwort-Aenderung fuer User %s gesperrt (%ds)", user["id"], remaining)
        security_log.warning("RATE_LIMITED key=%s", rate_key)
        raise HTTPException(
            status_code=429,
            detail=f"Zu viele Fehlversuche. Bitte {remaining}s warten.",
            headers={"Retry-After": str(remaining)},
        )

    with db_transaction() as db:
        row = db.execute(
            "SELECT password_hash FROM users WHERE id = ?", (user["id"],)
        ).fetchone()

        if not row or not verify_password(data.old_password, row["password_hash"]):
            _record_fail(rate_key)
            security_log.warning("PASSWORD_CHANGE_FAILED user=%s", user["username"])
            raise HTTPException(status_code=400, detail="Altes Passwort ist falsch")

        new_hash = hash_password(data.new_password)
        db.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (new_hash, user["id"]),
        )

    _reset_fails(rate_key)
    security_log.info("PASSWORD_CHANGED user=%s", user["username"])
    return {"message": "Passwort geaendert"}
