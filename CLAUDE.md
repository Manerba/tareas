# Tareas - Projektkontext

## Projektziel

Aufgabenplanungs-Tool auf Basis von **FastAPI + Vanilla JS**. Basiert auf dem Dashboard-Template und bietet eine Web-Oberflaeche fuer Aufgabenverwaltung und -planung.

## Entscheidungen

| Aspekt | Entscheidung |
|--------|--------------|
| Backend | FastAPI (Python) |
| Frontend | Vanilla JS (kein Framework) |
| Styling | CSS Custom Properties (Light/Dark Theme) |
| Port | 8504 |
| Routing | SPA mit History API + Catch-All im Backend |

## Infrastruktur

| Komponente | Details |
|------------|---------|
| OS | Ubuntu 24.04 |
| Dashboard | Port 8504 |
| Admin | Port 8505 |
| Service | `tareas.service` |

---

## Verfuegbare Komponenten

### ExpandableTable
Sortierbare, filterbare Tabelle mit aufklappbaren Detail-Rows.
- Python: `dashboard/components/expandable_table.py` (Column, DetailField, Filter, ExpandableTable, PaginationConfig)
- JS: `dashboard/static/js/expandable_table.js`
- CSS: `dashboard/static/css/expandable_table.css`

### KPI-Cards / Widget-Dashboard
Dashboard-Widgets mit KPI-Karten und Tabellen.
- Python: `dashboard/components/kpi_card.py` (KPICard, KPISection, Widget, WidgetDashboardConfig)
- JS: `dashboard/static/js/kpi_grid.js` (WidgetDashboard-Klasse)
- CSS: `dashboard/static/css/kpi_grid.css`

### Theme-System
Light/Dark mit CSS Custom Properties in `style.css`.

### Notification-System
`showNotification(message, type)` - Toast-Benachrichtigungen (info, success, error).

### DOM-Caching
Tab-Inhalte werden beim Wechsel als DocumentFragment gespeichert (Scroll, Filter, Zustand bleibt erhalten).

### URL-Routing
SPA-Routing mit `history.pushState()`. Backend hat Catch-All-Route fuer direkten URL-Zugriff.

---

## Neuen Tab hinzufuegen

1. JS-Datei erstellen: `dashboard/static/js/tab_example.js`
2. Init-Funktion definieren: `async function initExampleTab() { ... }`
3. In `app_core.js`: Tab in `switchTab()` registrieren + in `tabMap`/`buildPath` eintragen
4. In `index.html`: Tab-Link in `<nav class="tabs">` + `<script>`-Include
5. Optional: API-Endpunkt in `app.py` hinzufuegen
6. Optional: Tabellen-Config mit ExpandableTable definieren

---

## Projektstruktur

```
Tareas/
├── dashboard/
│   ├── app.py                  # FastAPI Backend (Port 8504)
│   ├── components/
│   │   ├── __init__.py
│   │   ├── expandable_table.py # Generische Tabellen-Komponente
│   │   └── kpi_card.py         # KPI-Card/Widget Dataclasses
│   ├── static/
│   │   ├── css/
│   │   │   ├── style.css              # Haupt-CSS (Theme, Layout)
│   │   │   ├── expandable_table.css   # Tabellen-Styles
│   │   │   └── kpi_grid.css           # Widget-Styles
│   │   └── js/
│   │       ├── app_core.js            # Core: Theme, Routing, Tabs
│   │       ├── expandable_table.js    # Tabellen-JS
│   │       └── kpi_grid.js            # Widget-Dashboard-JS
│   └── templates/
│       └── index.html          # SPA-Einstiegspunkt
├── scripts/
│   └── tareas.service          # Systemd-Service
├── git-commit.sh               # Auto-Version + Commit + Push
├── version.txt                 # Version (MAJOR.MINOR.PATCH+BUILD)
├── requirements.txt            # Python-Dependencies
├── .gitignore
└── CLAUDE.md                   # Diese Datei
```

---

## Versionierung & Commit-Workflow

**Immer `git-commit.sh` verwenden:**

```bash
./git-commit.sh "Kurze Beschreibung"
```

Das Script:
1. Inkrementiert Build-Nummer in `version.txt`
2. Staged alle Aenderungen (`git add -A`)
3. Commit mit Version-Prefix (z.B. `0.0.1+1: Beschreibung`)
4. Push zum Remote

---

## API-Endpunkte

| Methode | Pfad | Beschreibung |
|---------|------|--------------|
| GET | `/api/dashboard/info` | App-Infos und Entwickler-Hilfe |
| POST | `/api/dashboard/restart` | Service neu starten |

---

## Benutzer-Praeferenzen

- Sprache: Deutsch bevorzugt
