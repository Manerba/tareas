# Security Fixes - Master-Session TODO

**Erstellt:** 2026-02-14
**Kontext:** Ergebnisse des Security Code Reviews (docs/security-issues.md + docs/security-issues-fixes.md)
**Arbeitsweise:** Master-Session beauftragt Agenten mit Implementierung und Tests.

---

## Allgemeine Regeln

1. **Vor jedem Fix:** Agent prueft ob der Fix bestehende Funktionalitaet bricht (Abhaengigkeiten, Aufrufer, Seiteneffekte)
2. **Nach jedem Fix:** Kurzer Smoke-Test (Server starten, Seite laden, betroffene Funktion aufrufen)
3. **Commit:** Nach jeder abgeschlossenen Fix-Gruppe mit `./git-commit.sh` committen
4. **VOR jedem Push:** Changelog in `docs/changelog.md` aktualisieren
5. **Referenz:** Detaillierte Anweisungen in `docs/security-issues-fixes.md`
6. **Tracker:** Fix-Status in `docs/security-issues.md` (Issue-Tracker Tabelle, Spalte "Fix") aktualisieren

---

## Phase 1: Einfache Fixes (keine Architektur-Aenderungen)

### Fix 1.1: Explizite Auth-Depends [Issue #1-3]
- **Datei:** `dashboard/api_tasks.py`
- **Aktion:** `user=Depends(get_current_user)` als Parameter zu `get_users_list()`, `get_areas()`, `create_area()` hinzufuegen
- **Pruefung vorher:** Sicherstellen, dass `get_current_user` importiert ist (sollte ueber Router-Dependency schon geladen sein)
- **Risiko:** Keins - Router-Level Auth ist bereits aktiv

### Fix 1.2: escapeAttr() in onclick-Handlern [Issue #4-5]
- **Dateien:** `dashboard/static/js/expandable_table.js`, `dashboard/static/js/tab_aufgaben.js`
- **Aktion:** Alle dynamischen String-Werte in onclick/onchange-Attributen mit `escapeAttr()` escapen
- **Pruefung vorher:** Sicherstellen, dass `escapeAttr()` global verfuegbar ist (in helpers.js definiert)
- **Risiko:** Gering - Werte sind hardcoded, Escaping aendert sie nicht

### Fix 1.3: parseInt + escapeAttr in Render-Funktionen [Issue #10]
- **Datei:** `dashboard/static/js/expandable_table.js`
- **Aktion:** `renderActions()`: IDs mit `parseInt()` absichern. `renderToggle()`: `escapeHtml` durch `escapeAttr` ersetzen
- **Pruefung vorher:** Sicherstellen dass `escapeAttr()` als Methode oder globale Funktion verfuegbar ist
- **Risiko:** Keins - Integer bleiben Integer, escapeAttr ist strenger als escapeHtml

### Fix 1.4: WYSIWYG sanitizeHtml Pflicht [Issue #9]
- **Datei:** `dashboard/static/js/wysiwyg_editor.js`
- **Aktion:** Fallback-Check `typeof sanitizeHtml === 'function'` entfernen, direkt `sanitizeHtml()` aufrufen, bei Nicht-Verfuegbarkeit Default-Content anzeigen
- **Pruefung vorher:** Script-Ladereihenfolge in `index.html` pruefen (helpers.js mit sanitizeHtml muss VOR wysiwyg_editor.js geladen werden)
- **Risiko:** Gering - sanitizeHtml ist immer verfuegbar

### Fix 1.5: localStorage Whitelist [Issue #19]
- **Datei:** `dashboard/static/js/app_core.js`
- **Aktion:** In `initTheme()`: Theme-Wert gegen `['light', 'dark']` validieren
- **Risiko:** Keins - 1 Zeile

### Fix 1.6: Cookie client-seitig loeschen bei Logout [Issue #35]
- **Datei:** `dashboard/static/js/app_core.js`
- **Aktion:** In `logout()`: Cookie vor dem Redirect loeschen
- **Pruefung vorher:** Cookie-Name (`tareas_session`) und Path (`/`) aus `auth.py` verifizieren
- **Risiko:** Keins

### -> Commit nach Phase 1: `./git-commit.sh "Security: escapeAttr, sanitizeHtml, Auth-Depends, localStorage-Validierung, Logout-Cookie"`

---

## Phase 2: Backend-Hardening

### Fix 2.1: SQL-Identifier Whitelist [Issue #6-8]
- **Dateien:** `dashboard/db_utils.py`, `dashboard/config_utils.py`
- **Aktion:** ALLOWED_TABLES und ALLOWED_FIELDS Konstanten definieren, Validierung vor SQL-Ausfuehrung
- **Pruefung vorher:** ALLE Aufrufer von `get_masked_config()`, `resolve_masked_password()`, `upsert_singleton_config()`, `build_update_query()` identifizieren und deren Tabellen-/Feldnamen sammeln
- **Risiko:** Wenn ein Tabellenname fehlt -> ValueError bei laufendem Betrieb. Alle Aufrufer sorgfaeltig pruefen!

### Fix 2.2: Generische Error-Details [Issue #20+33]
- **Dateien:** `dashboard/api_nextcloud.py`, `dashboard/api_onlyoffice.py`, `dashboard/api_ldap.py`
- **Aktion:** `detail=str(e)` durch generische Meldungen ersetzen, Exceptions ins Log schreiben
- **Pruefung vorher:** Pruefen ob Frontend-Code auf spezifische Fehlermeldungen reagiert (z.B. String-Matching in JS)
- **Risiko:** Gering - Frontend zeigt nur generische Meldung statt Detail

### Fix 2.3: Rate-Limit verallgemeinern [Issue #23]
- **Datei:** `dashboard/api_auth.py`
- **Aktion:** Rate-Limit-Funktionen auf Key-basiert umbauen, auf Passwort-Aenderung anwenden
- **Pruefung vorher:** Bestehende Rate-Limit-Logik verstehen, alle Aufrufer pruefen
- **Risiko:** Mittel - Login-Rate-Limiting darf nicht brechen. Bestehende Tests (falls vorhanden) laufen lassen

### Fix 2.4: Upload-Groessenlimit [Issue #16]
- **Datei:** `dashboard/api_nextcloud.py`
- **Aktion:** MAX_UPLOAD_SIZE Konstante, Pruefung vor und nach file.read()
- **Pruefung vorher:** Pruefen ob es andere Upload-Endpunkte gibt
- **Risiko:** Keins - neuer Check, bestehende Uploads unter 500MB funktionieren weiter

### Fix 2.5: Secret-Key Dateiberechtigungen [Issue #17]
- **Datei:** `dashboard/auth.py`
- **Aktion:** `os.open()` mit 0o600 statt `Path.write_text()`, Verzeichnis mit 0o700
- **Pruefung vorher:** Bestehende secret.key Datei wird nicht ueberschrieben, nur neue Dateien betroffen
- **Risiko:** Gering - bestehende Keys bleiben lesbar, neue werden restriktiver erstellt

### -> Commit nach Phase 2: `./git-commit.sh "Security: SQL-Whitelist, generische Errors, Rate-Limit, Upload-Limit, Secret-Key Permissions"`

---

## Phase 3: Groessere Aenderungen

### Fix 3.1: File-Browser addEventListener Refactoring [Issue #11]
- **Datei:** `dashboard/static/js/file_browser.js`
- **Aktion:** Inline-onclick durch data-Attribute + Event-Delegation ersetzen, globale Referenz entfernen
- **Pruefung vorher:** Alle onclick/ondblclick/oncontextmenu Handler im File-Browser auflisten, sicherstellen dass Event-Delegation alle Faelle abdeckt
- **Risiko:** Mittel - umfangreiches Refactoring, alle Interaktionen (Klick, Doppelklick, Rechtsklick, Breadcrumb-Navigation, Tree-Expand) muessen getestet werden
- **Test:** Datei-Browser oeffnen, Ordner navigieren, Datei hochladen, Ordner erstellen, Kontextmenue testen

### Fix 3.2: Dynamische CSP fuer Editor [Issue #14]
- **Datei:** `dashboard/app.py`
- **Aktion:** OnlyOffice-URL aus DB laden, CSP dynamisch bauen, URL cachen
- **Pruefung vorher:** Pruefen wie OnlyOffice-Config geladen wird, ob DB-Zugriff in Middleware performant ist
- **Risiko:** Mittel - CSP zu restriktiv = Editor funktioniert nicht. Fallback auf restriktive Default-CSP wenn keine Config vorhanden
- **HINWEIS:** OnlyOffice ist auf Test-Server nicht installiert, daher nur Code-Review, kein Funktionstest

### Fix 3.3: Fernet-Verschluesselung fuer Credentials [Issue #18+29]
- **Dateien:** Neue Datei `dashboard/crypto_utils.py`, `dashboard/config_utils.py`, `requirements.txt`
- **Aktion:** encrypt/decrypt Funktionen, Key-Ableitung vom Secret Key, Migration bestehender Klartext-Werte
- **Pruefung vorher:** Secret Key Stabilitaet pruefen (wird er je rotiert?), alle Stellen wo Credentials gelesen/geschrieben werden identifizieren
- **Risiko:** Hoch - falsche Implementierung = alle Credentials unbrauchbar. Migration muss idempotent sein. Testen mit Nextcloud-, LDAP-, Mail-Config
- **Test:** Config speichern -> DB pruefen (verschluesselt) -> Config laden (entschluesselt + maskiert) -> Config nochmal speichern mit "********" (Passwort bleibt erhalten)

### Fix 3.4: Minimum-Versionen in requirements.txt [Issue #27]
- **Datei:** `requirements.txt`
- **Aktion:** `pip freeze` ausfuehren, Minimum-Versionen setzen, `cryptography` hinzufuegen
- **Pruefung vorher:** Installierte Versionen auf dem Server ermitteln
- **Risiko:** Keins

### -> Commit nach Phase 3: `./git-commit.sh "Security: File-Browser Refactoring, dynamische CSP, Credential-Verschluesselung, Dep-Versionen"`

---

## Phase 4: Infrastruktur

### Fix 4.1: Systemd-Haertung [Issue #28]
- **Dateien:** `packaging/services/tareas.service`, `packaging/services/tareas-admin.service`, `packaging/services/tareas-ldap-sync.service`
- **Aktion:** Haertungs-Direktiven hinzufuegen (NoNewPrivileges, ProtectSystem, PrivateTmp, etc.)
- **Pruefung vorher:** Pruefen welche Pfade Schreibzugriff brauchen (data/, /var/log/), ob der Service Netzwerk-Zugriff braucht (ja: HTTP)
- **Risiko:** Mittel - zu restriktive Einstellungen koennten den Service am Starten hindern. Auf dem Test-Server mit `systemctl restart tareas` testen
- **HINWEIS:** Service-Dateien werden erst beim naechsten Deployment aktiv

### Fix 4.2: Security-Logging [Issue #32]
- **Dateien:** `dashboard/app.py` oder neue `dashboard/logging_config.py`, diverse `api_*.py`
- **Aktion:** Security-Logger konfigurieren (/var/log/tareas.log), Log-Aufrufe in Admin-Endpunkten einfuegen
- **Pruefung vorher:** Bestehende Logging-Konfiguration verstehen, pruefen ob /var/log/ beschreibbar ist
- **Risiko:** Gering - zusaetzliches Logging, keine bestehende Logik betroffen
- **Test:** Login ausfuehren, Log-Datei pruefen

### -> Commit nach Phase 4: `./git-commit.sh "Security: Systemd-Haertung, Security-Logging"`

---

## Phase 5: Funktionstests

**WICHTIG:** OnlyOffice ist auf dem Test-Server NICHT installiert. Alle OnlyOffice-bezogenen Tests ueberspringen.

### Test 5.1: Auth & Session
- Login mit gueltigem User -> Erfolg
- Login mit falschem Passwort -> Fehler, Rate-Limit nach 3 Versuchen pruefen
- Zugriff auf /api/tasks ohne Cookie -> 401
- Zugriff auf /api/users/list ohne Cookie -> 401
- Zugriff auf /api/areas ohne Cookie -> 401
- Logout -> Cookie geloescht, Redirect auf /login
- Passwort aendern mit falschem altem PW -> Fehler
- Passwort aendern mit korrektem PW -> Erfolg
- Rate-Limit auf Passwort-Aenderung pruefen

### Test 5.2: Task-Management
- Task erstellen -> Erfolg
- Task bearbeiten -> Erfolg
- Task loeschen -> Erfolg
- SubTasks erstellen/bearbeiten/loeschen
- Task-Abhaengigkeiten hinzufuegen
- Bereiche erstellen (POST /api/areas)
- Benutzerliste abrufen (GET /api/users/list)
- Netzplan/Gantt anzeigen

### Test 5.3: File-Browser (Nextcloud)
- Dateien auflisten -> Grid- und Tree-Ansicht
- Ordner navigieren (Breadcrumb-Klicks)
- Ordner erstellen
- Datei hochladen (klein, < 1 MB)
- Datei hochladen (ueber Limit, falls konfiguriert -> 413)
- Kontextmenue (Rechtsklick auf Datei)
- Ansicht wechseln (Grid <-> Tree)

### Test 5.4: Admin-Bereich
- Benutzer erstellen -> Erfolg
- Benutzer loeschen -> Erfolg
- Nextcloud-Config speichern (mit maskiertem Passwort "********") -> altes PW bleibt erhalten
- LDAP-Config speichern (mit maskiertem Passwort) -> altes PW bleibt erhalten
- Mail-Config speichern -> Erfolg
- DB pruefen: Credentials sind verschluesselt (beginnen mit "gAAAAA")

### Test 5.5: Frontend-Rendering
- Theme-Wechsel (Light/Dark) -> funktioniert, bleibt nach Reload erhalten
- Tabellen-Sortierung -> Spalten-Header klickbar
- Kategorie-Filter in Aufgaben-Tab -> Buttons funktionieren
- ExpandableTable: Zeile aufklappen -> Detail sichtbar

### Test 5.6: Security-Spezifisch
- SQL-Injection Versuch in API (z.B. Task-Name mit SQL) -> wird escaped
- XSS Versuch in Task-Beschreibung (z.B. `<script>alert(1)</script>`) -> wird sanitized
- Path-Traversal Versuch (z.B. `../../etc/passwd` als Upload-Pfad) -> 400 Fehler
- Secret-Key Datei Berechtigungen pruefen (0600)
- Security-Log pruefen (/var/log/tareas.log existiert, enthaelt Eintraege)

### -> Commit nach Tests (nur wenn Fixes noetig): `./git-commit.sh "Security: Test-Fixes"`

---

## Abschluss

- [ ] Alle Fixes implementiert
- [ ] Alle Tests bestanden
- [ ] Issue-Tracker in `docs/security-issues.md` vollstaendig aktualisiert
- [ ] Changelog in `docs/changelog.md` aktualisiert
- [ ] Service auf Test-Server neu starten: `systemctl restart tareas`
