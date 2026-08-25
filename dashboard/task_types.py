"""Gemeinsame Validierung fuer Tareas-Aufgabentypen."""

ALLOWED_TASK_TYPES = {"aufgabe", "projekt"}


def normalize_task_type(value: str) -> str:
    """Normalisiert und validiert task_type-Werte."""
    normalized = (value or "").strip().lower()
    if normalized not in ALLOWED_TASK_TYPES:
        allowed = ", ".join(sorted(ALLOWED_TASK_TYPES))
        raise ValueError(f"Ungueltiger task_type '{value}'. Erlaubt: {allowed}")
    return normalized
