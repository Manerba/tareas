"""Dateiablagen mit temporaerer Datenbank/Dateien und gemocktem WebDAV/Office."""

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import AsyncMock, patch
from urllib.parse import quote

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dashboard import api_nextcloud, api_onlyoffice, api_tasks, database, file_storage, webdav
from dashboard.auth import get_admin_user, get_current_user, get_file_user


class FileStorageTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="tareas-files-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for name, value in {"DB_DIR": self.root, "DB_PATH": self.root / "test.db"}.items():
            self.patch(database, name, value)
        database.init_db()
        self.users = {"admin": {"id": 1, "username": "admin", "is_admin": True}}
        with closing(database.get_db()) as db, db:
            for name in ("owner", "reader", "editor", "assignee", "outsider"):
                uid = db.execute("INSERT INTO users (username, password_hash) VALUES (?, 'unused')", (name,)).lastrowid
                self.users[name] = {"id": uid, "username": name, "is_admin": False}
            self.task_id = db.execute(
                "INSERT INTO tasks (name, task_type, created_by, assigned_to) VALUES ('Project', 'projekt', ?, ?)",
                (self.users["owner"]["id"], self.users["assignee"]["id"]),
            ).lastrowid
            self.other_id = db.execute(
                "INSERT INTO tasks (name, created_by) VALUES ('Task', ?)", (self.users["outsider"]["id"],),
            ).lastrowid
            for name, edit in (("reader", 0), ("editor", 1)):
                db.execute(
                    "INSERT INTO project_members (project_id, user_id, can_read, can_edit) VALUES (?, ?, 1, ?)",
                    (self.task_id, self.users[name]["id"], edit),
                )
        self.user = self.users["owner"]
        app = FastAPI()
        for router in (api_tasks.router, api_nextcloud.files_router, api_nextcloud.admin_router,
                       api_onlyoffice.editor_router, api_onlyoffice.wopi_router):
            app.include_router(router)
        app.dependency_overrides[get_current_user] = lambda: self.user
        app.dependency_overrides[get_file_user] = lambda: self.user
        app.dependency_overrides[get_admin_user] = lambda: self.users["admin"]
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.patch(api_tasks, "notify_event", return_value=None)
        self.patch(api_onlyoffice, "_get_onlyoffice_config", return_value={
            "server_url": "https://office.example.test", "jwt_secret": "test-secret", "verify": True,
        })
        self.url = f"/api/tasks/{self.task_id}"

    def patch(self, obj, name, *args, **kwargs):
        patcher = patch.object(obj, name, *args, **kwargs)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def ok(self, method, path, **kwargs):
        response = self.client.request(method, path, **kwargs)
        self.assertEqual(response.status_code, 200, response.text)
        return response

    def assign(self, kind="local", **fields):
        return self.ok("PUT", self.url, json={"file_storage_type": kind, **fields})

    def upload(self, name="Plan.txt", content=b"original", path=""):
        return self.ok("POST", self.url + "/files/upload", params={"path": path},
                       files={"file": (name, content, "text/plain")})

    def row(self):
        items = self.ok("GET", "/api/tasks").json()["items"]
        return next(item for item in items if item["id"] == self.task_id)

    def open_editor(self, path="Plan.txt"):
        return self.ok("GET", self.url + "/files/edit", params={"path": path}).json()

    def test_local_round_trip_without_nextcloud(self):
        self.assertFalse(self.ok("GET", "/api/nextcloud/status").json()["configured"])
        self.assign()
        root = self.root / "files" / f"task-{self.task_id}"
        self.assertTrue(root.is_dir())
        self.assertEqual(root.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.row()["file_storage_type"], "local")
        self.assertEqual(self.row()["nextcloud_path"], "")
        self.ok("POST", self.url + "/files/mkdir", json={"name": "Unterlagen"})
        filename = "Grüße 100% fertig's.txt"
        self.upload(filename, b"example", "Unterlagen")
        self.upload("root.txt")
        listing = self.ok("GET", self.url + "/files").json()
        self.assertEqual([item["name"] for item in listing["items"]], ["Unterlagen", "root.txt"])
        self.assertTrue(listing["can_write"])
        self.assertEqual(listing["storage_type"], "local")
        file_path = "Unterlagen/" + filename
        response = self.ok("GET", self.url + "/files/download", params={"path": file_path, "inline": True})
        self.assertEqual(response.content, b"example")
        self.assertIn(quote(filename, safe=""), response.headers["content-disposition"])
        self.assertEqual(response.headers["content-security-policy"], "sandbox")
        self.ok("PUT", self.url + "/files/move", json={"source": file_path, "destination": "renamed.txt"})
        self.assertEqual((root / "renamed.txt").read_bytes(), b"example")
        self.upload("nested.txt", path="Unterlagen")
        self.ok("DELETE", self.url + "/files", params={"path": "Unterlagen"})
        self.assertFalse((root / "Unterlagen").exists())
        with closing(database.get_db()) as db:
            actions = {row[0] for row in db.execute("SELECT action FROM audit_log")}
        self.assertTrue({"file_upload", "file_mkdir", "file_move", "file_delete"} <= actions)

    def test_assignment_switch_preserves_files_and_revokes_office_tokens(self):
        self.assign()
        self.upload()
        editor = self.open_editor()
        self.assign("webdav", nextcloud_path="projects/example")
        self.assertEqual(self.row()["file_storage_type"], "webdav")
        self.assertIsNone(api_onlyoffice._validate_wopi_token(editor["access_token"]))
        with patch.object(api_tasks, "remove_local_directory") as remove_local, patch.object(webdav, "delete_item") as delete_remote:
            self.assign("none")
            remove_local.assert_not_called()
            delete_remote.assert_not_called()
        self.assertEqual(self.client.get(self.url + "/files").status_code, 400)
        self.assign()
        self.assertEqual(self.ok("GET", self.url + "/files/download", params={"path": "Plan.txt"}).content, b"original")
        self.ok("PUT", self.url, json={"name": "Renamed"})
        self.assertTrue((self.root / "files" / f"task-{self.task_id}" / "Plan.txt").exists())

    def test_removing_local_storage_deletes_only_its_directory_and_revokes_tokens(self):
        self.assign()
        self.upload()
        self.ok("POST", self.url + "/files/mkdir", json={"name": "nested"})
        self.upload("child.txt", path="nested")
        root = self.root / "files" / f"task-{self.task_id}"
        (root / ".hidden").write_text("remove me")
        file_storage.ensure_local_directory(self.other_id)
        other_root = self.root / "files" / f"task-{self.other_id}"
        (other_root / "keep.txt").write_text("keep me")
        (root / "other-project").symlink_to(other_root, target_is_directory=True)
        editor = self.open_editor()

        self.assign("none")

        self.assertFalse(root.exists())
        self.assertEqual((other_root / "keep.txt").read_text(), "keep me")
        self.assertEqual(self.row()["file_storage_type"], "none")
        self.assertIsNone(api_onlyoffice._validate_wopi_token(editor["access_token"]))
        with closing(database.get_db()) as db:
            audit = db.execute("SELECT changes_json FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
        self.assertTrue(json.loads(audit[0])["local_directory_deleted"])
        self.assign()
        self.assertEqual(self.ok("GET", self.url + "/files").json()["items"], [])

    def test_failed_local_removal_keeps_mapping_files_and_office_tokens(self):
        self.assign()
        self.upload()
        editor = self.open_editor()
        with patch.object(file_storage.shutil, "rmtree", side_effect=PermissionError("read only")):
            with self.assertLogs("dashboard.api_tasks", level="ERROR"):
                response = self.client.put(self.url, json={"file_storage_type": "none"})
        self.assertEqual(response.status_code, 500)
        self.assertEqual(self.row()["file_storage_type"], "local")
        self.assertEqual(self.ok("GET", self.url + "/files/download", params={"path": "Plan.txt"}).content, b"original")
        self.assertIsNotNone(api_onlyoffice._validate_wopi_token(editor["access_token"]))
        self.assign("none")
        self.assertFalse((self.root / "files" / f"task-{self.task_id}").exists())

    def test_failed_database_update_does_not_delete_local_storage(self):
        self.assign()
        self.upload()
        with patch.object(api_tasks, "remove_local_directory") as remove_local:
            with self.assertRaises(sqlite3.IntegrityError):
                self.client.put(self.url, json={"file_storage_type": "none", "assigned_to": 999999})
            remove_local.assert_not_called()
        self.assertEqual(self.row()["file_storage_type"], "local")
        self.assertEqual(self.ok("GET", self.url + "/files/download", params={"path": "Plan.txt"}).content, b"original")

    def test_local_removal_rejects_a_symlink_as_project_root(self):
        self.assign()
        root = self.root / "files" / f"task-{self.task_id}"
        root.rmdir()
        external = self.root / "external"
        external.mkdir()
        (external / "keep.txt").write_text("keep me")
        root.symlink_to(external, target_is_directory=True)
        response = self.client.put(self.url, json={"file_storage_type": "none"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual((external / "keep.txt").read_text(), "keep me")
        self.assertTrue(root.is_symlink())
        self.assertEqual(self.row()["file_storage_type"], "local")

    def test_removing_missing_local_directory_and_legacy_mapping_request(self):
        self.assign()
        root = self.root / "files" / f"task-{self.task_id}"
        root.rmdir()
        self.assign("none")
        self.assign("none")
        self.assertEqual(self.row()["file_storage_type"], "none")
        self.assign()
        self.upload()
        self.ok("PUT", self.url, json={"nextcloud_path": ""})
        self.assertFalse(root.exists())
        self.assertEqual(self.row()["file_storage_type"], "none")

    def test_each_task_owns_its_directory_and_deleted_files_are_retained(self):
        self.assign()
        self.upload()
        self.user = self.users["outsider"]
        self.ok("PUT", f"/api/tasks/{self.other_id}", json={"file_storage_type": "local"})
        self.assertEqual(self.ok("GET", f"/api/tasks/{self.other_id}/files").json()["items"], [])
        self.assertEqual(self.client.get(self.url + "/files").status_code, 403)
        self.user = self.users["owner"]
        self.ok("DELETE", self.url)
        self.assertEqual(self.client.get(self.url + "/files").status_code, 404)
        self.assertTrue((self.root / "files" / f"task-{self.task_id}" / "Plan.txt").is_file())

    def test_readers_and_assignees_cannot_modify_files_or_assignment(self):
        self.assign()
        self.upload()
        for name in ("reader", "assignee", "outsider"):
            self.user = self.users[name]
            with self.subTest(user=name):
                if name != "outsider":
                    self.assertFalse(self.ok("GET", self.url + "/files").json()["can_write"])
                    self.ok("GET", self.url + "/files/download", params={"path": "Plan.txt"})
                self.assertEqual(self.client.put(self.url, json={"file_storage_type": "none"}).status_code, 403)
                for method, suffix, kwargs in (
                    ("POST", "/upload", {"files": {"file": ("evil.txt", b"no")}}),
                    ("POST", "/mkdir", {"json": {"name": "no"}}),
                    ("PUT", "/move", {"json": {"source": "Plan.txt", "destination": "no.txt"}}),
                    ("DELETE", "", {"params": {"path": "Plan.txt"}}),
                ):
                    self.assertEqual(self.client.request(method, self.url + "/files" + suffix, **kwargs).status_code, 403)
        for name in ("editor", "admin"):
            self.user = self.users[name]
            self.assertTrue(self.ok("GET", self.url + "/files").json()["can_write"])
            self.upload(name + ".txt")

    def test_setup_guidance_matches_browser_rights_and_preserves_rest_detail(self):
        for name in ("owner", "admin", "editor", "reader", "assignee"):
            self.user = self.users[name]
            with self.subTest(user=name):
                response = self.client.get(self.url + "/files")
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json(), {"detail": "Keine Dateiablage zugeordnet"})
                response = self.client.put(self.url, json={"file_storage_type": "local"})
                if name in ("owner", "admin", "editor"):
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assign("none")
                else:
                    self.assertEqual(response.status_code, 403)
                    self.assertFalse(file_storage.local_path(self.task_id).exists())
        self.user = self.users["outsider"]
        self.assertEqual(self.client.get(self.url + "/files").status_code, 403)

    def test_traversal_and_empty_mutation_paths_are_rejected(self):
        self.assign()
        paths = ("../test.db", "/etc/passwd", "folder/../../test.db", "folder/../Plan.txt",
                 "%2e%2e/test.db", "%252e%252e/test.db", "..\\test.db", "a\x00b", "a\nb")
        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(self.url + "/files", params={"path": path}).status_code, 400)
                self.assertEqual(self.client.put(self.url, json={"file_storage_type": "webdav", "nextcloud_path": path}).status_code, 400)
        for path in ("", ".", "./"):
            self.assertEqual(self.client.delete(self.url + "/files", params={"path": path}).status_code, 400)
            self.assertEqual(self.client.put(self.url + "/files/move", json={"source": path, "destination": "moved"}).status_code, 400)
            self.assertEqual(self.client.put(self.url + "/files/move", json={"source": "file", "destination": path}).status_code, 400)
        for name in ("..", "a/b", "a\\b", "%252e%252e", "bad\x00name"):
            self.assertEqual(self.client.post(self.url + "/files/mkdir", json={"name": name}).status_code, 400)
        self.assertEqual(self.row()["file_storage_type"], "local")

    def test_symlinks_cannot_expose_other_tasks_or_server_files(self):
        self.assign()
        root = self.root / "files" / f"task-{self.task_id}"
        (root / "secret").symlink_to(self.root / "test.db")
        (root / "outside").symlink_to(self.root, target_is_directory=True)
        self.assertEqual(self.ok("GET", self.url + "/files").json()["items"], [])
        for path in ("secret", "outside/test.db"):
            self.assertEqual(self.client.get(self.url + "/files/download", params={"path": path}).status_code, 400)
            self.assertEqual(self.client.delete(self.url + "/files", params={"path": path}).status_code, 400)
        self.assertEqual(self.client.post(self.url + "/files/upload", params={"path": "outside"}, files={"file": ("no", b"no")}).status_code, 400)
        self.assertEqual(self.client.post(self.url + "/files/upload", files={"file": ("secret", b"no")}).status_code, 400)
        other_root = self.root / "files" / f"task-{self.other_id}"
        other_root.symlink_to(root, target_is_directory=True)
        self.user = self.users["outsider"]
        self.assertEqual(self.client.put(f"/api/tasks/{self.other_id}", json={"file_storage_type": "local"}).status_code, 400)

    def test_storage_root_symlink_and_creation_failure_leave_assignment_unchanged(self):
        external = self.root / "external"
        external.mkdir()
        (self.root / "files").symlink_to(external, target_is_directory=True)
        self.assertEqual(self.client.put(self.url, json={"file_storage_type": "local"}).status_code, 400)
        self.assertEqual(self.row()["file_storage_type"], "none")
        (self.root / "files").unlink()
        with patch.object(api_tasks, "ensure_local_directory", side_effect=PermissionError):
            self.assertEqual(self.client.put(self.url, json={"file_storage_type": "local"}).status_code, 500)
        self.assertEqual(self.row()["file_storage_type"], "none")

    def test_missing_files_conflicts_and_upload_limit(self):
        self.assign()
        self.upload()
        self.ok("POST", self.url + "/files/mkdir", json={"name": "folder"})
        self.assertEqual(self.client.post(self.url + "/files/mkdir", json={"name": "folder"}).status_code, 409)
        self.assertEqual(self.client.get(self.url + "/files/download", params={"path": "missing"}).status_code, 404)
        self.assertEqual(self.client.get(self.url + "/files", params={"path": "Plan.txt"}).status_code, 404)
        self.assertEqual(self.client.put(self.url + "/files/move", json={"source": "Plan.txt", "destination": "folder"}).status_code, 409)
        self.assertEqual(self.client.put(self.url + "/files/move", json={"source": "folder", "destination": "folder/sub"}).status_code, 400)
        with patch.object(api_nextcloud, "MAX_UPLOAD_SIZE", 3):
            self.assertEqual(self.client.post(self.url + "/files/upload", files={"file": ("Plan.txt", b"too big")}).status_code, 413)
        with patch.object(file_storage.os, "replace", side_effect=OSError("disk failure")):
            self.assertEqual(self.client.post(self.url + "/files/upload", files={"file": ("Plan.txt", b"replacement")}).status_code, 500)
        self.assertEqual(self.ok("GET", self.url + "/files/download", params={"path": "Plan.txt"}).content, b"original")
        self.assertFalse(list((self.root / "files" / f"task-{self.task_id}").glob(".upload-*")))

    def test_legacy_webdav_assignment_and_all_file_operations(self):
        with closing(database.get_db()) as db, db:
            db.execute("UPDATE tasks SET nextcloud_path = 'projects/old' WHERE id = ?", (self.task_id,))
        self.assertEqual(self.row()["file_storage_type"], "webdav")
        listing = self.patch(webdav, "list_directory", return_value=[{
            "name": "Plan.txt", "type": "file", "size": 8, "last_modified": "", "mime_type": "text/plain", "file_id": "123",
        }])
        self.patch(webdav, "_get_config", return_value={"server_url": "https://cloud.example.test"})
        get = self.patch(webdav, "get_file", return_value=(b"webdav", "text/plain"))
        upload = self.patch(webdav, "upload_file")
        mkdir = self.patch(webdav, "create_directory")
        move = self.patch(webdav, "move_item")
        delete = self.patch(webdav, "delete_item")
        item = self.ok("GET", self.url + "/files", params={"path": "sub"}).json()["items"][0]
        self.assertIn("nextcloud_link", item)
        listing.assert_called_once_with("projects/old/sub")
        self.ok("GET", self.url + "/files/download", params={"path": "Plan.txt"})
        get.assert_called_once_with("projects/old/Plan.txt")
        self.upload(path="sub")
        upload.assert_called_once_with("projects/old/sub/Plan.txt", b"original", "text/plain")
        self.ok("POST", self.url + "/files/mkdir", json={"name": "folder"})
        mkdir.assert_called_once_with("projects/old/folder")
        self.ok("PUT", self.url + "/files/move", json={"source": "Plan.txt", "destination": "new.txt"})
        move.assert_called_once_with("projects/old/Plan.txt", "projects/old/new.txt")
        self.ok("DELETE", self.url + "/files", params={"path": "new.txt"})
        delete.assert_called_once_with("projects/old/new.txt")
        # Der alte REST-Vertrag zur Zuordnung bleibt nutzbar.
        self.ok("PUT", self.url, json={"nextcloud_path": "projects/new"})
        self.assertEqual(self.row()["file_storage_type"], "webdav")
        self.ok("PUT", self.url, json={"nextcloud_path": ""})
        self.assertEqual(self.row()["file_storage_type"], "none")

    def test_removing_nextcloud_configuration_preserves_local_storage(self):
        self.assign()
        self.upload()
        with closing(database.get_db()) as db, db:
            db.execute("UPDATE tasks SET nextcloud_path = 'old' WHERE id = ?", (self.other_id,))
        self.ok("DELETE", "/api/admin/nextcloud/config")
        self.assertEqual(self.row()["file_storage_type"], "local")
        self.ok("GET", self.url + "/files/download", params={"path": "Plan.txt"})
        with closing(database.get_db()) as db:
            self.assertIsNone(db.execute("SELECT nextcloud_path FROM tasks WHERE id = ?", (self.other_id,)).fetchone()[0])

    def test_webdav_connection_failures_return_json_and_recover(self):
        self.assign("webdav", nextcloud_path="projects/example")
        for error, status, detail in (
            (httpx.ConnectError("Temporary failure in name resolution: private-server"), 503, "files.webdavConnectionError"),
            (httpx.ReadTimeout("Timed out reading private-server"), 504, "files.webdavTimeout"),
        ):
            for operation, method, path, kwargs in (
                ("list_directory", "GET", "/files", {}),
                ("get_file_stream", "GET", "/files/text", {"params": {"path": "readme.md"}}),
                ("upload_file", "POST", "/files/upload", {"files": {"file": ("Plan.txt", b"replacement")}}),
            ):
                with self.subTest(error=type(error).__name__, operation=operation):
                    with patch.object(webdav, operation, side_effect=error) as request:
                        response = self.client.request(method, self.url + path, **kwargs)
                    self.assertEqual(response.status_code, status)
                    self.assertEqual(response.json(), {"detail": detail})
                    self.assertNotIn("private-server", response.text)
                    self.assertEqual(request.call_count, 1)
        with patch.object(webdav, "list_directory", return_value=[]):
            self.assertEqual(self.ok("GET", self.url + "/files").json()["items"], [])

    def test_migration_keeps_existing_webdav_mapping_and_is_repeatable(self):
        with closing(database.get_db()) as db, db:
            db.execute("UPDATE tasks SET nextcloud_path = 'keep' WHERE id = ?", (self.task_id,))
            db.execute("ALTER TABLE tasks DROP COLUMN file_storage_type")
        database.init_db()
        database.init_db()
        self.assertEqual((self.row()["nextcloud_path"], self.row()["file_storage_type"]), ("keep", "webdav"))
        self.assign()
        database.init_db()
        self.assertEqual(self.row()["file_storage_type"], "local")

    def test_office_local_read_write_and_revoked_permissions(self):
        self.assign()
        self.upload()
        editor = self.open_editor()
        url = f"/api/wopi/files/{editor['file_id']}"
        params = {"access_token": editor["access_token"]}
        self.assertTrue(self.ok("GET", url, params=params).json()["UserCanWrite"])
        self.assertEqual(self.ok("GET", url + "/contents", params=params).content, b"original")
        self.ok("POST", url + "/contents", params=params, content=b"office edit")
        self.assertEqual(self.ok("GET", self.url + "/files/download", params={"path": "Plan.txt"}).content, b"office edit")
        self.user = self.users["reader"]
        viewer = self.open_editor()
        self.assertFalse(viewer["can_edit"])
        self.assertEqual(self.client.post(url + "/contents", params={"access_token": viewer["access_token"]}, content=b"no").status_code, 403)
        self.user = self.users["editor"]
        editor = self.open_editor()
        with closing(database.get_db()) as db, db:
            db.execute("UPDATE project_members SET can_edit = 0 WHERE user_id = ?", (self.user["id"],))
        params = {"access_token": editor["access_token"]}
        self.assertFalse(self.ok("GET", url, params=params).json()["UserCanWrite"])
        self.assertEqual(self.client.post(url + "/contents", params=params, content=b"no").status_code, 403)
        self.user = self.users["outsider"]
        self.assertEqual(self.client.get(self.url + "/files/edit", params={"path": "Plan.txt"}).status_code, 403)

    def test_office_callback_saves_locally_and_cannot_follow_storage_switch(self):
        self.assign()
        self.upload()
        editor = self.open_editor()
        callback_url = editor["editor_config"]["editorConfig"]["callbackUrl"]
        payload = {"status": 2, "key": editor["editor_config"]["document"]["key"], "url": "https://office.example.test/edited"}
        body = {"token": api_onlyoffice._sign_jwt(payload, "test-secret")}
        downloader = AsyncMock()
        downloader.__aenter__.return_value = downloader
        downloader.get.return_value = httpx.Response(200, content=b"callback edit")
        with patch.object(api_onlyoffice.httpx, "AsyncClient", return_value=downloader):
            self.assertEqual(self.ok("POST", callback_url, json=body).json(), {"error": 0})
            self.assertEqual(self.ok("GET", self.url + "/files/download", params={"path": "Plan.txt"}).content, b"callback edit")
            self.assign("webdav", nextcloud_path="new/storage")
            self.assertEqual(self.ok("POST", callback_url, json=body).json(), {"error": 1})
            self.assertEqual(downloader.get.await_count, 1)

    def test_invalid_storage_type_or_inconsistent_mapping_is_rejected(self):
        self.assertEqual(self.client.put(self.url, json={"file_storage_type": "disk"}).status_code, 422)
        self.assertEqual(self.client.put(self.url, json={"file_storage_type": "webdav"}).status_code, 400)
        self.assertEqual(self.client.put(self.url, json={"file_storage_type": "local", "nextcloud_path": "remote"}).status_code, 400)
        self.assertEqual(self.row()["file_storage_type"], "none")


if __name__ == "__main__":
    unittest.main()
