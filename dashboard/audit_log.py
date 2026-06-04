"""
Tareas - Audit-Log fuer Aenderungen an Aufgaben/Teilaufgaben/Notes/Dependencies.

Persistiert WHO did WHAT, WHEN, WHERE und (optional) ein JSON-Diff der Aenderungen.
Wird aus REST-Endpunkten und MCP-Tools aufgerufen. Reads werden NICHT geloggt
(zu viel Rauschen) - nur Schreib-Operationen.
"""

import json
import logging
from typing import Any

from dashboard.database import get_db

logger = logging.getLogger(__name__)


def log_change(
    actor: dict | None,
    entity_type: str,
    entity_id: int | None,
    action: str,
    changes: dict[str, Any] | None = None,
) -> None:
    """Schreibt einen Eintrag ins Audit-Log.

    Args:
        actor: User-Dict (mit 'id' und 'auth_source'). None = System-Action.
        entity_type: 'task' | 'sub_task' | 'note' | 'dependency' | 'mcp_token' | ...
        entity_id: ID der betroffenen Entitaet (None bei z.B. globalen Config-Aenderungen)
        action: 'create' | 'update' | 'delete' | 'status_change' | 'revoke' | ...
        changes: Optional dict mit Feld -> neuer Wert (oder {'old': ..., 'new': ...})

    Schluckt Fehler bewusst (Audit darf nie den eigentlichen Request zum Scheitern bringen).
    """
    try:
        actor_user_id = actor["id"] if actor else None
        actor_source = (actor.get("auth_source") if actor else None) or "local"
        actor_type = "mcp" if actor_source == "mcp" else ("system" if actor is None else "user")

        changes_json = json.dumps(changes, ensure_ascii=False, default=str) if changes else None

        db = get_db()
        try:
            db.execute(
                """INSERT INTO audit_log
                   (actor_user_id, actor_type, entity_type, entity_id, action, changes_json)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (actor_user_id, actor_type, entity_type, entity_id, action, changes_json),
            )
            db.commit()
        finally:
            db.close()
    except Exception as exc:
        logger.warning("audit_log write failed: %s", exc)


def diff_fields(before: dict, after: dict, fields: list[str]) -> dict[str, dict]:
    """Hilfsfunktion: berechnet Diff fuer eine Liste von Feldern.

    Returns dict {field: {'old': ..., 'new': ...}} - nur fuer geaenderte Felder.
    """
    result = {}
    for f in fields:
        b = before.get(f)
        a = after.get(f)
        if b != a:
            result[f] = {"old": b, "new": a}
    return result
