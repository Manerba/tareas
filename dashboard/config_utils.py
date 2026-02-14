"""Zentrale Hilfsfunktionen fuer Config-Operationen."""
from fastapi import HTTPException
from dashboard.db_utils import db_query, validate_identifier, ALLOWED_TABLES, ALLOWED_COLUMNS
from dashboard.crypto_utils import encrypt, decrypt


def get_masked_config(table: str, mask_fields: list[str] | None = None) -> dict | None:
    """Laedt Singleton-Config (id=1) und maskiert sensitive Felder.

    Args:
        table: Tabellenname
        mask_fields: Liste von Feldnamen deren Werte maskiert werden (z.B. ["password"])

    Returns:
        Config-Dict mit maskierten Feldern oder None
    """
    validate_identifier(table, ALLOWED_TABLES, "table")
    if mask_fields:
        for field in mask_fields:
            validate_identifier(field, ALLOWED_COLUMNS, "column")

    with db_query() as db:
        row = db.execute(f"SELECT * FROM {table} WHERE id = 1").fetchone()
        if not row:
            return None
        config = dict(row)
        if mask_fields:
            for field in mask_fields:
                if config.get(field):
                    # Wert entschluesseln bevor er maskiert wird
                    config[field] = decrypt(config[field])
                    config[field] = "********"
        return config


def resolve_masked_password(db, table: str, field: str, new_value: str) -> str:
    """Loest maskiertes Passwort auf: gibt echtes PW zurueck wenn '********', sonst new_value.

    Args:
        db: Offene DB-Connection (innerhalb eines Context Managers)
        table: Tabellenname
        field: Spaltenname des Passwort-Feldes
        new_value: Der vom Client gesendete Wert

    Returns:
        Das tatsaechliche Passwort

    Raises:
        HTTPException(400) wenn maskiert aber kein gespeichertes PW existiert
    """
    validate_identifier(table, ALLOWED_TABLES, "table")
    validate_identifier(field, ALLOWED_COLUMNS, "column")

    if new_value != "********":
        # Neues Passwort: verschluesseln vor dem Speichern
        return encrypt(new_value)

    # Maskiert: alten (bereits verschluesselten) Wert aus DB beibehalten
    row = db.execute(f"SELECT {field} FROM {table} WHERE id = 1").fetchone()
    if row and row[field]:
        return row[field]
    raise HTTPException(status_code=400, detail="Kein gespeichertes Passwort vorhanden")
