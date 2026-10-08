"""Kompakte MCP-Projektseiten mit echten DB-Abfragen und MCP-Aufrufen."""

import asyncio
import json
import tempfile
import unittest
from contextlib import closing, contextmanager
from pathlib import Path
from unittest.mock import patch

from fastmcp import Client
from fastmcp.exceptions import ToolError

from dashboard import database, mcp_server as api
from dashboard.api_admin_mcp import MCP_TOOLS


class MCPProjectPaginationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="tareas-project-pagination-")
        self.addCleanup(temp.cleanup)
        for name, value in {
            "DB_DIR": Path(temp.name),
            "DB_PATH": Path(temp.name) / "test.db",
        }.items():
            patcher = patch.object(database, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        database.init_db()
        self.users = {}
        with closing(database.get_db()) as db, db:
            for name in ("owner", "outsider"):
                user_id = db.execute(
                    "INSERT INTO users (username, password_hash, auth_source) VALUES (?, 'unused', 'mcp')",
                    (name,),
                ).lastrowid
                self.users[name] = {"id": user_id, "username": name, "is_admin": False, "auth_source": "mcp"}
        token = api.current_mcp_user.set(self.users["owner"])
        self.addCleanup(api.current_mcp_user.reset, token)

    def task(self, name="Project", *, creator="owner", task_type="projekt", status="offen",
             description="Project details", priority=50, created_at="2026-10-08 10:00:00",
             assigned_to=None, progress=(), file_storage_type="none", nextcloud_path=None):
        with closing(database.get_db()) as db, db:
            task_id = db.execute(
                """INSERT INTO tasks
                   (name, task_type, status, description, description_format, priority, created_at,
                    created_by, assigned_to, file_storage_type, nextcloud_path)
                   VALUES (?, ?, ?, ?, 'markdown', ?, ?, ?, ?, ?, ?)""",
                (name, task_type, status, description, priority, created_at,
                 self.users[creator]["id"] if creator else None, assigned_to,
                 file_storage_type, nextcloud_path),
            ).lastrowid
            db.executemany(
                "INSERT INTO sub_tasks (project_id, name, status_percent) VALUES (?, ?, ?)",
                [(task_id, f"Subtask {number}", percent) for number, percent in enumerate(progress)],
            )
        return task_id

    def test_pages_cover_all_projects_in_deterministic_order(self):
        tied = [self.task(f"Tied {number}") for number in range(5)]
        high = self.task("High", priority=100, created_at="2000-01-01 00:00:00")
        low = self.task("Low", priority=0, created_at="2030-01-01 00:00:00")
        newer = self.task("Newer", created_at="2026-10-08 10:01:00")
        expected = [high, newer, *reversed(tied), low]
        offset, seen = 0, []
        while offset is not None:
            page = api.list_projects_page(offset=offset, limit=3)
            self.assertEqual((page["total"], page["offset"], page["limit"]), (8, offset, 3))
            self.assertLessEqual(len(page["items"]), 3)
            seen.extend(item["id"] for item in page["items"])
            offset = page["next_offset"]
        self.assertEqual(seen, expected)
        self.assertEqual([item["id"] for item in api.list_projects()], expected)

    def test_empty_and_out_of_range_pages_keep_metadata(self):
        self.assertEqual(api.list_projects_page(), {
            "items": [], "total": 0, "offset": 0, "limit": 50, "next_offset": None,
        })
        self.task()
        for offset in (1, 20, 2 ** 80):
            with self.subTest(offset=offset):
                self.assertEqual(api.list_projects_page(offset=offset, limit=500), {
                    "items": [], "total": 1, "offset": offset, "limit": 500, "next_offset": None,
                })
        self.assertEqual(len(api.list_projects_page(limit=1)["items"]), 1)

    def test_runtime_rejects_invalid_bounds_and_status(self):
        for field, values in (
            ("offset", (-1, 1.0, 1.5, True, False, "1", None)),
            ("limit", (0, -1, 501, 1.0, 1.5, True, False, "1", None)),
        ):
            for value in values:
                with self.subTest(field=field, value=value), self.assertRaises(ToolError) as caught:
                    api.list_projects_page(**{field: value})
                self.assertEqual(caught.exception.code, "invalid_pagination")
                self.assertEqual(caught.exception.field, field)
        for status in ("", "unknown", "OFFEN", 0, True):
            for operation in (api.list_projects, api.list_projects_page):
                with self.subTest(tool=operation.__name__, status=status), self.assertRaises(ToolError):
                    operation(status=status)

    def test_visibility_and_total_follow_the_existing_access_rules(self):
        visible = {self.task("Owned"), self.task("Legacy", creator=None)}
        visible.add(self.task("Assigned", creator="outsider", assigned_to=self.users["owner"]["id"]))
        for permission in ("can_read", "can_edit", "can_create"):
            shared = self.task(permission, creator="outsider")
            with closing(database.get_db()) as db, db:
                db.execute(
                    f"INSERT INTO project_members (project_id, user_id, {permission}) VALUES (?, ?, 1)",
                    (shared, self.users["owner"]["id"]),
                )
            visible.add(shared)
        hidden = self.task("Hidden", creator="outsider")
        denied = self.task("No permissions", creator="outsider")
        create_only_task = self.task("Task sharing cannot grant create access", creator="outsider", task_type="aufgabe")
        with closing(database.get_db()) as db, db:
            db.execute(
                "INSERT INTO project_members (project_id, user_id, can_read, can_edit, can_create) VALUES (?, ?, 0, 0, 0)",
                (denied, self.users["owner"]["id"]),
            )
            db.execute(
                "INSERT INTO project_members (project_id, user_id, can_read, can_edit, can_create) VALUES (?, ?, 0, 0, 1)",
                (create_only_task, self.users["owner"]["id"]),
            )
            # A subtask assignment must not expose the otherwise hidden parent.
            db.execute(
                "INSERT INTO sub_tasks (project_id, name, assigned_to) VALUES (?, 'Assigned child', ?)",
                (hidden, self.users["owner"]["id"]),
            )
        pages = [api.list_projects_page(offset=offset, limit=2) for offset in range(0, len(visible), 2)]
        self.assertTrue(all(page["total"] == len(visible) for page in pages))
        self.assertEqual({item["id"] for page in pages for item in page["items"]}, visible)
        admin_token = api.current_mcp_user.set({"id": 1, "is_admin": True})
        try:
            self.assertEqual(api.list_projects_page()["total"], len(visible) + 3)
        finally:
            api.current_mcp_user.reset(admin_token)

    def test_effective_status_is_filtered_before_pagination(self):
        completed = [self.task("Complete", progress=(100, 100)) for _ in range(3)]
        active = self.task("Active", progress=(100, 0))
        open_project = self.task("Stored as completed but empty", status="erledigt")
        cancelled = self.task("Cancelled", status="abgebrochen", progress=(100, 100))
        manual = self.task("Manual task", task_type="aufgabe", status="erledigt")
        self.task("Hidden complete", creator="outsider", progress=(100, 100))
        expected = {
            "erledigt": [manual, *reversed(completed)], "in_arbeit": [active],
            "offen": [open_project], "abgebrochen": [cancelled],
        }
        for status, task_ids in expected.items():
            seen = []
            for offset in range(len(task_ids)):
                page = api.list_projects_page(status=status, offset=offset, limit=1)
                self.assertEqual(page["total"], len(task_ids))
                self.assertEqual(page["items"][0]["status"], status)
                seen.append(page["items"][0]["id"])
            self.assertEqual(seen, task_ids)

    def test_compact_pages_omit_descriptions_and_legacy_and_detail_keep_them(self):
        description = "Large project specification.\n" * 4000
        ids = [self.task(f"Project {number}", description=description) for number in range(3)]
        compact = api.list_projects_page()
        detailed = api.list_projects_page(include_description=True)
        legacy = api.list_projects()
        self.assertIsInstance(legacy, list)
        self.assertEqual(detailed["items"], legacy)
        for compact_item, full_item in zip(compact["items"], detailed["items"]):
            self.assertNotIn("description", compact_item)
            self.assertNotIn("description_format", compact_item)
            self.assertEqual(full_item["description"], description)
            self.assertEqual(full_item["description_format"], "markdown")
            self.assertEqual(compact_item, {
                key: value for key, value in full_item.items()
                if key not in ("description", "description_format")
            })
        detail = api.get_project(ids[0])
        self.assertEqual(detail["description"], description)
        self.assertEqual(detail["subtasks"], [])
        self.assertLess(len(json.dumps(compact)), len(json.dumps(detailed)) // 100)

    def test_compact_select_returns_no_description_columns(self):
        self.task(description="Never selected in compact mode")
        connection_factory = api.db_query
        selected_columns = []

        class ObservedConnection:
            def __init__(self, connection):
                self.connection = connection

            def execute(self, sql, parameters=()):
                cursor = self.connection.execute(sql, parameters)
                if " LIMIT ? OFFSET ?" in sql:
                    selected_columns.append({column[0] for column in cursor.description})
                return cursor

        @contextmanager
        def observed_query():
            with connection_factory() as connection:
                yield ObservedConnection(connection)

        with patch.object(api, "db_query", observed_query):
            api.list_projects_page()
            api.list_projects_page(include_description=True)
        self.assertEqual(len(selected_columns), 2)
        self.assertNotIn("description", selected_columns[0])
        self.assertNotIn("description_format", selected_columns[0])
        self.assertIn("description", selected_columns[1])
        self.assertIn("description_format", selected_columns[1])

    def test_total_and_items_share_one_snapshot_during_concurrent_deletion(self):
        first = self.task("First")
        second = self.task("Second")
        connection_factory = api.db_query
        changed = []

        class ConcurrentConnection:
            def __init__(self, connection):
                self.connection = connection

            def execute(self, sql, parameters=()):
                cursor = self.connection.execute(sql, parameters)
                if "SELECT COUNT(*) FROM tasks_with_status" in sql:
                    with closing(database.get_db()) as writer, writer:
                        writer.execute("DELETE FROM tasks WHERE id = ?", (second,))
                    changed.append(second)
                return cursor

        @contextmanager
        def concurrent_query():
            with connection_factory() as connection:
                yield ConcurrentConnection(connection)

        with patch.object(api, "db_query", concurrent_query):
            snapshot = api.list_projects_page()
        self.assertEqual(changed, [second])
        self.assertEqual(snapshot["total"], 2)
        self.assertEqual([item["id"] for item in snapshot["items"]], [second, first])
        self.assertEqual(api.list_projects_page()["total"], 1)

    def test_file_storage_metadata_uses_legacy_webdav_compatibility(self):
        for kind, path, expected in (("local", None, "local"), ("none", None, "none"),
                                     ("none", "old/webdav/path", "webdav")):
            self.task(kind, file_storage_type=kind, nextcloud_path=path)
            item = api.list_projects_page(limit=1)["items"][0]
            self.assertEqual(item["file_storage_type"], expected)

    def test_actual_mcp_schema_and_calls(self):
        first = self.task("First", description="Keep these details")
        second = self.task("Second")

        async def check():
            async with Client(api.mcp) as client:
                tools = {tool.name: tool for tool in await client.list_tools()}
                self.assertEqual(set(tools), set(MCP_TOOLS))
                tool = tools["list_projects_page"]
                self.assertTrue(tool.annotations.readOnlyHint)
                properties = tool.inputSchema["properties"]
                self.assertEqual(properties["offset"]["type"], "integer")
                self.assertEqual(properties["offset"]["minimum"], 0)
                self.assertEqual(properties["limit"]["minimum"], 1)
                self.assertEqual(properties["limit"]["maximum"], 500)
                self.assertEqual(properties["limit"]["default"], 50)
                self.assertFalse(properties["include_description"]["default"])
                status_type = next(part for part in properties["status"]["anyOf"] if "enum" in part)
                self.assertEqual(set(status_type["enum"]), {"offen", "in_arbeit", "erledigt", "abgebrochen"})
                self.assertEqual(tools["list_projects"].inputSchema["properties"]["status"], properties["status"])
                page = await client.call_tool("list_projects_page", {"limit": 1})
                self.assertEqual(page.data["total"], 2)
                self.assertEqual(page.data["next_offset"], 1)
                self.assertEqual(page.data["items"][0]["id"], second)
                self.assertNotIn("description", page.data["items"][0])
                page = await client.call_tool("list_projects_page", {"offset": 1, "include_description": True})
                self.assertIsNone(page.data["next_offset"])
                self.assertEqual(page.data["items"][0]["id"], first)
                self.assertEqual(page.data["items"][0]["description"], "Keep these details")
                legacy = await client.call_tool("list_projects", {})
                self.assertEqual(len(legacy.data), 2)
                self.assertEqual(legacy.data[1]["description"], "Keep these details")
                legacy_error = await client.call_tool("list_projects", {"status": "unknown"}, raise_on_error=False)
                self.assertTrue(legacy_error.is_error)
                for arguments in ({"offset": -1}, {"offset": 1.5}, {"offset": True}, {"offset": "1"},
                                  {"limit": 0}, {"limit": 501}, {"limit": 1.0}, {"status": "invalid"}):
                    with self.subTest(arguments=arguments):
                        result = await client.call_tool("list_projects_page", arguments, raise_on_error=False)
                        self.assertTrue(result.is_error)

        asyncio.run(check())


if __name__ == "__main__":
    unittest.main()
