"""Admin edits use real API routes and isolated SQLite data, without sending mail."""

import json
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from fastmcp.exceptions import ToolError

from dashboard import api_tasks, api_teams, api_nextcloud, api_onlyoffice, database, mcp_server
from dashboard.api_admin_mcp import MCP_TOOLS
from dashboard.auth import get_current_user


class AdminProjectAccessTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="tareas-admin-access-")
        self.addCleanup(temp.cleanup)
        for name, value in {"DB_DIR": Path(temp.name), "DB_PATH": Path(temp.name) / "test.db"}.items():
            patcher = patch.object(database, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        database.init_db()
        self.admin = {"id": 1, "username": "admin", "is_admin": True, "auth_source": "local"}
        self.users = {}
        with closing(database.get_db()) as db, db:
            for name in ("owner", "assignee", "reader", "outsider"):
                uid = db.execute("INSERT INTO users (username, password_hash) VALUES (?, 'unused')", (name,)).lastrowid
                self.users[name] = {"id": uid, "username": name, "is_admin": False, "auth_source": "local"}
            self.project = db.execute(
                """INSERT INTO tasks (name, task_type, created_by, assigned_to, description, nextcloud_path)
                   VALUES ('Foreign project', 'projekt', ?, ?, 'Original', 'projects/example')""",
                (self.users["owner"]["id"], self.users["assignee"]["id"]),
            ).lastrowid
            self.other = db.execute(
                "INSERT INTO tasks (name, task_type, created_by) VALUES ('Other', 'projekt', ?)",
                (self.users["owner"]["id"],),
            ).lastrowid
            self.subtask = db.execute(
                """INSERT INTO sub_tasks (name, project_id, position_number, created_by, assigned_to, description)
                   VALUES ('Foreign subtask', ?, 1, ?, ?, 'Original subtask')""",
                (self.project, self.users["owner"]["id"], self.users["assignee"]["id"]),
            ).lastrowid
            self.other_subtask = db.execute(
                "INSERT INTO sub_tasks (name, project_id, position_number) VALUES ('Other subtask', ?, 1)", (self.other,),
            ).lastrowid
            db.execute("INSERT INTO project_members (project_id, user_id) VALUES (?, ?)", (self.project, self.users["reader"]["id"]))
            self.records = []
            for table, column, scope in (
                ("task_notes", "task_id", self.project),
                ("sub_task_notes", "sub_task_id", self.subtask),
                ("task_note_entries", "task_id", self.project),
                ("sub_task_note_entries", "sub_task_id", self.subtask),
            ):
                rid = db.execute(
                    f"INSERT INTO {table} ({column}, user_id, content, content_format) VALUES (?, ?, 'Original note', 'legacy')",
                    (scope, self.users["assignee"]["id"]),
                ).lastrowid
                prefix = f"/api/tasks/{self.project}" + (f"/subtasks/{self.subtask}" if column == "sub_task_id" else "")
                endpoint = f"note-entries/{rid}" if "entries" in table else f"notes/{self.users['assignee']['id']}"
                self.records.append((table, rid, f"{prefix}/{endpoint}"))
        self.user = self.admin
        app = FastAPI()
        app.include_router(api_tasks.router)
        app.include_router(api_teams.router)
        app.dependency_overrides[get_current_user] = lambda: self.user
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        mail = patch.object(api_tasks, "notify_event", return_value=None)
        mail.start()
        self.addCleanup(mail.stop)

    def request_ok(self, method, path, body=None):
        response = self.client.request(method, path, json=body)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def row(self, table, rid):
        with closing(database.get_db()) as db:
            return dict(db.execute(f"SELECT * FROM {table} WHERE id = ?", (rid,)).fetchone())

    def test_admin_edits_all_task_fields_despite_foreign_owner_and_assignment(self):
        for assignee in (self.users["assignee"]["id"], self.admin["id"]):
            with self.subTest(assignee=assignee):
                with closing(database.get_db()) as db, db:
                    db.execute("UPDATE tasks SET assigned_to = ? WHERE id = ?", (assignee, self.project))
                listing = self.request_ok("GET", "/api/tasks")["items"]
                self.assertTrue(next(r for r in listing if r["id"] == self.project)["can_edit_status"])
                self.request_ok("PUT", f"/api/tasks/{self.project}", {
                    "name": "Admin edit", "description": "# Admin scope", "priority": 12,
                    "deadline": "2027-03-01", "status": "abgebrochen", "nextcloud_path": "projects/new",
                    "assigned_to": self.users["reader"]["id"],
                })
                row = self.row("tasks", self.project)
                self.assertEqual((row["name"], row["description"], row["description_format"]), ("Admin edit", "# Admin scope", "markdown"))
                self.assertEqual((row["priority"], row["deadline"], row["status"]), (12, "2027-03-01", "abgebrochen"))
                self.assertEqual(row["nextcloud_path"], "projects/new")
                self.assertEqual(row["assigned_to"], self.users["reader"]["id"])
                self.assertEqual(row["created_by"], self.users["owner"]["id"])
        self.request_ok("PUT", f"/api/tasks/{self.project}", {"task_type": "aufgabe", "assigned_to": 0})
        self.assertEqual(self.row("tasks", self.project)["task_type"], "aufgabe")
        self.assertIsNone(self.row("tasks", self.project)["assigned_to"])

    def test_admin_manages_subtasks_dependencies_order_and_layout(self):
        # An explicit read-only membership must not reduce admin privileges.
        with closing(database.get_db()) as db, db:
            db.execute("INSERT INTO project_members (project_id, user_id, can_edit, can_create) VALUES (?, 1, 0, 0)", (self.project,))
        st = self.request_ok("GET", f"/api/tasks/{self.project}/subtasks")["items"][0]
        self.assertEqual(st["permissions"], {"can_read": True, "can_edit": True, "can_create": True})
        self.request_ok("PUT", f"/api/subtasks/{self.subtask}", {
            "name": "Changed", "description": "## Edited", "priority": 19, "deadline": "2027-04-02",
            "status_percent": 63, "assigned_to": self.users["reader"]["id"],
        })
        st = self.row("sub_tasks", self.subtask)
        self.assertEqual((st["name"], st["description"], st["description_format"], st["status_percent"]), ("Changed", "## Edited", "markdown", 63))
        self.assertEqual(st["created_by"], self.users["owner"]["id"])
        second = self.request_ok("POST", f"/api/tasks/{self.project}/subtasks", {"name": "Admin-created"})["id"]
        self.request_ok("POST", f"/api/subtasks/{second}/move", {"direction": "up"})
        self.assertEqual(self.row("sub_tasks", second)["position_number"], 1)
        for operation in ("add-dependency", "remove-dependency"):
            self.request_ok("POST", f"/api/tasks/{self.project}/subtasks/{operation}", {"from_id": self.subtask, "to_id": second})
        self.request_ok("POST", f"/api/tasks/{self.project}/subtasks/save-netzplan-positions", {
            "positions": [{"id": 0, "x": 10, "y": 20}, {"id": self.subtask, "x": 30, "y": 40}],
        })
        self.assertEqual(self.row("tasks", self.project)["netzplan_project_x"], 10)
        self.assertEqual(self.row("sub_tasks", self.subtask)["netzplan_y"], 40)
        self.request_ok("DELETE", f"/api/subtasks/{second}")

    def test_admin_manages_team_and_file_permissions_without_becoming_owner(self):
        url = f"/api/tasks/{self.project}/members"
        self.request_ok("GET", url)
        self.request_ok("POST", url, {"user_id": self.admin["id"]})
        self.request_ok("PUT", f"{url}/{self.admin['id']}", {"can_read": True, "can_edit": True, "can_create": True})
        self.request_ok("DELETE", f"{url}/{self.admin['id']}")
        self.assertEqual(self.client.post(url, json={"user_id": self.users["owner"]["id"]}).status_code, 400)
        self.assertEqual(api_nextcloud._get_task_nextcloud_path(self.project, self.admin), "projects/example")
        self.assertTrue(api_onlyoffice._check_user_can_edit(self.project, self.admin))
        self.assertFalse(api_onlyoffice._check_user_can_edit(99999, self.admin))

    def test_admin_note_edits_keep_author_and_timestamp_and_audit_actor(self):
        for table, rid, url in self.records:
            with self.subTest(table=table):
                before = self.row(table, rid)
                self.request_ok("PUT", url, {"content": "# Corrected\n\n**Markdown**"})
                after = self.row(table, rid)
                self.assertEqual(after["content"], "# Corrected\n\n**Markdown**")
                self.assertEqual(after["content_format"], "markdown")
                self.assertEqual(after["user_id"], before["user_id"])
                if "created_at" in before:
                    self.assertEqual(after["created_at"], before["created_at"])
                with closing(database.get_db()) as db:
                    audit = db.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
                self.assertEqual(audit["actor_user_id"], self.admin["id"])
                self.assertEqual(audit["action"], "update")
                self.assertEqual(json.loads(audit["changes_json"])["owner_user_id"], before["user_id"])

    def test_note_edits_validate_scope_missing_entries_and_empty_handoffs(self):
        for table, rid, url in self.records:
            before = self.row(table, rid)
            wrong_project = url.replace(f"/tasks/{self.project}/", f"/tasks/{self.other}/")
            self.assertEqual(self.client.put(wrong_project, json={"content": "Wrong"}).status_code, 404)
            self.assertEqual(self.client.put(url.rsplit("/", 1)[0] + "/99999", json={"content": "Missing"}).status_code, 404)
            if "/subtasks/" in url:
                wrong_subtask = url.replace(f"/subtasks/{self.subtask}/", f"/subtasks/{self.other_subtask}/")
                self.assertEqual(self.client.put(wrong_subtask, json={"content": "Wrong"}).status_code, 404)
            if "entries" in table:
                self.assertEqual(self.client.put(url, json={"content": "  \n"}).status_code, 422)
            self.assertEqual(self.row(table, rid), before)

    def test_normal_users_keep_previous_permissions(self):
        for name in ("owner", "assignee", "reader", "outsider"):
            self.user = self.users[name]
            for table, rid, url in self.records:
                before = self.row(table, rid)
                self.assertEqual(self.client.put(url, json={"content": "Forbidden"}).status_code, 403)
                self.assertEqual(self.row(table, rid), before)
        for name in ("reader", "outsider"):
            self.user = self.users[name]
            self.assertEqual(self.client.put(f"/api/tasks/{self.project}", json={"description": "Forbidden"}).status_code, 403)
            self.assertEqual(self.client.put(f"/api/subtasks/{self.subtask}", json={"name": "Forbidden"}).status_code, 403)
            self.assertEqual(self.client.post(f"/api/tasks/{self.project}/subtasks", json={"name": "Forbidden"}).status_code, 403)
            self.assertEqual(self.client.get(f"/api/tasks/{self.project}/members").status_code, 403)
            self.assertFalse(api_onlyoffice._check_user_can_edit(self.project, self.user))
        with self.assertRaises(HTTPException):
            api_nextcloud._get_task_nextcloud_path(self.project, self.users["outsider"])
        self.user = self.users["assignee"]
        self.request_ok("PUT", f"/api/tasks/{self.project}", {"description": "Ignored", "status": "in_arbeit"})
        row = self.row("tasks", self.project)
        self.assertEqual((row["description"], row["status"]), ("Original", "in_arbeit"))
        # Existing own-note writes still work and keep their semantics.
        self.request_ok("PUT", f"/api/tasks/{self.project}/notes", {"content": "My own note"})

    def test_mcp_admin_can_correct_notes_and_typed_handoffs_with_scope_checks(self):
        token = mcp_server.current_mcp_user.set(self.admin)
        self.addCleanup(mcp_server.current_mcp_user.reset, token)
        self.assertIn("note.update", MCP_TOOLS)
        self.assertIn("handoff.update", MCP_TOOLS)
        for subtask_id in (None, self.subtask):
            mcp_server.note_update(self.project, self.users["assignee"]["id"], "MCP note", subtask_id)
            self.assertEqual(mcp_server.note_list(self.project, subtask_id)[0]["content"], "MCP note")
            entry = mcp_server.handoff_list(self.project, subtask_id)[0]
            # Omitted subtask_id is resolved from the typed ID and scoped project.
            mcp_server.handoff_update(self.project, entry["handoff_id"], "## MCP handoff")
            after = mcp_server.handoff_list(self.project, subtask_id)[0]
            self.assertEqual((after["user_id"], after["created_at"]), (entry["user_id"], entry["created_at"]))
            self.assertEqual((after["content"], after["content_format"]), ("## MCP handoff", "markdown"))
            with self.assertRaises(ToolError):
                mcp_server.handoff_update(self.other, entry["handoff_id"], "Wrong project", subtask_id)
        for handoff_id, sid in (("1", None), ("task:1", self.subtask), ("subtask:1", self.other_subtask)):
            with self.assertRaises(ToolError):
                mcp_server.handoff_update(self.project, handoff_id, "Invalid", sid)
        mcp_server.current_mcp_user.set(self.users["assignee"])
        with self.assertRaises(ToolError):
            mcp_server.note_update(self.project, self.users["assignee"]["id"], "Forbidden")
        with self.assertRaises(ToolError):
            mcp_server.handoff_update(self.project, "task:1", "Forbidden")


if __name__ == "__main__":
    unittest.main()
