"""Stabile MCP-Werkzeugfehler, ohne interne Exceptions oder Eingaben auszugeben."""

import json
import logging

from fastapi import HTTPException
from fastmcp.exceptions import NotFoundError, ToolError, ValidationError as FastMCPValidationError
from fastmcp.server.middleware import Middleware
from fastmcp.tools.base import ToolResult
from mcp.types import CallToolResult, TextContent
from pydantic import ValidationError

from dashboard.errors import ApplicationError

logger = logging.getLogger(__name__)


class MCPToolError(ToolError):
    """Typisierter, absichtlich oeffentlicher Fachfehler fuer direkte Toolaufrufe."""

    def __init__(
        self, code: str, message: str, *, field: str | None = None,
        fields: list[str] | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.field = field
        self.fields = fields

    def payload(self) -> dict:
        result = {"code": self.code, "message": self.message}
        if self.field is not None:
            result["field"] = self.field
        if self.fields:
            result["fields"] = list(self.fields)
        return result


def tool_error_from_http(exc: HTTPException) -> MCPToolError:
    if isinstance(exc, ApplicationError):
        return MCPToolError(exc.code, exc.detail, field=exc.field, fields=exc.fields)
    # Keine Freitexterkennung: unbekannte HTTP-Fehler nur ueber ihren Status
    # einordnen. detail kann technische Informationen enthalten.
    code, message = {
        400: ("invalid_arguments", "Ungueltige Werkzeugargumente"),
        401: ("authentication_required", "Authentifizierung erforderlich"),
        403: ("permission_denied", "Keine Berechtigung fuer diese Aktion"),
        404: ("not_found", "Eintrag nicht gefunden"),
        409: ("conflict", "Die Aenderung steht in Konflikt mit dem aktuellen Stand"),
        413: ("file_too_large", "Datei zu gross fuer diesen Aufruf"),
        422: ("invalid_arguments", "Ungueltige Werkzeugargumente"),
        503: ("storage_unavailable", "Dateiablage ist momentan nicht erreichbar"),
        504: ("storage_timeout", "Zeitueberschreitung beim Zugriff auf die Dateiablage"),
    }.get(exc.status_code, ("internal_error", "Werkzeugaufruf fehlgeschlagen"))
    return MCPToolError(code, message)


class _ErrorResult(ToolResult):
    """CallToolResult umgeht nur fuer Fehler die Erfolgs-Outputschema-Pruefung.

    FastMCPs ToolResult hat kein isError-Feld. Ein explizites CallToolResult ist
    der vom MCP-SDK vorgesehene Weg fuer Fehler mit strukturierten Metadaten.
    """

    def to_mcp_result(self) -> CallToolResult:
        return CallToolResult(
            isError=True, content=self.content,
            structuredContent=self.structured_content,
        )


def _error_result(payload: dict) -> _ErrorResult:
    return _ErrorResult(
        content=[TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))],
        structured_content=payload,
    )


def _validation_payload(exc: ValidationError) -> dict:
    errors = []
    for error in exc.errors(include_input=False, include_context=False, include_url=False):
        # Nur Feldnamen/Indizes aus loc, niemals input, msg oder ctx uebernehmen.
        field = ".".join(str(part) for part in error["loc"])
        root_field = str(error["loc"][0]) if error["loc"] else ""
        code, message = {
            "status": ("invalid_status", "status muss offen, in_arbeit, erledigt oder abgebrochen sein"),
            "status_percent": ("invalid_progress", "status_percent muss eine ganze Zahl von 0 bis 100 sein"),
            "parent_subtask_id": ("invalid_parent_subtask", "parent_subtask_id muss eine ganze Zahl ab 0 sein"),
            "encoding": ("invalid_encoding", "Die angegebene Kodierung wird nicht unterstuetzt"),
            "offset": ("invalid_pagination", "offset muss eine ganze Zahl ab 0 sein"),
            "limit": ("invalid_pagination", "limit liegt ausserhalb des erlaubten Bereichs"),
        }.get(root_field, ("invalid_argument", "Ungueltiger Wert fuer dieses Feld"))
        if error["type"] in {"missing", "missing_argument", "missing_keyword_only_argument"}:
            code, message = "missing_argument", "Ein erforderliches Feld fehlt"
        elif error["type"] == "unexpected_keyword_argument":
            code, message = "unknown_argument", "Dieses Werkzeug unterstuetzt das Feld nicht"
        item = {"code": code, "message": message}
        if field:
            item["field"] = field
        if item not in errors:
            errors.append(item)
    if len(errors) == 1:
        return errors[0]
    return {
        "code": "invalid_arguments", "message": "Mehrere Werkzeugargumente sind ungueltig",
        "fields": list(dict.fromkeys(error["field"] for error in errors if "field" in error)),
        "errors": errors,
    }


def _request_fields(payload: dict, name: str, arguments: dict) -> dict:
    """Gemeinsame Service-Felder an die jeweilige Werkzeugsignatur anpassen."""
    aliases = {}
    if "project_id" in arguments and "task_id" not in arguments:
        aliases["task_id"] = "project_id"
    if name == "add_dependency":
        aliases["predecessor_ids"] = "depends_on_id"
    if name == "note.update":
        aliases["note_user_id"] = "user_id"
    if name == "file.move" and payload.get("field") == "path":
        if payload["code"] == "file_exists":
            aliases["path"] = "destination"
        else:
            # Beispielsweise kann rename an einer inzwischen entfernten Quelle
            # oder einem fehlenden Ziel-Elternordner scheitern. Keine Zuordnung
            # anhand von Exception-Texten oder internen Dateisystempfaden raten.
            payload.pop("field")
            payload["fields"] = ["source", "destination"]
    if "field" in payload:
        payload["field"] = aliases.get(payload["field"], payload["field"])
    if "fields" in payload:
        payload["fields"] = [aliases.get(field, field) for field in payload["fields"]]
    return payload


class StructuredToolErrors(Middleware):
    """Fach- und Schemafehler fuer beide MCP-Transporte einheitlich ausgeben."""

    async def on_call_tool(self, context, call_next):
        try:
            return await call_next(context)
        except MCPToolError as exc:
            payload = exc.payload()
        except ValidationError as exc:
            payload = _validation_payload(exc)
        except HTTPException as exc:
            payload = tool_error_from_http(exc).payload()
        except FastMCPValidationError:
            payload = {"code": "invalid_arguments", "message": "Ungueltige Werkzeugargumente"}
        except NotFoundError:
            payload = {"code": "unknown_tool", "message": "Werkzeug nicht gefunden"}
        except Exception as exc:
            # FastMCP kann Nicht-FastMCP-Exceptions als ToolError weiterreichen.
            # Typisierte Ursachen bleiben auswertbar; untypisierte Fehlermeldungen
            # einschliesslich ToolError werden nie ungeprueft an Clients geschickt.
            cause = exc.__cause__
            if isinstance(cause, HTTPException):
                payload = tool_error_from_http(cause).payload()
            else:
                logger.exception("Unerwarteter MCP-Werkzeugfehler")
                payload = {"code": "internal_error", "message": "Werkzeugaufruf fehlgeschlagen"}
        return _error_result(_request_fields(
            payload, context.message.name, context.message.arguments or {},
        ))
