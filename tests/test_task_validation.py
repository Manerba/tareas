"""Status- und Fortschrittsvalidierung ueber REST und das echte MCP-Protokoll."""

import asyncio
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastmcp import Client
from fastmcp.exceptions import ToolError

from dashboard import api_tasks, database, mcp_server
from dashboard.auth import get_current_user


STATUSES = ("offen", "in_arbeit", "erledigt", "abgebrochen")
INVALID_STATUSES = ("unknown", "", "OFFEN", " offen ", 1, True, False, [], {})
PROGRESS_VALUES = (0, 1, 50, 99, 100)
INVALID_PROGRESS = (-1, 101, 1000000, 0.5, 50.0, True, False, "50", "", [], {})


class TaskValidationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="tareas-validation-")
        self.addCleanup(temp.cleanup)
        for name, value in {
            "DB_DIR": Path(temp.name), "DB_PATH": Path(temp.name) / "test.db",
        }.items():
            patcher = patch.object(database, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        database.init_db()
        self.user = {"id": 1, "username": "admin", "is_admin": True, "auth_source": "local"}
        token = mcp_server.current_mcp_user.set(self.user)
        self.addCleanup(mcp_server.current_mcp_user.reset, token)
        with closing(database.get_db()) as db, db:
            self.project = db.execute(
                "INSERT INTO tasks (name, task_type, status) VALUES ('Unchanged project', 'projekt', 'in_arbeit')",
            ).lastrowid
            self.predecessor = db.execute(
                "INSERT INTO sub_tasks (project_id, name, position_number) VALUES (?, 'Predecessor', 1)",
                (self.project,),
            ).lastrowid
            self.subtask = db.execute(
                "INSERT INTO sub_tasks (project_id, name, status_percent, position_number) VALUES (?, 'Unchanged subtask', 50, 2)",
                (self.project,),
            ).lastrowid
            db.execute(
                "INSERT INTO sub_task_dependencies (sub_task_id, depends_on_id) VALUES (?, ?)",
                (self.subtask, self.predecessor),
            )
        app = FastAPI()
        app.include_router(api_tasks.router)
        app.dependency_overrides[get_current_user] = lambda: self.user
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        mail_patch = patch.object(api_tasks, "notify_event", return_value=None)
        self.mail = mail_patch.start()
        self.addCleanup(mail_patch.stop)

    def snapshot(self):
        with closing(database.get_db()) as db:
            return [[tuple(row) for row in db.execute(sql)] for sql in (
                "SELECT * FROM tasks ORDER BY id",
                "SELECT * FROM sub_tasks ORDER BY id",
                "SELECT * FROM sub_task_dependencies ORDER BY id",
                "SELECT * FROM audit_log ORDER BY id",
            )]

    def task(self, task_id):
        with closing(database.get_db()) as db:
            return dict(db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone())

    def subtask_row(self, subtask_id):
        with closing(database.get_db()) as db:
            return dict(db.execute("SELECT * FROM sub_tasks WHERE id = ?", (subtask_id,)).fetchone())

    def assert_rest_rejected(self, response, before, field):
        self.assertEqual(response.status_code, 422, response.text)
        # A bare router retains FastAPI's validation envelope; production apps
        # may additionally expose the shared structured error envelope.
        self.assertIn(field, response.text)
        self.assertEqual(self.snapshot(), before)
        self.mail.assert_not_called()

    def test_rest_accepts_every_status_for_tasks_and_projects(self):
        for task_type in ("aufgabe", "projekt"):
            for status in STATUSES:
                with self.subTest(task_type=task_type, status=status):
                    response = self.client.post("/api/tasks", json={
                        "name": "Valid status", "task_type": task_type, "status": status,
                    })
                    self.assertEqual(response.status_code, 200, response.text)
                    task_id = response.json()["id"]
                    self.assertEqual(self.task(task_id)["status"], status)
                    response = self.client.put(f"/api/tasks/{self.project}", json={"status": status})
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(self.task(self.project)["status"], status)

    def test_rest_rejects_unknown_status_and_does_not_partially_update(self):
        before = self.snapshot()
        for value in (*INVALID_STATUSES, None):
            with self.subTest(operation="create", value=value):
                response = self.client.post("/api/tasks", json={
                    "name": "Must not be created", "task_type": "projekt", "status": value,
                })
                self.assert_rest_rejected(response, before, "status")
        for value in INVALID_STATUSES:
            with self.subTest(operation="update", value=value):
                response = self.client.put(f"/api/tasks/{self.project}", json={
                    "status": value, "name": "Must not change", "description": "Must not change",
                })
                self.assert_rest_rejected(response, before, "status")

    def test_invalid_status_prevents_file_storage_side_effects(self):
        before = self.snapshot()
        with patch.object(api_tasks, "ensure_local_directory") as ensure_directory:
            response = self.client.put(f"/api/tasks/{self.project}", json={
                "status": "invalid", "file_storage_type": "local",
            })
        self.assert_rest_rejected(response, before, "status")
        ensure_directory.assert_not_called()

    def test_rest_accepts_progress_boundaries_and_intermediate_integers(self):
        for value in PROGRESS_VALUES:
            with self.subTest(value=value):
                response = self.client.post(f"/api/tasks/{self.project}/subtasks", json={
                    "name": "Valid progress", "status_percent": value,
                })
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(self.subtask_row(response.json()["id"])["status_percent"], value)
                response = self.client.put(f"/api/subtasks/{self.subtask}", json={"status_percent": value})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(self.subtask_row(self.subtask)["status_percent"], value)

    def test_rest_rejects_invalid_progress_without_changes_to_data_dependencies_or_audit(self):
        before = self.snapshot()
        for value in (*INVALID_PROGRESS, None):
            with self.subTest(operation="create", value=value):
                response = self.client.post(f"/api/tasks/{self.project}/subtasks", json={
                    "name": "Must not be created", "status_percent": value,
                    "predecessor_ids": [self.predecessor],
                })
                self.assert_rest_rejected(response, before, "status_percent")
        for value in INVALID_PROGRESS:
            with self.subTest(operation="update", value=value):
                response = self.client.put(f"/api/subtasks/{self.subtask}", json={
                    "status_percent": value, "name": "Must not change", "predecessor_ids": [],
                })
                self.assert_rest_rejected(response, before, "status_percent")

    def test_rest_defaults_and_nullable_updates_remain_compatible(self):
        response = self.client.post("/api/tasks", json={"name": "Defaults"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.task(response.json()["id"])["status"], "offen")
        response = self.client.post(f"/api/tasks/{self.project}/subtasks", json={"name": "Defaults"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.subtask_row(response.json()["id"])["status_percent"], 0)
        response = self.client.put(f"/api/tasks/{self.project}", json={"name": "Renamed", "status": None})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.task(self.project)["status"], "in_arbeit")
        self.assertEqual(self.task(self.project)["name"], "Renamed")
        response = self.client.put(f"/api/subtasks/{self.subtask}", json={"name": "Renamed", "status_percent": None})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.subtask_row(self.subtask)["status_percent"], 50)
        self.assertEqual(self.subtask_row(self.subtask)["name"], "Renamed")

    def test_direct_mcp_calls_reject_invalid_status_before_writing(self):
        before = self.snapshot()
        for value in (*INVALID_STATUSES, None):
            with self.subTest(operation="create", value=value), self.assertRaises(ToolError):
                mcp_server.create_project("Invalid", status=value)
            self.assertEqual(self.snapshot(), before)
        for value in INVALID_STATUSES:
            with self.subTest(operation="update", value=value), self.assertRaises(ToolError):
                mcp_server.update_project(self.project, name="Must not change", status=value)
            self.assertEqual(self.snapshot(), before)

    def test_direct_mcp_calls_reject_invalid_progress_before_writing(self):
        before = self.snapshot()
        for value in (*INVALID_PROGRESS, None):
            with self.subTest(operation="create", value=value), self.assertRaises(ToolError):
                mcp_server.create_subtask(self.project, "Invalid", status_percent=value, predecessor_ids=[self.predecessor])
            self.assertEqual(self.snapshot(), before)
        for value in INVALID_PROGRESS:
            with self.subTest(operation="update", value=value), self.assertRaises(ToolError):
                mcp_server.update_subtask(self.subtask, name="Must not change", status_percent=value, predecessor_ids=[])
            self.assertEqual(self.snapshot(), before)

    def test_real_mcp_tool_schemas_advertise_status_enum_and_progress_limits(self):
        async def check():
            async with Client(mcp_server.mcp) as client:
                tools = {tool.name: tool for tool in await client.list_tools()}
                for tool in ("create_project", "update_project"):
                    schema = tools[tool].inputSchema["properties"]["status"]
                    if tool == "update_project":
                        self.assertIn({"type": "null"}, schema["anyOf"])
                        schema = next(item for item in schema["anyOf"] if item.get("type") == "string")
                    self.assertEqual(schema["type"], "string")
                    self.assertEqual(set(schema["enum"]), set(STATUSES))
                for tool in ("create_subtask", "update_subtask"):
                    schema = tools[tool].inputSchema["properties"]["status_percent"]
                    if tool == "update_subtask":
                        self.assertIn({"type": "null"}, schema["anyOf"])
                        schema = next(item for item in schema["anyOf"] if item.get("type") == "integer")
                    self.assertEqual((schema["type"], schema["minimum"], schema["maximum"]), ("integer", 0, 100))
        asyncio.run(check())

    def test_real_mcp_rejects_invalid_values_without_partial_mutations(self):
        async def check():
            async with Client(mcp_server.mcp) as client:
                before = self.snapshot()
                for value in (*INVALID_STATUSES, None):
                    for name, arguments in (
                        ("create_project", {"name": "Invalid", "status": value}),
                        ("update_project", {"project_id": self.project, "name": "Must not change", "status": value}),
                    ):
                        if name == "update_project" and value is None:
                            continue
                        with self.subTest(tool=name, value=value):
                            result = await client.call_tool(name, arguments, raise_on_error=False)
                            self.assertTrue(result.is_error, result)
                            self.assertEqual(self.snapshot(), before)
                for value in (*INVALID_PROGRESS, None):
                    for name, arguments in (
                        ("create_subtask", {"project_id": self.project, "name": "Invalid", "status_percent": value,
                                            "predecessor_ids": [self.predecessor]}),
                        ("update_subtask", {"subtask_id": self.subtask, "name": "Must not change", "status_percent": value,
                                            "predecessor_ids": []}),
                    ):
                        if name == "update_subtask" and value is None:
                            continue
                        with self.subTest(tool=name, value=value):
                            result = await client.call_tool(name, arguments, raise_on_error=False)
                            self.assertTrue(result.is_error, result)
                            self.assertEqual(self.snapshot(), before)
        asyncio.run(check())

    def test_real_mcp_accepts_valid_statuses_progress_and_defaults(self):
        async def check():
            async with Client(mcp_server.mcp) as client:
                for status in STATUSES:
                    result = await client.call_tool("create_project", {"name": "Valid", "status": status})
                    self.assertEqual(self.task(result.data["id"])["status"], status)
                    await client.call_tool("update_project", {"project_id": self.project, "status": status})
                    self.assertEqual(self.task(self.project)["status"], status)
                for value in PROGRESS_VALUES:
                    result = await client.call_tool("create_subtask", {
                        "project_id": self.project, "name": "Valid", "status_percent": value,
                    })
                    self.assertEqual(self.subtask_row(result.data["id"])["status_percent"], value)
                    await client.call_tool("update_subtask", {"subtask_id": self.subtask, "status_percent": value})
                    self.assertEqual(self.subtask_row(self.subtask)["status_percent"], value)
                result = await client.call_tool("create_project", {"name": "Defaults"})
                project_id = result.data["id"]
                self.assertEqual(self.task(project_id)["status"], "offen")
                result = await client.call_tool("create_subtask", {"project_id": project_id, "name": "Defaults"})
                subtask_id = result.data["id"]
                self.assertEqual(self.subtask_row(subtask_id)["status_percent"], 0)
                await client.call_tool("update_project", {"project_id": project_id, "name": "Renamed", "status": None})
                self.assertEqual(self.task(project_id)["status"], "offen")
                await client.call_tool("update_subtask", {"subtask_id": subtask_id, "name": "Renamed", "status_percent": None})
                self.assertEqual(self.subtask_row(subtask_id)["status_percent"], 0)
        asyncio.run(check())


if __name__ == "__main__":
    unittest.main()
