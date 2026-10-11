"""Kompakte Ansichten: echte MCP-Aufrufe, Rechte, Snapshot und Antwortgroessen."""

import asyncio
import json
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from fastmcp import Client

from dashboard import database, mcp_server as api
from dashboard.errors import ApplicationError
from dashboard.mcp_errors import MCPToolError
from dashboard.project_views import (
    derive_readiness, get_project_status, handoff_preview, without_descriptions,
)


class ProjectViewTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="tareas-project-views-")
        self.addCleanup(temporary.cleanup)
        for name, value in (("DB_DIR", Path(temporary.name)), ("DB_PATH", Path(temporary.name) / "test.db")):
            patcher = patch.object(database, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        database.init_db()
        self.users = {"admin": {"id": 1, "username": "admin", "is_admin": True, "auth_source": "mcp"}}
        with closing(database.get_db()) as db, db:
            for name in ("owner", "reader", "editor", "assignee", "subtask_only", "outsider", "creator"):
                uid = db.execute("INSERT INTO users (username,password_hash) VALUES (?, 'unused')", (name,)).lastrowid
                self.users[name] = {"id": uid, "username": name, "is_admin": False, "auth_source": "local"}
            self.project = db.execute(
                "INSERT INTO tasks (name, task_type, created_by, assigned_to, description) "
                "VALUES ('Private project title', 'projekt', ?, ?, 'Private project specification')",
                (self.users["owner"]["id"], self.users["assignee"]["id"]),
            ).lastrowid
            for name, edit, create in (("reader", 0, 0), ("editor", 1, 0), ("creator", 0, 1)):
                db.execute("INSERT INTO project_members (project_id,user_id,can_read,can_edit,can_create) VALUES (?,?,1,?,?)",
                           (self.project, self.users[name]["id"], edit, create))
        token = api.current_mcp_user.set(self.users["owner"])
        self.addCleanup(api.current_mcp_user.reset, token)

    def subtask(self, name="Fachlicher Titel", *, progress=0, assigned=None, description="Private description", area_id=None):
        with closing(database.get_db()) as db, db:
            return db.execute(
                "INSERT INTO sub_tasks (project_id,name,description,status_percent,assigned_to,area_id,position_number) "
                "VALUES (?,?,?,?,?,?,(SELECT COUNT(*)+1 FROM sub_tasks WHERE project_id=?))",
                (self.project, name, description, progress, assigned, area_id, self.project),
            ).lastrowid

    def handoff(self, sid, content, *, date="2026-10-11 10:00:00", user="owner"):
        with closing(database.get_db()) as db, db:
            return db.execute(
                "INSERT INTO sub_task_note_entries (sub_task_id,user_id,content,created_at) VALUES (?,?,?,?)",
                (sid, self.users[user]["id"], content, date),
            ).lastrowid

    def dependency(self, sid, predecessor):
        with closing(database.get_db()) as db, db:
            db.execute("INSERT INTO sub_task_dependencies (sub_task_id,depends_on_id) VALUES (?,?)", (sid, predecessor))

    def overview(self, user="owner"):
        with closing(database.get_db()) as db:
            return get_project_status(db, self.project, self.users[user])

    def last_changes(self):
        with closing(database.get_db()) as db:
            changes = db.execute("SELECT changes_json FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()[0]
        return json.loads(changes) if changes else {}

    def test_handoff_latest_ties_preview_area_and_empty_entries(self):
        with closing(database.get_db()) as db:
            area = db.execute("SELECT id,name FROM areas ORDER BY id LIMIT 1").fetchone()
        first = self.subtask(area_id=area["id"])
        second = self.subtask("Ohne Handoff")
        self.handoff(first, "Older", date="2026-10-10 23:59:59")
        self.handoff(first, "Same timestamp but lower ID")
        newest = self.handoff(first, "\n  \n " + "Ä" * 240 + "\nPrivate following line", user="editor")
        result = self.overview()
        self.assertEqual(result["counts"]["total"], 2)
        self.assertEqual(result["subtasks"][0]["area_name"], area["name"])
        latest = result["subtasks"][0]["latest_handoff"]
        self.assertEqual(latest, {
            "handoff_id": f"subtask:{newest}", "user_id": self.users["editor"]["id"],
            "user_name": "editor", "created_at": "2026-10-11 10:00:00", "preview": "Ä" * 200,
        })
        self.assertEqual(result["subtasks"][1]["id"], second)
        self.assertIsNone(result["subtasks"][1]["latest_handoff"])
        self.assertNotIn("description", json.dumps(result))
        self.assertNotIn("Private following line", json.dumps(result))
        self.assertEqual(handoff_preview(" \n\r\n"), "")

    def test_readiness_chain_branch_join_anchor_delegation_and_recalculation(self):
        done = self.subtask("Start abgeschlossen", progress=100)
        left, right = self.subtask("Links"), self.subtask("Rechts", progress=50)
        joined, tail = self.subtask("Zusammenführen"), self.subtask("Nachgelagert")
        anchor, delegated = self.subtask("Projektanker"), self.subtask("Delegierter Umfang")
        for sid, pred in ((left, done), (right, done), (joined, left), (joined, right), (tail, joined)):
            self.dependency(sid, pred)
        with closing(database.get_db()) as db, db:
            db.execute("UPDATE sub_tasks SET depends_on_project=1 WHERE id=?", (anchor,))
            child = db.execute("INSERT INTO tasks (name,task_type,created_by,parent_subtask_id) VALUES ('Child','projekt',?,?)",
                               (self.users["owner"]["id"], delegated)).lastrowid
        first = self.overview()
        items = {item["id"]: item for item in first["subtasks"]}
        self.assertEqual(items[done]["readiness"], "completed")
        self.assertEqual([items[s]["readiness"] for s in (left, right, anchor)], ["ready"] * 3)
        self.assertEqual(items[joined]["blocker_ids"], [left, right])
        self.assertEqual(items[tail]["blocker_ids"], [joined])
        self.assertEqual(items[delegated]["readiness"], "delegated")
        self.assertEqual(items[delegated]["child_project_id"], child)
        self.assertEqual(first["project"]["status"], "in_arbeit")
        with closing(database.get_db()) as db, db:
            db.execute("UPDATE sub_tasks SET status_percent=100 WHERE id IN (?,?)", (left, right))
        second = {item["id"]: item for item in self.overview()["subtasks"]}
        self.assertEqual(second[joined]["readiness"], "ready")
        self.assertEqual(second[tail]["readiness"], "blocked")
        with closing(database.get_db()) as db, db:
            db.execute("DELETE FROM sub_task_dependencies WHERE sub_task_id=?", (tail,))
        self.assertEqual({item["id"]: item for item in self.overview()["subtasks"]}[tail]["readiness"], "ready")
        self.assertEqual(derive_readiness([{"id": 8, "status_percent": 0}], {8: [0]})[8]["readiness"], "ready")

    def test_cancelled_completed_and_delegated_never_offer_next_work(self):
        finished = self.subtask(progress=100)
        open_id = self.subtask()
        delegated = self.subtask()
        with closing(database.get_db()) as db, db:
            db.execute("INSERT INTO tasks (name,task_type,created_by,parent_subtask_id) VALUES ('Child','projekt',?,?)",
                       (self.users["owner"]["id"], delegated))
            db.execute("UPDATE tasks SET status='abgebrochen' WHERE id=?", (self.project,))
            before = [tuple(row) for row in db.execute("SELECT id,status_percent,depends_on_project FROM sub_tasks")]
        result = self.overview()
        self.assertEqual(result["counts"]["ready"], 0)
        self.assertEqual(result["counts"]["completed"], 1)
        self.assertEqual(result["counts"]["cancelled"], 2)
        self.assertEqual(result["project"]["status"], "abgebrochen")
        self.assertEqual({item["id"]: item["readiness"] for item in result["subtasks"]},
                         {finished: "completed", open_id: "cancelled", delegated: "cancelled"})
        with closing(database.get_db()) as db:
            self.assertEqual([tuple(row) for row in db.execute("SELECT id,status_percent,depends_on_project FROM sub_tasks")], before)

    def test_all_project_grants_and_restricted_subtask_visibility(self):
        visible = self.subtask("Assigned title", assigned=self.users["subtask_only"]["id"])
        hidden = self.subtask("Foreign title")
        own_handoff = self.handoff(visible, "Visible handoff")
        self.handoff(hidden, "Foreign handoff")
        self.dependency(visible, hidden)
        for role in ("owner", "admin", "reader", "editor", "assignee", "creator"):
            with self.subTest(role=role):
                self.assertEqual(self.overview(role)["counts"]["total"], 2)
        result = self.overview("subtask_only")
        self.assertEqual(result["project"], {"id": self.project, "limited": True})
        self.assertEqual(result["counts"]["total"], 1)
        self.assertEqual(result["counts"]["restricted"], 1)
        self.assertEqual(result["subtasks"][0]["blocker_ids"], [])
        self.assertEqual(result["subtasks"][0]["latest_handoff"]["handoff_id"], f"subtask:{own_handoff}")
        for private in ("Foreign title", "Foreign handoff", "Private project title", "Private project specification"):
            self.assertNotIn(private, json.dumps(result))
        with closing(database.get_db()) as db, db:
            db.execute("UPDATE sub_tasks SET status_percent=100 WHERE id=?", (hidden,))
        self.assertEqual(self.overview("subtask_only"), result)
        with self.assertRaises(ApplicationError) as denied:
            self.overview("outsider")
        self.assertEqual(denied.exception.code, "permission_denied")
        with closing(database.get_db()) as db, db:
            db.execute("DELETE FROM project_members WHERE project_id=? AND user_id=?", (self.project, self.users["reader"]["id"]))
        with self.assertRaises(ApplicationError):
            self.overview("reader")

    def test_empty_missing_projects_and_existing_transaction_remain_valid(self):
        result = self.overview()
        self.assertEqual(result["subtasks"], [])
        self.assertEqual(result["counts"]["total"], 0)
        self.assertEqual(result["project"]["status"], "offen")
        with closing(database.get_db()) as db:
            db.execute("BEGIN")
            get_project_status(db, self.project, self.users["owner"])
            self.assertTrue(db.in_transaction)
            db.rollback()
            with self.assertRaises(ApplicationError) as missing:
                get_project_status(db, 987654, self.users["owner"])
            self.assertEqual(missing.exception.code, "task_not_found")
            self.assertFalse(db.in_transaction)

    def test_consistent_snapshot_across_concurrent_progress_and_handoff_writes(self):
        sid = self.subtask()
        old_handoff = self.handoff(sid, "Before")
        wrote = []
        with closing(database.get_db()) as db:
            def observe(sql):
                if "SELECT st.id, st.project_id" in sql and not wrote:
                    wrote.append(True)
                    with closing(database.get_db()) as other, other:
                        other.execute("UPDATE tasks SET name='After title' WHERE id=?", (self.project,))
                        other.execute("UPDATE sub_tasks SET status_percent=100 WHERE id=?", (sid,))
                        other.execute("INSERT INTO sub_task_note_entries (sub_task_id,user_id,content,created_at) VALUES (?,?,?,?)",
                                      (sid, self.users["owner"]["id"], "After", "2026-10-12 10:00:00"))
            db.set_trace_callback(observe)
            first = get_project_status(db, self.project, self.users["owner"])
        self.assertTrue(wrote)
        self.assertEqual(first["project"]["name"], "Private project title")
        self.assertEqual(first["subtasks"][0]["status_percent"], 0)
        self.assertEqual(first["subtasks"][0]["latest_handoff"]["handoff_id"], f"subtask:{old_handoff}")
        second = self.overview()
        self.assertEqual(second["project"]["name"], "After title")
        self.assertEqual(second["project"]["status"], "erledigt")
        self.assertEqual(second["subtasks"][0]["latest_handoff"]["preview"], "After")

    def test_query_count_is_constant_for_one_and_twenty_five_subtasks(self):
        self.subtask()
        counts = []
        for size in (1, 25):
            if size == 25:
                for index in range(24):
                    self.handoff(self.subtask(f"Item {index}"), "Handoff")
            queries = []
            with closing(database.get_db()) as db:
                db.set_trace_callback(lambda sql: queries.append(sql) if sql.lstrip().upper().startswith(("SELECT", "WITH")) else None)
                result = get_project_status(db, self.project, self.users["owner"])
            self.assertEqual(result["counts"]["total"], size)
            counts.append(len(queries))
        self.assertEqual(counts, [4, 4])

    def test_compact_helpers_never_echo_description_and_keep_metadata(self):
        original = {"id": 7, "description": "Original text", "description_format": "markdown",
                    "description_revision": "revision", "subtasks": [{"description": "Child", "id": 8}]}
        stripped = without_descriptions(original)
        self.assertEqual(stripped["description_revision"], "revision")
        self.assertEqual(stripped["description_format"], "markdown")
        self.assertNotIn("description", stripped)
        self.assertNotIn("description", stripped["subtasks"][0])
        self.assertEqual(original["description"], "Original text")

    def test_mcp_compact_reads_and_update_acknowledgements_with_response_sizes(self):
        description = "P" * 9000
        with closing(database.get_db()) as db, db:
            db.execute("UPDATE tasks SET description=? WHERE id=?", (description, self.project))
        ids = [self.subtask(f"Fachlicher Titel {index}", description="S" * 4096) for index in range(25)]
        for sid in ids:
            self.handoff(sid, "H" * 8000)
        full = api.get_project(self.project)
        compact = api.get_project(self.project, include_description=False)
        full_size = len(json.dumps(full, ensure_ascii=False).encode())
        compact_size = len(json.dumps(compact, ensure_ascii=False).encode())
        overview = api.get_project_status(self.project)
        overview_size = len(json.dumps(overview, ensure_ascii=False).encode())
        print(f"\nProject view bytes (25 x 4096-byte subtasks, 9000-byte project): full={full_size}, compact={compact_size}, status={overview_size}")
        self.assertLess(compact_size, full_size * 0.25)
        self.assertLess(overview_size, full_size * 0.25)
        self.assertEqual(full["description"], description)
        self.assertEqual(api.list_subtasks(self.project)[0]["description"], "S" * 4096)
        self.assertNotIn("description", compact)
        self.assertTrue(all("description" not in item for item in compact["subtasks"]))
        self.assertTrue(all("description" not in item for item in api.list_subtasks(self.project, include_description=False)))
        result = api.update_subtask(ids[0], status_percent=100)
        self.assertEqual(result, {"ok": True})
        self.assertEqual(api.get_subtask(ids[0])["status_percent"], 100)
        result = api.update_project(self.project, description="Fresh full text")
        self.assertEqual(result, {"ok": True})
        self.assertEqual(api.get_project(self.project)["description"], "Fresh full text")
        self.assertEqual(api.update_subtask(ids[0], priority=51), {"ok": True})
        self.assertEqual(api.get_subtask(ids[0])["description"], "S" * 4096)

        async def wire():
            async with Client(api.mcp) as client:
                tools = {tool.name: tool for tool in await client.list_tools()}
                self.assertTrue(tools["get_project"].inputSchema["properties"]["include_description"]["default"])
                self.assertTrue(tools["list_subtasks"].inputSchema["properties"]["include_description"]["default"])
                self.assertNotIn("compact", tools["update_project"].inputSchema["properties"])
                self.assertNotIn("compact", tools["update_subtask"].inputSchema["properties"])
                response = await client.call_tool("get_project_status", {"project_id": self.project})
                self.assertFalse(response.is_error)
                self.assertEqual(response.structured_content["counts"]["total"], 25)
                for tool, identifier, value, read in (
                    ("update_subtask", {"subtask_id": ids[1]}, {"status_percent": 40}, lambda: api.get_subtask(ids[1])),
                    ("update_subtask", {"subtask_id": ids[1]}, {"description": "日本語" * 1400}, lambda: api.get_subtask(ids[1])),
                    ("update_subtask", {"subtask_id": ids[1]}, {"description": ""}, lambda: api.get_subtask(ids[1])),
                    ("update_project", {"project_id": self.project}, {"priority": 77}, lambda: api.get_project(self.project)),
                    ("update_project", {"project_id": self.project}, {"description": "Ä" * 9000}, lambda: api.get_project(self.project)),
                    ("update_project", {"project_id": self.project}, {"description": ""}, lambda: api.get_project(self.project)),
                ):
                    with self.subTest(tool=tool, field=next(iter(value)), empty=value.get("description") == ""):
                        arguments = {**identifier, **value}
                        if "description" in value:
                            arguments["description_revision"] = read()["description_revision"]
                        response = await client.call_tool(tool, arguments)
                        self.assertFalse(response.is_error)
                        self.assertEqual(response.structured_content, {"ok": True})
                        self.assertEqual(json.loads(response.content[0].text), {"ok": True})
                        persisted = read()
                        for field, expected in value.items():
                            self.assertEqual(persisted[field], expected)
        asyncio.run(wire())

    def test_position_changes_preserve_ids_titles_and_dependency_graph(self):
        first, second, third = [self.subtask(name) for name in ("Port 443", "ICMP-Schalter", "15 historischer Titel")]
        self.dependency(third, first)
        expected = {item["id"]: item["name"] for item in self.overview()["subtasks"]}
        api.move_subtask(third, "up")
        api.set_subtask_position(first, 3)
        result = self.overview()
        self.assertEqual({item["id"]: item["name"] for item in result["subtasks"]}, expected)
        self.assertEqual([item["id"] for item in result["subtasks"]], [third, second, first])
        self.assertEqual([item["position_number"] for item in result["subtasks"]], [1, 2, 3])
        self.assertEqual(api.get_subtask(third)["predecessor_ids"], [first])

    def test_updates_preserve_audit_diffs_effective_status_and_linked_progress(self):
        parent_subtask = self.subtask()
        child = api.create_project("Delegierte Arbeit", parent_subtask_id=parent_subtask)["id"]
        leaf = api.create_subtask(child, "Fachliche Arbeit", description="Secret leaf specification")["id"]
        other = api.create_subtask(child, "Weitere Arbeit")["id"]
        result = api.update_project(child, status="erledigt")
        self.assertEqual(result, {"ok": True})
        self.assertEqual(self.last_changes(), {"status": {"old": "offen", "new": "erledigt"}})
        self.assertEqual(api.get_project(child)["status"], "offen")
        result = api.update_subtask(leaf, status_percent=100)
        self.assertEqual(result, {"ok": True})
        self.assertEqual(self.last_changes(), {"status_percent": {"old": 0, "new": 100}})
        parent = next(item for item in api.get_project(self.project, include_description=False)["subtasks"] if item["id"] == parent_subtask)
        self.assertEqual(parent["status_percent"], 50)
        self.assertTrue(parent["progress_automatic"])
        self.assertEqual(parent["child_project_id"], child)
        result = api.update_subtask(parent_subtask, name="Nachverfolgung")
        self.assertEqual(result, {"ok": True})
        parent = api.get_subtask(parent_subtask)
        self.assertEqual(parent["name"], "Nachverfolgung")
        self.assertEqual(parent["status_percent"], 50)
        self.assertTrue(parent["progress_automatic"])
        with self.assertRaises(MCPToolError) as readonly:
            api.update_subtask(parent_subtask, status_percent=100)
        self.assertEqual(readonly.exception.code, "linked_project_progress_readonly")
        result = api.update_subtask(leaf, status_percent=100)
        self.assertEqual(result, {"ok": True})
        self.assertEqual(self.last_changes(), {})
        result = api.update_subtask(leaf, description="Geänderter Volltext")
        self.assertEqual(result, {"ok": True})
        self.assertEqual(self.last_changes(), {"description": {"old": "Secret leaf specification", "new": "Geänderter Volltext"}})
        self.assertEqual(api.get_subtask(leaf)["description"], "Geänderter Volltext")
        api.update_subtask(other, status_percent=100)
        self.assertEqual(api.get_project_status(self.project)["subtasks"][0]["readiness"], "completed")

    def test_mcp_overview_visibility_and_write_permissions(self):
        visible = self.subtask("Assigned scope", assigned=self.users["subtask_only"]["id"])
        hidden = self.subtask("Hidden scope")
        self.handoff(visible, "Readable handoff")
        self.handoff(hidden, "Unreadable handoff")
        api.current_mcp_user.set(self.users["subtask_only"])
        self.assertEqual(api.get_project_status(self.project)["counts"]["total"], 1)
        self.assertEqual([item["id"] for item in api.list_subtasks(self.project, include_description=False)], [visible])
        for include_description in (True, False):
            with self.assertRaises(MCPToolError):
                api.get_project(self.project, include_description=include_description)
        result = api.update_subtask(visible, status_percent=50)
        self.assertEqual(result, {"ok": True})
        self.assertEqual(api.get_subtask(visible)["status_percent"], 50)
        with self.assertRaises(MCPToolError):
            api.update_subtask(hidden, status_percent=50)
        api.current_mcp_user.set(self.users["reader"])
        with self.assertRaises(MCPToolError):
            api.update_project(self.project, description="Denied")
        with self.assertRaises(MCPToolError):
            api.update_subtask(visible, description="Denied")
        api.current_mcp_user.set(self.users["outsider"])

        async def wire():
            async with Client(api.mcp) as client:
                result = await client.call_tool("get_project_status", {"project_id": self.project}, raise_on_error=False)
                self.assertTrue(result.is_error)
                self.assertEqual(result.structured_content["code"], "permission_denied")
                self.assertNotIn("Assigned scope", json.dumps(result.structured_content))
        asyncio.run(wire())

    def test_dependency_audit_includes_project_anchor_and_ignores_order_only(self):
        first, second, third = self.subtask("Erster Vorgänger"), self.subtask("Zweiter Vorgänger"), self.subtask("Folgearbeit")
        anchor = api.update_subtask(third, predecessor_ids=[0])
        self.assertEqual(anchor, {"ok": True})
        self.assertEqual(self.last_changes(), {"depends_on_project": {"old": False, "new": True}})
        self.assertTrue(api.get_subtask(third)["depends_on_project"])
        self.assertEqual(api.get_subtask(third)["predecessor_ids"], [])
        repeated = api.update_subtask(third, predecessor_ids=[0])
        self.assertEqual(repeated, {"ok": True})
        self.assertEqual(self.last_changes(), {})
        cleared = api.update_subtask(third, predecessor_ids=[])
        self.assertEqual(cleared, {"ok": True})
        self.assertEqual(self.last_changes(), {"depends_on_project": {"old": True, "new": False}})
        self.assertFalse(api.get_subtask(third)["depends_on_project"])
        linked = api.update_subtask(third, predecessor_ids=[second, first])
        self.assertEqual(linked, {"ok": True})
        self.assertEqual(self.last_changes(), {"predecessor_ids": {"old": [], "new": [first, second]}})
        self.assertEqual(sorted(api.get_subtask(third)["predecessor_ids"]), [first, second])
        repeated = api.update_subtask(third, predecessor_ids=[first, second])
        self.assertEqual(repeated, {"ok": True})
        self.assertEqual(self.last_changes(), {})


if __name__ == "__main__":
    unittest.main()
