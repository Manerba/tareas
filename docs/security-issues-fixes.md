# Security Issues - Handlungsanweisungen

**Erstellt:** 2026-02-14

---

## Issue #1-3: Auth auf /api/users/list, /api/areas [CRITICAL -> False Positive]

**Status:** False Positive (Router-Level Auth vorhanden), aber explizite Auth wird hinzugefuegt.

**Anweisung:** `user=Depends(get_current_user)` als Parameter zu folgenden Endpunkten hinzufuegen:
- `GET /api/users/list` (api_tasks.py, Funktion `get_users_list`)
- `GET /api/areas` (api_tasks.py, Funktion `get_areas`)
- `POST /api/areas` (api_tasks.py, Funktion `create_area`)

---

## Issue #4-5: Unescapte Werte in onclick-Handlern [CRITICAL -> Low]

**Status:** Geringes reales Risiko (hardcoded Werte), aber Defence-in-Depth Fix.

**Anweisung:** Alle dynamischen String-Werte in `onclick`-Attributen mit `escapeAttr()` escapen:
- `expandable_table.js`, `renderHeader()`: `this.config.id` und `col.field` in onclick escapen
- `tab_aufgaben.js`, `renderCategoryFilters()`: `cat` in onclick escapen
- Alle weiteren onclick-Handler in `expandable_table.js` pruefen (renderPagination, renderRow) und escapen

---

## Issue #6-8: SQL-Identifier Injection in config_utils.py / db_utils.py [HIGH -> Low]

**Status:** Geringes reales Risiko (alle Aufrufer hardcoded), Whitelist-Validierung wird hinzugefuegt.

**Anweisung:**
- In `config_utils.py`: Konstante `ALLOWED_TABLES` und `ALLOWED_FIELDS` definieren mit allen gueltigen Werten (aus aktuellen Aufrufern ableitbar: `ldap_config`, `mail_config`, `nextcloud_config`, `onlyoffice_config`, `tls_config`, `tls_verify_config`, `app_config` / `password`, `bind_password`, `jwt_secret`)
- In `get_masked_config()` und `resolve_masked_password()`: vor SQL-Ausfuehrung `table` gegen `ALLOWED_TABLES` und `field` gegen `ALLOWED_FIELDS` validieren, bei Verletzung `ValueError` werfen
- In `db_utils.py`: `upsert_singleton_config()` und `build_update_query()` analog absichern - Tabellennamen und Spaltennamen vor Query-Bau gegen Whitelist pruefen
- Die Whitelist-Konstanten koennen zentral in `db_utils.py` definiert und von `config_utils.py` importiert werden

---

## Issue #9: WYSIWYG Fallback ohne sanitizeHtml [HIGH]

**Status:** Fix - Fallback entfernen.

**Anweisung:** In `wysiwyg_editor.js` (Konstruktor Zeile 33 und `setContent()` Zeile 102):
- Statt `typeof sanitizeHtml === 'function' ? sanitizeHtml(html) : html` direkt `sanitizeHtml(html)` aufrufen
- Wenn `sanitizeHtml` nicht verfuegbar ist, soll der Default-Content `<p><br></p>` angezeigt werden, NICHT der unsanitized HTML-String
- Kein try/catch-Fallback auf unsanitized Content

---

## Issue #10: Inkonsistentes Escaping in renderActions/renderToggle [HIGH -> Low]

**Status:** Low Risk, Defence-in-Depth Fix.

**Anweisung:** In `expandable_table.js`:
- `renderActions()`: `row.id` mit `parseInt(id)` absichern bevor es in onclick eingefuegt wird
- `renderToggle()`: `this.escapeHtml(codename)` durch `escapeAttr(codename)` ersetzen (Attribut-Kontext statt HTML-Kontext)

---

## Issue #11: File-Browser globale Referenzen + Inline-Handler [HIGH]

**Status:** Refactoring auf addEventListener.

**Anweisung:** In `file_browser.js`:
- Upload- und Ordner-Buttons (Zeile 41, 44): Inline-onclick entfernen, stattdessen nach dem Einfuegen des HTML per `addEventListener` binden (analog zum Toggle-Button Zeile 62)
- `renderBreadcrumb()`: onclick-Handler durch `data-path`-Attribut ersetzen, nach `innerHTML`-Zuweisung Event-Delegation auf dem Breadcrumb-Container registrieren
- `renderGrid()`: ondblclick/oncontextmenu durch `data-name`/`data-type`-Attribute ersetzen, Event-Delegation auf dem Grid-Container
- `renderTree()` / `_renderTreeLevel()`: onclick/ondblclick/oncontextmenu analog durch data-Attribute + Event-Delegation ersetzen
- Globale Referenz `window[_fb${taskId}]` entfernen, da nicht mehr benoetigt
- Alle Pfad-Werte in data-Attributen weiterhin mit `escapeAttr()` escapen

---

## Issue #12: IDOR in WOPI-Endpunkten [HIGH -> False Positive]

**Status:** False Positive. Token-basierte Auth ist korrekt (256 Bit Token, File-ID-Pruefung, Permission-Check bei Erstellung).

---

## Issue #14: Ueberpermissive CSP fuer Editor [HIGH]

**Status:** Fix - CSP dynamisch einschraenken.

**Anweisung:** In `app.py`, Middleware `security_headers`:
- Fuer `/editor`-Pfade die OnlyOffice-Server-URL aus der DB-Konfiguration (`onlyoffice_config.server_url`) laden
- Daraus den Origin extrahieren (Schema + Host + Port)
- In der CSP `https: http:` und `*` durch den konkreten OnlyOffice-Origin ersetzen (z.B. `script-src 'self' 'unsafe-inline' http://onlyoffice-host:8080`)
- Fallback: Wenn keine OnlyOffice-Config vorhanden, restriktive Default-CSP verwenden (`'self'` only)
- WICHTIG: Keine hardcoded IPs/Hosts - die URL muss aus der DB kommen, da Tareas auf verschiedenen Servern installiert werden kann
- Performance: OnlyOffice-URL cachen (nicht bei jedem Request DB abfragen), Cache invalidieren wenn Config geaendert wird

---

## Issue #15: Kein HTTPS-Enforcement / HSTS [HIGH -> Akzeptiert]

**Status:** By Design - kein Fix noetig.

**Begruendung:** HSTS ist bewusst nicht implementiert weil:
- TLS ist optional und in der Admin-UI umschaltbar (HSTS + TLS deaktivieren = App unerreichbar)
- OnlyOffice laeuft in Docker auf HTTP
- Nextcloud WebDAV kann HTTP oder HTTPS sein
- CSP fuer Editor erlaubt bereits HTTP-Quellen

---

## Issue #16: Fehlende Upload-Groessenlimits [HIGH]

**Status:** Fix - Groessenlimit einfuehren.

**Anweisung:** In `api_nextcloud.py`, Funktion `upload_task_file()`:
- Vor `file.read()` die Dateigroesse pruefen (via `Content-Length` Header oder `file.size`)
- Maximale Dateigroesse als Konstante definieren (z.B. `MAX_UPLOAD_SIZE = 500 * 1024 * 1024` = 500 MB)
- Bei Ueberschreitung HTTP 413 (Payload Too Large) zurueckgeben
- Sicherheitshalber auch nach dem Lesen die tatsaechliche Groesse von `content` pruefen (Content-Length kann gefaelscht sein)

---

## Issue #17: Secret-Key Datei world-readable [HIGH]

**Status:** Fix - Restriktive Dateiberechtigungen.

**Anweisung:** In `auth.py`, Funktion `get_secret_key()`:
- Verzeichnis mit `mode=0o700` erstellen (nur Owner)
- Datei mit `os.open(path, os.O_CREAT | os.O_WRONLY, 0o600)` erstellen statt `Path.write_text()`
- Bestehende Dateien: beim Lesen optional Berechtigungen pruefen und korrigieren (`os.chmod(path, 0o600)`)

---

## Issue #18 + #29: Credentials in Klartext in DB [MEDIUM]

**Status:** Fix - Fernet-Verschluesselung at-rest.

**Anweisung:**
- Neue Datei `dashboard/crypto_utils.py` mit `encrypt(value)` und `decrypt(value)` Funktionen (Fernet-basiert)
- Encryption-Key vom bestehenden `secret.key` ableiten (z.B. via HKDF oder SHA-256 Hash des Secret Keys als Fernet-Key)
- In `config_utils.py`: `get_masked_config()` entschluesselt Passwort-Felder nach dem Lesen, `resolve_masked_password()` verschluesselt neue Passwoerter vor dem Speichern
- Betroffene Felder: `nextcloud_config.password`, `ldap_config.bind_password`, `mail_config.password`, `onlyoffice_config.jwt_secret`
- Migration: beim App-Start pruefen ob Felder noch Klartext enthalten (z.B. Fernet-Token beginnt mit `gAAAAA`), falls ja automatisch verschluesseln
- `requirements.txt`: `cryptography` hinzufuegen
- NICHT den bestehenden Secret Key aendern, nur davon ableiten

---

## Issue #19: Unvalidierte localStorage-Werte [MEDIUM -> Info]

**Status:** Fix - 1-Zeilen-Whitelist.

**Anweisung:** In `app_core.js`, Funktion `initTheme()`:
- Nach dem Lesen aus localStorage den Wert gegen `['light', 'dark']` validieren, bei ungueltigem Wert auf `'light'` fallen

---

## Issue #20 + #33: Ungefilterte Error-Details an Client [MEDIUM + LOW]

**Status:** Fix - Backend-seitig generische Fehler senden.

**Anweisung:** In allen API-Dateien die `detail=str(e)` Stellen aendern:
- `api_nextcloud.py`: 8 Stellen (Zeilen 174, 176, 262, 282, 321, 348, 369, 392)
- `api_onlyoffice.py`: 3 Stellen (Zeilen 374, 419, 454)
- `api_ldap.py`: 1 Stelle (Zeile 137)
- Statt `detail=str(e)` eine generische Meldung verwenden (z.B. "Interner Fehler" oder "Vorgang fehlgeschlagen")
- Die tatsaechliche Exception per `logger.exception()` oder `logger.error()` ins Log schreiben
- Fuer `RuntimeError` (api_nextcloud.py:174) kann die Meldung beibehalten werden, da diese von uns kontrolliert ist (WebDAV-Fehlermeldungen)

---

## Issue #23: Kein Rate-Limiting auf Passwort-Aenderung [MEDIUM]

**Status:** Fix - Rate-Limiting verallgemeinern und auf Passwort-Aenderung anwenden.

**Anweisung:** In `api_auth.py`:
- Die bestehenden Rate-Limit-Funktionen (`_check_rate_limit`, `_record_fail`, `_reset_fails`) verallgemeinern: statt nur `_login_fails`-Dict einen Namespace/Key-Parameter akzeptieren (z.B. `_check_rate_limit(key)` statt `_check_rate_limit(ip)`)
- Bestehende Logik beibehalten: 3 Fehlversuche frei, dann 10s Base-Delay + 5s pro weiterem Fehlversuch
- Login-Endpunkt: Key = `login:{client_ip}`
- Passwort-Aenderungs-Endpunkt: Key = `pwchange:{user_id}` (User-basiert statt IP-basiert, da User bereits eingeloggt)
- Bei falschem alten Passwort: `_record_fail()` aufrufen, bei Erfolg: `_reset_fails()` aufrufen
- Rate-Limit am Anfang des Passwort-Endpunkts pruefen, bei Ueberschreitung HTTP 429 zurueckgeben

---

## Issue #27: Dependencies nicht gepinnt [MEDIUM]

**Status:** Fix - Minimum-Versionen setzen.

**Anweisung:** In `requirements.txt`:
- Aktuell installierte Versionen ermitteln (`pip freeze` auf dem Produktions-Server oder im venv)
- Fuer jedes Paket die aktuelle Version als Minimum-Version setzen (Format: `paketname>=x.y.z`)
- `cryptography` hinzufuegen (wird fuer Issue #18+29 benoetigt)

---

## Issue #28: Fehlende Systemd-Haertung [MEDIUM]

**Status:** Fix - Haertungs-Direktiven hinzufuegen.

**Anweisung:** In allen drei Service-Dateien (`tareas.service`, `tareas-admin.service`, `tareas-ldap-sync.service`) unter `[Service]` folgende Direktiven hinzufuegen:
- `NoNewPrivileges=true` - verhindert Privilege Escalation
- `ProtectSystem=strict` - Dateisystem read-only ausser explizit erlaubte Pfade
- `ProtectHome=yes` - kein Zugriff auf /home, /root
- `PrivateTmp=true` - eigenes /tmp Verzeichnis
- `ReadWritePaths=/opt/tareas/data` - Schreibzugriff nur auf data-Verzeichnis
- `RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6` - nur noetige Netzwerk-Protokolle
- `ProtectKernelTunables=yes` - kein Zugriff auf /proc/sys etc.
- `ProtectControlGroups=yes` - kein cgroups-Zugriff

---

## Issue #32: Unzureichendes Security-Logging [LOW]

**Status:** Fix - Dediziertes Security-Log hinzufuegen.

**Anweisung:**
- Separaten Security-Logger konfigurieren, der nach `/var/log/tareas.log` schreibt (Syslog-Format: Timestamp, Level, User, Aktion)
- Logger in `app.py` oder einer neuen `logging_config.py` konfigurieren (FileHandler, Formatter im Syslog-Stil)
- Folgende Aktionen loggen (jeweils mit User-ID/Username):
  - Login (erfolgreich + fehlgeschlagen, inkl. IP)
  - Logout
  - User erstellen/loeschen/aendern (api_admin.py)
  - Passwort aendern (api_auth.py)
  - Config-Aenderungen (LDAP, Nextcloud, OnlyOffice, Mail, TLS)
  - Service-Restart (admin_app.py)
  - Rate-Limit ausgeloest
- Log-Rotation: via logrotate oder Python RotatingFileHandler
- Systemd-Service: `ReadWritePaths` um `/var/log/` erweitern (oder `/var/log/tareas.log`)

---

## Issue #35: Kein Logout ueber alle Tabs [INFO]

**Status:** Fix - Cookie client-seitig loeschen.

**Anweisung:** In `app_core.js`, Funktion `logout()`:
- Im `catch`-Block (und auch im Erfolgsfall, vor dem Redirect) den Session-Cookie client-seitig loeschen: `document.cookie = 'tareas_session=; path=/; expires=Thu, 01 Jan 1970 00:00:00 GMT';`
- Damit ist sichergestellt, dass der Cookie auch bei Netzwerk-Fehlern entfernt wird

---

