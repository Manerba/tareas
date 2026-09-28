"""MCP-Dateiwerkzeuge mit isolierter Ablage, echten MCP-Aufrufen und WebDAV-Mocks."""

import asyncio
import base64
import json
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import httpx
from fastmcp import Client
from fastmcp.exceptions import ToolError

from dashboard import database, file_storage, mcp_server as api, webdav
from dashboard.api_admin_mcp import MCP_TOOLS


class MCPFileTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="tareas-mcp-files-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for name, value in {"DB_DIR": self.root, "DB_PATH": self.root / "test.db"}.items():
            self.patch(database, name, value)
        database.init_db()
        self.users = {"admin": {"id": 1, "is_admin": True, "auth_source": "mcp"}}
        with closing(database.get_db()) as db, db:
            for name in ("owner", "reader", "editor", "outsider", "assignee"):
                uid = db.execute("INSERT INTO users (username, password_hash, auth_source) VALUES (?, 'unused', 'mcp')", (name,)).lastrowid
                self.users[name] = {"id": uid, "is_admin": False, "auth_source": "mcp"}
            self.task = db.execute(
                "INSERT INTO tasks (name, task_type, created_by, assigned_to, file_storage_type) VALUES ('Files', 'projekt', ?, ?, 'local')",
                (self.users["owner"]["id"], self.users["assignee"]["id"]),
            ).lastrowid
            self.other = db.execute(
                "INSERT INTO tasks (name, created_by, file_storage_type) VALUES ('Other', ?, 'local')",
                (self.users["outsider"]["id"],),
            ).lastrowid
            for name, edit in (("reader", 0), ("editor", 1)):
                db.execute("INSERT INTO project_members (project_id, user_id, can_read, can_edit) VALUES (?, ?, 1, ?)",
                           (self.task, self.users[name]["id"], edit))
        file_storage.ensure_local_directory(self.task)
        file_storage.ensure_local_directory(self.other)
        self.files = file_storage.local_path(self.task)
        self.other_files = file_storage.local_path(self.other)
        self.token = api.current_mcp_user.set(self.users["owner"])
        self.addCleanup(api.current_mcp_user.reset, self.token)

    def patch(self, obj, name, *args, **kwargs):
        patcher = patch.object(obj, name, *args, **kwargs)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def login(self, name):
        api.current_mcp_user.set(self.users[name])

    def test_local_text_binary_empty_files_and_audit(self):
        api.file_mkdir(self.task, "Unterlagen")
        path = "Unterlagen/Grüße 100% fertig's.md"
        api.file_write(self.task, path, "# Planung\nÄnderungen\n")
        self.assertEqual(api.file_read(self.task, path)["content"], "# Planung\nÄnderungen\n")
        api.file_write(self.task, path, "# Aktualisiert")
        self.assertEqual((self.files / path).read_text(), "# Aktualisiert")
        raw = b"\x00\xff\x80\x01binary"
        api.file_write(self.task, "binary.bin", base64.b64encode(raw).decode(), "base64")
        result = api.file_read(self.task, "binary.bin")
        self.assertEqual(result["encoding"], "base64")
        self.assertEqual(base64.b64decode(result["content"]), raw)
        with self.assertRaisesRegex(ToolError, "UTF-8"):
            api.file_read(self.task, "binary.bin", "utf-8")
        api.file_write(self.task, "empty.txt", "")
        self.assertEqual(api.file_read(self.task, "empty.txt")["content"], "")
        api.file_move(self.task, path, "renamed.md")
        self.assertFalse((self.files / path).exists())
        api.file_write(self.task, "Unterlagen/child.txt", "private file body")
        api.file_delete(self.task, "Unterlagen")
        self.assertFalse((self.files / "Unterlagen").exists())
        self.assertEqual(api.get_project(self.task)["file_storage_type"], "local")
        with closing(database.get_db()) as db:
            logs = [dict(row) for row in db.execute("SELECT * FROM audit_log")]
        self.assertTrue({"file_upload", "file_move", "file_delete", "file_mkdir"} <= {r["action"] for r in logs})
        self.assertTrue(all(r["actor_type"] == "mcp" and r["actor_user_id"] == self.users["owner"]["id"] for r in logs))
        self.assertNotIn("private file body", json.dumps(logs))

    def test_readers_outsiders_admins_assignees_and_revocation(self):
        api.file_write(self.task, "readme.txt", "contents")
        for name in ("owner", "editor", "admin", "assignee"):
            self.login(name)
            self.assertTrue(api.file_list(self.task)["can_write"])
            api.file_write(self.task, "writer.txt", name)
        self.login("reader")
        self.assertFalse(api.file_list(self.task)["can_write"])
        self.assertEqual(api.file_read(self.task, "readme.txt")["content"], "contents")
        mutations = (
            lambda: api.file_write(self.task, "readme.txt", "forbidden"),
            lambda: api.file_mkdir(self.task, "forbidden"),
            lambda: api.file_move(self.task, "readme.txt", "renamed.txt"),
            lambda: api.file_delete(self.task, "readme.txt"),
        )
        for operation in mutations:
            with self.assertRaises(ToolError):
                operation()
        self.login("outsider")
        for operation in (*mutations, lambda: api.file_list(self.task), lambda: api.file_read(self.task, "readme.txt")):
            with self.assertRaises(ToolError):
                operation()
        self.login("editor")
        with closing(database.get_db()) as db, db:
            db.execute("DELETE FROM project_members WHERE project_id = ? AND user_id = ?", (self.task, self.users["editor"]["id"]))
        for operation in (*mutations, lambda: api.file_list(self.task), lambda: api.file_read(self.task, "readme.txt")):
            with self.assertRaises(ToolError):
                operation()
        self.assertEqual((self.files / "readme.txt").read_text(), "contents")

    def test_paths_symlinks_roots_and_cross_project_access(self):
        (self.other_files / "secret.txt").write_text("other project")
        (self.files / "link").symlink_to(self.other_files, target_is_directory=True)
        api.file_write(self.task, "safe.txt", "original")
        invalid = ("../secret.txt", "/etc/passwd", "..\\secret.txt", "%252e%252e/secret.txt", "bad\x00name", "link/secret.txt")
        for path in invalid:
            for operation in (
                lambda: api.file_list(self.task, path), lambda: api.file_read(self.task, path),
                lambda: api.file_write(self.task, path, "forbidden"), lambda: api.file_mkdir(self.task, path),
                lambda: api.file_move(self.task, "safe.txt", path), lambda: api.file_move(self.task, path, "moved.txt"),
                lambda: api.file_delete(self.task, path),
            ):
                with self.subTest(path=path), self.assertRaises(ToolError):
                    operation()
        for path in ("", ".", "./"):
            for operation in (lambda: api.file_delete(self.task, path), lambda: api.file_write(self.task, path, ""),
                              lambda: api.file_mkdir(self.task, path), lambda: api.file_move(self.task, "safe.txt", path)):
                with self.assertRaises(ToolError):
                    operation()
        with self.assertRaises(ToolError):
            api.file_read(self.other, "secret.txt")
        self.assertNotIn("link", [item["name"] for item in api.file_list(self.task)["items"]])
        self.assertEqual((self.other_files / "secret.txt").read_text(), "other project")
        self.assertEqual((self.files / "safe.txt").read_text(), "original")

    def test_limits_invalid_encoding_missing_storage_and_safe_errors(self):
        api.file_write(self.task, "file.txt", "keep")
        with patch.object(api, "MCP_FILE_MAX_BYTES", 8):
            for content, encoding in (("123456789", "utf-8"), ("€€€", "utf-8"),
                                      (base64.b64encode(b"123456789").decode(), "base64"), ("%%%", "base64")):
                with self.assertRaises(ToolError):
                    api.file_write(self.task, "file.txt", content, encoding)
            (self.files / "big.txt").write_bytes(b"x" * 9)
            with self.assertRaisesRegex(ToolError, "gross"):
                api.file_read(self.task, "big.txt")
            api.file_write(self.task, "eight.txt", "12345678")
            self.assertEqual(api.file_read(self.task, "eight.txt")["size"], 8)
        self.assertEqual((self.files / "file.txt").read_text(), "keep")
        with self.assertRaisesRegex(ToolError, "nicht gefunden"):
            api.file_read(self.task, "missing.txt")
        with self.assertRaisesRegex(ToolError, "existiert"):
            api.file_move(self.task, "eight.txt", "file.txt")
        with patch.object(file_storage.TaskStorage, "upload_file", side_effect=OSError("/internal/private/path")):
            with self.assertLogs(api.logger, level="ERROR"), self.assertRaises(ToolError) as error:
                api.file_write(self.task, "file.txt", "test")
            self.assertNotIn("/internal", str(error.exception))
        with closing(database.get_db()) as db, db:
            db.execute("UPDATE tasks SET file_storage_type = 'none' WHERE id = ?", (self.task,))
        with self.assertRaisesRegex(ToolError, "Keine Dateiablage"):
            api.file_list(self.task)

    def test_pagination_and_actual_mcp_tool_registration_and_calls(self):
        for index in range(5):
            api.file_write(self.task, f"{index}.txt", str(index))
        result = api.file_list(self.task, limit=2)
        self.assertEqual((result["total"], result["next_offset"]), (5, 2))
        self.assertEqual([r["path"] for r in api.file_list(self.task, offset=4, limit=2)["items"]], ["4.txt"])
        self.assertIsNone(api.file_list(self.task, offset=4, limit=2)["next_offset"])
        for args in ({"offset": -1}, {"limit": 0}, {"limit": 501}):
            with self.assertRaises(ToolError):
                api.file_list(self.task, **args)

        async def protocol_check():
            async with Client(api.mcp) as client:
                tools = {tool.name: tool for tool in await client.list_tools()}
                self.assertEqual(set(tools), set(MCP_TOOLS))
                self.assertTrue({"file.list", "file.read", "file.write", "file.mkdir", "file.move", "file.delete"} <= tools.keys())
                self.assertTrue(tools["file.read"].annotations.readOnlyHint)
                self.assertTrue(tools["file.delete"].annotations.destructiveHint)
                await client.call_tool("file.write", {"task_id": self.task, "path": "mcp.txt", "content": "via MCP"})
                result = await client.call_tool("file.read", {"task_id": self.task, "path": "mcp.txt"})
                self.assertEqual(result.data["content"], "via MCP")
                denied = await client.call_tool("file.read", {"task_id": self.other, "path": "x"}, raise_on_error=False)
                self.assertTrue(denied.is_error)
        asyncio.run(protocol_check())

    def test_webdav_operations_stay_in_project_directory_and_close_streams(self):
        with closing(database.get_db()) as db, db:
            db.execute("UPDATE tasks SET file_storage_type = 'webdav', nextcloud_path = 'projects/files' WHERE id = ?", (self.task,))
        operations = []
        clients = []
        real_client = httpx.Client

        def handle(request):
            operations.append(request)
            self.assertTrue(request.url.path.startswith("/remote.php/dav/files/test/projects/files/"))
            if request.method == "GET":
                if request.url.path.endswith("missing.txt"):
                    return httpx.Response(404)
                return httpx.Response(200, content=b"remote content", headers={"Content-Type": "text/plain"})
            return httpx.Response(201 if request.method in ("PUT", "MKCOL") else 204)

        def client_factory(**kwargs):
            client = real_client(transport=httpx.MockTransport(handle), **kwargs)
            clients.append(client)
            return client

        self.patch(webdav, "_get_config", return_value={"server_url": "https://dav.example.test", "username": "test",
                   "password": "test-placeholder", "base_path": "", "verify": True})
        self.patch(webdav.httpx, "Client", side_effect=client_factory)
        with patch.object(webdav, "list_directory", return_value=[{"name": "remote.txt", "type": "file", "size": 14}]) as listing:
            self.assertEqual(api.file_list(self.task)["items"][0]["path"], "remote.txt")
            listing.assert_called_once_with("projects/files")
        self.assertEqual(api.file_read(self.task, "remote.txt")["content"], "remote content")
        api.file_write(self.task, "remote.txt", "updated")
        api.file_mkdir(self.task, "folder")
        api.file_move(self.task, "remote.txt", "folder/moved.txt")
        api.file_delete(self.task, "folder/moved.txt")
        move = next(r for r in operations if r.method == "MOVE")
        self.assertEqual(move.headers["Destination"], "https://dav.example.test/remote.php/dav/files/test/projects/files/folder/moved.txt")
        self.assertEqual(move.headers["Overwrite"], "F")
        with patch.object(api, "MCP_FILE_MAX_BYTES", 4), self.assertRaisesRegex(ToolError, "gross"):
            api.file_read(self.task, "remote.txt")
        with self.assertRaisesRegex(ToolError, "nicht gefunden"):
            api.file_read(self.task, "missing.txt")
        with patch.object(real_client, "send", side_effect=httpx.ConnectError("unavailable")):
            with self.assertLogs(api.logger, level="ERROR"), self.assertRaises(ToolError):
                api.file_read(self.task, "remote.txt")
        self.assertTrue(all(client.is_closed for client in clients))


if __name__ == "__main__":
    unittest.main()
