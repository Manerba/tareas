"""Zentrale Hilfsfunktionen fuer User-Operationen."""


def get_display_name(user: dict) -> str:
    """Gibt den Anzeigenamen eines Users zurueck (Vorname Nachname oder Username).

    Args:
        user: User-Dict mit 'vorname', 'nachname', 'username' Keys

    Returns:
        Vollstaendiger Name oder Fallback auf Username
    """
    full_name = f"{user.get('vorname', '')} {user.get('nachname', '')}".strip()
    return full_name or user.get("username", "Unbekannt")
