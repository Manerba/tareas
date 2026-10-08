"""Projektverknuepfung: REST/MCP, Rechte, atomare Weitergabe und Lebenszyklus."""

import asyncio
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastmcp import Client

from dashboard import api_tasks, database, mcp_server as mcp
from dashboard.auth import get_current_user
from dashboard.mcp_errors import MCPToolError


class ProjectLinkTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="tareas-project-links-")
        self.addCleanup(temporary.cleanup)
        for name, value in {"DB_DIR": Path(temporary.name), "DB_PATH": Path(temporary.name) / "test.db"}.items():
            patcher = patch.object(database, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        database.init_db()
        self.user = {"id": 1, "username": "admin", "is_admin": True, "auth_source": "local"}
        token = mcp.current_mcp_user.set(self.user)
        self.addCleanup(mcp.current_mcp_user.reset, token)
        app = FastAPI()
        app.include_router(api_tasks.router)
        app.dependency_overrides[get_current_user] = lambda: self.user
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        mail = patch.object(api_tasks, "notify_event", return_value=None)
        mail.start()
        self.addCleanup(mail.stop)
        self.parent = mcp.create_project("Parent")["id"]
        self.subtask = mcp.create_subtask(self.parent, "Parent subtask", status_percent=75)["id"]

    def child(self, percentages=(), **kwargs):
        child = mcp.create_project("Child", **kwargs)["id"]
        ids = [mcp.create_subtask(child, f"Work {i}", status_percent=pct)["id"]
               for i, pct in enumerate(percentages)]
        return child, ids

    def put(self, path, body, status=200):
        response = self.client.put(path, json=body)
        self.assertEqual(response.status_code, status, response.text)
        return response.json()

    def snapshot(self):
        with closing(database.get_db()) as db:
            return [[tuple(row) for row in db.execute(sql)] for sql in (
                "SELECT * FROM tasks ORDER BY id", "SELECT * FROM sub_tasks ORDER BY id",
                "SELECT * FROM audit_log ORDER BY id",
            )]

    def assert_progress(self, expected, child, subtask=None, parent=None):
        subtask = subtask or self.subtask
        parent = parent or self.parent
        responses = [
            mcp.get_subtask(subtask),
            next(st for st in mcp.list_subtasks(parent) if st["id"] == subtask),
            next(st for st in mcp.get_project(parent)["subtasks"] if st["id"] == subtask),
            next(st for st in self.client.get(f"/api/tasks/{parent}/subtasks").json()["items"] if st["id"] == subtask),
        ]
        for item in responses:
            self.assertEqual(item["status_percent"], expected, item)
            self.assertEqual(item["child_project_id"], child, item)
            self.assertEqual(item["progress_automatic"], child is not None, item)

    def test_existing_project_link_and_metadata_in_all_lists(self):
        child, _ = self.child([100, 50, 0])
        self.put(f"/api/tasks/{child}", {"parent_subtask_id": self.subtask})
        self.assert_progress(33, child)
        for item in (
            mcp.get_project(child),
            next(t for t in mcp.list_projects() if t["id"] == child),
            next(t for t in mcp.list_projects_page()["items"] if t["id"] == child),
            next(t for t in self.client.get('/api/tasks').json()["items"] if t["id"] == child),
        ):
            self.assertEqual(item["parent_subtask_id"], self.subtask)
            self.assertEqual(item["parent_project_id"], self.parent)
            self.assertEqual(item["progress_percent"], 33)

    def test_create_linked_project_over_rest_and_mcp_empty_progress_zero(self):
        for surface in ("rest", "mcp"):
            with self.subTest(surface=surface):
                if surface == "rest":
                    response = self.client.post('/api/tasks', json={
                        "name": "Child", "task_type": "projekt", "parent_subtask_id": self.subtask,
                    })
                    self.assertEqual(response.status_code, 200, response.text)
                    child = response.json()["id"]
                else:
                    child, _ = self.child(parent_subtask_id=self.subtask)
                self.assert_progress(0, child)
                mcp.update_project(child, parent_subtask_id=0)

    def test_recursive_progress_updates_on_create_update_delete_through_both_transports(self):
        child, ids = self.child([100, 0], parent_subtask_id=self.subtask)
        grandchild, leaves = self.child([100, 0], parent_subtask_id=ids[1])
        self.assert_progress(50, grandchild, ids[1], child)
        self.assert_progress(50, child)
        self.put(f'/api/subtasks/{leaves[1]}', {"status_percent": 100})
        self.assert_progress(100, grandchild, ids[1], child)
        self.assert_progress(100, child)
        self.assertEqual(mcp.get_project(self.parent)["status"], "erledigt")
        self.assertIn(self.parent, [t["id"] for t in mcp.list_projects_page(status="erledigt")["items"]])
        new = self.client.post(f'/api/tasks/{grandchild}/subtasks', json={"name": "Extra"}).json()["id"]
        self.assert_progress(66, grandchild, ids[1], child)
        self.assert_progress(50, child)
        mcp.update_subtask(new, status_percent=100)
        self.assert_progress(100, child)
        mcp.delete_subtask(leaves[0])
        self.assert_progress(100, child)
        self.client.delete(f'/api/subtasks/{leaves[1]}')
        mcp.delete_subtask(new)
        self.assert_progress(0, grandchild, ids[1], child)
        self.assert_progress(50, child)

    def test_manual_update_blocked_even_for_admin_and_other_changes_roll_back(self):
        child, _ = self.child([100, 0], parent_subtask_id=self.subtask)
        before = self.snapshot()
        result = self.put(f'/api/subtasks/{self.subtask}', {"status_percent": 50, "name": "Do not write"}, 409)
        self.assertIn("automatisch", result["detail"])
        self.assertEqual(self.snapshot(), before)
        with self.assertRaises(MCPToolError) as raised:
            mcp.update_subtask(self.subtask, status_percent=100, description="Do not write")
        self.assertEqual(raised.exception.code, "linked_project_progress_readonly")
        self.assertEqual(raised.exception.field, "status_percent")
        self.assertEqual(self.snapshot(), before)
        updated = mcp.update_subtask(self.subtask, name="Editable title")
        self.assertEqual(updated["name"], "Editable title")
        self.put(f'/api/subtasks/{self.subtask}', {"priority": 42})
        self.assert_progress(50, child)

    def test_mcp_wire_errors_and_parent_parameter_schema(self):
        child, ids = self.child([0], parent_subtask_id=self.subtask)

        async def run():
            async with Client(mcp.mcp) as client:
                tools = {tool.name: tool for tool in await client.list_tools()}
                self.assertIn("parent_subtask_id", tools["create_project"].inputSchema["properties"])
                self.assertIn("parent_subtask_id", tools["update_project"].inputSchema["properties"])
                for name, args, code, field in (
                    ("update_project", {"project_id": child, "parent_subtask_id": -1},
                     "invalid_parent_subtask", "parent_subtask_id"),
                    ("update_subtask", {"subtask_id": self.subtask, "status_percent": 100},
                     "linked_project_progress_readonly", "status_percent"),
                    ("update_project", {"project_id": self.parent, "parent_subtask_id": ids[0]},
                     "project_link_cycle", "parent_subtask_id"),
                ):
                    result = await client.call_tool(name, args, raise_on_error=False)
                    self.assertTrue(result.is_error)
                    self.assertEqual(result.structured_content["code"], code)
                    self.assertEqual(result.structured_content["field"], field)
                    self.assertEqual(json.loads(result.content[0].text), result.structured_content)
                result = await client.call_tool("update_project", {"project_id": child, "parent_subtask_id": 0})
                self.assertFalse(result.is_error)

        asyncio.run(run())

    def test_reject_duplicate_cycle_wrong_type_and_unknown_parent_atomically(self):
        child, ids = self.child([0], parent_subtask_id=self.subtask)
        other, _ = self.child()
        normal = mcp.create_project("Task", task_type="aufgabe")["id"]
        for project, pid, code, status in (
            (other, self.subtask, "parent_subtask_already_linked", 409),
            (child, ids[0], "project_link_cycle", 409),
            (self.parent, ids[0], "project_link_cycle", 409),
            (normal, ids[0], "project_link_requires_project", 409),
            (other, 999999, "subtask_not_found", 404),
        ):
            with self.subTest(code=code, project=project):
                before = self.snapshot()
                self.put(f'/api/tasks/{project}', {"name": "Do not write", "parent_subtask_id": pid}, status)
                with self.assertRaises(MCPToolError) as raised:
                    mcp.update_project(project, name="Do not write", parent_subtask_id=pid)
                self.assertEqual(raised.exception.code, code)
                self.assertEqual(self.snapshot(), before)
        # Failed creations must not leave an orphan project behind.
        before = self.snapshot()
        response = self.client.post('/api/tasks', json={"name": "Invalid", "task_type": "projekt", "parent_subtask_id": self.subtask})
        self.assertEqual(response.status_code, 409)
        with self.assertRaises(MCPToolError):
            mcp.create_project("Invalid", parent_subtask_id=self.subtask)
        self.assertEqual(self.snapshot(), before)

    def test_unlink_relink_and_deletion_keep_projects_and_last_progress(self):
        child, ids = self.child([100, 0], parent_subtask_id=self.subtask)
        second = mcp.create_subtask(self.parent, "Second")["id"]
        mcp.update_project(child, parent_subtask_id=second)
        self.assert_progress(50, None)
        self.assert_progress(50, child, second)
        self.put(f'/api/tasks/{child}', {"parent_subtask_id": None})
        self.assert_progress(50, None, second)
        mcp.update_subtask(second, status_percent=10)
        self.assert_progress(10, None, second)
        mcp.update_project(child, parent_subtask_id=self.subtask)
        self.client.delete(f'/api/subtasks/{self.subtask}')
        self.assertIsNone(mcp.get_project(child)["parent_subtask_id"])
        self.assertEqual(len(mcp.get_project(child)["subtasks"]), 2)
        mcp.update_project(child, parent_subtask_id=second)
        mcp.delete_project(child)
        self.assert_progress(50, None, second)
        self.put(f'/api/subtasks/{second}', {"status_percent": 42})

    def test_deleting_parent_project_only_detaches_children(self):
        child, _ = self.child([100], parent_subtask_id=self.subtask)
        mcp.delete_project(self.parent)
        self.assertIsNone(mcp.get_project(child)["parent_subtask_id"])
        self.assertEqual(mcp.get_project(child)["status"], "erledigt")

    def test_type_change_blocked_on_both_sides_until_links_removed(self):
        child, _ = self.child([100], parent_subtask_id=self.subtask)
        for task in (self.parent, child):
            self.put(f'/api/tasks/{task}', {"task_type": "aufgabe"}, 409)
        self.put(f'/api/tasks/{child}', {"parent_subtask_id": 0, "task_type": "aufgabe"})
        self.put(f'/api/tasks/{self.parent}', {"task_type": "aufgabe"})

    def test_cancellation_preserves_progress_and_partial_work_does_not_count(self):
        child, _ = self.child([50, 99], parent_subtask_id=self.subtask)
        self.assert_progress(0, child)
        mcp.update_project(child, status="abgebrochen")
        self.assert_progress(0, child)
        self.assertEqual(mcp.get_project(child)["status"], "abgebrochen")

    def test_permissions_require_both_sides_and_do_not_propagate(self):
        child, _ = self.child([100])
        with closing(database.get_db()) as db, db:
            uid = db.execute("INSERT INTO users (username, password_hash) VALUES ('editor', 'unused')").lastrowid
            db.execute("INSERT INTO project_members (project_id, user_id, can_read, can_edit) VALUES (?, ?, 1, 1)", (child, uid))
        self.user = {"id": uid, "username": "editor", "is_admin": False, "auth_source": "local"}
        token = mcp.current_mcp_user.set(self.user)
        self.addCleanup(mcp.current_mcp_user.reset, token)
        self.put(f'/api/tasks/{child}', {"parent_subtask_id": self.subtask}, 403)
        with self.assertRaises(MCPToolError):
            mcp.update_project(child, parent_subtask_id=self.subtask)
        with closing(database.get_db()) as db, db:
            db.execute("INSERT INTO project_members (project_id, user_id, can_read, can_edit) VALUES (?, ?, 1, 1)", (self.parent, uid))
        mcp.update_project(child, parent_subtask_id=self.subtask)
        with closing(database.get_db()) as db, db:
            db.execute("DELETE FROM project_members WHERE project_id = ? AND user_id = ?", (child, uid))
        self.put(f'/api/tasks/{child}', {"parent_subtask_id": None}, 403)
        self.assertNotIn(child, [t["id"] for t in mcp.list_projects()])
        # Progress is still readable on the parent without exposing child content.
        self.assert_progress(100, child)
        with self.assertRaises(MCPToolError):
            mcp.get_project(child)
        # Assigned subtask rows carry the same lock as the project view.
        with closing(database.get_db()) as db, db:
            db.execute("UPDATE sub_tasks SET assigned_to = ? WHERE id = ?", (uid, self.subtask))
        row = next(t for t in self.client.get('/api/tasks').json()["items"] if t["id"] == -self.subtask)
        self.assertTrue(row["progress_automatic"])
        self.assertEqual(row["child_project_id"], child)
        self.put(f'/api/subtasks/{self.subtask}', {"status_percent": 0}, 409)

    def test_parallel_links_do_not_overwrite_or_introduce_cycles(self):
        first, first_subs = self.child([0])
        second, second_subs = self.child([0])
        barrier = threading.Barrier(2)

        def link(project, parent):
            token = mcp.current_mcp_user.set(self.user)
            try:
                barrier.wait(timeout=5)
                mcp.update_project(project, parent_subtask_id=parent)
                return "ok"
            except MCPToolError as exc:
                return exc.code
            finally:
                mcp.current_mcp_user.reset(token)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(link, first, second_subs[0]), pool.submit(link, second, first_subs[0])]
            self.assertCountEqual([f.result() for f in futures], ["ok", "project_link_cycle"])

    def test_existing_database_migration_is_idempotent_and_pid_column_available(self):
        before = mcp.get_subtask(self.subtask)
        with closing(database.get_db()) as db, db:
            db.execute("DROP INDEX idx_tasks_parent_subtask")
            db.execute("ALTER TABLE tasks DROP COLUMN parent_subtask_id")
        database.init_db()
        database.init_db()
        self.assertEqual(mcp.get_subtask(self.subtask), before)
        self.assertIsNone(mcp.get_project(self.parent)["parent_subtask_id"])
        config = self.client.get('/api/tasks/config').json()
        column = next(c for c in config["columns"] if c["field"] == "parent_subtask_id")
        self.assertEqual(column["label"], "PID")
        self.assertTrue(column["sortable"])


if __name__ == '__main__':
    unittest.main()
