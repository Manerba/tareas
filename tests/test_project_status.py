"""Projektstatus ist in REST, MCP-Einzelabrufen und MCP-Listen identisch."""

import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from dashboard import api_tasks, database, mcp_server
from dashboard.auth import get_current_user


class ProjectStatusTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="tareas-project-status-")
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
            user_id = db.execute(
                "INSERT INTO users (username, password_hash, auth_source) VALUES ('owner', 'unused', 'mcp')",
            ).lastrowid
        self.user = {"id": user_id, "username": "owner", "is_admin": False, "auth_source": "mcp"}
        token = mcp_server.current_mcp_user.set(self.user)
        self.addCleanup(mcp_server.current_mcp_user.reset, token)
        app = FastAPI()
        app.include_router(api_tasks.router)
        app.dependency_overrides[get_current_user] = lambda: self.user
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        mail_patch = patch.object(api_tasks, "notify_event", return_value=None)
        mail_patch.start()
        self.addCleanup(mail_patch.stop)

    def project(self, progress=(), **fields):
        project_id = mcp_server.create_project("Status test", **fields)["id"]
        subtask_ids = [
            mcp_server.create_subtask(project_id, f"Subtask {number}", status_percent=percent)["id"]
            for number, percent in enumerate(progress)
        ]
        return project_id, subtask_ids

    def assert_status(self, project_id, expected):
        response = self.client.get("/api/tasks")
        self.assertEqual(response.status_code, 200, response.text)
        rest = next(item for item in response.json()["items"] if item["id"] == project_id)
        detail = mcp_server.get_project(project_id)
        listed = next(item for item in mcp_server.list_projects() if item["id"] == project_id)
        self.assertEqual(rest["status"], expected)
        self.assertEqual(detail["status"], expected)
        self.assertEqual(listed["status"], expected)
        return rest

    def test_four_completed_subtasks_complete_project_without_manual_update(self):
        project_id, subtasks = self.project([100, 100, 100, 100])
        row = self.assert_status(project_id, "erledigt")
        self.assertEqual(row["subtask_total"], 4)
        self.assertEqual(row["subtask_done"], 4)
        self.assertEqual(len(mcp_server.get_project(project_id)["subtasks"]), len(subtasks))
        with closing(database.get_db()) as db:
            self.assertEqual(db.execute("SELECT status FROM tasks WHERE id = ?", (project_id,)).fetchone()[0], "offen")

    def test_existing_progress_rule_uses_completed_subtasks_only(self):
        for progress, expected in (
            ([], "offen"), ([50], "offen"), ([0, 50], "offen"),
            ([100, 50], "in_arbeit"), ([100, 100], "erledigt"),
        ):
            with self.subTest(progress=progress):
                project_id, _ = self.project(progress, status="erledigt")
                self.assert_status(project_id, expected)
                updated = mcp_server.update_project(project_id, name="Renamed")
                self.assertEqual(updated["status"], expected)

    def test_create_returns_effective_status_for_empty_projects_and_manual_tasks(self):
        for status in ("offen", "in_arbeit", "erledigt", "abgebrochen"):
            for task_type in ("projekt", "aufgabe"):
                with self.subTest(status=status, task_type=task_type):
                    task = mcp_server.create_project("New task", status=status, task_type=task_type)
                    expected = status if task_type == "aufgabe" or status == "abgebrochen" else "offen"
                    self.assertEqual(task["status"], expected)
                    self.assert_status(task["id"], expected)

    def test_progress_changes_recompute_status_through_rest_and_mcp(self):
        project_id, subtasks = self.project([100, 0])
        self.assert_status(project_id, "in_arbeit")
        mcp_server.update_subtask(subtasks[1], status_percent=100)
        self.assert_status(project_id, "erledigt")
        response = self.client.put(f"/api/subtasks/{subtasks[0]}", json={"status_percent": 50})
        self.assertEqual(response.status_code, 200, response.text)
        self.assert_status(project_id, "in_arbeit")
        mcp_server.update_subtask(subtasks[1], status_percent=0)
        self.assert_status(project_id, "offen")
        mcp_server.delete_subtask(subtasks[0])
        mcp_server.delete_subtask(subtasks[1])
        row = self.assert_status(project_id, "offen")
        self.assertEqual(row["subtask_total"], 0)

    def test_cancellation_overrides_progress_until_resumed(self):
        for progress, expected in (([], "offen"), ([0, 50], "offen"), ([100, 50], "in_arbeit"), ([100, 100], "erledigt")):
            with self.subTest(progress=progress):
                project_id, _ = self.project(progress)
                cancelled = mcp_server.update_project(project_id, status="abgebrochen")
                self.assertEqual(cancelled["status"], "abgebrochen")
                self.assert_status(project_id, "abgebrochen")
                resumed = mcp_server.update_project(project_id, status="offen")
                self.assertEqual(resumed["status"], expected)
                self.assert_status(project_id, expected)

    def test_progress_can_change_while_cancelled_and_resume_uses_current_progress(self):
        project_id, subtasks = self.project([0, 50], status="abgebrochen")
        for subtask_id in subtasks:
            mcp_server.update_subtask(subtask_id, status_percent=100)
        self.assert_status(project_id, "abgebrochen")
        response = self.client.put(f"/api/tasks/{project_id}", json={"status": "offen"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assert_status(project_id, "erledigt")
        self.assertEqual(mcp_server.update_project(project_id, status="in_arbeit")["status"], "erledigt")

    def test_converted_task_keeps_manual_status_despite_remaining_subtasks(self):
        project_id, _ = self.project([100, 100])
        response = self.client.put(f"/api/tasks/{project_id}", json={"task_type": "aufgabe"})
        self.assertEqual(response.status_code, 200, response.text)
        for status in ("offen", "in_arbeit", "erledigt", "abgebrochen"):
            with self.subTest(status=status):
                self.assertEqual(mcp_server.update_project(project_id, status=status)["status"], status)
                self.assert_status(project_id, status)
        response = self.client.put(f"/api/tasks/{project_id}", json={"task_type": "projekt", "status": "offen"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assert_status(project_id, "erledigt")

    def test_filter_uses_effective_status_and_keeps_visibility_restrictions(self):
        expected = {status: set() for status in ("offen", "in_arbeit", "erledigt", "abgebrochen")}
        for progress, stored, effective in (
            ([], "erledigt", "offen"), ([0, 50], "in_arbeit", "offen"),
            ([100, 50], "offen", "in_arbeit"), ([100, 100], "offen", "erledigt"),
            ([100, 100], "abgebrochen", "abgebrochen"),
        ):
            project_id, _ = self.project(progress, status=stored)
            expected[effective].add(project_id)
        for status in expected:
            task_id = mcp_server.create_project("Manual task", task_type="aufgabe", status=status)["id"]
            expected[status].add(task_id)
        with closing(database.get_db()) as db, db:
            outsider = db.execute("INSERT INTO users (username, password_hash) VALUES ('outsider', 'unused')").lastrowid
            hidden = db.execute(
                "INSERT INTO tasks (name, task_type, created_by) VALUES ('Hidden complete project', 'projekt', ?)",
                (outsider,),
            ).lastrowid
            db.execute("INSERT INTO sub_tasks (project_id, name, status_percent) VALUES (?, 'Hidden', 100)", (hidden,))
        for status, ids in expected.items():
            with self.subTest(status=status):
                listed = mcp_server.list_projects(status=status)
                self.assertEqual({item["id"] for item in listed}, ids)
                self.assertTrue(all(item["status"] == status for item in listed))
        self.assertNotIn(hidden, {item["id"] for item in mcp_server.list_projects()})

    def test_legacy_progress_over_100_still_counts_as_complete(self):
        project_id, subtasks = self.project([0])
        with closing(database.get_db()) as db, db:
            db.execute("UPDATE sub_tasks SET status_percent = 101 WHERE id = ?", (subtasks[0],))
        self.assert_status(project_id, "erledigt")


if __name__ == "__main__":
    unittest.main()
