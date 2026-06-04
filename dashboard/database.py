"""
Tareas - SQLite Datenbank
Schema, Initialisierung und Verbindungsmanagement.
"""

import sqlite3
from pathlib import Path

DB_DIR = Path(__file__).parent.parent / "data"
DB_PATH = DB_DIR / "tareas.db"

DEFAULT_AREAS = ["Entwicklung", "Design", "Planung", "Testing", "Allgemein"]


def get_db() -> sqlite3.Connection:
    """Liefert eine SQLite-Verbindung mit Row-Factory und Foreign Keys."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    """Erstellt Tabellen und Default-Daten beim App-Startup."""
    DB_DIR.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys = ON")

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS areas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            deadline TEXT,
            priority INTEGER DEFAULT 50,
            task_type TEXT DEFAULT 'aufgabe',
            status TEXT DEFAULT 'offen',
            description TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS sub_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            area_id INTEGER REFERENCES areas(id) ON DELETE SET NULL,
            created_at TEXT DEFAULT (datetime('now')),
            deadline TEXT,
            priority INTEGER DEFAULT 50,
            status_percent INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS sub_task_dependencies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sub_task_id INTEGER NOT NULL REFERENCES sub_tasks(id) ON DELETE CASCADE,
            depends_on_id INTEGER NOT NULL REFERENCES sub_tasks(id) ON DELETE CASCADE,
            UNIQUE(sub_task_id, depends_on_id)
        );

        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            vorname TEXT DEFAULT '',
            nachname TEXT DEFAULT '',
            email TEXT DEFAULT '',
            is_admin INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            token TEXT NOT NULL UNIQUE,
            created_at TEXT DEFAULT (datetime('now')),
            expires_at TEXT NOT NULL
        );
    """)

    # Migrationen: Spalten hinzufuegen falls nicht vorhanden
    cols = [r[1] for r in conn.execute("PRAGMA table_info(sub_tasks)").fetchall()]

    if "description" not in cols:
        conn.execute("ALTER TABLE sub_tasks ADD COLUMN description TEXT DEFAULT ''")

    if "position_number" not in cols:
        conn.execute("ALTER TABLE sub_tasks ADD COLUMN position_number INTEGER")
        # Bestehende SubTasks nachtraeglich nummerieren (pro Projekt)
        projects = conn.execute(
            "SELECT DISTINCT project_id FROM sub_tasks"
        ).fetchall()
        for (pid,) in projects:
            rows = conn.execute(
                "SELECT id FROM sub_tasks WHERE project_id = ? ORDER BY created_at ASC, id ASC",
                (pid,),
            ).fetchall()
            for i, (sid,) in enumerate(rows, start=1):
                conn.execute(
                    "UPDATE sub_tasks SET position_number = ? WHERE id = ?", (i, sid)
                )

    # Migration: created_by fuer tasks
    task_cols = [r[1] for r in conn.execute("PRAGMA table_info(tasks)").fetchall()]
    if "created_by" not in task_cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN created_by INTEGER REFERENCES users(id)")

    # Migration: assigned_to fuer tasks
    if "assigned_to" not in task_cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN assigned_to INTEGER REFERENCES users(id)")

    # Migration: created_by und assigned_to fuer sub_tasks
    sub_cols = [r[1] for r in conn.execute("PRAGMA table_info(sub_tasks)").fetchall()]
    if "created_by" not in sub_cols:
        conn.execute("ALTER TABLE sub_tasks ADD COLUMN created_by INTEGER REFERENCES users(id)")
    if "assigned_to" not in sub_cols:
        conn.execute("ALTER TABLE sub_tasks ADD COLUMN assigned_to INTEGER REFERENCES users(id)")

    # Migration: Netzplan-Positionen
    if "netzplan_x" not in sub_cols:
        conn.execute("ALTER TABLE sub_tasks ADD COLUMN netzplan_x REAL")
    if "netzplan_y" not in sub_cols:
        conn.execute("ALTER TABLE sub_tasks ADD COLUMN netzplan_y REAL")

    # Migration: Netzplan-Projektknoten-Position
    if "netzplan_project_x" not in task_cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN netzplan_project_x REAL")
    if "netzplan_project_y" not in task_cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN netzplan_project_y REAL")

    # Migration: Abhaengigkeit vom Projektknoten
    if "depends_on_project" not in sub_cols:
        conn.execute("ALTER TABLE sub_tasks ADD COLUMN depends_on_project INTEGER DEFAULT 0")

    # Migration: Notes-Tabellen
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS task_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            content TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            UNIQUE(task_id, user_id)
        );

        CREATE TABLE IF NOT EXISTS sub_task_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sub_task_id INTEGER NOT NULL REFERENCES sub_tasks(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            content TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            UNIQUE(sub_task_id, user_id)
        );

        CREATE TABLE IF NOT EXISTS project_members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            can_read INTEGER DEFAULT 1,
            can_edit INTEGER DEFAULT 0,
            can_create INTEGER DEFAULT 0,
            added_at TEXT DEFAULT (datetime('now')),
            UNIQUE(project_id, user_id)
        );

    """)

    # Migration: subtask_permissions entfernen (nicht mehr benoetigt)
    conn.execute("DROP TABLE IF EXISTS subtask_permissions")

    # Migration: LDAP-Felder fuer users-Tabelle
    user_cols = [r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
    if "auth_source" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN auth_source TEXT DEFAULT 'local'")
    if "ldap_dn" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN ldap_dn TEXT")
    if "is_active" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN is_active INTEGER DEFAULT 1")

    # LDAP-Config Tabelle
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS ldap_config (
            id INTEGER PRIMARY KEY,
            server TEXT NOT NULL,
            port INTEGER DEFAULT 389,
            use_ssl INTEGER DEFAULT 0,
            bind_dn TEXT NOT NULL,
            bind_password TEXT NOT NULL,
            search_base TEXT NOT NULL,
            group_dn TEXT,
            group_name TEXT,
            sync_interval_minutes INTEGER DEFAULT 60,
            last_sync_at TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS nextcloud_config (
            id INTEGER PRIMARY KEY,
            server_url TEXT NOT NULL,
            username TEXT NOT NULL,
            password TEXT NOT NULL,
            base_path TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS onlyoffice_config (
            id INTEGER PRIMARY KEY,
            server_url TEXT NOT NULL,
            jwt_secret TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS wopi_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token TEXT NOT NULL UNIQUE,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
            file_path TEXT NOT NULL,
            permissions TEXT DEFAULT 'edit',
            created_at TEXT DEFAULT (datetime('now')),
            expires_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS wopi_locks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id TEXT NOT NULL UNIQUE,
            lock_id TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            expires_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_wopi_tokens_token ON wopi_tokens(token);
    """)

    # Migration: nextcloud_path fuer tasks
    task_cols2 = [r[1] for r in conn.execute("PRAGMA table_info(tasks)").fetchall()]
    if "nextcloud_path" not in task_cols2:
        conn.execute("ALTER TABLE tasks ADD COLUMN nextcloud_path TEXT")

    # App-Config Tabelle
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS app_config (
            id INTEGER PRIMARY KEY,
            server_address TEXT NOT NULL DEFAULT 'localhost:8504'
        );
    """)

    # TLS-Config Tabelle
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS tls_config (
            id INTEGER PRIMARY KEY,
            enabled INTEGER DEFAULT 0,
            cert_path TEXT NOT NULL,
            key_path TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS tls_verify_config (
            id INTEGER PRIMARY KEY,
            verify_nextcloud INTEGER DEFAULT 0,
            verify_onlyoffice INTEGER DEFAULT 0,
            verify_smtp INTEGER DEFAULT 0,
            verify_ldap INTEGER DEFAULT 0
        );
    """)

    # Mail-Tabellen
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS mail_config (
            id INTEGER PRIMARY KEY,
            smtp_server TEXT NOT NULL,
            smtp_port INTEGER DEFAULT 587,
            encryption TEXT DEFAULT 'starttls',
            auth_enabled INTEGER DEFAULT 1,
            username TEXT DEFAULT '',
            password TEXT DEFAULT '',
            from_address TEXT NOT NULL,
            from_name TEXT DEFAULT 'Tareas',
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS mail_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL UNIQUE,
            subject TEXT NOT NULL,
            body_text TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS user_mail_preferences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            event_type TEXT NOT NULL,
            enabled INTEGER DEFAULT 1,
            days_before INTEGER DEFAULT 2,
            UNIQUE(user_id, event_type)
        );
    """)

    # MCP-Tabellen (Token-Verwaltung, Audit-Log, globale Config)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS mcp_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            token_hash TEXT NOT NULL UNIQUE,
            display_name TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            last_used_at TEXT,
            revoked_at TEXT,
            created_by INTEGER REFERENCES users(id) ON DELETE SET NULL
        );
        CREATE INDEX IF NOT EXISTS idx_mcp_tokens_hash ON mcp_tokens(token_hash);

        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT DEFAULT (datetime('now')),
            actor_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            actor_type TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            entity_id INTEGER,
            action TEXT NOT NULL,
            changes_json TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_audit_log_timestamp ON audit_log(timestamp DESC);
        CREATE INDEX IF NOT EXISTS idx_audit_log_entity ON audit_log(entity_type, entity_id);
        CREATE INDEX IF NOT EXISTS idx_audit_log_actor ON audit_log(actor_user_id);

        CREATE TABLE IF NOT EXISTS mcp_config (
            id INTEGER PRIMARY KEY,
            enabled INTEGER DEFAULT 1
        );
        INSERT OR IGNORE INTO mcp_config (id, enabled) VALUES (1, 1);
    """)

    # Default-Mail-Templates einfuegen (nur wenn Tabelle leer)
    tmpl_count = conn.execute("SELECT COUNT(*) FROM mail_templates").fetchone()[0]
    if tmpl_count == 0:
        default_templates = [
            ("task_assigned", "Aufgabe zugewiesen: {task_name}",
             "Hallo {recipient_name},\n\nDir wurde die Aufgabe \"{task_name}\" von {actor_name} zugewiesen.\n\nDeadline: {deadline}\nPrioritaet: {priority}\n\nViele Gruesse\nTareas\n\n{app_url}"),
            ("status_change", "Status geaendert: {task_name}",
             "Hallo {recipient_name},\n\nDer Status der Aufgabe \"{task_name}\" wurde von {actor_name} auf \"{new_status}\" geaendert.\n\nViele Gruesse\nTareas\n\n{app_url}"),
            ("deadline_reached", "Faelligkeit heute: {task_name}",
             "Hallo {recipient_name},\n\nDie Aufgabe \"{task_name}\" ist heute faellig.\n\nBitte pruefe den aktuellen Stand.\n\nViele Gruesse\nTareas\n\n{app_url}"),
            ("deadline_warning", "Faelligkeit in {days_before} Tagen: {task_name}",
             "Hallo {recipient_name},\n\nDie Aufgabe \"{task_name}\" ist in {days_before} Tagen faellig (Deadline: {deadline}).\n\nViele Gruesse\nTareas\n\n{app_url}"),
            ("invite", "Einladung zu Tareas",
             "Hallo {recipient_name},\n\nDu wurdest zu Tareas eingeladen.\n\nBenutzername: {username}\nURL: {app_url}\n\nBitte melde dich an und aendere dein Passwort.\n\nViele Gruesse\nTareas"),
            ("test", "Testmail von Tareas",
             "Dies ist eine Testmail von Tareas.\n\nWenn du diese Mail erhaeltst, funktioniert der Mailversand korrekt."),
        ]
        for event_type, subject, body_text in default_templates:
            conn.execute(
                "INSERT INTO mail_templates (event_type, subject, body_text) VALUES (?, ?, ?)",
                (event_type, subject, body_text),
            )

    # Default-Bereiche einfuegen (nur wenn Tabelle leer)
    cursor = conn.execute("SELECT COUNT(*) FROM areas")
    if cursor.fetchone()[0] == 0:
        for area in DEFAULT_AREAS:
            conn.execute("INSERT INTO areas (name) VALUES (?)", (area,))

    # Default-Admin anlegen (nur wenn keine User vorhanden)
    user_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    if user_count == 0:
        import bcrypt
        hashed = bcrypt.hashpw("admin".encode(), bcrypt.gensalt()).decode()
        conn.execute(
            "INSERT INTO users (username, password_hash, vorname, nachname, is_admin) VALUES (?, ?, ?, ?, ?)",
            ("admin", hashed, "Administrator", "", 1),
        )

    conn.commit()
    conn.close()
