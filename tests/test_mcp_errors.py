"""MCP-Fachfehler und Schemafehler ueber echte Protokollaufrufe, ohne Live-Daten."""

import asyncio
import json
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError, ValidationError as FastMCPValidationError

from dashboard import api_tasks, database, file_storage, mcp_server as api
from dashboard.auth import get_current_user
from dashboard.errors import ApplicationError
from dashboard.mcp_errors import StructuredToolErrors


class MCPErrorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="tareas-mcp-errors-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for name, value in (("DB_DIR", self.root), ("DB_PATH", self.root / "test.db")):
            patcher = patch.object(database, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        database.init_db()
        self.user = {"id": 1, "username": "admin", "is_admin": True, "auth_source": "mcp"}
        token = api.current_mcp_user.set(self.user)
        self.addCleanup(api.current_mcp_user.reset, token)
        with closing(database.get_db()) as db, db:
            self.task = db.execute(
                "INSERT INTO tasks (name, task_type, created_by, file_storage_type) "
                "VALUES ('Project', 'projekt', 1, 'local')",
            ).lastrowid
            self.empty = db.execute(
                "INSERT INTO tasks (name, task_type, created_by) VALUES ('Without storage', 'projekt', 1)",
            ).lastrowid
            self.a, self.b, self.c = [db.execute(
                "INSERT INTO sub_tasks (project_id, name, created_by) VALUES (?, ?, 1)",
                (self.task, name),
            ).lastrowid for name in ("A", "B", "C")]
            self.foreign = db.execute(
                "INSERT INTO sub_tasks (project_id, name, created_by) VALUES (?, 'Foreign', 1)",
                (self.empty,),
            ).lastrowid
            db.executemany(
                "INSERT INTO sub_task_dependencies (sub_task_id, depends_on_id) VALUES (?, ?)",
                ((self.b, self.a), (self.c, self.b)),
            )
        file_storage.ensure_local_directory(self.task)
        (file_storage.local_path(self.task) / "keep.txt").write_text("original")

    def snapshot(self):
        with closing(database.get_db()) as db:
            return [[tuple(row) for row in db.execute(sql)] for sql in (
                "SELECT * FROM tasks ORDER BY id",
                "SELECT * FROM sub_tasks ORDER BY id",
                "SELECT * FROM sub_task_dependencies ORDER BY id",
                "SELECT * FROM audit_log ORDER BY id",
            )]

    async def reject(self, client, name, arguments, code, *, field=None, fields=None):
        result = await client.call_tool(name, arguments, raise_on_error=False)
        self.assertTrue(result.is_error, result)
        payload = result.structured_content
        self.assertIsInstance(payload, dict, result)
        self.assertEqual(payload["code"], code, payload)
        self.assertTrue(payload["message"])
        self.assertEqual(json.loads(result.content[0].text), payload)
        if field is not None:
            self.assertEqual(payload.get("field"), field, payload)
        if fields is not None:
            self.assertEqual(payload.get("fields"), fields, payload)
        return payload

    def test_schema_errors_have_fields_and_never_echo_values_or_write_data(self):
        before = self.snapshot()
        private = "DO-NOT-ECHO-SECRET-OR-CONTENT"

        async def run():
            async with Client(api.mcp) as client:
                for name, arguments, code, field in (
                    ("create_project", {"name": "Rejected", "status": private}, "invalid_status", "status"),
                    ("update_project", {"project_id": self.task, "name": "Changed", "status": private}, "invalid_status", "status"),
                    ("create_subtask", {"project_id": self.task, "name": "Rejected", "status_percent": 101}, "invalid_progress", "status_percent"),
                    ("update_subtask", {"subtask_id": self.a, "name": "Changed", "status_percent": -1}, "invalid_progress", "status_percent"),
                    ("update_subtask", {"subtask_id": self.a, "status_percent": True}, "invalid_progress", "status_percent"),
                    ("update_subtask", {"subtask_id": self.a, "status_percent": 50.0}, "invalid_progress", "status_percent"),
                    ("get_project", {"project_id": private}, "invalid_argument", "project_id"),
                    ("get_project", {}, "missing_argument", "project_id"),
                    ("file.write", {"task_id": self.task, "path": "keep.txt", "content": private, "encoding": private}, "invalid_encoding", "encoding"),
                ):
                    with self.subTest(name=name, code=code, field=field):
                        payload = await self.reject(client, name, arguments, code, field=field)
                        self.assertNotIn(private, json.dumps(payload))
                multiple = await self.reject(client, "create_subtask", {
                    "project_id": private, "name": "Rejected", "status_percent": 101,
                }, "invalid_arguments", fields=["project_id", "status_percent"])
                self.assertEqual([error["code"] for error in multiple["errors"]], ["invalid_argument", "invalid_progress"])
                self.assertNotIn(private, json.dumps(multiple))

        asyncio.run(run())
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(file_storage.local_path(self.task, "keep.txt").read_text(), "original")

    def test_dependency_codes_and_rollback_for_add_update_and_create(self):
        before = self.snapshot()

        async def run():
            async with Client(api.mcp) as client:
                for predecessor, code in (
                    (self.b, "dependency_cycle"),
                    (self.a, "dependency_self_reference"),
                    (self.foreign, "dependency_wrong_project"),
                ):
                    await self.reject(client, "add_dependency", {
                        "subtask_id": self.a, "depends_on_id": predecessor,
                    }, code, field="depends_on_id")
                await self.reject(client, "add_dependency", {
                    "subtask_id": self.c, "depends_on_id": self.a,
                }, "dependency_redundant", field="depends_on_id")
                await self.reject(client, "update_subtask", {
                    "subtask_id": self.a, "name": "Must roll back", "predecessor_ids": [self.c],
                }, "dependency_cycle", field="predecessor_ids")
                await self.reject(client, "create_subtask", {
                    "project_id": self.task, "name": "Must roll back", "predecessor_ids": [0, self.a],
                }, "dependency_project_exclusive", field="predecessor_ids")

        asyncio.run(run())
        self.assertEqual(self.snapshot(), before)

    def test_storage_and_argument_errors_are_actionable_and_leave_files_untouched(self):
        async def run():
            async with Client(api.mcp) as client:
                await self.reject(client, "file.list", {"task_id": self.empty},
                                  "storage_not_configured", field="file_storage_type")
                await self.reject(client, "file.write", {
                    "task_id": self.task, "path": "../keep.txt", "content": "Bad",
                }, "invalid_path", field="path")
                await self.reject(client, "file.write", {
                    "task_id": self.task, "path": "keep.txt", "content": "%%%", "encoding": "base64",
                }, "invalid_content", field="content")
                await self.reject(client, "file.read", {"task_id": self.task, "path": "missing.txt"},
                                  "file_not_found", field="path")
                await self.reject(client, "file.move", {
                    "task_id": self.task, "source": "missing.txt", "destination": "new.txt",
                }, "file_not_found", field="source")
                await self.reject(client, "file.move", {
                    "task_id": self.task, "source": "keep.txt", "destination": "../new.txt",
                }, "invalid_path", field="destination")
                await self.reject(client, "file.list", {"task_id": self.task, "offset": -1, "limit": 501},
                                  "invalid_pagination", fields=["offset", "limit"])
                await self.reject(client, "get_project", {"project_id": 999999}, "task_not_found", field="project_id")
                await self.reject(client, "get_subtask", {"subtask_id": 999999}, "subtask_not_found", field="subtask_id")
                await self.reject(client, "list_subtasks", {"project_id": 999999}, "task_not_found", field="project_id")
                await self.reject(client, "create_project", {"name": " "}, "empty_name", field="name")
                await self.reject(client, "create_project", {"name": "Invalid", "task_type": "secret-type"},
                                  "invalid_task_type", field="task_type")
                await self.reject(client, "update_project", {"project_id": self.task}, "empty_update")
                await self.reject(client, "assign_self", {}, "missing_target", fields=["task_id", "subtask_id"])
                await self.reject(client, "handoff.delete", {"task_id": self.task, "handoff_id": "123"},
                                  "invalid_handoff_id", field="handoff_id")
                await self.reject(client, "handoff.update", {
                    "task_id": self.task, "handoff_id": "task:123", "content": "x", "subtask_id": self.a,
                }, "invalid_handoff_target", fields=["handoff_id", "subtask_id"])

        asyncio.run(run())
        self.assertEqual(file_storage.local_path(self.task, "keep.txt").read_text(), "original")

    def test_move_errors_identify_the_actual_path_argument_without_data_loss(self):
        files = file_storage.local_path(self.task)
        (files / "existing.txt").write_text("destination content")
        (files / "folder").mkdir()
        (files / "link").symlink_to(self.root, target_is_directory=True)
        before = self.snapshot()

        async def run():
            async with Client(api.mcp) as client:
                for source, destination, code, field in (
                    ("../secret.txt", "new.txt", "invalid_path", "source"),
                    ("keep.txt", "../secret.txt", "invalid_path", "destination"),
                    ("", "new.txt", "path_required", "source"),
                    ("keep.txt", "", "path_required", "destination"),
                    ("link/secret.txt", "new.txt", "invalid_path", "source"),
                    ("keep.txt", "link/new.txt", "invalid_path", "destination"),
                    ("missing.txt", "new.txt", "file_not_found", "source"),
                    ("keep.txt", "existing.txt", "file_exists", "destination"),
                    ("folder", "folder/nested", "invalid_destination", "destination"),
                ):
                    with self.subTest(source=source, destination=destination):
                        payload = await self.reject(client, "file.move", {
                            "task_id": self.task, "source": source, "destination": destination,
                        }, code, field=field)
                        self.assertNotIn(str(self.root), json.dumps(payload))
                # Ein OS-ENOENT beim rename kann beide Seiten betreffen. Dieser
                # Fall darf nicht pauschal als fehlende Quelle ausgegeben werden.
                await self.reject(client, "file.move", {
                    "task_id": self.task, "source": "keep.txt", "destination": "missing-parent/new.txt",
                }, "file_not_found", fields=["source", "destination"])
                with patch.object(file_storage.TaskStorage, "move_item", side_effect=FileNotFoundError("/private/server/path")):
                    payload = await self.reject(client, "file.move", {
                        "task_id": self.task, "source": "keep.txt", "destination": "new.txt",
                    }, "file_not_found", fields=["source", "destination"])
                    self.assertNotIn("/private", json.dumps(payload))

        asyncio.run(run())
        self.assertEqual(self.snapshot(), before)
        self.assertEqual((files / "keep.txt").read_text(), "original")
        self.assertEqual((files / "existing.txt").read_text(), "destination content")
        self.assertFalse((files / "new.txt").exists())

    def test_permission_failures_keep_the_tool_field_and_real_error_flag(self):
        with closing(database.get_db()) as db, db:
            uid = db.execute("INSERT INTO users (username, password_hash) VALUES ('reader', 'unused')").lastrowid
            db.execute("INSERT INTO project_members (project_id, user_id, can_read) VALUES (?, ?, 1)", (self.task, uid))
        token = api.current_mcp_user.set({"id": uid, "is_admin": False, "auth_source": "mcp"})
        self.addCleanup(api.current_mcp_user.reset, token)

        async def run():
            async with Client(api.mcp) as client:
                await self.reject(client, "update_project", {"project_id": self.task, "name": "Denied"},
                                  "permission_denied", field="project_id")
                await self.reject(client, "file.write", {"task_id": self.task, "path": "keep.txt", "content": "Denied"},
                                  "permission_denied", field="task_id")
                with self.assertRaises(ToolError):
                    await client.call_tool("update_project", {"project_id": self.task, "name": "Denied"})

        asyncio.run(run())

    def test_transport_errors_are_safe_and_distinct(self):
        async def run():
            async with Client(api.mcp) as client:
                for error, code in (
                    (httpx.ReadTimeout("private-server-token"), "storage_timeout"),
                    (httpx.ConnectError("private-server-token"), "storage_unavailable"),
                    (OSError("/private/server/path"), "file_operation_failed"),
                ):
                    with patch.object(file_storage.TaskStorage, "get_file", side_effect=error), self.assertLogs(api.logger, level="ERROR"):
                        payload = await self.reject(client, "file.read", {"task_id": self.task, "path": "keep.txt"}, code)
                    self.assertNotIn("private", json.dumps(payload))

        asyncio.run(run())

    def test_unknown_exceptions_are_safe_and_success_schemas_remain_intact(self):
        # Separater Testserver vermeidet zusaetzliche Tools am produktiven Singleton.
        server = FastMCP("errors-test", middleware=[StructuredToolErrors()], strict_input_validation=False)

        @server.tool
        def fail(kind: str) -> list[dict]:
            errors = {
                "runtime": RuntimeError("/private/path bearer-secret"),
                "tool": ToolError("/private/path bearer-secret"),
                "http": HTTPException(500, "database credentials bearer-secret"),
                "framework": FastMCPValidationError("private input bearer-secret"),
                "typed": ApplicationError(409, "Aenderung konkurriert", code="revision_conflict", field="revision"),
            }
            raise errors[kind]

        @server.tool
        def success() -> list[dict]:
            return [{"id": 1}]

        async def run():
            async with Client(server) as client:
                for kind, code in (("runtime", "internal_error"), ("tool", "internal_error"),
                                   ("http", "internal_error"), ("framework", "invalid_arguments"),
                                   ("typed", "revision_conflict")):
                    result = await self.reject(client, "fail", {"kind": kind}, code)
                    self.assertNotIn("private", json.dumps(result))
                    self.assertNotIn("bearer-secret", json.dumps(result))
                await self.reject(client, "missing_tool", {}, "unknown_tool")
                result = await client.call_tool("success", {})
                self.assertFalse(result.is_error)
                self.assertEqual(result.data, [{"id": 1}])
                self.assertEqual(result.structured_content, {"result": [{"id": 1}]})
            async with Client(api.mcp) as client:
                project = await client.call_tool("get_project", {"project_id": self.task})
                self.assertEqual(project.data["id"], self.task)
                subtasks = await client.call_tool("list_subtasks", {"project_id": self.task})
                self.assertEqual({row["id"] for row in subtasks.data}, {self.a, self.b, self.c})

        asyncio.run(run())

    def test_rest_dependency_error_keeps_existing_detail_contract(self):
        app = FastAPI()
        app.include_router(api_tasks.router)
        app.dependency_overrides[get_current_user] = lambda: self.user
        with TestClient(app) as client:
            response = client.put(f"/api/subtasks/{self.a}", json={"predecessor_ids": [self.b]})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"detail": "Zirkulaere Abhaengigkeit nicht erlaubt"})


if __name__ == "__main__":
    unittest.main()
