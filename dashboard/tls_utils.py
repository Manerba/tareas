"""
Tareas - TLS Hilfsfunktionen
Config-Lesen und Zertifikats-Validierung.
"""

import sqlite3
from pathlib import Path

from dashboard.database import DB_PATH


def get_tls_config() -> dict | None:
    """Liest TLS-Config aus DB. Gibt None zurueck wenn nicht konfiguriert oder DB fehlt."""
    if not DB_PATH.exists():
        return None
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM tls_config WHERE id = 1").fetchone()
        conn.close()
        if not row:
            return None
        return dict(row)
    except Exception:
        return None


def validate_cert_files(cert_path: str, key_path: str) -> str | None:
    """Prueft Existenz, Lesbarkeit und PEM-Format. Gibt Fehlermeldung oder None zurueck."""
    cert = Path(cert_path)
    key = Path(key_path)

    if not cert.exists():
        return f"Zertifikat nicht gefunden: {cert_path}"
    if not key.exists():
        return f"Key-Datei nicht gefunden: {key_path}"

    if not cert.is_file():
        return f"Zertifikat ist keine Datei: {cert_path}"
    if not key.is_file():
        return f"Key ist keine Datei: {key_path}"

    # Lesbarkeit und PEM-Format pruefen
    try:
        cert_content = cert.read_text()
    except PermissionError:
        return f"Zertifikat nicht lesbar (Berechtigung): {cert_path}"

    try:
        key_content = key.read_text()
    except PermissionError:
        return f"Key-Datei nicht lesbar (Berechtigung): {key_path}"

    if "-----BEGIN CERTIFICATE-----" not in cert_content:
        return f"Zertifikat ist kein gueltiges PEM-Format: {cert_path}"

    if "-----BEGIN" not in key_content or "PRIVATE KEY" not in key_content:
        return f"Key-Datei ist kein gueltiges PEM-Format: {key_path}"

    return None


def get_tls_verify_config() -> dict:
    """Liest TLS-Verify-Config aus DB. Gibt Dict mit 4 Booleans zurueck (Default: alle False)."""
    defaults = {
        "verify_nextcloud": False,
        "verify_onlyoffice": False,
        "verify_smtp": False,
        "verify_ldap": False,
    }
    if not DB_PATH.exists():
        return defaults
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM tls_verify_config WHERE id = 1").fetchone()
        conn.close()
        if not row:
            return defaults
        return {
            "verify_nextcloud": bool(row["verify_nextcloud"]),
            "verify_onlyoffice": bool(row["verify_onlyoffice"]),
            "verify_smtp": bool(row["verify_smtp"]),
            "verify_ldap": bool(row["verify_ldap"]),
        }
    except Exception:
        return defaults
