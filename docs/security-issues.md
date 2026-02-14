# Security Code Review - Tareas Projekt

**Datum:** 2026-02-14
**Scope:** Vollstaendiges Code-Review aller Backend- und Frontend-Dateien
**Methode:** Statische Analyse in 4 parallelen Sprints

---

## Uebersicht

| Severity | Anzahl |
|----------|--------|
| Critical | 5 |
| High     | 11 |
| Medium   | 11 |
| Low      | 5 |
| Info     | 4 |
| **Gesamt** | **36** |

---

## Issue-Tracker

| Nr | Kritikalitaet | Bereich | Problem | Datei | Fix |
|----|---------------|---------|---------|-------|-----|
| 1 | Critical | Auth | Fehlende Auth auf /api/users/list | api_tasks.py:1006 | Done: Auth-Depends hinzugefuegt (0.0.1+77) |
| 2 | Critical | Auth | Fehlende Auth auf GET /api/areas | api_tasks.py:1109 | Done: Auth-Depends hinzugefuegt (0.0.1+77) |
| 3 | Critical | Auth | Fehlende Auth auf POST /api/areas | api_tasks.py:1117 | Done: Auth-Depends hinzugefuegt (0.0.1+77) |
| 4 | Critical | XSS | Unescapte col.field in onclick | expandable_table.js:564 | Done: escapeAttr() hinzugefuegt (0.0.1+77) |
| 5 | Critical | XSS | Unescapte Kategorie in onclick | tab_aufgaben.js:194 | Done: escapeAttr() hinzugefuegt (0.0.1+77) |
| 6 | High | SQLi | Table-Name Injection via f-String | config_utils.py:17,46 | Done: SQL-Whitelist (0.0.1+78) |
| 7 | High | SQLi | Column-Name Injection via f-String | config_utils.py:46 | Done: SQL-Whitelist (0.0.1+78) |
| 8 | High | SQLi | Dynamische UPDATE-Queries via f-String | db_utils.py:38-46 | Done: SQL-Whitelist (0.0.1+78) |
| 9 | High | XSS | WYSIWYG Fallback ohne sanitizeHtml | wysiwyg_editor.js:33,102 | Done: sanitizeHtml Pflicht (0.0.1+77) |
| 10 | High | XSS | Inkonsistentes Escaping renderActions | expandable_table.js:760 | Done: parseInt + escapeAttr (0.0.1+77) |
| 11 | High | XSS | File-Browser globale Refs + Inline-Handler | file_browser.js:41,91 | Done: addEventListener Refactoring (0.0.1+79) |
| 12 | High | Auth | IDOR in WOPI-Endpunkten | api_onlyoffice.py:342 | False Positive |
| 13 | High | Auth | Session-Timeout 1 Jahr | auth.py:17 | Akzeptiert (internes Tool) |
| 14 | High | Auth | Ueberpermissive CSP fuer Editor | app.py:121-140 | Done: Dynamische CSP (0.0.1+79) |
| 15 | High | Auth | Kein HTTPS-Enforcement / HSTS | app.py, auth.py:139 | Akzeptiert (by design) |
| 16 | High | File | Fehlende Upload-Groessenlimits | api_nextcloud.py:299 | Done: Upload-Limit 500MB (0.0.1+78) |
| 17 | High | Infra | Secret-Key Datei world-readable | auth.py:25-31 | Done: 0600 Permissions (0.0.1+78) |
| 18 | Medium | SQLi | LDAP-Passwort in Klartext in DB | ldap_sync.py:38 | Done: Fernet-Verschluesselung (0.0.1+80) |
| 19 | Medium | XSS | Unvalidierte localStorage-Werte | app_core.js:220 | Done: localStorage Whitelist (0.0.1+77) |
| 20 | Medium | XSS | Ungefilterte Server-Errors an User | app_core.js:202,623 | Done: Generische Fehler (0.0.1+78) |
| 21 | Medium | XSS | Inkonsistentes Escaping tab_benutzer | tab_benutzer.js:164 | False Positive |
| 22 | Medium | Auth | Schwache Passwort-Anforderungen (min 4) | api_admin.py:75 | Akzeptiert (internes Tool) |
| 23 | Medium | Auth | Kein Rate-Limiting Passwort-Aenderung | api_auth.py:203 | Done: Rate-Limit verallgemeinert (0.0.1+78) |
| 24 | Medium | Auth | Rate-Limit Timing-Leak | api_auth.py:102 | Akzeptiert (HTTP-Standard) |
| 25 | Medium | Auth | Path-Traversal: doppeltes unquote | api_nextcloud.py:53 | False Positive |
| 26 | Medium | File | Fehlende Dateityp-Validierung Upload | api_nextcloud.py:299 | Akzeptiert (WebDAV) |
| 27 | Medium | Infra | Dependencies nicht gepinnt | requirements.txt | Done: Minimum-Versionen (0.0.1+79) |
| 28 | Medium | Infra | Fehlende Systemd-Haertung | tareas.service | Done: Systemd-Haertung (0.0.1+81) |
| 29 | Medium | Infra | Credentials in Klartext in DB | diverse api_*.py | Done: Fernet-Verschluesselung (0.0.1+80) |
| 30 | Medium | DoS | Keine Pagination auf Task-Endpunkten | api_tasks.py:228 | Akzeptiert (Frontend-Pag.) |
| 31 | Low | DoS | Topo-Sortierung ohne Iterations-Limit | api_tasks.py:150 | Akzeptiert |
| 32 | Low | Log | Unzureichendes Security-Logging | diverse | Done: Security-Logging (0.0.1+81) |
| 33 | Low | Log | Detail-Fehler an Client exponiert | diverse api_*.py | Done: Generische Fehler (0.0.1+78) |
| 34 | Info | XSS | console.error in Produktion | diverse JS | Akzeptiert |
| 35 | Info | Auth | Kein Logout ueber alle Tabs | app_core.js:143 | Done: Cookie loeschen (0.0.1+77) |
| 36 | Info | Auth | X-Frame-Options vs CSP Konflikt | app.py:119 | Durch Fix #14 adressiert |

---

## Sprint 1: SQL Injection & Datenbank-Sicherheit

### SQLI-01: Table-Name Injection in config_utils.py [HIGH]

**Datei:** `dashboard/config_utils.py:17,46`

Tabellen- und Spaltennamen werden per f-String direkt in SQL interpoliert.

```python
row = db.execute(f"SELECT * FROM {table} WHERE id = 1").fetchone()
row = db.execute(f"SELECT {field} FROM {table} WHERE id = 1").fetchone()
```

**Risiko:** Wenn `table` oder `field` jemals aus User-Input stammen, ist SQL-Injection moeglich.

**Empfehlung:** Whitelist fuer erlaubte Tabellen-/Spaltennamen:
```python
ALLOWED_TABLES = {"ldap_config", "mail_config", "onlyoffice_config", "nextcloud_config", "app_config"}
if table not in ALLOWED_TABLES:
    raise ValueError(f"Invalid table: {table}")
```

---

### SQLI-02: Dynamische UPDATE-Queries via f-String [HIGH]

**Dateien:** `dashboard/db_utils.py:38-46`, `dashboard/api_tasks.py:417,687`, `dashboard/api_admin.py:126`

```python
set_clause = ", ".join(f"{k} = ?" for k in fields.keys())
db.execute(f"UPDATE {table} SET {set_clause} WHERE id = 1", list(fields.values()))
```

**Risiko:** Aktuell sicher (Spaltennamen sind intern hardcoded), aber fragiles Pattern. Wird zum Problem, sobald Spaltennamen aus User-Input kommen.

**Empfehlung:** Whitelist-Validierung fuer Spaltennamen vor Query-Bau.

---

### SQLI-03: LDAP-Passwort in Klartext in DB [MEDIUM]

**Datei:** `dashboard/ldap_sync.py:38`

LDAP Bind-Passwort wird unverschluesselt in SQLite gespeichert.

**Empfehlung:** Verschluesselung at-rest (z.B. `cryptography.fernet`) oder Umstellung auf Umgebungsvariablen.

---

## Sprint 2: XSS & Frontend-Sicherheit

### XSS-01: Unescapte Parameter in onclick-Attributen [CRITICAL]

**Datei:** `dashboard/static/js/expandable_table.js:564`

```javascript
const clickHandler = col.sortable
    ? `onclick="tables['${this.config.id}'].sortBy('${col.field}')"`
    : '';
```

`col.field` wird NICHT mit `escapeAttr()` escaped. Wenn der Wert Sonderzeichen enthaelt, kann JavaScript injiziert werden.

**Empfehlung:** Konsequent `escapeAttr()` fuer alle String-Parameter in Event-Handlern verwenden. Besser: Event-Delegation mit `data-*` Attributen statt Inline-Handler.

---

### XSS-02: Unescapte Kategorie-Parameter in onclick [CRITICAL]

**Datei:** `dashboard/static/js/tab_aufgaben.js:194`

```javascript
onclick="toggleCategoryFilter('${cat}')"
```

`cat` wird ohne Escaping in den Handler eingefuegt.

**Empfehlung:** `escapeAttr(cat)` verwenden.

---

### XSS-03: WYSIWYG-Editor Fallback ohne Sanitization [HIGH]

**Datei:** `dashboard/static/js/wysiwyg_editor.js:33,102`

```javascript
this.content.innerHTML = (typeof sanitizeHtml === 'function' ? sanitizeHtml(initialHTML) : initialHTML);
```

Wenn `sanitizeHtml` nicht verfuegbar ist (Ladereihenfolge, CSP), wird HTML unsanitized eingefuegt.

**Empfehlung:** `sanitizeHtml` als Pflicht-Abhaengigkeit behandeln, bei Fehlen Error werfen.

---

### XSS-04: Inkonsistentes HTML-Escaping [HIGH]

**Dateien:** `dashboard/static/js/tab_aufgaben.js:505,571`, `dashboard/static/js/expandable_table.js:760-761`

Manche Stellen nutzen `sanitizeHtml()`, andere `escapeHtml()`, wieder andere gar nichts. Insbesondere bei `renderActions()` fehlt Escaping fuer IDs.

**Empfehlung:** Einheitliche Escaping-Policy: `escapeHtml()` fuer Text, `escapeAttr()` fuer Attribute, `sanitizeHtml()` fuer Rich-HTML.

---

### XSS-05: File-Browser globale Referenzen und unescapte Pfade [HIGH]

**Datei:** `dashboard/static/js/file_browser.js:41,44,91,143,225`

```javascript
onclick="window._fb${this.taskId}.onUploadClick()"
window[`_fb${this.taskId}`] = this;
```

Globale Referenzen + Inline-Handler mit dynamischen Pfaden.

**Empfehlung:** Event-Delegation mit `data-*` Attributen statt globaler Referenzen.

---

### XSS-06: Unvalidierte localStorage-Werte [MEDIUM]

**Datei:** `dashboard/static/js/app_core.js:220,230`

Theme-Wert aus `localStorage` wird ohne Validierung in `data-theme` gesetzt.

**Empfehlung:** Whitelist-Validierung (`light`/`dark`).

---

### XSS-07: Ungefilterte Error-Messages an User [MEDIUM]

**Datei:** `dashboard/static/js/app_core.js:202,623`

Server-Fehlermeldungen werden direkt via `showNotification()` angezeigt und koennten interne Details enthalten.

**Empfehlung:** Nur bekannte/sichere Fehlermeldungen weiterleiten.

---

## Sprint 3: Authentifizierung, Autorisierung & Secrets

### AUTH-01: Fehlende Authentifizierung auf Endpunkten [CRITICAL]

**Datei:** `dashboard/api_tasks.py:1006-1018,1109-1125`

Drei Endpunkte haben KEIN explizites `Depends(get_current_user)`:
- `GET /api/users/list` - gibt alle Benutzernamen zurueck
- `GET /api/areas` - gibt alle Bereiche zurueck
- `POST /api/areas` - erstellt neue Bereiche

**Hinweis:** Moeglicherweise durch Router-Level-Dependencies geschuetzt, aber explizite Auth ist sicherer.

**Empfehlung:** Explizit `user=Depends(get_current_user)` zu jedem Endpunkt hinzufuegen.

---

### AUTH-02: IDOR in WOPI-Endpunkten [HIGH]

**Datei:** `dashboard/api_onlyoffice.py:342-456`

WOPI-Token-Validierung prueft Token-File-Zuordnung, aber NICHT ob der User Zugriff auf den zugehoerigen Task hat.

**Empfehlung:** Task-Berechtigungspruefung in WOPI-Endpunkte einbauen.

---

### AUTH-03: Session-Timeout 1 Jahr [HIGH]

**Datei:** `dashboard/auth.py:17`

```python
SESSION_MAX_AGE = 365 * 86400  # 1 Jahr
```

**Empfehlung:** Auf 8-24 Stunden reduzieren. Fuer "Angemeldet bleiben" separaten Refresh-Token implementieren.

---

### AUTH-04: Ueberpermissive CSP fuer Editor [HIGH]

**Datei:** `dashboard/app.py:121-140`

```python
"script-src 'self' 'unsafe-inline' https: http:; "
"connect-src *; "
"frame-src *;"
```

Erlaubt Scripts von beliebigen Quellen und Verbindungen zu jedem Host.

**Empfehlung:** Auf spezifische OnlyOffice-Domain einschraenken.

---

### AUTH-05: Kein HTTPS-Enforcement / Fehlender HSTS-Header [HIGH]

**Dateien:** `dashboard/app.py`, `dashboard/auth.py:139`

Kein `Strict-Transport-Security` Header. Session-Cookie `secure`-Flag ist optional.

**Empfehlung:** HSTS-Header setzen, HTTPS in Produktion erzwingen.

---

### AUTH-06: Schwache Passwort-Anforderungen [MEDIUM]

**Datei:** `dashboard/api_admin.py:75`

```python
if len(data.password) < 4:
```

Minimum 4 Zeichen ist unzureichend.

**Empfehlung:** Minimum 8-12 Zeichen, Komplexitaetsregeln einfuehren.

---

### AUTH-07: Kein Rate-Limiting auf Passwort-Aenderung [MEDIUM]

**Datei:** `dashboard/api_auth.py:203`

`PUT /api/auth/password` hat kein Rate-Limiting (Login-Endpunkt schon).

**Empfehlung:** Rate-Limiting analog zum Login einbauen.

---

### AUTH-08: Rate-Limit Timing-Leak [MEDIUM]

**Datei:** `dashboard/api_auth.py:102-107`

```python
detail=f"Zu viele Fehlversuche. Bitte {remaining}s warten."
```

Exakte Wartezeit wird offengelegt.

**Empfehlung:** Generische Meldung ohne genaue Zeitangabe.

---

## Sprint 4: Dateien, Dependencies & Infrastruktur

### FILE-01: Fehlende Upload-Groessenlimits [HIGH]

**Datei:** `dashboard/api_nextcloud.py:299-323`

```python
content = await file.read()  # Gesamte Datei in Speicher
```

Kein Groessen-Limit. Kann zu Memory-Exhaustion fuehren.

**Empfehlung:** Max-Groesse pruefen (z.B. 500MB), Streaming fuer grosse Dateien.

---

### FILE-02: Fehlende Dateityp-Validierung bei Upload [MEDIUM]

**Datei:** `dashboard/api_nextcloud.py:299-323`

Keine Pruefung der Dateierweiterung. Ausfuehrbare Dateien (.sh, .exe) koennen hochgeladen werden.

**Empfehlung:** Blacklist/Whitelist fuer erlaubte Dateitypen.

---

### FILE-03: Secret-Key Dateiberechtigungen [HIGH]

**Datei:** `dashboard/auth.py:25-31`

```python
SECRET_KEY_PATH.write_text(key)  # Default umask = 0644 (world-readable)
```

Secret-Key-Datei wird mit Default-Berechtigungen erstellt und ist fuer alle Systembenutzer lesbar.

**Empfehlung:** `os.open()` mit `0o600` Berechtigungen verwenden.

---

### INFRA-01: Fehlende Systemd-Haertung [MEDIUM]

**Dateien:** `packaging/services/tareas.service`, `packaging/services/tareas-admin.service`

Fehlende Sicherheits-Direktiven:
- `NoNewPrivileges=true`
- `ProtectSystem=strict`
- `ProtectHome=yes`
- `PrivateTmp=true`
- `RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6`
- `MemoryLimit` / `TasksMax`

**Empfehlung:** Hardening-Direktiven zum Service hinzufuegen.

---

### INFRA-02: Dependencies nicht gepinnt [MEDIUM]

**Datei:** `requirements.txt`

Alle Dependencies ohne Versionsangabe:
```
fastapi
uvicorn[standard]
bcrypt
itsdangerous
ldap3
httpx
python-multipart
```

**Empfehlung:** Versionen pinnen, regelmaessig mit `pip-audit` pruefen.

---

### INFRA-03: Credentials in Klartext in DB [MEDIUM]

Nextcloud-, LDAP-, SMTP- und OnlyOffice-Credentials werden unverschluesselt in SQLite gespeichert.

**Empfehlung:** Encryption-at-rest implementieren oder Umstellung auf Secrets-Manager / Umgebungsvariablen.

---

### DOS-01: Keine Pagination auf Task-Endpunkten [MEDIUM]

**Datei:** `dashboard/api_tasks.py:228-247`

Alle Tasks/SubTasks werden ohne Limit zurueckgegeben.

**Empfehlung:** Server-seitige Pagination mit `LIMIT`/`OFFSET`.

---

### DOS-02: Topologische Sortierung ohne Iterations-Limit [LOW]

**Datei:** `dashboard/api_tasks.py:150-195`

Kein Schutz gegen extrem grosse Dependency-Graphen.

**Empfehlung:** `max_iterations` Parameter einfuehren.

---

### LOG-01: Unzureichendes Security-Logging [LOW]

Admin-Operationen (User erstellen/loeschen, Passwort aendern, Service-Restart) werden nicht spezifisch geloggt.

**Empfehlung:** Dediziertes Security-Logging fuer sensitive Operationen.

---

### LOG-02: Detail-Fehler an Client exponiert [LOW]

**Dateien:** Diverse `api_*.py`

```python
except Exception as e:
    raise HTTPException(status_code=500, detail=str(e))
```

Exception-Details koennten interne Pfade/Konfiguration offenlegen.

**Empfehlung:** Generische Fehlermeldungen an Client, Details nur ins Log.

---

### MISC-01: console.error in Produktion [INFO]

**Dateien:** Diverse JS-Dateien

Error-Logs in Browser-Konsole koennten Implementierungsdetails verraten.

**Empfehlung:** In Produktion deaktivieren oder filtern.

---

### MISC-02: Kein Logout ueber alle Tabs [INFO]

**Datei:** `dashboard/static/js/app_core.js:143-150`

Wenn der Logout-Request fehlschlaegt, bleibt der Cookie in anderen Tabs aktiv.

**Empfehlung:** Cookie client-seitig loeschen, unabhaengig vom Server-Response.

---

### MISC-03: CSRF nur auf mutierende Methoden [INFO]

**Datei:** `dashboard/app.py:97-111`

GET-Requests sind nicht CSRF-geschuetzt. Akzeptabel, solange GET idempotent bleibt.

---

### MISC-04: X-Frame-Options vs. CSP Konflikt [INFO]

**Datei:** `dashboard/app.py:119`

`X-Frame-Options: DENY` wird global gesetzt, aber CSP fuer `/editor` erlaubt `frame-src *`. CSP hat Vorrang.

**Empfehlung:** `frame-src` auf spezifische Domain einschraenken.

---

## Positive Befunde

- Bcrypt fuer Passwort-Hashing
- Parameterized Queries fuer Werte (nicht Identifier)
- CSRF-Middleware vorhanden
- Security-Headers gesetzt (X-Content-Type-Options, X-Frame-Options)
- Path-Traversal-Schutz in `_safe_rel_path()` gut implementiert
- Session-Cookies: HttpOnly, SameSite=Lax
- `escapeHtml()`, `escapeAttr()`, `sanitizeHtml()` vorhanden (aber inkonsistent genutzt)
- Rate-Limiting auf Login-Endpunkt
- Foreign-Key-Constraints aktiviert
- `subprocess.Popen` in List-Form (kein Shell-Injection)

---

## Priorisierte Massnahmen

### Phase 1 - Sofort (Critical)
1. Auth auf `/api/users/list`, `/api/areas` explizit setzen
2. `escapeAttr()` konsequent in ALLEN Inline-Event-Handlern verwenden
3. `sanitizeHtml` als Pflicht-Abhaengigkeit im WYSIWYG-Editor

### Phase 2 - Diese Woche (High)
4. SQL-Identifier-Whitelist in `config_utils.py` / `db_utils.py`
5. WOPI-Endpunkte: Task-Berechtigung pruefen
6. Session-Timeout reduzieren (365d -> 8-24h)
7. CSP fuer Editor auf spezifische Domain einschraenken
8. HSTS-Header setzen
9. Upload-Groessenlimit einfuehren
10. Secret-Key Dateiberechtigungen auf 0600 setzen

### Phase 3 - Dieser Sprint (Medium)
11. Passwort-Mindestlaenge erhoehen (4 -> 8+)
12. Dependencies pinnen
13. Systemd-Service haerten
14. Credentials in DB verschluesseln
15. Fehler-Details nicht an Client senden
16. Rate-Limiting auf Passwort-Aenderung

### Phase 4 - Backlog (Low/Info)
17. Security-Logging fuer Admin-Operationen
18. Pagination auf Task-Endpunkten
19. Iterations-Limit fuer Graph-Algorithmen
20. console.error in Produktion deaktivieren
