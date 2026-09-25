# Changelog

Zeitstempel;Version;Kategorie;Beschreibung
25.09.2026 15:02;0.0.1+116;Bugfix;Datenbankinitialisierung von Haupt-App, Admin und Schedulern durch gemeinsame Dateisperre serialisiert. Gleichzeitige Erststarts und Schema-Migrationen kollidieren nicht mehr, Verbindungen und Sperren werden auch bei Fehlern freigegeben. Regressionstests fuer parallele Starts, bestehende Daten und Prozessabbrueche ergaenzt
13.09.2026 05:28;0.0.1+115;Bugfix;Teilaufgaben zeigen in Projektlisten und zugewiesenen Ansichten den genauen Prozentwert. Numerische Eingabe von 0 bis 100 ersetzt die Statusauswahl, bewahrt Zwischenwerte beim Speichern und aktualisiert die Anzeige unmittelbar
12.09.2026 19:57;0.0.1+114;Feature;Aufgaben und Projekte koennen per Statusauswahl abgebrochen und wieder aufgenommen werden. Inhalte und Teilaufgaben bleiben erhalten, Abbruch hat Vorrang vor dem Projektfortschritt. Statusfilter, sieben Sprachen, Team-Bearbeitungsrechte und Fristerinnerungen angepasst
25.08.2026 15:06;0.0.1+113;Aenderung;Verlauf-/Handoff-Eintraege in Aufgaben, Projekten und Teilaufgaben sind standardmaessig kompakt zugeklappt, zeigen eine einzeilige Textvorschau und lassen sich per Maus oder Tastatur vollstaendig aufklappen
23.08.2026 15:19;0.0.1+112;Aenderung;Aufgaben- und Projekt-Tabelle zeigt die technische ID als erste Datenspalte vor dem Namen, sortierbar und kompakt wie die Subtask-ID. Zugewiesene Subtask-Pseudozeilen zeigen ihre echte Subtask-ID; die Inline-Bearbeitung ordnet Zellen robust anhand ihrer Datenfelder zu
26.06.2026 00:49;0.0.1+112;Security;MCP note.* und handoff.* pruefen jetzt Projekt-/Teilaufgaben-Leserechte wie REST, search begrenzt Treffer per SQL nach Sichtbarkeitsfilter und gibt fuer Subtask-Notes/-Handoffs task_id/project_id zurueck, handoff_id ist typisiert (task:<id>/subtask:<id>) gegen Cross-Type-Loeschungen, handoff.delete erlaubt Subtask-only-Rechte ohne Handoff-Enumeration und loggt den Owner geloeschter Handoffs. Admin-Audit-Filter markiert historische note_entry-Typen als legacy
26.06.2026 00:32;0.0.1+112;Feature;MCP-Notiz-API objektorientiert umgestellt: editierbare User-Notizen laufen jetzt ueber note.list/note.write/note.delete, Verlaufseintraege ueber handoff.list/handoff.add/handoff.delete. Alte Notiz-Toolnamen werden nicht mehr registriert, Handoffs koennen per handoff_id geloescht werden
26.06.2026 00:10;0.0.1+112;Bugfix;MCP-geschriebene Plain-Text-Notizen rendern Zeilenumbrueche korrekt: WYSIWYG-Editor und Read-only-Notizboxen erhalten pre-wrap/overflow-wrap, sodass Newlines in Projekt- und Teilaufgaben-Notes sichtbar bleiben
24.06.2026 18:41;0.0.1+111;Feature;MCP-Tool delete_note ergaenzt: Agenten koennen ihre eigene aktuelle Upsert-Notiz an Aufgaben oder Teilaufgaben loeschen, waehrend append-only Verlaufseintraege unveraenderlich bleiben. Admin-Toolliste und Agent-Guide dokumentieren das neue Verhalten
22.06.2026 09:29;0.0.1+111;Bugfix;ExpandableTable-Detailbereiche haben kein fixes max-height-Limit mehr. Lange Projekt-/Aufgaben-Details mit vielen oder langen Notizen wachsen auf ihre natuerliche Hoehe und werden unten nicht mehr abgeschnitten
21.06.2026 02:12;0.0.1+110;Security;Notiz- und Verlauf-Endpunkte pruefen jetzt Leseberechtigungen und liefern 404/403 statt leerer Listen bei falschen IDs oder fehlendem Zugriff. UI zeigt HTTP-Fehler als Ladefehler, MCP write_note validiert task_id/subtask_id-Kombinationen, Append-Tabellen erhalten user_id-Indizes und der Agent-Guide ist konsistent
21.06.2026 01:45;0.0.1+109;Feature;Append-only Notizverlauf ergaenzt: neue MCP-Tools append_note/get_note_entries, separate task_note_entries/sub_task_note_entries Tabellen, REST-Leseendpunkte und UI-Verlauf fuer Aufgaben und Teilaufgaben. Agent-Guide empfiehlt append_note fuer Handoffs/Fortschritt, write_note bleibt Upsert der aktuellen User-Notiz
19.06.2026 21:52;0.0.1+108;Enhancement;Teilaufgaben-Detailansicht zeigt alle Notizen einer Teilaufgabe als read-only Liste, inklusive MCP-/Agent-Notizen. Dadurch sind per MCP geschriebene sub_task_notes in der UI nachvollziehbar sichtbar
18.06.2026 04:54;0.0.1+107;Enhancement;Teilaufgaben-Tabelle zeigt die technische Subtask-ID als zweite Spalte neben der Positionsnummer. Dadurch sind MCP-Referenzen wie subtask_id eindeutig in der UI nachvollziehbar
14.06.2026 23:26;0.0.1+106;Bugfix;Task-Typen werden serverseitig validiert: REST und MCP akzeptieren nur noch aufgabe/projekt, MCP create_project legt standardmaessig projekt an. Ungueltiger Runtime-Datensatz task_type=epic wurde auf projekt bereinigt und im Audit-Log dokumentiert
14.06.2026 22:54;0.0.1+105;Feature;Agenten-Bootstrap direkt in Tareas bereitgestellt: oeffentlicher Markdown-Guide unter /agent-guide.md, maschinenlesbare Metadaten unter /.well-known/tareas-agent.json und MCP-Tool get_agent_guide. Guide enthaelt MCP-Setup, Token-Regeln und AGENTS.md-Snippet ohne Gitea-Abhaengigkeit
14.06.2026 15:00;0.0.1+104;Docs;Tareas-Agent-Guide ergaenzt: neues How-to fuer Projekt-Onboarding mit AGENTS.md-Snippet, lokaler Codex/Claude-MCP-Konfiguration, Verifikation, Tareas-Arbeitsregeln und Secret-Regeln
07.06.2026 20:59;0.0.1+103;Enhancement;Netzplan: Doppelklick auf Projekt- oder Teilaufgabenknoten oeffnet ein modales Detail-Overlay mit Status, Metadaten, Vorgaengern und Beschreibung. Single-Click-Linking bleibt per kurzer Klickverzoegerung erhalten
07.06.2026 20:43;0.0.1+102;Feature;MCP-Server: Teilaufgaben koennen per move_subtask(subtask_id, direction) um eine Position und per set_subtask_position(subtask_id, position_number) direkt auf eine 1-basierte Zielposition verschoben werden. Neue Positionen werden konsistent neu nummeriert und im Audit-Log protokolliert
07.06.2026 20:43;0.0.1+102;Bugfix;Teilaufgaben-Positionen von Abhaengigkeitsgueltigkeit entkoppelt: Positionsaenderungen loeschen keine Vorgaenger mehr, Dependency-Add/Remove sortiert Positionen nicht mehr topologisch um und das Vorgaenger-Dropdown erlaubt positionsunabhaengige, azyklische Kanten
07.06.2026 20:43;0.0.1+102;Aenderung;SQLite-Runtime-Dateien data/*.db-shm und data/*.db-wal werden ignoriert, damit lokale DB-Zugriffe den Git-Status nicht verschmutzen
04.06.2026 19:28;0.0.1+101;Enhancement;Netzplan Auto-Anordnung verbessert: lange Knotennamen werden automatisch umgebrochen, Knotenabstaende werden aus Label-Groessen berechnet und eine neue Auto-Ansicht "Von oben nach unten" ordnet Abhaengigkeitsebenen vertikal an
04.06.2026 17:29;0.0.1+100;Aenderung;GitHub-Publish-Dryrun abgesichert: Public-Clean-Copy behaelt .gitignore, schliesst AGENTS.md und .codex aus und verwendet fuer den Mail-Scheduler eine Production-Service-Datei ohne lokale Dev-Pfade
04.06.2026 17:29;0.0.1+100;Docs;LDAP-Admin-Placeholder neutralisiert, damit keine konkrete private Infrastruktur-IP im oeffentlichen Repo erscheint
04.06.2026 13:29;0.0.1+99;Aenderung;UI-Farbschemas korrigiert: Hell und Dunkel sind jetzt Schema 1/2, Graphit/Cyan, Slate/Emerald und Ink/Indigo sind drei zusaetzliche feste Schemas. Separater Dark-Mode-Schalter entfernt, data-theme wird aus dem gewaehlten Schema abgeleitet
04.06.2026 13:29;0.0.1+99;Enhancement;Kontrast der Farbschemas deutlich erhoeht: Haupttexte, Header, Buttons, Badges, Highlights, Login, Datei-Browser, OnlyOffice und Editor verwenden themefaehige Text-/Soft-Farben mit geprueften AA-Kontrasten
04.06.2026 13:29;0.0.1+99;Docs;README um MCP-Server, AI-Agent-Collaboration, MCP-Konfiguration, Token-/Audit-Sicherheit und Architekturhinweise ergaenzt. Erster Abschnitt erwaehnt Optimierung fuer Zusammenarbeit mit KI-Agenten
04.06.2026 12:50;0.0.1+98;Feature;UI-Farbschemas: drei kontrastreiche Paletten Graphit/Cyan, Slate/Emerald und Ink/Indigo mit je Light- und Dark-Variante. Neues ausklappbares Farbschema-Menue im Settings-Dropdown von Haupt- und Admin-App, Persistenz via localStorage
04.06.2026 12:50;0.0.1+98;Bugfix;Admin-UI MCP/API-Tokens: Modal-Styles global in style.css verfuegbar gemacht, damit Token-Erstellung und Einmal-Anzeige als Overlay statt im View-Bereich erscheinen. Token-Anzeige nutzt breite Modal-Variante
04.06.2026 12:50;0.0.1+98;Docs;AGENTS.md aus CLAUDE.md und Claude-Memory aufgebaut und gegen aktuellen Repo-Stand geprueft: Architektur, Services, Import-Pattern, DB, API-Router, MCP, Frontend, Security, Commit- und Zwei-Umgebungen-Konvention
04.06.2026 12:50;0.0.1+98;Aenderung;publish.sh: Clean-Copy-Excludes an .gitignore angeglichen und interne Publish-/Packaging-Artefakte gezielter entfernt
04.06.2026 05:02;0.0.1+97;Feature;MCP-Server: FastMCP an /mcp/ in app.py gemounted, Streamable-HTTP-Transport, Bearer-Token-Auth via Middleware. 19 Tools fuer Projekte, Subtasks, Notes, Dependencies, Suche, Users, Areas, Selbstzuweisung, whoami
04.06.2026 05:02;0.0.1+97;Feature;Audit-Log: neue Tabelle audit_log + dashboard/audit_log.py Helper. log_change-Aufrufe an allen Schreib-Endpunkten (tasks, sub_tasks, notes, dependencies, mcp_tokens, mcp_config). Reads werden bewusst nicht geloggt
04.06.2026 05:02;0.0.1+97;Feature;User-Provider 'mcp' (neben local/ldap): jeder MCP-Token bekommt eigenen User in users-Tabelle. Token als SHA-256-Hash gespeichert, Plain-Token nur beim Anlegen einmal sichtbar
04.06.2026 05:02;0.0.1+97;Feature;Admin-UI: neuer Tab 'MCP' (admin.html, tab_mcp.js, api_admin_mcp.py) mit Server-Status, Tool-Liste, Token-Verwaltung, Audit-Log-Viewer mit Filtern (Actor/Entity/Action/Pagination), globalem Kill-Switch
04.06.2026 05:02;0.0.1+97;Feature;Admin-Sicht erweitert: GET /api/tasks und GET /api/tasks/{id}/subtasks liefern Admins alle Eintraege (Read-Bypass, Edit bleibt beim Creator). Neue Frontend-Kategorie 'mcp' fuer Projekte mit creator.auth_source='mcp', neuer Filter-Button im Aufgaben-Tab, i18n-Key category.mcp in 7 Sprachen
04.06.2026 05:02;0.0.1+97;Aenderung;Admin darf alle Tasks und SubTasks loeschen (delete_subtask-Endpunkt + UI-Buttons in tab_aufgaben.js: getTaskPermissions liefert isAdmin, renderDeleteAction und Subtask-Action-Spalte zeigen Buttons fuer Admin)
04.06.2026 05:02;0.0.1+97;Aenderung;MCP-User-Assignees haben volle Edit-Rechte auf zugewiesene Tasks (Permission-Bypass fuer auth_source='mcp' in update_task), menschliche Assignees bleiben auf Status beschraenkt
04.06.2026 05:02;0.0.1+97;Aenderung;requirements.txt: fastmcp>=3.0 ergaenzt
04.06.2026 05:02;0.0.1+97;Bugfix;Aufgaben-Detail: Beschreibung editierbar fuer Creator-Assignee, wenn isOwnTask (Logik-Korrektur in tab_aufgaben.js: !perm.isOwnTask im Creator+Assigned-Branch)
04.06.2026 05:02;0.0.1+97;Docs;CLAUDE.md: Zwei-Umgebungen-Konvention dokumentiert (Hauptprojekt Bash/Linux mir/Tareas vs. Desktop-App PowerShell/Windows mir/Tareas-Desktop)
04.06.2026 05:02;0.0.1+97;Docs;docs/spec-desktop-app.md: Sprint-1/2-Markierungen, Installer-Architektur (zweistufig via Inno-Setup + --install), JWT statt generischem Token
15.02.2026 15:30;0.0.1+95;Feature;Desktop-App: Komplettes Projekt-Skelett (22 Dateien) unter clients/desktop/ - Standalone/Client-Modus, pywebview-Fenster, System Tray, Benachrichtigungen, Settings-Dialog, Dateiablage, Welcome-Dialog, i18n (DE/EN), PyInstaller/Inno-Setup Build-Config
14.02.2026 19:20;0.0.1+93;Docs;README: 3 Screenshots eingefuegt (Projekt-Ansicht, Subtask-Liste, Netzplan)
14.02.2026 19:05;0.0.1+92;Enhancement;Demo-Daten: Subtasks von 26 auf 15 konsolidiert mit sauberen Abhaengigkeiten (5 Levels), ausfuehrliche Projekt- und Aufgabenbeschreibungen fuer GitHub-Screenshots
14.02.2026 18:30;0.0.1+91;Enhancement;Netzplan: Auto-Button durch Dropdown-Submenu mit 3 Layout-Optionen ersetzt (Barycenter/Kompakt/Oben ausgerichtet), Mindestabstand zwischen Knoten von 70 auf 90 erhoeht
14.02.2026 17:45;0.0.1+90;Feature;Netzplan: Barycenter-Heuristik (Sugiyama) fuer Kreuzungsminimierung bei Auto-Anordnung. 4 Sweep-Iterationen (forward+backward), Projektknoten Y-zentriert auf Nachfolger
14.02.2026 13:36;0.0.1+89;Feature;i18n: 4 neue Sprachen hinzugefuegt - Franzoesisch (fr), Rumaenisch (ro), Ukrainisch (uk), Russisch (ru). Je 407 Keys uebersetzt, Sprachauswahl in i18n.js erweitert (7 Sprachen)
14.02.2026 13:24;0.0.1+88;fix;Changelog: Uhrzeiten anhand gitea nachgetragen.
14.02.2026 13:24;0.0.1+87;Bugfix;i18n: Fehlende Uebersetzung der Spaltenuebrschriften in der Aufgaben-Tabelle (Name, Typ, Status, Prioritaet, Von, Zugewiesen an, Erstellt, Deadline). i18n_key-Feld in Column-Klasse, t()-Aufruf in renderHeader(), 8 neue tasks.col.*-Keys in de/en/es.json
14.02.2026 13:09;0.0.1+86;Enhancement;i18n: Sprachauswahl als aufklappbares Submenu statt Cycle-Toggle (zeigt alle verfuegbaren Sprachen mit Haken bei aktiver Sprache)
14.02.2026 12:57;0.0.1+85;Feature;i18n Mehrsprachigkeit (DE/EN/ES): i18n.js Core-System mit t()-Funktion, data-i18n Auto-Patch, Sprachauswahl im Settings-Dropdown, 399 Translation-Keys in 3 JSON-Dateien (de/en/es.json), alle 17 JS-Dateien und 5 HTML-Templates auf t()-Aufrufe umgestellt
14.02.2026 04:29;0.0.1+84;Release;Open-Source-Vorbereitung: Hardcodierte IPs entfernt (app.py, admin_app.py, tab_tls.js, tab_onlyoffice.js, tab_allgemein.js, security-issues-fixes.md, changelog.md), docs/prompts.txt geloescht, Packaging-Maintainer auf GitHub aktualisiert, CLAUDE.md bereinigt, .gitignore erweitert, LICENSE (MIT) und README.md erstellt, publish.sh um --github/--github-init erweitert (prepare_clean_copy fuer bereinigte Kopie)
14.02.2026 02:47;0.0.1+83;Bugfix;ldap_sync_cron.py: TypeError offset-naive vs offset-aware datetime behoben (last_dt.replace(tzinfo=timezone.utc))
14.02.2026 02:39;0.0.1+82;Docs;Security-Fixes abgeschlossen: Issue-Tracker in security-issues.md vollstaendig aktualisiert (17 Fix-Gruppen = 22 Issues auf Done), alle Funktionstests bestanden
14.02.2026 02:31;0.0.1+81;Security;Phase 4: Systemd-Haertung (7 Service-Dateien: NoNewPrivileges, ProtectSystem, PrivateTmp etc.), Security-Logging (logging_config.py, RotatingFileHandler /var/log/tareas.log, 25 Events in api_auth/api_admin/api_tls/api_onlyoffice/api_nextcloud/api_mail/api_ldap/api_app)
14.02.2026 02:23;0.0.1+80;Security;Phase 3.3: Fernet-Verschluesselung fuer Credentials at-rest. crypto_utils.py (encrypt/decrypt/migrate), config_utils.py (verschluesselt speichern, entschluesselt lesen), decrypt() in webdav/mail_service/ldap_sync/api_auth/api_ldap/api_onlyoffice, Migration beim Startup
14.02.2026 02:20;0.0.1+79;Security;Phase 3.1: file_browser.js - Alle inline onclick/ondblclick/oncontextmenu Handler durch data-Attribute + Event-Delegation ersetzt, globale window._fb-Referenz entfernt
14.02.2026 02:15;0.0.1+78;Security;Phase 2: SQL-Identifier Whitelist (db_utils/config_utils), generische Error-Details (api_nextcloud/api_onlyoffice/api_ldap), Rate-Limit verallgemeinert + Passwort-Aenderung (api_auth), Upload-Groessenlimit 500MB (api_nextcloud), Secret-Key Dateiberechtigungen 0600 (auth.py)
14.02.2026 02:11;0.0.1+77;Security;Phase 1: escapeAttr in onclick-Handlern (expandable_table/tab_aufgaben), sanitizeHtml-Pflicht im WYSIWYG-Editor, Auth-Depends auf /api/users/list + /api/areas, localStorage-Whitelist, Logout-Cookie client-seitig loeschen, parseInt fuer IDs in renderActions
14.02.2026 00:17;0.0.1+76;Refactoring;Sprint 2-4: config_utils.py (get_masked_config, resolve_masked_password), user_utils.py (get_display_name), JS-Helpers (loadConfigFromAPI/saveConfigToAPI/deleteConfigFromAPI, createModal/closeModal, initTabGeneric). 15 Dateien refactored, ~170 Zeilen netto reduziert
14.02.2026 00:04;0.0.1+75;Refactoring;db_utils.py: Zentrale DB-Context-Manager (db_query/db_transaction) + upsert_singleton_config + build_update_query. Alle 10 api_*.py refactored: try/finally durch with-Bloecke ersetzt, get_db-Import entfernt
13.02.2026 23:57;0.0.1+74;Refactoring;CSS-Duplikate konsolidiert: Login-CSS in login.css extrahiert, Spinner-Duplikate aus expandable_table.css/editor.html entfernt, Shadow Custom Properties (--shadow-focus/sm/md/lg), escapeHtml()-Duplikate in ExpandableTable/WidgetDashboard delegieren an globale Funktion
13.02.2026 23:39;0.0.1+72;Bugfix;datetime.utcnow() durch datetime.now(timezone.utc) ersetzt - 8 Stellen in auth.py, api_onlyoffice.py, ldap_sync.py, ldap_sync_cron.py (deprecated seit Python 3.12)
13.02.2026 23:39;0.0.1+71;Security;Security-Header: CSP (script/style 'self' 'unsafe-inline'), X-Frame-Options DENY, X-Content-Type-Options nosniff, Referrer-Policy. Editor-Seite mit lockerer CSP fuer OnlyOffice
13.02.2026 23:31;0.0.1+70;Security;DOM-Selector-Injection via rowId behoben: escapeAttr() fuer HTML-Attribute, CSS.escape() fuer querySelector in ExpandableTable
13.02.2026 23:29;0.0.1+69;Refactoring;escapeAttr() zentralisiert in app_core.js - 4 Duplikate aus tab_aufgaben.js, tab_mail.js, tab_ldap.js, tab_benutzer.js entfernt
13.02.2026 23:26;0.0.1+68;Security;CSRF-Protection: Middleware prueft X-Requested-With Header bei mutierenden API-Requests. Globaler fetch-Wrapper setzt Header automatisch. WOPI-Endpunkte ausgenommen
13.02.2026 23:19;0.0.1+67;Feature;Konfigurierbare Server-Adresse: Admin-Tab "Allgemein" mit Server-Adresse-Feld, Protokoll aus TLS-Config abgeleitet, {app_url}-Platzhalter in allen Mail-Templates, hardcodierte IP in Einladungsmail ersetzt
13.02.2026 23:19;0.0.1+67;Aenderung;DB-Schema: app_config Tabelle. Neue Dateien: api_app.py (Config-API), tab_allgemein.js (Admin-Tab). Default-Templates mit {app_url} am Ende
13.02.2026 22:53;0.0.1+66;Security;Notes-Endpunkte: Fehlende Authentifizierung bei GET task_notes und subtask_notes ergaenzt (Depends get_current_user)
13.02.2026 22:49;0.0.1+65;Security;Rate-Limiting am Login (Fritz!Box-Style): Nach 3 Fehlversuchen 10s Sperre, +5s pro weiterem Fehlversuch. Frontend zeigt Countdown mit gesperrten Eingabefeldern
13.02.2026 22:44;0.0.1+64;Infra;Komplettes Logging auf /var/log/tareas.log umgestellt: Uvicorn-Access/Error + alle App-Module, log_config=None verhindert Uvicorn-Override
13.02.2026 22:37;0.0.1+63;Security;User-Enumeration beim Login behoben: Einheitliche Fehlermeldung, Details ins Log (/var/log/tareas.log). Globales Logging eingerichtet (Syslog-Format, RotatingFileHandler)
13.02.2026 18:43;0.0.1+62;Bugfix;Netzplan-Positionen: Nicht-Ersteller mit Edit-Rechten koennen jetzt speichern (falsche Tabelle task_members -> project_members)
13.02.2026 15:49;0.0.1+61;Security;SQL Injection in onlyoffice-setup.sh behoben: Single-Quote-Escaping fuer sqlite3-Variablen
13.02.2026 14:04;0.0.1+60;Security;ONLYOFFICE JWT Secret nicht mehr auf Admin-Seite sichtbar: API liefert kein Secret mehr aus, Formular leer bei Aenderung, Status zeigt nur ob konfiguriert
13.02.2026 13:58;0.0.1+59;Security;XSS in msgbox() behoben: message-Parameter wird jetzt mit escapeHtml() sanitisiert
13.02.2026 13:56;0.0.1+58;Security;Path Traversal bei Nextcloud-Dateioperationen behoben: Zentrale Pfadvalidierung (posixpath.normpath + doppelte URL-Dekodierung) fuer alle 7 Endpoints
13.02.2026 13:48;0.0.1+57;Security;TLS-Zertifikatsvalidierung fuer Integrationen: Pro Integration (Nextcloud, ONLYOFFICE, SMTP, LDAP) konfigurierbar via Admin-UI, prueft gegen System-Trust-Store
13.02.2026 13:26;0.0.1+56;Security;TLS-Support als optionales Feature: Admin-UI Tab, Zertifikats-Validierung, Uvicorn SSL-Startup, Cookie Secure-Flag bei aktivem TLS
13.02.2026 13:05;0.0.1+55;Security;Task-API Autorisierung: Update nur fuer Ersteller/Team/Zugewiesene (Zugewiesene nur Status), Delete nur fuer Ersteller/Admin
13.02.2026 12:57;0.0.1+54;Security;Stored XSS behoben: HTML-Sanitizer (Tag-Allowlist) fuer WYSIWYG-Beschreibungen und Notizen - sanitize bei Anzeige, Speichern und Editor-Init (14 Stellen)
13.02.2026 10:06;0.0.1+53;Security;ONLYOFFICE Callback: SSRF unterbunden - JWT-Verifizierung (nur signierte Requests akzeptiert) + URL-Validierung (nur http/https, Cloud-Metadata-IPs blockiert)
12.02.2026 04:00;0.0.1+52;Feature;Netzplan: Projektknoten (Node 0) als Vorgaenger waehlbar - im Netzplan per Linking und in der Teilaufgaben-Liste per Dropdown (nur wenn keine Vorgaenger vorhanden)
12.02.2026 03:00;0.0.1+51;Bugfix;Inline-Editing: Teilaufgaben-Name und andere Felder werden nach Silent-Save sofort in der Tabellenzeile aktualisiert (kein Neuladen noetig)
12.02.2026 02:15;0.0.1+50;Bugfix;Mail: TLS-Zertifikatsvalidierung deaktiviert (CERT_NONE) fuer selbst-signierte Zertifikate
12.02.2026 02:00;0.0.1+49;Feature;Mail-Integration: SMTP-Konfiguration, Testmail, Mail-Vorlagen (Admin-Tab), Benutzer-Benachrichtigungs-Einstellungen (Modal), Event-getriggerter Sofort-Versand bei Zuweisung/Statusaenderung, taeglicher Scheduler fuer Faelligkeits-Mails, Einladungsmail an Benutzer
12.02.2026 02:00;0.0.1+49;Aenderung;DB-Schema: mail_config, mail_templates, user_mail_preferences Tabellen mit Default-Templates (6 Event-Typen)
12.02.2026 02:00;0.0.1+49;Aenderung;Neue Dateien: mail_service.py (SMTP-Versand, Template-Rendering), api_mail.py (Admin+User API), tab_mail.js (Admin-UI), mail_scheduler.py + systemd-Dateien
12.02.2026 00:30;0.0.1+48;Feature;Netzplan: Projektknoten (Node 0) als Diamond-Shape mit Projektnamen, persistente Position, Toolbar-Buttons im Header, visuelles Dimmen im Linking-Modus
11.02.2026 23:55;0.0.1+47;Feature;Netzplan: Persistente Node-Positionen (DB-gespeichert), Auto-Anordnung-Button, Undo/Redo fuer Positionsaenderungen und Abhaengigkeiten (Ctrl+Z/Y)
11.02.2026 22:30;0.0.1+46;Feature;Dateiablage: Bilddateien (jpg, png, gif, webp, svg, etc.) oeffnen bei Doppelklick im neuen Browser-Tab statt Download, Kontextmenue-Eintrag Anzeigen
11.02.2026 22:00;0.0.1+45;Aenderung;ONLYOFFICE: Dateien aus Dateiablage oeffnen sich jetzt in neuem Browser-Tab statt Fullscreen-Overlay, eigene Editor-Seite mit Theme-Support
11.02.2026 21:30;0.0.1+44;Bugfix;Inline-Editing: Metadaten-Aenderungen in Tabellenzeilen (Name, Status, Prioritaet, Zuweisung, Deadline) werden jetzt korrekt uebernommen - optimistisches lokales Daten-Update und Zellen-Re-Rendering beim Zuklappen
11.02.2026 17:00;0.0.1+43;Feature;ONLYOFFICE-Integration: Dokumente (docx, xlsx, pptx, etc.) direkt im Browser bearbeiten via WOPI-Host, Fullscreen-Editor-Overlay, Admin-Tab fuer Konfiguration, automatisches Docker-Setup ueber apt-Pipeline
11.02.2026 14:00;0.0.1+42;Aenderung;Dateiablage: Baumansicht als Standard-Ansicht statt Grid
11.02.2026 13:00;0.0.1+41;Aenderung;Netzplan: Kanten als cubicBezier mit horizontalem Start/Ende (zwei Kreisboegen statt geschwungene Kurve)
11.02.2026 13:00;0.0.1+41;Aenderung;Netzplan: Knoten frei verschiebbar (eigene Positionsberechnung statt fixiertes hierarchisches Layout)
11.02.2026 12:00;0.0.1+40;Aenderung;Teilaufgaben-Berechtigungsmanagement entfernt: nur noch Projekt-Berechtigungen aus Teammitglieder-Dialog
11.02.2026 12:00;0.0.1+40;Aenderung;subtask_permissions Tabelle gedroppt, Schloss-Icons und Override-Dialog aus UI entfernt
11.02.2026 04:10;0.0.1+39;Feature;Inline-Editing: Metadaten-Felder direkt in Tabellenzeilen editierbar (Aufgaben und Teilaufgaben)
11.02.2026 04:10;0.0.1+39;Aenderung;Detail-Header bei Aufgaben und Teilaufgaben entfernt, Detail startet direkt mit Beschreibungs-Editor
11.02.2026 04:10;0.0.1+39;Aenderung;Vorgaenger bei Teilaufgaben: Direkt-Hinzufuegen per Dropdown ohne +-Button
11.02.2026 03:28;0.0.1+38;Aenderung;Projektstatus wird automatisch aus Teilaufgaben berechnet, Status-Dropdown bei Projekten entfernt
11.02.2026 03:25;0.0.1+37;Aenderung;Loeschen-Button von Detailansicht in Tabellenzeile verlegt (Aufgaben/Projekte)
11.02.2026 03:15;0.0.1+36;Feature;Kategorie-Filter: Toggle-Buttons (Team/Zugewiesen/Vergeben/Eigene) im Aufgaben-Tab mit dynamischer Anzeige und Count-Badges
11.02.2026 02:30;0.0.1+35;Aenderung;Teilaufgaben-Status: Prozentbalken durch Offen/In Arbeit/Erledigt-Schema ersetzt (Tabelle und Detail)
11.02.2026 02:00;0.0.1+34;Feature;Auto-Save: Alle Aenderungen an Aufgaben, Projekten und Teilaufgaben werden sofort gespeichert
11.02.2026 02:00;0.0.1+34;Aenderung;Speichern-Button bei Aufgaben, Projekten und Teilaufgaben entfernt (Loeschen-Button bleibt)
11.02.2026 01:15;0.0.1+33;Feature;Dateiablage auch fuer Aufgaben (nicht nur Projekte) verfuegbar
11.02.2026 01:15;0.0.1+33;Feature;Projektstatus zeigt Teilaufgaben-Fortschritt (n/n) in der Aufgabentabelle
11.02.2026 01:15;0.0.1+33;Aenderung;Bestaetigung vor Aenderung einer bereits zugeordneten Dateiablage
11.02.2026 01:15;0.0.1+33;Aenderung;Texte von Projektverzeichnis auf Dateiablage vereinheitlicht
11.02.2026 00:30;0.0.1+32;Feature;Editor und Dateiablage synchron in der Hoehe vergroesserbar/verkleinerbar (Resize-Handle)
11.02.2026 00:30;0.0.1+32;Aenderung;Dateiablage-Titel entfernt, Header-Hoehen von Editor und Dateiablage angeglichen
11.02.2026 00:30;0.0.1+32;Kosmetik;Team-Button Groesse an Netzplan-Button angeglichen, Ober-/Unterkanten beider Spalten pixelgenau ausgerichtet
10.02.2026 23:45;0.0.1+31;Aenderung;Dateiablage neben WYSIWYG-Editor statt neben Teilaufgaben-Tabelle (Zwei-Spalten-Layout umpositioniert)
10.02.2026 23:45;0.0.1+31;Feature;Projektverzeichnis-Dialog: Browse-Funktion zum Navigieren in tiefere Unterverzeichnisse mit Breadcrumb
10.02.2026 23:45;0.0.1+31;Aenderung;Jedes Verzeichnis zeigt "Waehlen"-Button + Ordner-Klick navigiert hinein (statt direkter Auswahl)
10.02.2026 23:30;0.0.1+30;Aenderung;Projektverzeichnis: Dropdown im Header ersetzt durch Button "Projektverzeichnis" neben Netzplan/Team (nur fuer Ersteller)
10.02.2026 23:30;0.0.1+30;Feature;Projektverzeichnis-Dialog: Klickbare Verzeichnisliste mit Ordner-Icons, Auswahl-Haken, Zuordnung-Entfernen-Button
10.02.2026 23:50;0.0.1+29;Feature;Nextcloud-Dateiablage: WebDAV-Proxy-Integration fuer Projektverzeichnisse (browsen, oeffnen, hochladen, loeschen, ordner erstellen, umbenennen)
10.02.2026 23:50;0.0.1+29;Feature;Admin-UI: Neuer Tab "Nextcloud" mit Verbindungskonfiguration (Server-URL, Benutzer, Passwort, Wurzelverzeichnis) und Verbindungstest
10.02.2026 23:50;0.0.1+29;Feature;Dateiablage-Ansichten: Umschaltbare Baumansicht (Explorer-Stil, Lazy-Load) und Icon-Grid (Doppelklick-Navigation)
10.02.2026 23:50;0.0.1+29;Feature;Breadcrumb-Navigation, Drag-and-Drop Upload, Upload-Fortschritt, Rechtsklick-Kontextmenue (Herunterladen, In Nextcloud oeffnen, Umbenennen, Loeschen)
10.02.2026 23:50;0.0.1+29;Feature;Verzeichniszuordnung: Projekt-Ersteller waehlt Unterverzeichnis aus Nextcloud-Wurzelverzeichnis per Dropdown
10.02.2026 23:50;0.0.1+29;Feature;Zwei-Spalten-Layout im Projekt-Detail: Links Editor+Teilaufgaben, rechts Dateiablage (responsive: untereinander ab 900px)
10.02.2026 23:50;0.0.1+29;Feature;Datei-Icons: SVG-basiert nach Dateityp (Ordner, Bilder, PDF, Office, Text/Code, Archive, Video, Audio)
10.02.2026 23:50;0.0.1+29;Aenderung;DB-Schema: nextcloud_config Tabelle, nextcloud_path Spalte in tasks
10.02.2026 23:50;0.0.1+29;Aenderung;requirements.txt: httpx + python-multipart hinzugefuegt
10.02.2026 23:50;0.0.1+29;Aenderung;Neue Dateien: webdav.py (WebDAV-Modul), api_nextcloud.py (Admin+Datei-API), file_browser.js, file_browser.css, tab_nextcloud.js
10.02.2026 23:00;0.0.1+28;Feature;Netzplan: Interaktiver Projekt-Aufgabengraph als Fullscreen-Overlay (vis-network)
10.02.2026 23:00;0.0.1+28;Feature;Netzplan: Hierarchisches Layout (links-nach-rechts), Knoten nach Status farbcodiert (Offen/In Arbeit/Erledigt)
10.02.2026 23:00;0.0.1+28;Feature;Netzplan: Zoom, Pan, Tooltips (Name, Status, Deadline, Bereich), Fit-Button, ESC/X schliessen
10.02.2026 23:00;0.0.1+28;Feature;Netzplan: Theme-Kompatibilitaet (Farben passen sich bei Light/Dark-Wechsel live an)
10.02.2026 23:00;0.0.1+28;Aenderung;vis-network 9.1.9 als statische Bibliothek eingebunden (JS + CSS)
10.02.2026 21:45;0.0.1+27;Bugfix;LDAP-Login: Benutzersuche ab Domain-Root statt konfigurierter Search-Base (OU=Gruppen enthielt keine User-Objekte)
10.02.2026 21:30;0.0.1+26;Bugfix;LDAP-Login: Detaillierte Fehlermeldungen statt generisches "Ungueltige Anmeldedaten" (zeigt Search-Base, DN, LDAP-Fehler)
10.02.2026 21:15;0.0.1+25;Bugfix;LDAP: Blockierende LDAP-Aufrufe via asyncio.to_thread() in Thread-Pool ausgelagert (behebt "Failed to fetch" bei langsamer DC-Verbindung)
10.02.2026 21:15;0.0.1+25;Bugfix;Admin-Benutzertabelle: showHeader + gridTemplate ergaenzt (Header fehlte, Spalten waren untereinander statt nebeneinander)
10.02.2026 20:45;0.0.1+24;Bugfix;LDAP: get_info=NONE statt ALL (Samba-DC Schema-Query nach Bind schlug fehl mit raise_exceptions)
10.02.2026 20:30;0.0.1+23;Bugfix;LDAP: Falsches Bind-Passwort wurde nicht erkannt (raise_exceptions=True fuer ldap3 Connection)
10.02.2026 20:30;0.0.1+23;Bugfix;APT-Repo: GPG-Key im binaeren Format exportieren (statt ASCII-Armor)
10.02.2026 20:00;0.0.1+21;Feature;APT-Packaging: .deb-Paket-Bau mit packaging/DEBIAN (control, postinst, prerm, postrm)
10.02.2026 20:00;0.0.1+21;Feature;publish.sh: Build + Upload + Repo-Index-Aktualisierung + GPG-Signierung auf Repo-Server
10.02.2026 20:00;0.0.1+21;Feature;install.sh: Bootstrap-Script fuer Einzeiler-Installation (curl | bash)
10.02.2026 20:00;0.0.1+21;Feature;APT-Repository eingerichtet (Apache-Alias, Verzeichnisstruktur, GPG-Key)
10.02.2026 20:00;0.0.1+21;Aenderung;Angepasste Service-Dateien fuer Paket (User tareas, /opt/tareas Pfade, venv)
10.02.2026 17:15;0.0.1+20;Bugfix;LDAP: StartTLS auf Port 389 wenn SSL nicht aktiviert (behebt strongerAuthRequired bei Samba-DCs)
10.02.2026 17:15;0.0.1+20;Bugfix;LDAP: TLS-Zertifikatsvalidierung deaktiviert (CERT_NONE) fuer selbst-signierte/abgelaufene Zertifikate
10.02.2026 17:00;0.0.1+19;Feature;LDAP-Integration: Active-Directory-Anbindung, AD-Benutzer automatisch synchronisieren
10.02.2026 17:00;0.0.1+19;Feature;LDAP-Login: AD-Benutzer melden sich mit AD-Zugangsdaten an (LDAP-Bind), lokale Benutzer als Fallback
10.02.2026 17:00;0.0.1+19;Feature;Admin-UI: Neuer Tab "LDAP" mit Server-Konfiguration, Verbindungstest, Gruppenauswahl
10.02.2026 17:00;0.0.1+19;Feature;Admin-UI: Benutzerverwaltung zeigt Quelle (Lokal/LDAP) und Status (Aktiv/Deaktiviert)
10.02.2026 17:00;0.0.1+19;Feature;LDAP-Sync: Manuell via Button + periodisch via systemd-Timer (15 Min.)
10.02.2026 17:00;0.0.1+19;Aenderung;DB-Schema: auth_source, ldap_dn, is_active in users; neue Tabelle ldap_config
10.02.2026 17:00;0.0.1+19;Aenderung;LDAP-Benutzer in Benutzerverwaltung: Felder readonly (ausser Admin-Checkbox), Loeschen nur wenn deaktiviert
10.02.2026 17:00;0.0.1+19;Aenderung;requirements.txt: ldap3 hinzugefuegt
10.02.2026 16:15;0.0.1+18;Bugfix;Subtask-View Save: Negative ID als strukturelles Signal (robust gegen Browser-Cache mit altem JS)
10.02.2026 15:00;0.0.1+17;Bugfix;Zugewiesene Teilaufgaben erscheinen jetzt in der Aufgabenliste des Zugewiesenen (als Pseudo-Aufgabe mit Projekt-Info)
10.02.2026 15:00;0.0.1+17;Bugfix;Readonly-Logik korrigiert: Deadline+Prioritaet bleiben fuer Ersteller nach Zuweisung editierbar
10.02.2026 15:00;0.0.1+17;Bugfix;Name+Beschreibung werden nach Zuweisung fuer den Ersteller readonly
10.02.2026 15:00;0.0.1+17;Aenderung;Subtask-Detail im Projekt: Creator sieht bei zugewiesener Subtask Beschreibung readonly + Zugewiesenen-Notizen
10.02.2026 15:00;0.0.1+17;Aenderung;saveSubTask() sendet nur editierbare Felder (respektiert readonly-Status)
10.02.2026 12:00;0.0.1+16;Feature;Teams: Projekte koennen Teammitglieder mit konfigurierbaren Berechtigungen (Lesen/Bearbeiten/Erstellen) haben
10.02.2026 12:00;0.0.1+16;Feature;Granulare Subtask-Berechtigungen: Ueberschreibungen pro Teilaufgabe fuer einzelne Teammitglieder
10.02.2026 12:00;0.0.1+16;Feature;Team-Dialog: Overlay zum Verwalten von Teammitgliedern (hinzufuegen, Berechtigungen aendern, entfernen)
10.02.2026 12:00;0.0.1+16;Feature;Subtask-Berechtigungs-Dialog: Schloss-Icon pro Teilaufgabe, Override setzen/entfernen
10.02.2026 12:00;0.0.1+16;Feature;Sichtbarkeit: Teammitglieder sehen Projekte in der Aufgabenliste, nicht-lesbare Teilaufgaben werden gefiltert
10.02.2026 12:00;0.0.1+16;Aenderung;DB-Schema: project_members + subtask_permissions Tabellen
10.02.2026 12:00;0.0.1+16;Aenderung;API: Neuer Router api_teams.py (Team-CRUD + Subtask-Permissions), Berechtigungschecks in bestehenden Subtask-Endpoints
10.02.2026 12:00;0.0.1+16;Aenderung;Frontend: Readonly-Erzwingung basierend auf Backend-Permissions, Team-Button nur fuer Ersteller
10.02.2026 09:00;0.0.1+15;Feature;Aufgabenzuweisung: Aufgaben/Teilaufgaben an andere Benutzer zuweisen
10.02.2026 09:00;0.0.1+15;Feature;Sichtbarkeitsfilter: Benutzer sehen nur eigene/zugewiesene Aufgaben (+ Altdaten ohne Ersteller)
10.02.2026 09:00;0.0.1+15;Feature;Notizen-System: Zugewiesene koennen Notizen hinterlassen, Ersteller sehen diese readonly
10.02.2026 09:00;0.0.1+15;Feature;Bearbeitbarkeitsregeln: Ersteller vs. Zugewiesener (readonly-Felder je nach Rolle)
10.02.2026 09:00;0.0.1+15;Aenderung;DB-Schema: assigned_to fuer tasks/sub_tasks, task_notes/sub_task_notes Tabellen
10.02.2026 09:00;0.0.1+15;Aenderung;Tabelle: Neue Spalten "Von" und "Zugewiesen an" in Aufgaben- und Teilaufgabenliste
10.02.2026 09:00;0.0.1+15;Aenderung;API: /api/users/list, /api/tasks/{id}/notes (GET/PUT), Sichtbarkeitsfilter in GET /api/tasks
10.02.2026 01:05;0.0.1+14;Bugfix;Admin-Benutzertabelle: Config-Feldnamen an ExpandableTable-Konvention angepasst (apiEndpoint, defaultSort, wildcardFields)
10.02.2026 01:00;0.0.1+13;Bugfix;Login-Redirect: 302 zu /login statt inline login.html (Browser-Cache-Problem behoben)
10.02.2026 00:55;0.0.1+12;Bugfix;Request Type-Hints in HTML-Routen (app.py, admin_app.py) - FastAPI interpretierte request ohne Hint als Query-Parameter
10.02.2026 00:45;0.0.1+11;Feature;Authentifizierung: Login/Logout mit Cookie-Sessions (bcrypt + itsdangerous), 401-Redirect
10.02.2026 00:45;0.0.1+11;Feature;Admin-App auf Port 8505: Benutzerverwaltung (CRUD) mit ExpandableTable
10.02.2026 00:45;0.0.1+11;Feature;Passwort-Aendern-Modal im Settings-Dropdown
10.02.2026 00:45;0.0.1+11;Feature;Username-Anzeige im Header, Abmelde-Button
10.02.2026 00:45;0.0.1+11;Aenderung;DB-Schema: users + sessions Tabellen, created_by-Spalte in tasks
10.02.2026 00:45;0.0.1+11;Aenderung;Alle API-Endpunkte (ausser Login) sind jetzt authentifiziert
09.02.2026 23:35;0.0.1+10;Feature;Globale msgbox()-Funktion in app_core.js (buttons: ok/cancel-yes, types: alert/confirm/warning)
09.02.2026 23:35;0.0.1+10;Aenderung;showConfirmDialog() durch msgbox() ersetzt, Bestaetigung beim Loeschen von Teilaufgaben hinzugefuegt
09.02.2026 23:21;0.0.1+9;Bugfix;Scroll-Position bleibt beim Zuklappen von Detail-Bereichen erhalten (Aufgaben + Teilaufgaben)
09.02.2026 23:21;0.0.1+9;Aenderung;WYSIWYG-Toolbar: Dropdowns auf feste Breite, einzeilig, nicht mehr von Subtask-Table-Styles ueberschrieben
09.02.2026 22:45;0.0.1+8;Aenderung;Mehr Kontrast fuer Teilaufgaben-Detailbereich (Light/Dark)
09.02.2026 22:41;0.0.1+7;Aenderung;Detail-Hintergruende: Aufgaben-Detail und Teilaufgaben-Detail mit unterschiedlichen Farben fuer besseren Kontrast
09.02.2026 22:38;0.0.1+6;Bugfix;Changelog
09.02.2026 22:36;0.0.1+5;Bugfix;Changelog
09.02.2026 22:34;0.0.1+4;Bugfix;Changelog
09.02.2026 22:30;0.0.1+3;Bugfix;Expanded-State bleibt nach Vorgaenger hinzufuegen/entfernen erhalten
09.02.2026 22:30;0.0.1+3;Aenderung;Browser-confirm() durch eigenes Overlay-Modal ersetzt (Confirm-Dialog)
09.02.2026 22:30;0.0.1+3;Aenderung;Vorgaenger-Chips als 4-Spalten-Grid inline positioniert
09.02.2026 22:30;0.0.1+3;Aenderung;Vorgaenger-Dropdown in Header-Zeile verschoben (zwischen Name und Bereich)
09.02.2026 21:24;0.0.1+2;Feature;WYSIWYG-Editor fuer Teilaufgaben-Beschreibungen (description-Spalte in sub_tasks)
09.02.2026 21:24;0.0.1+2;Feature;Aufklappbare Teilaufgaben: Text-Zeilen mit Detailbereich (gleiches Layout wie Hauptaufgaben)
09.02.2026 21:02;0.0.1+1;Aenderung;ExpandableTable: toggleAllRows(), client-seitige Select-Filter
09.02.2026 21:02;0.0.1+1;Aenderung;Tab "Dashboard" entfernt, ersetzt durch "Aufgaben" als Startseite
09.02.2026 21:02;0.0.1+1;Feature;Modal-Dialog fuer neue Aufgaben
09.02.2026 21:02;0.0.1+1;Feature;Control-Bar: Typ-/Status-Filter, Volltext-Suche, Alle auf-/zuklappen
09.02.2026 21:02;0.0.1+1;Feature;Vorgaenger-System: Abhaengigkeiten zwischen Teilaufgaben (Dropdown + Chips-UI)
09.02.2026 21:02;0.0.1+1;Feature;Positionsnummern fuer Teilaufgaben (auto-increment pro Projekt)
09.02.2026 21:02;0.0.1+1;Feature;Bereiche-Verwaltung mit +-Button (inline neue Bereiche anlegen)
09.02.2026 21:02;0.0.1+1;Feature;Typ "Projekt" mit Nested SubTask-Tabelle (inline-editierbar)
09.02.2026 21:02;0.0.1+1;Feature;WYSIWYG-Editor (contentEditable) fuer Aufgabenbeschreibungen
09.02.2026 21:02;0.0.1+1;Feature;DB-Schema (tasks, sub_tasks, areas, sub_task_dependencies), CRUD-API, Aufgaben-Tab mit ExpandableTable
