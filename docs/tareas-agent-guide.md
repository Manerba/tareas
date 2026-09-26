# Tareas-Agent-Guide

Dieser Guide beschreibt, wie ein neues Projekt Tareas als gemeinsame
Planungs- und Audit-Ebene fuer Codex/Claude-Sessions nutzt.

Der Kern ist bewusst einfach:

- Das Projekt dokumentiert die Tareas-Arbeitsweise in `AGENTS.md`.
- Der echte MCP-Zugriff wird lokal im Agenten-Client konfiguriert.
- Der MCP-Token gehoert nie ins Repository.

## Kurzprompt fuer neue Projekte

Diesen Text kann der Benutzer in einem neuen Projekt an eine Agenten-Session
geben:

```text
Wir verwenden Tareas zur Projektplanung. Lies den Tareas-Agent-Guide:
http://10.0.12.7:8504/agent-guide.md

Richte dieses Repo danach ein: AGENTS.md ergaenzen, Tareas-MCP pruefen,
Tareas-Projekt anlegen oder referenzieren, Projekt-ID dokumentieren.
Token niemals ins Repo schreiben.
```

## Gemeinsame Tareas-Instanz

- Web-UI: `http://10.0.12.7:8504/`
- Agent-Guide: `http://10.0.12.7:8504/agent-guide.md`
- Agent-Bootstrap-JSON: `http://10.0.12.7:8504/.well-known/tareas-agent.json`
- MCP-Endpoint: `http://10.0.12.7:8504/mcp/`
- MCP-Transport: Streamable HTTP
- MCP-Servername: `tareas`
- Admin-UI fuer MCP-Tokens: `http://10.0.12.7:8505`, Tab `MCP`

`10.0.12.16:8505` ist in dieser Umgebung Kiron, nicht Tareas.

Die laufende Tareas-Instanz ist die kanonische Quelle fuer diesen Guide. Das
Gitea-Repo versioniert die Dokumentation nur; neue Projekte sollen den Guide
ueber die Tareas-URL lesen, damit keine Gitea-Verfuegbarkeit vorausgesetzt
wird.

## Lokale MCP-Konfiguration

Die Tool-Integration ist kein Projektcode und keine Runtime-Abhaengigkeit der
Zielanwendung. Sie gehoert in die lokale Konfiguration des jeweiligen
Agenten-Clients.

Codex-Beispiel in `/root/.codex/config.toml`:

```toml
[mcp_servers.tareas]
url = "http://10.0.12.7:8504/mcp/"
http_headers = { Authorization = "Bearer <token>" }
```

Claude-Beispiel:

```bash
claude mcp add --transport http tareas \
  http://10.0.12.7:8504/mcp/ \
  --header "Authorization: Bearer <token>"
```

Regeln:

- `<token>` ist ein Platzhalter. Den echten Token nie in Repo-Dateien schreiben.
- Tokens werden in Tareas unter Admin UI -> MCP erzeugt und nur einmal angezeigt.
- Der MCP-Bearer ist nur fuer `/mcp/`, nicht fuer normale REST/Web-API-Calls.
- Wenn die lokale Konfiguration nicht beschreibbar ist, den Benutzer um Setup
  oder Token-Konfiguration bitten.

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
Benutzer um die lokale Konfiguration bitten.

## AGENTS.md-Snippet

Dieses Snippet in das Zielprojekt uebernehmen und die Platzhalter ersetzen:

```markdown
## Tareas-Projektplanung

Projektplanung laeuft in Tareas. Tareas ist fuer dieses Projekt die gemeinsame
Planungsoberflaeche fuer Projekte, Sprintpakete, Abhaengigkeiten, Fortschritt
und Entscheidungen. Codex/Claude greift per MCP darauf zu.

- Web-UI: `http://10.0.12.7:8504/`
- MCP-Endpoint: `http://10.0.12.7:8504/mcp/`
- MCP-Server: `tareas`
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
  AGENTS.md, docs/, .env, Logs oder Issues schreiben.
```

Eine separate Kopiervorlage liegt in `docs/tareas-agents-snippet.md`.

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
10. Fortschritt, Handoffs und Entscheidungen waehrend der Arbeit per
    `handoff.add` dokumentieren.

## Fachliche Nutzung

Tareas ist die Steuerungsebene:

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
