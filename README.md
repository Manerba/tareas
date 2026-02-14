# Tareas

A self-hosted task and project management tool built with **FastAPI** and **Vanilla JavaScript**.

## Features

- **Task & Project Management** - Create tasks, organize them into projects with subtasks, dependencies, and deadlines
- **Interactive Network Diagram** - Visualize project dependencies as an interactive graph (vis-network)
- **Team Collaboration** - Assign tasks, manage team permissions (read/edit/create), notes system
- **WYSIWYG Editor** - Rich text descriptions for tasks and subtasks
- **File Storage** - Nextcloud/WebDAV integration with tree view, drag & drop upload, context menus
- **ONLYOFFICE Integration** - Edit Office documents (docx, xlsx, pptx) directly in the browser via WOPI
- **LDAP/Active Directory** - Authenticate users against AD, automatic sync, group-based access
- **Email Notifications** - SMTP integration with configurable templates for assignments, status changes, deadlines
- **TLS Support** - Optional HTTPS with certificate management via Admin UI
- **Admin Panel** - Separate admin interface (Port 8505) for user management, LDAP, Nextcloud, ONLYOFFICE, mail, and TLS configuration
- **Light/Dark Theme** - CSS Custom Properties with persistent preference
- **APT Package** - Install via `.deb` package on Ubuntu/Debian

## Screenshots

**Project view** — Rich text description, Nextcloud file browser, inline editing
![Project View](docs/Screenshot-Projekt.png)

**Subtask list** — Dependencies, status tracking, priority, assignments
![Subtask List](docs/Screenshot-Projekt2.png)

**Network diagram** — Interactive dependency graph with auto-layout and status colors
![Network Diagram](docs/Screenshot-Netplan.png)

## Installation

### Option 1: APT Package (Recommended)

```bash
curl -s https://your-repo-server/tareas/install.sh | bash
```

This sets up the APT repository and installs Tareas with all dependencies.

### Option 2: Manual Installation

```bash
# Clone the repository
git clone https://github.com/Manerba/tareas.git
cd tareas

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Start the application
python dashboard/app.py
```

The dashboard is available at `http://localhost:8504`.

### Option 3: Systemd Service

After manual installation, copy the service files:

```bash
cp scripts/tareas.service /etc/systemd/system/
cp scripts/tareas-admin.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now tareas tareas-admin
```

## Configuration

All configuration is managed through the **Admin Panel** at `http://localhost:8505`:

| Feature | Admin Tab | Description |
|---------|-----------|-------------|
| General | Allgemein | Server address for email links |
| Users | Benutzer | Create/manage local users |
| LDAP | LDAP | Active Directory connection and sync |
| Nextcloud | Nextcloud | WebDAV file storage integration |
| ONLYOFFICE | ONLYOFFICE | Document Server URL and JWT secret |
| Mail | Mail | SMTP settings and notification templates |
| TLS | TLS | HTTPS certificate paths |

### Default Credentials

On first start, create an admin user via the admin panel. The first user created automatically gets admin privileges.

### Database

Tareas uses **SQLite** with WAL mode. The database is stored at `data/tareas.db` and created automatically on first start. No external database server required.

## Architecture

```
Tareas/
├── dashboard/
│   ├── app.py              # Main FastAPI app (Port 8504)
│   ├── admin_app.py         # Admin FastAPI app (Port 8505)
│   ├── api_*.py             # API routers (tasks, auth, teams, etc.)
│   ├── components/          # Reusable Python components
│   ├── static/
│   │   ├── css/             # Stylesheets (theme, components)
│   │   └── js/              # Frontend JavaScript (SPA)
│   └── templates/           # HTML templates
├── scripts/                 # Systemd service files
├── packaging/               # .deb package configuration
├── data/                    # SQLite database (created at runtime)
├── requirements.txt
└── version.txt
```

### Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.10+, FastAPI, Uvicorn |
| Frontend | Vanilla JavaScript (no framework), CSS Custom Properties |
| Database | SQLite (WAL mode, foreign keys) |
| Auth | bcrypt + itsdangerous (cookie sessions) |
| Encryption | Fernet (credentials at rest) |
| File Storage | Nextcloud WebDAV proxy |
| Documents | ONLYOFFICE via WOPI |
| Directory | LDAP/Active Directory (ldap3) |

## Development

```bash
# Run in development mode
source venv/bin/activate
python dashboard/app.py        # Main app on :8504
python dashboard/admin_app.py  # Admin app on :8505
```

### Adding a New Tab

1. Create JS file: `dashboard/static/js/tab_example.js`
2. Define init function: `async function initExampleTab() { ... }`
3. Register in `app_core.js`: add to `switchTab()` and `tabMap`/`buildPath`
4. Add to `index.html`: tab link in `<nav class="tabs">` + `<script>` include
5. Optional: add API router in a new `api_example.py`

### Components

- **ExpandableTable** - Sortable, filterable table with expandable detail rows
- **KPI Cards** - Dashboard widgets with KPI cards and sections
- **WYSIWYG Editor** - ContentEditable-based rich text editor with HTML sanitization

## Security

- CSRF protection (X-Requested-With header validation)
- Content Security Policy (CSP) headers
- Rate limiting on login and password changes
- SQL identifier whitelisting
- HTML sanitization (XSS prevention)
- Fernet encryption for stored credentials
- Path traversal protection for file operations
- Systemd hardening (NoNewPrivileges, ProtectSystem, PrivateTmp)

## License

[MIT](LICENSE)
