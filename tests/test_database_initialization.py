"""Regression tests for concurrent service startup, using isolated databases.

Run: ./venv/bin/python -m unittest discover -s tests -v
"""

import multiprocessing
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from dashboard import database


def _initialize_worker(db_dir, ready, start, results, pause_sql=None,
                       paused=None, resume=None):
    """Run a real initialization in a separate process, optionally pausing SQL."""
    database.DB_DIR = Path(db_dir)
    database.DB_PATH = database.DB_DIR / "tareas.db"
    connect = sqlite3.connect

    class PausingConnection(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            if sql == pause_sql:
                paused.set()
                if not resume.wait(15):
                    raise TimeoutError("Test did not release paused initialization")
            return super().execute(sql, *args, **kwargs)

    def connect_for_test(*args, **kwargs):
        return connect(*args, factory=PausingConnection, **kwargs)

    ready.set()
    try:
        if not start.wait(15):
            raise TimeoutError("Test did not start initialization")
        with patch.object(database.sqlite3, "connect", connect_for_test):
            database.init_db()
    except Exception as exc:
        results.put(f"{type(exc).__name__}: {exc}")
    else:
        results.put(None)


class DatabaseInitializationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="tareas-init-test-")
        self.addCleanup(temp.cleanup)
        self.db_dir = Path(temp.name) / "data"
        self.db_path = self.db_dir / "tareas.db"
        for name, value in {"DB_DIR": self.db_dir, "DB_PATH": self.db_path}.items():
            patcher = patch.object(database, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        # spawn avoids inheriting connections or lock descriptors from the parent.
        self.ctx = multiprocessing.get_context("spawn")
        self.results = self.ctx.Queue()
        self.addCleanup(self.results.close)

    def start_worker(self, start, **kwargs):
        ready = self.ctx.Event()
        process = self.ctx.Process(
            target=_initialize_worker,
            args=(str(self.db_dir), ready, start, self.results),
            kwargs=kwargs,
        )
        process.start()

        def cleanup():
            if process.is_alive():
                process.kill()
            process.join(timeout=5)
            process.close()

        self.addCleanup(cleanup)
        self.assertTrue(ready.wait(10), "Worker did not become ready")
        return process

    def assert_workers_succeeded(self, workers):
        for process in workers:
            process.join(timeout=15)
            self.assertEqual(process.exitcode, 0, "Initialization process hung or crashed")
        self.assertEqual([self.results.get(timeout=2) for _ in workers], [None] * len(workers))

    def assert_database_healthy(self):
        with closing(sqlite3.connect(self.db_path)) as conn:
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchall(), [("ok",)])
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone(), ("wal",))
            self.assertEqual(conn.execute("SELECT username, is_admin FROM users").fetchall(), [("admin", 1)])
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM mail_templates").fetchone(), (6,))
            self.assertEqual(
                {row[0] for row in conn.execute("SELECT name FROM areas")},
                set(database.DEFAULT_AREAS),
            )
            self.assertEqual(conn.execute("SELECT id, enabled FROM mcp_config").fetchall(), [(1, 1)])

    def test_simultaneous_first_start_and_restart(self):
        for phase in ("first start", "restart"):
            with self.subTest(phase=phase):
                start = self.ctx.Event()
                workers = [self.start_worker(start) for _ in range(4)]
                start.set()
                self.assert_workers_succeeded(workers)
                self.assert_database_healthy()

    def assert_serialized_at(self, pause_sql):
        start = self.ctx.Event()
        paused = self.ctx.Event()
        resume = self.ctx.Event()
        first = self.start_worker(
            start,
            pause_sql=pause_sql,
            paused=paused,
            resume=resume,
        )
        start.set()
        self.assertTrue(paused.wait(10), "First initializer did not reach pause point")
        second = self.start_worker(start)
        try:
            # The second process must wait even before WAL is set up and between
            # a column check and ALTER TABLE, across executescript() commits.
            second.join(timeout=1)
            waited = second.is_alive()
        finally:
            resume.set()
        self.assert_workers_succeeded([first, second])
        self.assertTrue(waited, "Second initializer bypassed the pending migration")
        self.assert_database_healthy()

    def test_second_initializer_waits_before_wal_setup(self):
        self.assert_serialized_at("PRAGMA journal_mode=WAL")

    def test_second_initializer_waits_for_pending_migration(self):
        self.assert_serialized_at("ALTER TABLE sub_tasks ADD COLUMN description TEXT DEFAULT ''")

    def test_concurrent_upgrade_preserves_existing_data(self):
        self.db_dir.mkdir()
        with closing(sqlite3.connect(self.db_path)) as conn:
            # Schema from before the description/position/assignment migrations.
            conn.executescript("""
                CREATE TABLE tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    created_at TEXT DEFAULT (datetime('now')),
                    deadline TEXT,
                    priority INTEGER DEFAULT 50,
                    task_type TEXT DEFAULT 'aufgabe',
                    status TEXT DEFAULT 'offen',
                    description TEXT DEFAULT ''
                );
                CREATE TABLE sub_tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    area_id INTEGER REFERENCES areas(id) ON DELETE SET NULL,
                    created_at TEXT DEFAULT (datetime('now')),
                    deadline TEXT,
                    priority INTEGER DEFAULT 50,
                    status_percent INTEGER DEFAULT 0
                );
                INSERT INTO tasks (id, name, task_type, description)
                    VALUES (1, 'Existing project', 'projekt', '<p>Keep me</p>');
                INSERT INTO sub_tasks (project_id, name, created_at, status_percent)
                    VALUES (1, 'Later', '2026-01-02', 42), (1, 'Earlier', '2026-01-01', 100);
            """)
        start = self.ctx.Event()
        workers = [self.start_worker(start) for _ in range(4)]
        start.set()
        self.assert_workers_succeeded(workers)
        self.assert_database_healthy()
        with closing(sqlite3.connect(self.db_path)) as conn:
            self.assertEqual(
                conn.execute("SELECT name, description FROM tasks").fetchall(),
                [("Existing project", "<p>Keep me</p>")],
            )
            self.assertEqual(
                conn.execute("SELECT name, status_percent, position_number FROM sub_tasks ORDER BY position_number").fetchall(),
                [("Earlier", 100, 1), ("Later", 42, 2)],
            )

    def test_process_death_releases_initialization_lock(self):
        start = self.ctx.Event()
        paused = self.ctx.Event()
        first = self.start_worker(
            start,
            pause_sql="ALTER TABLE sub_tasks ADD COLUMN description TEXT DEFAULT ''",
            paused=paused,
            resume=self.ctx.Event(),
        )
        start.set()
        self.assertTrue(paused.wait(10), "First initializer did not reach migration")
        first.kill()
        first.join(timeout=5)
        second = self.start_worker(start)
        self.assert_workers_succeeded([second])
        self.assert_database_healthy()

    def test_database_error_is_propagated_and_connection_closed(self):
        connect = sqlite3.connect
        connections = []

        class FailingConnection(sqlite3.Connection):
            def execute(self, sql, *args, **kwargs):
                if sql.startswith("ALTER TABLE"):
                    raise sqlite3.OperationalError("injected migration failure")
                return super().execute(sql, *args, **kwargs)

        def failing_connect(*args, **kwargs):
            conn = connect(*args, factory=FailingConnection, **kwargs)
            connections.append(conn)
            return conn

        with patch.object(database.sqlite3, "connect", failing_connect):
            with self.assertRaisesRegex(sqlite3.OperationalError, "injected migration failure"):
                database.init_db()
        self.assertEqual(len(connections), 1)
        with self.assertRaises(sqlite3.ProgrammingError):
            connections[0].execute("SELECT 1")
        start = self.ctx.Event()
        worker = self.start_worker(start)
        start.set()
        self.assert_workers_succeeded([worker])
        self.assert_database_healthy()


if __name__ == "__main__":
    unittest.main()
