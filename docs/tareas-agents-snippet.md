# Tareas AGENTS.md Snippet

Kopiere diesen Abschnitt in die `AGENTS.md` eines Zielprojekts und ersetze die
Platzhalter.

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
- `handoff.add` dokumentiert Fortschritt, Handoffs, Entscheidungen,
  Testergebnisse, Blocker und Audit-Zusammenfassungen als neuen Verlaufseintrag.
- `handoff.list` liest diese Verlaufseintraege; `handoff.delete` loescht einen
  Handoff anhand seiner typisierten `handoff_id` (`task:123` oder
  `subtask:456`).
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
