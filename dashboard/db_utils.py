"""Zentrale DB-Hilfsfunktionen zur Vermeidung von Code-Duplikaten."""
from contextlib import contextmanager
from dashboard.database import get_db


# ============================================================
# SQL-Identifier Whitelist (Defence-in-Depth)
# ============================================================

ALLOWED_TABLES = {
    "ldap_config", "mail_config", "nextcloud_config",
    "onlyoffice_config", "tls_config", "tls_verify_config", "app_config",
}

ALLOWED_COLUMNS = {
    # Passwort-/Secret-Felder (get_masked_config, resolve_masked_password)
    "password", "bind_password", "jwt_secret",
    # tls_config Felder
    "cert_path", "key_path", "enabled",
    # tls_verify_config Felder
    "verify_nextcloud", "verify_onlyoffice", "verify_smtp", "verify_ldap",
    # onlyoffice_config Felder
    "server_url",
    # nextcloud_config Felder
    "username", "base_path",
    # ldap_config Felder
    "server", "port", "use_ssl", "bind_dn", "search_base",
    "sync_interval_minutes", "group_dn", "group_name",
    # mail_config Felder
    "smtp_server", "smtp_port", "encryption", "auth_enabled",
    "from_address", "from_name",
}


def validate_identifier(value: str, allowed: set, kind: str = "identifier") -> str:
    """Validiert SQL-Identifier gegen Whitelist.

    Args:
        value: Der zu pruefende Identifier
        allowed: Set erlaubter Werte
        kind: Art des Identifiers fuer Fehlermeldung (z.B. "table", "column")

    Returns:
        Den validierten Wert (unveraendert)

    Raises:
        ValueError: Wenn der Wert nicht in der Whitelist ist
    """
    if value not in allowed:
        raise ValueError(f"Invalid SQL {kind}: {value}")
    return value


@contextmanager
def db_query():
    """Context Manager fuer Read-Only DB-Queries. Schliesst Connection automatisch."""
    db = get_db()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def db_transaction():
    """Context Manager fuer DB-Transaktionen mit Auto-Commit/Rollback."""
    db = get_db()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def upsert_singleton_config(db, table: str, fields: dict):
    """UPSERT fuer Singleton-Konfigurationen (id=1).

    Args:
        db: DB-Connection
        table: Tabellenname
        fields: Dict mit Spaltenname -> Wert
    """
    validate_identifier(table, ALLOWED_TABLES, "table")
    for col in fields:
        validate_identifier(col, ALLOWED_COLUMNS, "column")

    existing = db.execute(f"SELECT id FROM {table} WHERE id = 1").fetchone()

    if existing:
        set_clause = ", ".join(f"{k} = ?" for k in fields.keys())
        db.execute(f"UPDATE {table} SET {set_clause} WHERE id = 1", list(fields.values()))
    else:
        cols = ", ".join(["id"] + list(fields.keys()))
        placeholders = ", ".join(["1"] + ["?"] * len(fields))
        db.execute(f"INSERT INTO {table} ({cols}) VALUES ({placeholders})", list(fields.values()))


def build_update_query(table: str, fields: dict, where_clause: str, where_values: list):
    """Baut dynamische UPDATE-Query aus nicht-None Feldern.

    Args:
        table: Tabellenname
        fields: Dict mit Spaltenname -> Wert (None-Werte werden ignoriert)
        where_clause: WHERE-Bedingung (z.B. "id = ?")
        where_values: Werte fuer WHERE-Bedingung

    Returns:
        Tuple (query, values) oder None wenn keine Felder zu aktualisieren
    """
    validate_identifier(table, ALLOWED_TABLES, "table")
    for col in fields:
        validate_identifier(col, ALLOWED_COLUMNS, "column")

    updates = []
    values = []

    for key, value in fields.items():
        if value is not None:
            updates.append(f"{key} = ?")
            values.append(value)

    if not updates:
        return None

    query = f"UPDATE {table} SET {', '.join(updates)} WHERE {where_clause}"
    return query, values + where_values
