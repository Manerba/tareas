"""REST-Dateizugriff ueber die Haupt-App mit echten MCP-Tokens und Sessions."""

import json
import logging
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from dashboard import auth, database, file_storage, webdav


class RESTFileAuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Die echte Router- und Middleware-Konfiguration testen, ohne Logdateien
        # oder den produktiven App-Lifespan (DB-Migrationen) zu starten.
        loggers = [logging.getLogger(name) for name in ("", "uvicorn", "uvicorn.access", "uvicorn.error")]
        saved = [(logger, list(logger.handlers), logger.level, logger.propagate) for logger in loggers]

        def restore_logging():
            for logger, handlers, level, propagate in saved:
                logger.handlers[:] = handlers
                logger.setLevel(level)
                logger.propagate = propagate

        cls.addClassCleanup(restore_logging)
        with patch("logging.handlers.RotatingFileHandler", return_value=logging.NullHandler()):
            from dashboard.app import app
        cls.app = app

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="tareas-rest-file-auth-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.patch(database, "DB_DIR", self.root)
        self.patch(database, "DB_PATH", self.root / "test.db")
        self.patch(auth, "SECRET_KEY_PATH", self.root / "secret.key")
        database.init_db()
        self.tokens = {}
        self.token_ids = {}
        self.users = {}
        for name in ("owner", "reader", "editor", "outsider", "assignee", "admin"):
            self.tokens[name], self.token_ids[name] = auth.create_mcp_token(name, 1)
        with closing(database.get_db()) as db, db:
            for name, token_id in self.token_ids.items():
                self.users[name] = db.execute("SELECT user_id FROM mcp_tokens WHERE id = ?", (token_id,)).fetchone()[0]
            db.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (self.users["admin"],))
            self.task_id = db.execute(
                "INSERT INTO tasks (name, task_type, created_by, assigned_to, file_storage_type) VALUES ('Files', 'projekt', ?, ?, 'local')",
                (self.users["owner"], self.users["assignee"]),
            ).lastrowid
            self.other_id = db.execute(
                "INSERT INTO tasks (name, created_by, file_storage_type) VALUES ('Other', 1, 'local')",
            ).lastrowid
            for name, edit in (("reader", 0), ("editor", 1)):
                db.execute(
                    "INSERT INTO project_members (project_id, user_id, can_read, can_edit) VALUES (?, ?, 1, ?)",
                    (self.task_id, self.users[name], edit),
                )
        file_storage.ensure_local_directory(self.task_id)
        file_storage.ensure_local_directory(self.other_id)
        self.files = file_storage.local_path(self.task_id)
        (self.files / "readme.txt").write_text("original")
        self.url = f"/api/tasks/{self.task_id}/files"
        self.client = TestClient(self.app, headers={"X-Requested-With": "XMLHttpRequest"})
        self.addCleanup(self.client.close)

    def patch(self, obj, name, *args, **kwargs):
        patcher = patch.object(obj, name, *args, **kwargs)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def headers(self, name="editor"):
        return {"Authorization": f"Bearer {self.tokens[name]}"}

    def operations(self):
        return (
            ("GET", self.url, {}),
            ("GET", self.url + "/download", {"params": {"path": "readme.txt"}}),
            ("POST", self.url + "/upload", {"files": {"file": ("readme.txt", b"changed", "text/plain")}}),
            ("POST", self.url + "/mkdir", {"json": {"name": "new"}}),
            ("PUT", self.url + "/move", {"json": {"source": "readme.txt", "destination": "moved.txt"}}),
            ("DELETE", self.url, {"params": {"path": "readme.txt"}}),
        )

    def assert_status(self, response, status=200):
        self.assertEqual(response.status_code, status, response.text[:500])
        return response

    def assert_denied(self, headers, status, *, mutations_only=False):
        operations = self.operations()[2:] if mutations_only else self.operations()
        for method, url, kwargs in operations:
            with self.subTest(method=method, url=url):
                self.assert_status(self.client.request(method, url, headers=headers, **kwargs), status)
        self.assertEqual((self.files / "readme.txt").read_text(), "original")
        self.assertEqual([p.name for p in self.files.iterdir()], ["readme.txt"])

    def test_bearer_round_trip_binary_files_and_mcp_audit(self):
        headers = self.headers()
        listing = self.assert_status(self.client.get(self.url, headers=headers)).json()
        self.assertTrue(listing["can_write"])
        self.assertEqual(listing["storage_type"], "local")
        self.assert_status(self.client.post(self.url + "/mkdir", headers=headers, json={"name": "Unterlagen"}))
        content = b"\x00\xff\x80" * 400_000  # REST unterstuetzt auch Dateien ueber dem 1-MiB-MCP-Limit.
        path = "Unterlagen/Grüße.bin"
        self.assert_status(self.client.post(self.url + "/upload", headers=headers, params={"path": "Unterlagen"},
                                            files={"file": ("Grüße.bin", content, "application/octet-stream")}))
        response = self.assert_status(self.client.get(self.url + "/download", headers=headers, params={"path": path}))
        self.assertEqual(response.content, content)
        self.assertEqual(response.headers["content-security-policy"], "sandbox")
        self.assert_status(self.client.put(self.url + "/move", headers=headers,
                                           json={"source": path, "destination": "Unterlagen/moved.bin"}))
        self.assertEqual((self.files / "Unterlagen/moved.bin").read_bytes(), content)
        self.assert_status(self.client.delete(self.url, headers=headers, params={"path": "Unterlagen"}))
        self.assertFalse((self.files / "Unterlagen").exists())
        with closing(database.get_db()) as db:
            logs = [dict(row) for row in db.execute("SELECT * FROM audit_log")]
            last_used = db.execute("SELECT last_used_at FROM mcp_tokens WHERE id = ?", (self.token_ids["editor"],)).fetchone()[0]
        self.assertIsNotNone(last_used)
        self.assertEqual({row["action"] for row in logs}, {"file_upload", "file_mkdir", "file_move", "file_delete"})
        self.assertTrue(all(row["actor_type"] == "mcp" and row["actor_user_id"] == self.users["editor"] for row in logs))
        self.assertNotIn(self.tokens["editor"], json.dumps(logs))
        self.assertEqual(json.loads(next(row["changes_json"] for row in logs if row["action"] == "file_upload")),
                         {"path": path, "storage_type": "local"})

    def test_existing_owner_assignee_admin_and_reader_permissions(self):
        for name in ("owner", "assignee", "admin"):
            with self.subTest(role=name):
                headers = self.headers(name)
                self.assertTrue(self.assert_status(self.client.get(self.url, headers=headers)).json()["can_write"])
                self.assert_status(self.client.post(self.url + "/upload", headers=headers,
                                                    files={"file": ("readme.txt", b"original", "text/plain")}))
        headers = self.headers("reader")
        self.assertFalse(self.assert_status(self.client.get(self.url, headers=headers)).json()["can_write"])
        self.assertEqual(self.assert_status(self.client.get(self.url + "/download", headers=headers,
                                                           params={"path": "readme.txt"})).content, b"original")
        self.assert_denied(headers, 403, mutations_only=True)
        self.assert_denied(self.headers("outsider"), 403)
        self.assert_status(self.client.get(f"/api/tasks/{self.other_id}/files", headers=self.headers("owner")), 403)

    def test_missing_invalid_and_revoked_tokens_on_every_file_operation(self):
        for value in (None, "", "Basic placeholder", "Bearer", "Bearer invalid", f"Bearer {self.tokens['editor']} extra"):
            with self.subTest(authorization=value if value != f"Bearer {self.tokens['editor']} extra" else "malformed"):
                self.assert_denied({"Authorization": value} if value is not None else {}, 401)
        self.assert_status(self.client.get(self.url, headers=self.headers()))
        self.assertTrue(auth.revoke_mcp_token(self.token_ids["editor"]))
        self.assert_denied(self.headers(), 401)
        with closing(database.get_db()) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0], 0)

    def test_kill_switch_and_revoked_project_permissions_apply_immediately(self):
        self.assert_status(self.client.get(self.url, headers=self.headers()))
        auth.set_mcp_enabled(False)
        self.assert_denied(self.headers(), 403)
        auth.set_mcp_enabled(True)
        self.assert_status(self.client.get(self.url, headers=self.headers()))
        with closing(database.get_db()) as db, db:
            db.execute("DELETE FROM project_members WHERE project_id = ? AND user_id = ?", (self.task_id, self.users["editor"]))
        self.assert_denied(self.headers(), 403)

    def test_explicit_bearer_never_falls_back_to_privileged_cookie(self):
        self.client.cookies.set(auth.COOKIE_NAME, auth.sign_token(auth.create_session(1)))
        self.assertTrue(self.assert_status(self.client.get(self.url)).json()["can_write"])
        self.assertFalse(self.assert_status(self.client.get(self.url, headers=self.headers("reader"))).json()["can_write"])
        self.assert_denied(self.headers("reader"), 403, mutations_only=True)
        self.assert_denied(self.headers("outsider"), 403)
        for value in ("", "Basic placeholder", "Bearer invalid"):
            self.assert_denied({"Authorization": value}, 401)
        auth.revoke_mcp_token(self.token_ids["editor"])
        self.assert_denied(self.headers(), 401)
        auth.set_mcp_enabled(False)
        self.assert_denied(self.headers("owner"), 403)
        self.assert_status(self.client.get(self.url))

    def test_cookie_access_and_csrf_for_both_authentication_methods(self):
        self.client.headers.pop("X-Requested-With")
        for mode in ("bearer", "cookie"):
            with self.subTest(mode=mode):
                headers = self.headers() if mode == "bearer" else {}
                if mode == "cookie":
                    self.client.cookies.set(auth.COOKIE_NAME, auth.sign_token(auth.create_session(1)))
                    auth.set_mcp_enabled(False)
                self.assert_status(self.client.get(self.url, headers=headers))
                self.assert_denied(headers, 403, mutations_only=True)
                self.assert_status(self.client.post(self.url + "/upload",
                                                    headers={**headers, "X-Requested-With": "XMLHttpRequest"},
                                                    files={"file": ("readme.txt", b"original", "text/plain")}))

    def test_bearer_scope_is_limited_to_file_operations(self):
        for path in ("/api/tasks", "/api/dashboard/info", "/api/nextcloud/directories", "/api/nextcloud/status",
                     self.url + "/edit?path=readme.txt"):
            with self.subTest(path=path):
                self.assert_status(self.client.get(path, headers=self.headers("admin")), 401)
        # Auch ein echter Session-Token im Bearer-Header ist kein MCP-Token.
        self.assert_status(self.client.get(self.url, headers={"Authorization": f"Bearer {auth.create_session(1)}"}), 401)
        self.assert_status(self.client.get(self.url, params={"token": self.tokens["editor"]}), 401)

    def test_bearer_scheme_and_path_validation(self):
        self.assert_status(self.client.get(self.url, headers={"Authorization": f"bEaReR {self.tokens['editor']}"}))
        for method, url, kwargs in (
            ("GET", self.url + "/download", {"params": {"path": "../other/readme.txt"}}),
            ("POST", self.url + "/upload", {"params": {"path": "../other"}, "files": {"file": ("bad.txt", b"bad")}}),
            ("DELETE", self.url, {"params": {"path": ""}}),
        ):
            self.assert_status(self.client.request(method, url, headers=self.headers(), **kwargs), 400)
        self.assertEqual((self.files / "readme.txt").read_text(), "original")

    def test_webdav_uses_the_same_token_and_project_rights(self):
        with closing(database.get_db()) as db, db:
            db.execute("UPDATE tasks SET file_storage_type = 'webdav', nextcloud_path = 'projects/files' WHERE id = ?", (self.task_id,))
        listing = self.patch(webdav, "list_directory", return_value=[{"name": "remote.txt", "type": "file", "size": 6}])
        self.patch(webdav, "_get_config", return_value=None)
        download = self.patch(webdav, "get_file", return_value=(b"remote", "text/plain"))
        upload = self.patch(webdav, "upload_file")
        self.assertEqual(self.assert_status(self.client.get(self.url, headers=self.headers("reader"))).json()["storage_type"], "webdav")
        listing.assert_called_once_with("projects/files")
        self.assertEqual(self.assert_status(self.client.get(self.url + "/download", headers=self.headers("reader"),
                                                           params={"path": "remote.txt"})).content, b"remote")
        download.assert_called_once_with("projects/files/remote.txt")
        self.assert_status(self.client.post(self.url + "/upload", headers=self.headers("reader"),
                                            files={"file": ("remote.txt", b"changed", "text/plain")}), 403)
        upload.assert_not_called()
        self.assert_status(self.client.post(self.url + "/upload", headers=self.headers(),
                                            files={"file": ("remote.txt", b"changed", "text/plain")}))
        upload.assert_called_once_with("projects/files/remote.txt", b"changed", "text/plain")


if __name__ == "__main__":
    unittest.main()
