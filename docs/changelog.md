# Changelog

Zeitstempel;Version;Kategorie;Beschreibung
14.02.2026;0.0.1+84;Release;Open-Source-Vorbereitung: Hardcodierte IPs entfernt (app.py, admin_app.py, tab_tls.js, tab_onlyoffice.js, tab_allgemein.js, security-issues-fixes.md, changelog.md), docs/prompts.txt geloescht, Packaging-Maintainer auf GitHub aktualisiert, CLAUDE.md bereinigt, .gitignore erweitert, LICENSE (MIT) und README.md erstellt, publish.sh um --github/--github-init erweitert (prepare_clean_copy fuer bereinigte Kopie)
14.02.2026;0.0.1+83;Bugfix;ldap_sync_cron.py: TypeError offset-naive vs offset-aware datetime behoben (last_dt.replace(tzinfo=timezone.utc))
14.02.2026;0.0.1+82;Docs;Security-Fixes abgeschlossen: Issue-Tracker in security-issues.md vollstaendig aktualisiert (17 Fix-Gruppen = 22 Issues auf Done), alle Funktionstests bestanden
14.02.2026;0.0.1+81;Security;Phase 4: Systemd-Haertung (7 Service-Dateien: NoNewPrivileges, ProtectSystem, PrivateTmp etc.), Security-Logging (logging_config.py, RotatingFileHandler /var/log/tareas.log, 25 Events in api_auth/api_admin/api_tls/api_onlyoffice/api_nextcloud/api_mail/api_ldap/api_app)
14.02.2026;0.0.1+80;Security;Phase 3.3: Fernet-Verschluesselung fuer Credentials at-rest. crypto_utils.py (encrypt/decrypt/migrate), config_utils.py (verschluesselt speichern, entschluesselt lesen), decrypt() in webdav/mail_service/ldap_sync/api_auth/api_ldap/api_onlyoffice, Migration beim Startup
14.02.2026;0.0.1+79;Security;Phase 3.1: file_browser.js - Alle inline onclick/ondblclick/oncontextmenu Handler durch data-Attribute + Event-Delegation ersetzt, globale window._fb-Referenz entfernt
14.02.2026;0.0.1+78;Security;Phase 2: SQL-Identifier Whitelist (db_utils/config_utils), generische Error-Details (api_nextcloud/api_onlyoffice/api_ldap), Rate-Limit verallgemeinert + Passwort-Aenderung (api_auth), Upload-Groessenlimit 500MB (api_nextcloud), Secret-Key Dateiberechtigungen 0600 (auth.py)
14.02.2026;0.0.1+77;Security;Phase 1: escapeAttr in onclick-Handlern (expandable_table/tab_aufgaben), sanitizeHtml-Pflicht im WYSIWYG-Editor, Auth-Depends auf /api/users/list + /api/areas, localStorage-Whitelist, Logout-Cookie client-seitig loeschen, parseInt fuer IDs in renderActions
14.02.2026;0.0.1+76;Refactoring;Sprint 2-4: config_utils.py (get_masked_config, resolve_masked_password), user_utils.py (get_display_name), JS-Helpers (loadConfigFromAPI/saveConfigToAPI/deleteConfigFromAPI, createModal/closeModal, initTabGeneric). 15 Dateien refactored, ~170 Zeilen netto reduziert
14.02.2026;0.0.1+75;Refactoring;db_utils.py: Zentrale DB-Context-Manager (db_query/db_transaction) + upsert_singleton_config + build_update_query. Alle 10 api_*.py refactored: try/finally durch with-Bloecke ersetzt, get_db-Import entfernt
14.02.2026;0.0.1+74;Refactoring;CSS-Duplikate konsolidiert: Login-CSS in login.css extrahiert, Spinner-Duplikate aus expandable_table.css/editor.html entfernt, Shadow Custom Properties (--shadow-focus/sm/md/lg), escapeHtml()-Duplikate in ExpandableTable/WidgetDashboard delegieren an globale Funktion
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
