"""
Tokenfreier Bootstrap-Guide fuer Agenten-Integration.

Die Daten werden zur Laufzeit aus der Tareas-Konfiguration abgeleitet, damit
neue Projekte nicht von externen Repos oder statischen Kopien abhaengen.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit, urlunsplit

from fastapi import Request

from dashboard.db_utils import db_query


DEFAULT_ADDRESS = "localhost:8504"
MCP_SERVER_NAME = "tareas"
MCP_TRANSPORT = "streamable_http"


def _strip_protocol(address: str) -> str:
    value = (address or "").strip().rstrip("/")
    for prefix in ("https://", "http://"):
        if value.lower().startswith(prefix):
            return value[len(prefix):]
    return value


def _get_configured_address() -> str | None:
    try:
        with db_query() as db:
            row = db.execute("SELECT server_address FROM app_config WHERE id = 1").fetchone()
        if row and row["server_address"]:
            return _strip_protocol(row["server_address"])
    except Exception:
        return None
    return None


def _tls_enabled() -> bool:
    try:
        with db_query() as db:
            row = db.execute("SELECT enabled FROM tls_config WHERE id = 1").fetchone()
        return bool(row and row["enabled"])
    except Exception:
        return False


def _request_base_url(request: Request | None) -> str | None:
    if not request:
        return None
    return str(request.base_url).rstrip("/")


def public_base_url(request: Request | None = None) -> str:
    """Liefert die oeffentliche Basis-URL der Haupt-App."""
    configured = _get_configured_address()
    if configured:
        protocol = "https" if _tls_enabled() else "http"
        return f"{protocol}://{configured}"

    requested = _request_base_url(request)
    if requested:
        return requested

    protocol = "https" if _tls_enabled() else "http"
    return f"{protocol}://{DEFAULT_ADDRESS}"


def _with_port(base_url: str, port: int) -> str:
    parts = urlsplit(base_url)
    hostname = parts.hostname or parts.netloc.split(":")[0]
    if not hostname:
        return base_url
    netloc = f"{hostname}:{port}"
    return urlunsplit((parts.scheme or "http", netloc, "", "", "")).rstrip("/")


def build_agent_metadata(request: Request | None = None) -> dict[str, Any]:
    base_url = public_base_url(request)
    web_ui = f"{base_url}/"
    mcp_endpoint = f"{base_url}/mcp/"
    guide_url = f"{base_url}/agent-guide.md"
    well_known_url = f"{base_url}/.well-known/tareas-agent.json"
    admin_url = _with_port(base_url, 8505)
    snippet = build_agents_snippet(web_ui, mcp_endpoint)

    return {
        "name": "Tareas",
        "description": "Shared project planning and audit layer for AI-agent collaboration.",
        "web_ui": web_ui,
        "agent_guide_url": guide_url,
        "well_known_url": well_known_url,
        "mcp": {
            "server_name": MCP_SERVER_NAME,
            "endpoint": mcp_endpoint,
            "transport": MCP_TRANSPORT,
            "authorization_header": "Authorization: Bearer <token>",
            "token_placeholder": "<token>",
            "token_admin_url": admin_url,
        },
        "client_examples": {
            "codex_config_toml": (
                "[mcp_servers.tareas]\n"
                f"url = \"{mcp_endpoint}\"\n"
                "http_headers = { Authorization = \"Bearer <token>\" }"
            ),
            "claude_command": (
                f"claude mcp add --transport http tareas {mcp_endpoint} "
                "--header \"Authorization: Bearer <token>\""
            ),
        },
        "agents_md_snippet": snippet,
        "rules": [
            "Store the real MCP token only in the local agent-client configuration.",
            "Never write tokens or secrets to AGENTS.md, docs, .env files, logs, issues, or Tareas notes.",
            "Use Tareas MCP as the single point of truth for project planning.",
            "If MCP tools are missing, configure MCP instead of creating a local shadow database.",
            "Use descriptions for scope and acceptance criteria; use handoff.add for progress, handoffs, and decisions.",
            "Write descriptions, notes, and handoffs as Markdown source, not rendered HTML.",
            "note.write is an upsert for the current user's one editable note and overwrites that note on repeated calls.",
            "Admins can correct existing content with note.update and handoff.update; author and creation time are preserved.",
            "note.delete removes only the current user's editable note.",
            "handoff.add creates a separate history item; handoff.delete removes a handoff by handoff_id.",
            "handoff_id is typed, e.g. task:123 or subtask:456; never pass a bare numeric entry id.",
            "Treat predecessor_ids as dependency edges; position_number is display order only.",
        ],
    }


def build_agents_snippet(web_ui: str, mcp_endpoint: str) -> str:
    return f"""## Tareas-Projektplanung

Projektplanung laeuft in Tareas. Tareas ist fuer dieses Projekt die gemeinsame
Planungsoberflaeche fuer Projekte, Sprintpakete, Abhaengigkeiten, Fortschritt
und Entscheidungen. Codex/Claude greift per MCP darauf zu.

- Web-UI: `{web_ui}`
- MCP-Endpoint: `{mcp_endpoint}`
- MCP-Server: `{MCP_SERVER_NAME}`
- Tareas-Projekt: `<Projektname>` (ID `<project_id>`)
- Erwarteter MCP-User: `<MCP-User-Anzeigename>`
- Token niemals ins Repo schreiben; nur `<token>` als Platzhalter dokumentieren.

Arbeitsregeln:

- Vor Schreiboperationen den aktuellen Projektstand per MCP lesen.
- `description` enthaelt Scope, Implementierungsbriefing, Akzeptanzkriterien
  und Definition of Done.
- Beschreibungen, Notizen und Handoffs als Markdown-Quelltext schreiben,
  nicht als gerendertes HTML.
- `handoff.add` dokumentiert Fortschritt, Handoffs, Entscheidungen,
  Testergebnisse, Blocker und Audit-Zusammenfassungen als neuen Verlaufseintrag.
- `handoff.list` liest diese Verlaufseintraege; `handoff.delete` loescht einen
  Handoff anhand seiner typisierten `handoff_id` (`task:123` oder
  `subtask:456`).
- Admins koennen bestehende Inhalte mit `note.update` (Autor-`user_id`) und
  `handoff.update` (typisierte `handoff_id`) korrigieren. Autor und
  Erstellungszeit bleiben erhalten; der Admin wird im Audit protokolliert.
- `note.write` aktualisiert nur die eine aktuelle Notiz des aufrufenden Users;
  wiederholte Aufrufe ueberschreiben diese Notiz.
- `note.list` liest editierbare User-Notizen; `note.delete` loescht nur die
  eine aktuelle Notiz des aufrufenden Users.
- `predecessor_ids` sind echte DAG-Abhaengigkeiten zwischen Sprintpaketen.
- Positionsaenderungen sind nur Sortierung/Anzeige; sie aendern keine
  Abhaengigkeitsgueltigkeit.
- `assign_self` nutzen, wenn eine Session konkrete Bearbeitung uebernimmt.
- Gitea-Issues enthalten konkrete Findings/Bugs; Tareas enthaelt
  Zusammenfassung und Issue-IDs/Links.
- Keine Secrets, Tokens, Passwoerter oder privaten Schluessel in Tareas-Notizen,
  AGENTS.md, docs/, .env, Logs oder Issues schreiben."""


def build_agent_guide_markdown(metadata: dict[str, Any]) -> str:
    web_ui = metadata["web_ui"]
    mcp_endpoint = metadata["mcp"]["endpoint"]
    admin_url = metadata["mcp"]["token_admin_url"]
    guide_url = metadata["agent_guide_url"]
    codex_config = metadata["client_examples"]["codex_config_toml"]
    claude_command = metadata["client_examples"]["claude_command"]
    snippet = metadata["agents_md_snippet"]

    return f"""# Tareas Agent Guide

Dieser Guide beschreibt, wie ein neues Projekt Tareas als gemeinsame
Planungs- und Audit-Ebene fuer Codex/Claude-Sessions nutzt.

Der Kern:

- Das Projekt dokumentiert die Tareas-Arbeitsweise in `AGENTS.md`.
- Der echte MCP-Zugriff wird lokal im Agenten-Client konfiguriert.
- Der MCP-Token gehoert nie ins Repository.

## Kurzprompt fuer neue Projekte

```text
Wir verwenden Tareas zur Projektplanung. Lies den Tareas-Agent-Guide:
{guide_url}

Richte dieses Repo danach ein: AGENTS.md ergaenzen, Tareas-MCP pruefen,
Tareas-Projekt anlegen oder referenzieren, Projekt-ID dokumentieren.
Token niemals ins Repo schreiben.
```

## Tareas-Instanz

- Web-UI: `{web_ui}`
- MCP-Endpoint: `{mcp_endpoint}`
- MCP-Transport: `{MCP_TRANSPORT}`
- MCP-Servername: `{MCP_SERVER_NAME}`
- Admin-UI fuer MCP-Tokens: `{admin_url}`, Tab `MCP`

## Token-Ablauf

1. Admin-UI oeffnen: `{admin_url}`
2. Tab `MCP` oeffnen.
3. Neuen Token erstellen, z.B. `Codex @ Projektname` oder `Claude @ Projektname`.
4. Token sofort kopieren. Er wird nur einmal angezeigt.
5. Token nur in die lokale MCP-Konfiguration des Agenten-Clients eintragen.

Der Token darf nicht in `AGENTS.md`, docs, `.env`, Logs, Issues oder
Tareas-Notizen gespeichert werden.

## Lokale MCP-Konfiguration

Codex-Beispiel in `/root/.codex/config.toml`:

```toml
{codex_config}
```

Claude-Beispiel:

```bash
{claude_command}
```

Nach Aenderung der MCP-Konfiguration muss die Agenten-Session neu gestartet
werden, damit die Tools erscheinen.

## Verifikation

Eine korrekt gestartete Session sieht Tareas-Tools, z.B.:

```text
mcp__tareas__whoami
mcp__tareas__list_projects
mcp__tareas__get_project
mcp__tareas__note.write
mcp__tareas__note.update
mcp__tareas__note.delete
mcp__tareas__handoff.add
mcp__tareas__handoff.list
mcp__tareas__handoff.update
mcp__tareas__handoff.delete
```

Pruefablauf:

1. `whoami` aufrufen.
2. Erwarteten `display_name` und `auth_source: "mcp"` pruefen.
3. `list_projects` aufrufen.
4. Zielprojekt mit `get_project(project_id=...)` lesen.

Wenn keine Tareas-MCP-Tools verfuegbar sind, keine lokale Ersatz-DB und keine
Schattenquelle in einem anderen System anlegen. Erst MCP einrichten oder den
Benutzer um lokale Konfiguration bitten.

## AGENTS.md-Snippet

```markdown
{snippet}
```

## Zielprojekt Einrichten

1. `AGENTS.md` im Zielprojekt lesen oder anlegen.
2. Abschnitt `Tareas-Projektplanung` aus dem Snippet einfuegen.
3. Lokale MCP-Verfuegbarkeit pruefen.
4. Falls MCP fehlt: lokale Client-Konfiguration einrichten, Session neu starten.
5. Tareas-Projekt per MCP suchen oder anlegen.
6. Projekt-ID und erwarteten MCP-User in `AGENTS.md` dokumentieren.
7. Sprintpakete als Tareas-Subtasks anlegen.
8. Abhaengigkeiten mit `predecessor_ids` bzw. Dependency-Tools modellieren.
9. Laengere Implementierungsplaene im Zielprojekt unter `docs/TODOS/` ablegen
   und dort auf Tareas-Projekt-/Subtask-IDs verweisen.
10. Fortschritt, Handoffs und Entscheidungen per `handoff.add` dokumentieren.

## Fachliche Nutzung

- Projekte: Epics oder groessere Arbeitsvorhaben.
- Subtasks: operative Sprintpakete oder klar abgrenzbare Arbeitspakete.
- Dependencies: echte Reihenfolge-/Blocker-Beziehungen im DAG.
- Notes: `note.write` fuer die aktuelle eigene Notiz, `note.list` zum Lesen
  editierbarer User-Notizen, `note.delete` zum Loeschen der eigenen Notiz.
- Handoffs: `handoff.add` fuer Fortschritt, Entscheidungen, Testergebnisse,
  Uebergaben und Audit-Zusammenfassungen; `handoff.list` zum Lesen;
  `handoff.delete` zum Loeschen anhand der typisierten `handoff_id`
  (`task:123` oder `subtask:456`).
- Assignments: aktuelle Bearbeitung und Verantwortlichkeit.

Markdown-Dateien im Zielprojekt bleiben sinnvoll fuer laengere Plaene,
Spezifikationen und Sprint-TODOs. Sie ersetzen Tareas nicht, sondern verlinken
auf Tareas-IDs.

## Haeufige Fehler

- Token in `AGENTS.md`, docs, `.env`, Logs oder Tareas-Notizen schreiben.
- `localhost:8504` in einem Remote-Projekt dokumentieren.
- Kiron (`10.0.12.16:8505`) statt Tareas verwenden.
- Bei fehlendem MCP eine lokale Schatten-DB anlegen.
- REST-Web-API mit MCP-Bearer aufrufen.
- Positionsnummern als Dependency-Gueltigkeit interpretieren.
"""
