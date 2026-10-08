"""Gemeinsame Status- und Fortschrittsregeln fuer REST und MCP."""

from typing import Annotated, Literal

from pydantic import Field, TypeAdapter, ValidationError

from dashboard.errors import ApplicationError


ProjectStatus = Literal["offen", "in_arbeit", "erledigt", "abgebrochen"]
StatusPercent = Annotated[int, Field(strict=True, ge=0, le=100)]

_project_status = TypeAdapter(ProjectStatus)
_status_percent = TypeAdapter(StatusPercent)


def validate_project_status(value: object) -> ProjectStatus:
    """Prueft auch direkte Python-Aufrufe ohne Framework-Validierung."""
    try:
        return _project_status.validate_python(value)
    except ValidationError as exc:
        raise ApplicationError(
            422, "Status muss offen, in_arbeit, erledigt oder abgebrochen sein",
            code="invalid_status", field="status",
        ) from exc


def validate_status_percent(value: object) -> int:
    """Akzeptiert ausschliesslich Ganzzahlen; bool, float und Text sind ungueltig."""
    try:
        return _status_percent.validate_python(value)
    except ValidationError as exc:
        raise ApplicationError(
            422, "Fortschritt muss eine Ganzzahl zwischen 0 und 100 sein",
            code="invalid_progress", field="status_percent",
        ) from exc
