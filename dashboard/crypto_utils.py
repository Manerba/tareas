"""Verschluesselung fuer Credentials in der Datenbank (Fernet-basiert)."""

import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken

from dashboard.auth import get_secret_key

logger = logging.getLogger(__name__)


def _get_fernet():
    """Fernet-Instanz aus dem Secret Key ableiten."""
    secret = get_secret_key()
    # SHA-256 des Secret Keys als Fernet-Key (base64-encoded 32 bytes)
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
    return Fernet(key)


def encrypt(value):
    """Verschluesselt einen String-Wert. Gibt Fernet-Token als String zurueck."""
    if not value:
        return value
    return _get_fernet().encrypt(value.encode()).decode()


def decrypt(value):
    """Entschluesselt einen Fernet-Token. Gibt Klartext als String zurueck."""
    if not value:
        return value
    try:
        return _get_fernet().decrypt(value.encode()).decode()
    except InvalidToken:
        # Wahrscheinlich noch Klartext - unveraendert zurueckgeben
        return value


def is_encrypted(value):
    """Prueft ob ein Wert ein Fernet-Token ist (beginnt mit gAAAAA)."""
    return isinstance(value, str) and value.startswith("gAAAAA")


def migrate_plaintext_credentials():
    """Migriert bestehende Klartext-Credentials zu verschluesselten Werten."""
    from dashboard.db_utils import db_query, db_transaction, validate_identifier, ALLOWED_TABLES, ALLOWED_COLUMNS

    fields_to_encrypt = [
        ("nextcloud_config", "password"),
        ("ldap_config", "bind_password"),
        ("mail_config", "password"),
        ("onlyoffice_config", "jwt_secret"),
    ]

    migrated = 0
    for table, field in fields_to_encrypt:
        validate_identifier(table, ALLOWED_TABLES, "table")
        validate_identifier(field, ALLOWED_COLUMNS, "column")

        with db_query() as db:
            row = db.execute(f"SELECT {field} FROM {table} WHERE id = 1").fetchone()
            if row and row[field] and not is_encrypted(row[field]):
                encrypted = encrypt(row[field])
                with db_transaction() as tx:
                    tx.execute(f"UPDATE {table} SET {field} = ? WHERE id = 1", (encrypted,))
                migrated += 1

    if migrated:
        logger.info("Credential-Migration: %d Klartext-Werte verschluesselt", migrated)
