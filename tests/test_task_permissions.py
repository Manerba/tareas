"""Freigaben fuer Aufgaben/Projekte: Rollen, atomare Speicherung und API/MCP/Dateien."""

import json
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastmcp.exceptions import ToolError

from dashboard import api_tasks, api_teams, api_nextcloud, api_onlyoffice, database, mcp_server
from dashboard.auth import get_current_user


class TaskPermissionsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="tareas-permissions-")
        self.addCleanup(temp.cleanup)
        for name, value in {"DB_DIR": Path(temp.name), "DB_PATH": Path(temp.name) / "test.db"}.items():
            patcher = patch.object(database, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        database.init_db()
        self.users = {"admin": {"id": 1, "username": "admin", "is_admin": True, "auth_source": "local"}}
        with closing(database.get_db()) as db, db:
            for name in ("owner", "editor", "reader", "outsider", "assignee"):
                uid = db.execute("INSERT INTO users (username, password_hash) VALUES (?, 'unused')", (name,)).lastrowid
                self.users[name] = {"id": uid, "username": name, "is_admin": False, "auth_source": "local"}
        self.user = self.users["owner"]
        app = FastAPI()
        for router in (api_tasks.router, api_teams.router, api_nextcloud.files_router, api_onlyoffice.wopi_router):
            app.include_router(router)
        app.dependency_overrides[get_current_user] = lambda: self.user
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        mail = patch.object(api_tasks, "notify_event", return_value=None)
        mail.start()
        self.addCleanup(mail.stop)
        token = mcp_server.current_mcp_user.set(self.user)
        self.addCleanup(mcp_server.current_mcp_user.reset, token)
        self.task = self.ok("POST", "/api/tasks", {"name": "Shared task", "description": "accessmarker"})["id"]
        self.project = self.ok("POST", "/api/tasks", {"name": "Shared project", "task_type": "projekt", "description": "accessmarker"})["id"]
        self.subtask = self.ok("POST", f"/api/tasks/{self.project}/subtasks", {"name": "Subtask", "description": "accessmarker"})["id"]
        self.second = self.ok("POST", f"/api/tasks/{self.project}/subtasks", {"name": "Second"})["id"]
        for task_id in (self.task, self.project):
            self.ok("PUT", f"/api/tasks/{task_id}", {"file_storage_type": "local"})
            self.set_members(task_id, [self.member("reader"), self.member("editor", edit=True)])
        self.ok("PUT", f"/api/tasks/{self.project}/subtasks/{self.subtask}/notes", {"content": "accessmarker"})
        self.entry = mcp_server.handoff_add(self.project, "accessmarker", self.subtask)

    def login(self, name):
        self.user = self.users[name]
        mcp_server.current_mcp_user.set(self.user)

    def ok(self, method, path, body=None):
        response = self.client.request(method, path, json=body)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def member(self, name, *, edit=False, create=False, read=True):
        return {"user_id": self.users[name]["id"], "can_read": read, "can_edit": edit, "can_create": create}

    def set_members(self, task_id, members):
        url = f"/api/tasks/{task_id}/members"
        revision = self.ok("GET", url)["revision"]
        return self.ok("PUT", url, {"revision": revision, "members": members})

    def assert_denied(self, method, path, body=None):
        response = self.client.request(method, path, json=body)
        self.assertEqual(response.status_code, 403, response.text)

    def test_only_owner_and_admin_manage_permissions_on_tasks_and_projects(self):
        for name in self.users:
            self.login(name)
            for task_id in (self.task, self.project):
                url = f"/api/tasks/{task_id}/members"
                if name in ("owner", "admin"):
                    data = self.ok("GET", url)
                    self.assertEqual(data["task"]["created_by"], self.users["owner"]["id"])
                    self.assertTrue(any(user["is_admin"] for user in data["users"]))
                    self.ok("PUT", url, {"revision": data["revision"], "members": [self.member("editor", edit=True), self.member("reader")]})
                else:
                    self.assert_denied("GET", url)
                    self.assert_denied("PUT", url, {"revision": "unknown", "members": []})
                    self.assert_denied("POST", url, self.member("outsider"))
                    self.assert_denied("PUT", f"{url}/{self.users['reader']['id']}", self.member("reader", edit=True))
                    self.assert_denied("DELETE", f"{url}/{self.users['reader']['id']}")

    def test_bulk_save_normalizes_read_access_preserves_owner_and_audits_actor(self):
        self.login("admin")
        self.set_members(self.task, [self.member("editor", edit=True, read=False)])
        data = self.ok("GET", f"/api/tasks/{self.task}/members")
        self.assertEqual([(m["can_read"], m["can_edit"]) for m in data["items"]], [(True, True)])
        with closing(database.get_db()) as db:
            self.assertEqual(db.execute("SELECT created_by FROM tasks WHERE id = ?", (self.task,)).fetchone()[0], self.users["owner"]["id"])
            audit = db.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(audit["actor_user_id"], 1)
        self.assertEqual(json.loads(audit["changes_json"])["removed_user_ids"], [self.users["reader"]["id"]])
        self.login("editor")
        task = next(row for row in self.ok("GET", "/api/tasks")["items"] if row["id"] == self.task)
        self.assertTrue(task["permissions"]["can_edit"])
        self.assertFalse(task["permissions"]["can_manage"])

    def test_invalid_or_concurrent_bulk_updates_are_atomic(self):
        url = f"/api/tasks/{self.task}/members"
        before = self.ok("GET", url)
        invalid = [
            [self.member("reader"), self.member("reader")], [self.member("owner")],
            [self.member("editor", create=True)], [{"user_id": 999999}],
        ]
        for members in invalid:
            response = self.client.put(url, json={"revision": before["revision"], "members": members})
            self.assertEqual(response.status_code, 422, response.text)
            self.assertEqual(self.ok("GET", url)["items"], before["items"])
        self.ok("POST", url, self.member("outsider"))
        response = self.client.put(url, json={"revision": before["revision"], "members": []})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(len(self.ok("GET", url)["items"]), 3)

    def test_editors_can_edit_content_but_cannot_reassign_delete_or_create_without_grant(self):
        self.login("editor")
        self.ok("PUT", f"/api/tasks/{self.task}", {"name": "Edited", "description": "# Scope", "status": "in_arbeit", "priority": 12})
        self.ok("PUT", f"/api/subtasks/{self.subtask}", {"description": "## Subtask", "status_percent": 55})
        self.ok("POST", f"/api/tasks/{self.project}/subtasks/add-dependency", {"from_id": self.subtask, "to_id": self.second})
        self.ok("POST", f"/api/subtasks/{self.second}/move", {"direction": "up"})
        for url in (f"/api/tasks/{self.task}", f"/api/subtasks/{self.subtask}"):
            self.assert_denied("PUT", url, {"assigned_to": self.user["id"]})
            self.assert_denied("DELETE", url)
        self.assert_denied("POST", f"/api/tasks/{self.project}/subtasks", {"name": "Forbidden"})
        mcp_server.update_project(self.task, description="MCP edit")
        mcp_server.update_subtask(self.subtask, description="MCP subtask")
        for operation in (
            lambda: mcp_server.assign_self(task_id=self.task),
            lambda: mcp_server.assign_self(subtask_id=self.subtask),
            lambda: mcp_server.update_project(self.task, assigned_to=self.user["id"]),
            lambda: mcp_server.update_subtask(self.subtask, assigned_to=self.user["id"]),
            lambda: mcp_server.delete_project(self.task), lambda: mcp_server.delete_subtask(self.subtask),
            lambda: mcp_server.create_subtask(self.project, "Forbidden"),
            lambda: mcp_server.handoff_update(self.project, self.entry["handoff_id"], "Foreign"),
        ):
            with self.assertRaises(ToolError):
                operation()
        self.login("owner")
        self.set_members(self.project, [self.member("editor", edit=True, create=True)])
        self.login("editor")
        self.ok("POST", f"/api/tasks/{self.project}/subtasks", {"name": "Allowed"})
        mcp_server.create_subtask(self.project, "Allowed MCP")

    def test_read_only_users_can_read_inherited_content_but_no_write_paths(self):
        self.login("reader")
        self.assertEqual({r["id"] for r in self.ok("GET", "/api/tasks")["items"]}, {self.task, self.project})
        for task_id in (self.task, self.project):
            self.ok("GET", f"/api/tasks/{task_id}/files")
            self.assert_denied("PUT", f"/api/tasks/{task_id}", {"description": "Forbidden"})
            self.assert_denied("PUT", f"/api/tasks/{task_id}/notes", {"content": "Forbidden"})
            self.assert_denied("POST", f"/api/tasks/{task_id}/files/mkdir", {"name": "Forbidden"})
            mcp_server.get_project(task_id)
        self.ok("GET", f"/api/tasks/{self.project}/subtasks/{self.subtask}/note-entries")
        self.assert_denied("PUT", f"/api/subtasks/{self.subtask}", {"status_percent": 100})
        self.assert_denied("PUT", f"/api/tasks/{self.project}/subtasks/{self.subtask}/notes", {"content": "Forbidden"})
        self.assertEqual(len(mcp_server.list_subtasks(self.project)), 2)
        self.assertEqual(mcp_server.get_subtask(self.subtask)["id"], self.subtask)
        self.assertTrue(mcp_server.search("accessmarker"))
        for operation in (
            lambda: mcp_server.update_project(self.task, status="erledigt"),
            lambda: mcp_server.update_subtask(self.subtask, description="Forbidden"),
            lambda: mcp_server.move_subtask(self.subtask, "down"),
            lambda: mcp_server.set_subtask_position(self.subtask, 2),
            lambda: mcp_server.add_dependency(self.second, self.subtask),
            lambda: mcp_server.remove_dependency(self.second, self.subtask),
            lambda: mcp_server.note_write(self.project, "Forbidden", self.subtask),
            lambda: mcp_server.handoff_add(self.project, "Forbidden", self.subtask),
            lambda: mcp_server.note_delete(self.project, self.subtask),
        ):
            with self.assertRaises(ToolError):
                operation()

    def test_revocation_applies_to_rest_mcp_files_and_existing_office_tokens(self):
        self.login("editor")
        response = self.client.post(f"/api/tasks/{self.task}/files/upload", files={"file": ("note.txt", b"content")})
        self.assertEqual(response.status_code, 200, response.text)
        token, _ = api_onlyoffice._generate_wopi_token(self.user["id"], self.task, "note.txt", "edit")
        file_id = api_onlyoffice._encode_file_id(self.task, "note.txt")
        self.login("owner")
        self.set_members(self.task, [])
        self.set_members(self.project, [])
        self.login("editor")
        self.assertEqual(self.ok("GET", "/api/tasks")["items"], [])
        self.assertEqual(mcp_server.list_projects(), [])
        self.assertEqual(mcp_server.search("accessmarker"), [])
        for operation in (lambda: mcp_server.get_project(self.task), lambda: mcp_server.get_subtask(self.subtask),
                          lambda: mcp_server.list_subtasks(self.project)):
            with self.assertRaises(ToolError):
                operation()
        self.assert_denied("GET", f"/api/tasks/{self.project}/subtasks")
        self.assert_denied("GET", f"/api/tasks/{self.task}/files")
        self.assert_denied("POST", f"/api/tasks/{self.task}/files/mkdir", {"name": "Forbidden"})
        response = self.client.get(f"/api/wopi/files/{file_id}/contents", params={"access_token": token})
        self.assertEqual(response.status_code, 403, response.text)


if __name__ == "__main__":
    unittest.main()
