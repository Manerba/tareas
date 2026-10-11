# Tareas AGENTS.md Snippet

Kopiere diesen Abschnitt in die `AGENTS.md` eines Zielprojekts und ersetze die
Platzhalter.

Der MCP-Endpoint kann auch `http://10.0.12.7:8506/mcp/` sein. Der separate Dienst
nutzt dieselben Tokens und Rechte; bestehende Verbindungen auf 8504 bleiben gueltig.

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

- Vor Schreiboperationen `get_project_status` lesen; Beschreibungen gezielt nachladen.
  Projekt-Beschreibungsrevision: `project.description_revision`.
- Ueblich: `get_project/list_subtasks(include_description=false)`.
- `update_project/update_subtask` bestaetigen nur mit `{"ok": true}`, ohne
  `compact`-Parameter. Optionale Audit-Begruendung: `reason`.
- Beschreibungsrevision mitsenden; `patch_description` fuer exaktes Anhaengen
  oder eindeutiges Ersetzen. Bei Konflikt neu lesen und abgleichen.
- `id` ist stabil, `position_number` die veraenderliche Reihenfolge, `name`
  der fachliche Titel ohne Positionspraefix. Sinnvolle Zahlen nicht entfernen.
- `search` verwendet UND-Woerter, zitierte Phrasen und Wortpraefixe mit `*`;
  `project_id`, `types`, `since` filtern, `handoff.get` laedt gezielt nach.
- `file.append` ergaenzt vorhandene UTF-8-Dateien exakt bis 1 MiB gesamt,
  ohne impliziten Zeilenumbruch; nach unklarem Fehler erst Inhalt pruefen.
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
  Erstellungszeit bleiben erhalten; der Bearbeiter wird im Audit protokolliert.
  Autoren duerfen eigene Handoffs mit Beitragsrechten fuer 300 Sekunden mit
  Revision aus `handoff.get/list` korrigieren; Admins bleiben zeitlich unbeschraenkt.
- `note.write` aktualisiert nur die eine aktuelle Notiz des aufrufenden Users;
  wiederholte Aufrufe ueberschreiben diese Notiz.
- `note.list` liest editierbare User-Notizen; `note.delete` loescht nur die
  eine aktuelle Notiz des aufrufenden Users.
- Eine Projektzuweisung erlaubt Lesen der Notizen/Handoffs aller Teilaufgaben
  und Schreiben eigener Beitraege, ohne Einzelzuweisung. Dabei ist `task_id`
  die Projekt-ID und `subtask_id` die Teilaufgaben-ID.
- `predecessor_ids` sind echte DAG-Abhaengigkeiten zwischen Sprintpaketen.
- Nur notwendige direkte Vorgaenger im selben Projekt angeben. Wenn C von B
  und B von A abhaengt, darf C nicht zusaetzlich direkt von A abhaengen.
  Das gilt auch, wenn eine neue Verbindung eine vorhandene direkte Kante
  ueberfluessig machen wuerde. Vorgaengerlisten bei Bedarf komplett ersetzen.
- Positionsaenderungen sind nur Sortierung/Anzeige; sie aendern keine
  Abhaengigkeitsgueltigkeit.
- REST, MCP und Dateiablage pruefen dieselben Aufgabenrechte. Lesefreigaben
  erlauben nur Lesen, Schreibfreigaben auch Inhalte und eigene Beitraege.
  Projektfreigaben gelten fuer alle Teilaufgaben, Erstellen braucht ein eigenes Recht.
- Zuweisungen, Loeschen von Aufgaben/Teilaufgaben und Freigaben verwalten nur Ersteller und Admins.
  Das gilt auch fuer `assign_self`. Bei fremden Aufgaben muss der Ersteller
  oder ein Admin die Session zuweisen oder freigeben.
- `file.list`, `file.read`, `file.write`, `file.mkdir`, `file.move` und
  `file.delete` nutzen die konfigurierte lokale oder WebDAV-Projektablage.
  `task_id` ist die Projekt-ID, Pfade sind relativ zu dessen Ablage.
  Lesen braucht Leserechte, Dateiaenderungen brauchen Bearbeitungsrechte.
- `file.read` liefert UTF-8 oder Base64 (Feld `encoding`). `file.write`
  ersetzt die gesamte Datei, daher vorher lesen. Maximal 1 MiB je Datei beim
  Lesen/Schreiben, groessere Dateien ueber die REST-Datei-API oder Web-UI. `file.list` liefert
  bei weiteren Eintraegen `next_offset`. `file.delete` loescht Ordner samt Inhalt.
- Die REST-Dateiablage unter `/api/tasks/{task_id}/files` akzeptiert denselben
  MCP-Token als `Authorization: Bearer <token>`. Schreibzugriffe brauchen
  zusaetzlich `X-Requested-With: XMLHttpRequest`. Uploads laufen als Multipart
  mit Feld `file` (maximal 500 MiB), Downloads liefern die Dateibytes.
  Aufgabenrechte, Token-Widerruf und der MCP-Ausschalter gelten auch dort.
- Gitea-Issues enthalten konkrete Findings/Bugs; Tareas enthaelt
  Zusammenfassung und Issue-IDs/Links.
- Keine Secrets, Tokens, Passwoerter oder privaten Schluessel in Tareas-Notizen,
  AGENTS.md, docs/, .env, Logs oder Issues schreiben.
```
