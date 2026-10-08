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
- Separater MCP-Dienst: `http://10.0.12.7:8506/mcp/`
- MCP-Transport: Streamable HTTP
- MCP-Servername: `tareas`
- Admin-UI fuer MCP-Tokens: `http://10.0.12.7:8505`, Tab `MCP`

`10.0.12.16:8505` ist in dieser Umgebung Kiron, nicht Tareas.

Beide MCP-Zugaenge verwenden dieselben Werkzeuge, Tokens und Projektrechte.
Bestehende Konfigurationen mit Port 8504 bleiben gueltig. Fuer eine separate
Firewall-Freigabe kann Port 8506 verwendet werden: Dort stehen auch Datei-REST,
`/agent-guide.md` und `/.well-known/tareas-agent.json` bereit, aber keine Web-UI.
Dateitransfers koennen damit ebenfalls vollstaendig ueber Port 8506 laufen.

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
- Der MCP-Bearer gilt fuer `/mcp/` und die REST-Dateioperationen unter
  `/api/tasks/{task_id}/files`. Andere REST-Endpunkte brauchen eine Web-Anmeldung.
- Wenn die lokale Konfiguration nicht beschreibbar ist, den Benutzer um Setup
  oder Token-Konfiguration bitten.

Nach Aenderung der MCP-Konfiguration muss die Agenten-Session neu gestartet
werden, damit die Tools erscheinen.

## Verifikation

Eine korrekt gestartete Session sieht Tareas-Tools, z.B.:

```text
mcp__tareas__whoami
mcp__tareas__list_projects
mcp__tareas__list_projects_page
mcp__tareas__get_project
mcp__tareas__note.write
mcp__tareas__note.update
mcp__tareas__note.delete
mcp__tareas__handoff.add
mcp__tareas__handoff.list
mcp__tareas__handoff.update
mcp__tareas__handoff.delete
mcp__tareas__file.list
mcp__tareas__file.read
mcp__tareas__file.write
mcp__tareas__file.mkdir
mcp__tareas__file.move
mcp__tareas__file.delete
```

Pruefablauf:

1. `whoami` aufrufen.
2. Erwarteten `display_name` und `auth_source: "mcp"` pruefen.
3. `list_projects_page` fuer eine kompakte, paginierte Uebersicht aufrufen.
4. Zielprojekt mit `get_project(project_id=...)` lesen.
5. Bei konfigurierter Dateiablage (`file_storage_type` ist `local` oder `webdav`)
   mit `file.list(task_id=...)` die Dateien und effektiven Schreibrechte pruefen.

Wenn keine Tareas-MCP-Tools verfuegbar sind, keine lokale Ersatz-DB und keine
Schattenquelle in einem anderen System anlegen. Erst MCP einrichten oder den
Benutzer um die lokale Konfiguration bitten.

## Projektlisten, Status und Fehler

`list_projects_page(status=null, offset=0, limit=50, include_description=false)`
liefert `items`, `total`, `offset`, `limit` und `next_offset`. Maximal 500 Eintraege
pro Seite; fuer die naechste Seite `offset=next_offset` verwenden, bis
`next_offset=null` ist. Filter beim Blaettern beibehalten. Sortierung: Prioritaet,
Erstellungszeit und ID, jeweils absteigend. Gleichzeitige Aenderungen koennen
Offsets verschieben. Beschreibungen fehlen standardmaessig; Details mit
`get_project(project_id=...)` nachladen oder `include_description=true` setzen.
Das bisherige `list_projects` liefert fuer bestehende Clients weiterhin eine
Liste. Beide Werkzeuge beachten Sichtbarkeitsrechte und filtern den wirksamen Status.

REST und MCP akzeptieren nur `offen`, `in_arbeit`, `erledigt` und `abgebrochen`
als Status sowie ganzzahligen Teilaufgabenfortschritt von 0 bis 100.
Bei Projekten gilt wie in der Weboberflaeche:

- Keine vollstaendig erledigte Teilaufgabe: `offen`, auch bei Teilfortschritt.
- Einige, aber nicht alle Teilaufgaben zu 100 % erledigt: `in_arbeit`.
- Alle Teilaufgaben zu 100 % erledigt: `erledigt`, ohne manuellen Abschluss.
- Leeres Projekt: `offen`.
- `abgebrochen` hat Vorrang. `update_project(status="offen")` nimmt das Projekt
  wieder auf; danach gilt erneut die Ableitung aus den Teilaufgaben.

Normale Aufgaben behalten ihren manuell gesetzten Status.

Mit `create_project(parent_subtask_id=123)` oder
`update_project(project_id=456, parent_subtask_id=123)` wird ein Kind-Projekt an
Teilaufgabe 123 gehaengt. Pro Teilaufgabe ist ein Kind-Projekt erlaubt. Beide
Seiten brauchen Bearbeitungsrechte, die Verknuepfung vererbt keine Zugriffsrechte.
Selbstbezuege und Zyklen ueber mehrere Projekte werden abgewiesen.
Die PID-Spalte zeigt die Eltern-Teilaufgaben-ID, nicht die Eltern-Projekt-ID.
Projektantworten enthalten `parent_subtask_id`, `parent_project_id` und
`progress_percent`, Teilaufgaben `child_project_id` und `progress_automatic`.

Der Eltern-Subtask uebernimmt den Anteil vollstaendig erledigter Teilaufgaben des
Kind-Projekts, auf ganze Prozent abgerundet (leer = 0). Teilfortschritte und der
separate Abbruchstatus aendern die Berechnung nicht. Aenderungen werden innerhalb
derselben Transaktion ueber alle Elternebenen weitergegeben. Bei
`progress_automatic=true` darf `status_percent` auch als Admin nicht geschrieben
werden: `linked_project_progress_readonly`, Feld `status_percent`.
Andere Teilaufgabenfelder bleiben entsprechend den bisherigen Rechten editierbar.
`update_project(parent_subtask_id=0)` trennt die Verbindung. Der letzte Fortschritt
bleibt erhalten und ist wieder editierbar. Das gilt auch beim Loeschen des
Kind-Projekts. Das Loeschen eines Eltern-Subtasks/-Projekts trennt die Kinder,
ohne diese Projekte zu loeschen. Typwechsel zu einer normalen Aufgabe brauchen
vorher die Trennung aller betroffenen Projektverknuepfungen.

MCP-Werkzeugfehler liefern `isError=true`. `structuredContent` und der
JSON-Textinhalt enthalten `code`, `message` und bei feldbezogenen Fehlern
`field`/`fields`. Stabile Codes wie `invalid_status`, `invalid_progress`,
`dependency_cycle` und `storage_not_configured` fuer die Fehlerbehandlung
verwenden, statt Meldungstexte auszuwerten. Das bestehende REST-Fehlerformat bleibt erhalten.

Bei `storage_not_configured`: Aufgabe/Projekt in der Weboberflaeche aufklappen und
den Button **Dateiablage** verwenden (lokal oder WebDAV). Nach der Einrichtung
stehen die Einstellungen im Header des Dateibrowsers. Hierfuer sind
Bearbeitungsrechte am Projekt erforderlich, etwa als Ersteller, Admin oder durch
eine Bearbeitungsfreigabe. Eine blosse Zuweisung reicht nicht aus. Dateien lesen
braucht Leserechte, Dateiaenderungen brauchen Bearbeitungsrechte. Die MCP-Werkzeuge
legen keine Ablage automatisch an.

## Dateiablage per REST

Fuer direkte Dateiuebertragungen, auch ueber 1 MiB, gilt derselbe MCP-Token
an den folgenden Endpunkten. Eine Web-Anmeldung ist dafuer nicht erforderlich.

| Methode | Pfad | Parameter / Body |
|---------|------|------------------|
| GET | `/api/tasks/{task_id}/files` | Optionaler Unterordner als Query `path` |
| GET | `/api/tasks/{task_id}/files/download` | Datei als Query `path`, Antwort sind Dateibytes |
| POST | `/api/tasks/{task_id}/files/upload` | Multipart-Feld `file`, optional Zielordner als Query `path`, maximal 500 MiB |
| POST | `/api/tasks/{task_id}/files/mkdir` | JSON `{"name": "Ordner"}`, optional Elternordner als Query `path` |
| PUT | `/api/tasks/{task_id}/files/move` | JSON `{"source": "alt.txt", "destination": "neu.txt"}` |
| DELETE | `/api/tasks/{task_id}/files` | Query `path`, Ordner werden rekursiv geloescht |

Bei jedem Aufruf `Authorization: Bearer <token>` senden, bei Schreibzugriffen
zusaetzlich `X-Requested-With: XMLHttpRequest`. Alle Pfade sind relativ zur
konfigurierten lokalen oder WebDAV-Ablage der Aufgabe bzw. des Projekts.
Leserechte erlauben Auflisten und Download, Bearbeitungsrechte auch Aenderungen.
Token-Widerruf und der globale MCP-Ausschalter greifen bei jedem Aufruf.
Ein expliziter Authorization-Header hat Vorrang vor einer Web-Session.
Andere REST-Endpunkte, Ablagekonfiguration und Office-Editor brauchen weiterhin
eine Web-Anmeldung.

```bash
curl --fail --get 'http://10.0.12.7:8504/api/tasks/<task_id>/files/download' \
  --header 'Authorization: Bearer <token>' \
  --data-urlencode 'path=Unterlagen/Plan.pdf' --output Plan.pdf

curl --fail 'http://10.0.12.7:8504/api/tasks/<task_id>/files/upload' \
  --header 'Authorization: Bearer <token>' \
  --header 'X-Requested-With: XMLHttpRequest' \
  --form 'file=@Plan.pdf'
```

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
- Eine Projektzuweisung erlaubt Lesen der Notizen/Handoffs aller Teilaufgaben
  und Schreiben eigener Beitraege, ohne Einzelzuweisung. Dabei ist `task_id`
  die Projekt-ID und `subtask_id` die Teilaufgaben-ID.
- `predecessor_ids` sind echte DAG-Abhaengigkeiten zwischen Sprintpaketen.
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
- Andere REST-Endpunkte als die Dateioperationen mit MCP-Bearer aufrufen.
- Positionsnummern als Dependency-Gueltigkeit interpretieren.
