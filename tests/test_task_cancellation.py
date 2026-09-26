"""Regression checks using an isolated SQLite database, without sending mail.

Run: ./venv/bin/python -m unittest discover -s tests -v
"""

import asyncio
import json
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from dashboard import api_tasks, database, mcp_server
from dashboard.auth import get_current_user
from scripts import mail_scheduler


class TaskCancellationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="tareas-cancellation-")
        self.addCleanup(temp.cleanup)
        for name, value in {
            "DB_DIR": Path(temp.name),
            "DB_PATH": Path(temp.name) / "test.db",
        }.items():
            patcher = patch.object(database, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        database.init_db()
        with closing(database.get_db()) as db, db:
            self.users = {}
            for name in ("owner", "assignee", "reader", "editor", "outsider"):
                cursor = db.execute(
                    "INSERT INTO users (username, password_hash) VALUES (?, 'unused')",
                    (name,),
                )
                self.users[name] = {
                    "id": cursor.lastrowid, "username": name,
                    "is_admin": False, "auth_source": "local",
                }
        self.user = self.users["owner"]
        app = FastAPI()
        app.include_router(api_tasks.router)
        app.dependency_overrides[get_current_user] = lambda: self.user
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        mail_patch = patch.object(api_tasks, "notify_event", return_value=None)
        self.mail = mail_patch.start()
        self.addCleanup(mail_patch.stop)

    def create_task(self, task_type="aufgabe", **fields):
        response = self.client.post("/api/tasks", json={
            "name": "Keep this task", "task_type": task_type,
            "description": "<p>Keep this description</p>", **fields,
        })
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["id"]

    def task(self, task_id):
        response = self.client.get("/api/tasks")
        self.assertEqual(response.status_code, 200, response.text)
        return next(row for row in response.json()["items"] if row["id"] == task_id)

    def set_status(self, task_id, status):
        response = self.client.put(f"/api/tasks/{task_id}", json={"status": status})
        self.assertEqual(response.status_code, 200, response.text)

    def test_tasks_can_be_cancelled_and_resumed_from_every_status(self):
        for status in ("offen", "in_arbeit", "erledigt"):
            with self.subTest(status=status):
                task_id = self.create_task(status=status)
                before = self.task(task_id)
                self.set_status(task_id, "abgebrochen")
                self.assertEqual(self.task(task_id), {**before, "status": "abgebrochen"})
                self.set_status(task_id, status)
                self.assertEqual(self.task(task_id), before)

    def test_project_cancellation_overrides_progress_and_can_be_resumed(self):
        for progress, expected in (
            ([], "offen"), ([0, 50], "offen"),
            ([100, 50], "in_arbeit"), ([100, 100], "erledigt"),
        ):
            with self.subTest(progress=progress):
                task_id = self.create_task("projekt")
                for percent in progress:
                    response = self.client.post(f"/api/tasks/{task_id}/subtasks", json={
                        "name": "Keep this subtask", "status_percent": percent,
                    })
                    self.assertEqual(response.status_code, 200, response.text)
                before = self.task(task_id)
                self.assertEqual(before["status"], expected)
                self.set_status(task_id, "abgebrochen")
                self.assertEqual(self.task(task_id), {**before, "status": "abgebrochen"})
                self.set_status(task_id, "offen")
                self.assertEqual(self.task(task_id), before)

    def test_cancelling_retains_subtasks_dependencies_notes_team_and_files(self):
        task_id = self.create_task("projekt")
        owner_id = self.user["id"]
        with closing(database.get_db()) as db, db:
            db.execute("UPDATE tasks SET nextcloud_path = 'projects/keep' WHERE id = ?", (task_id,))
            first = db.execute(
                "INSERT INTO sub_tasks (project_id, name, status_percent) VALUES (?, 'First', 100)",
                (task_id,),
            ).lastrowid
            second = db.execute(
                "INSERT INTO sub_tasks (project_id, name, status_percent) VALUES (?, 'Second', 50)",
                (task_id,),
            ).lastrowid
            db.execute("INSERT INTO sub_task_dependencies (sub_task_id, depends_on_id) VALUES (?, ?)", (second, first))
            db.execute("INSERT INTO task_notes (task_id, user_id, content) VALUES (?, ?, 'Task note')", (task_id, owner_id))
            db.execute("INSERT INTO task_note_entries (task_id, user_id, content) VALUES (?, ?, 'History')", (task_id, owner_id))
            db.execute("INSERT INTO sub_task_notes (sub_task_id, user_id, content) VALUES (?, ?, 'Subtask note')", (second, owner_id))
            db.execute("INSERT INTO sub_task_note_entries (sub_task_id, user_id, content) VALUES (?, ?, 'Subtask history')", (second, owner_id))
            db.execute("INSERT INTO project_members (project_id, user_id) VALUES (?, ?)", (task_id, self.users["reader"]["id"]))
        tables = (
            "sub_tasks", "sub_task_dependencies", "task_notes", "task_note_entries",
            "sub_task_notes", "sub_task_note_entries", "project_members",
        )
        with closing(database.get_db()) as db:
            before = {table: db.execute(f"SELECT * FROM {table} ORDER BY id").fetchall() for table in tables}
        self.set_status(task_id, "abgebrochen")
        with closing(database.get_db()) as db:
            for table in tables:
                self.assertEqual(db.execute(f"SELECT * FROM {table} ORDER BY id").fetchall(), before[table])
            audit = db.execute("SELECT action, changes_json FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
            self.assertEqual(audit["action"], "status_change")
            self.assertEqual(json.loads(audit["changes_json"]), {"status": "abgebrochen"})
        self.assertEqual(self.task(task_id)["nextcloud_path"], "projects/keep")
        response = self.client.put(f"/api/subtasks/{second}", json={"status_percent": 100})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.task(task_id)["status"], "abgebrochen")

    def test_cancellation_respects_existing_edit_permissions(self):
        for actor, expected in (("outsider", 403), ("reader", 403), ("editor", 200), ("assignee", 200), ("owner", 200)):
            with self.subTest(actor=actor):
                self.user = self.users["owner"]
                task_id = self.create_task("projekt")
                with closing(database.get_db()) as db, db:
                    db.execute("UPDATE tasks SET assigned_to = ? WHERE id = ?", (self.users["assignee"]["id"], task_id))
                    for member, can_edit in (("reader", 0), ("editor", 1)):
                        db.execute("INSERT INTO project_members (project_id, user_id, can_edit) VALUES (?, ?, ?)", (task_id, self.users[member]["id"], can_edit))
                self.user = self.users[actor]
                if actor != "outsider":
                    self.assertEqual(self.task(task_id)["can_edit_status"], expected == 200)
                response = self.client.put(f"/api/tasks/{task_id}", json={"status": "abgebrochen"})
                self.assertEqual(response.status_code, expected, response.text)
                self.user = self.users["owner"]
                self.assertEqual(self.task(task_id)["status"], "abgebrochen" if expected == 200 else "offen")

    def test_mcp_can_cancel_list_and_resume_without_deleting(self):
        task_id = self.create_task("projekt")
        token = mcp_server.current_mcp_user.set(self.user)
        try:
            result = mcp_server.update_project(task_id, status="abgebrochen")
            self.assertEqual(result["status"], "abgebrochen")
            self.assertEqual(self.task(task_id)["status"], "abgebrochen")
            self.assertEqual([row["id"] for row in mcp_server.list_projects(status="abgebrochen")], [task_id])
            self.assertEqual(mcp_server.get_project(task_id)["description"], "<p>Keep this description</p>")
            mcp_server.update_project(task_id, status="offen")
            self.assertEqual(self.task(task_id)["status"], "offen")
        finally:
            mcp_server.current_mcp_user.reset(token)

    def test_admin_can_see_and_edit_cancelled_projects(self):
        task_id = self.create_task("projekt", status="abgebrochen")
        self.user = {**self.users["outsider"], "is_admin": True}
        row = self.task(task_id)
        self.assertEqual(row["status"], "abgebrochen")
        self.assertTrue(row["can_edit_status"])
        self.set_status(task_id, "offen")
        self.assertEqual(self.task(task_id)["status"], "offen")

    def test_status_filter_and_notification_include_cancelled(self):
        config = self.client.get("/api/tasks/config").json()
        status_filter = next(item for item in config["filters"] if item["id"] == "statusFilter")
        self.assertIn({"value": "abgebrochen", "label": "Abgebrochen"}, status_filter["options"])
        asyncio.run(api_tasks._send_status_mail(self.user["id"], "Task", "Owner", "abgebrochen"))
        self.assertEqual(self.mail.call_args.args[2]["new_status"], "Abgebrochen")

    def test_cancelled_tasks_and_projects_receive_no_deadline_mail(self):
        now = datetime.now()
        expected = set()
        for days, event in ((0, "deadline_reached"), (2, "deadline_warning")):
            for task_type in ("aufgabe", "projekt"):
                for status in ("offen", "in_arbeit", "erledigt", "abgebrochen"):
                    name = f"{days}-{task_type}-{status}"
                    self.create_task(task_type, name=name, status=status, deadline=(now + timedelta(days=days)).strftime("%Y-%m-%d"))
                    if status in ("offen", "in_arbeit"):
                        expected.add((event, name))
        with closing(database.get_db()) as db, db:
            db.execute("INSERT INTO mail_config (id, smtp_server, from_address) VALUES (1, 'invalid', 'test@example.invalid')")
            db.execute("INSERT INTO user_mail_preferences (user_id, event_type, enabled, days_before) VALUES (?, 'deadline_warning', 1, 2)", (self.user["id"],))
        with patch.object(mail_scheduler, "notify_event", return_value=None) as mail:
            mail_scheduler.main()
        actual = [(call.args[0], call.args[2]["task_name"]) for call in mail.call_args_list]
        self.assertEqual(set(actual), expected)
        self.assertEqual(len(actual), len(expected))


if __name__ == "__main__":
    unittest.main()
