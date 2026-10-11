"""FTS search regressions; every test uses a separate disposable database."""

from contextlib import closing
import asyncio
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from dashboard import database, search_service
from dashboard.errors import ApplicationError


class SearchTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="tareas-search-")
        self.addCleanup(temp.cleanup)
        for name, value in {"DB_DIR": Path(temp.name), "DB_PATH": Path(temp.name) / "test.db"}.items():
            patcher = patch.object(database, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        database.init_db()
        self.db = database.get_db()
        self.addCleanup(self.db.close)
        self.db.execute("INSERT INTO users(id, username, password_hash) VALUES (2, 'reader', 'unused')")
        self.db.execute("INSERT INTO users(id, username, password_hash) VALUES (3, 'author', 'unused')")
        self.admin = {"id": 1, "is_admin": True}
        self.reader = {"id": 2, "is_admin": False}
        self.db.commit()

    def task(self, body="", name="Container", **fields):
        fields = {"description": body, "name": name, "task_type": "projekt", "created_by": 1, **fields}
        return self._insert("tasks", fields)

    def subtask(self, project, body="", name="Subcontainer", **fields):
        return self._insert("sub_tasks", {
            "project_id": project, "name": name, "description": body, "created_by": 1, **fields,
        })

    def _insert(self, table, fields):
        return self.db.execute(
            f"INSERT INTO {table} ({','.join(fields)}) VALUES ({','.join('?' for _ in fields)})",
            list(fields.values()),
        ).lastrowid

    def content(self, table, target, text, **fields):
        column = "sub_task_id" if table.startswith("sub_task_") else "task_id"
        return self._insert(table, {column: target, "user_id": 3, "content": text, **fields})

    def search(self, query, **kwargs):
        return search_service.search_content(self.db, self.admin, query, **kwargs)

    def test_and_matches_separate_words_and_across_title_body(self):
        matching = self.task("Freigabe etwas anderes Domain", name="Proxy")
        self.task("Proxy Domain")
        self.assertEqual([r["id"] for r in self.search("Proxy Freigabe Domain")], [matching])

    def test_phrase_differs_from_and_and_punctuation_is_controlled(self):
        phrase = self.task("Proxy Freigabe")
        apart = self.task("Freigabe und Proxy")
        self.assertEqual({r["id"] for r in self.search("Proxy Freigabe")}, {phrase, apart})
        self.assertEqual([r["id"] for r in self.search('"Proxy Freigabe"')], [phrase])
        self.assertEqual({r["id"] for r in self.search("Proxy-Freigabe")}, {phrase, apart})
        self.assertEqual({r["id"] for r in self.search("Proxy, Freigabe")}, {phrase, apart})
        self.assertEqual(self.search("Proxy OR Freigabe"), [])

    def test_unicode_case_diacritics_and_word_prefix_not_infix(self):
        word = self.task("ÄNDERUNGEN an Fenstern")
        self.task("Zeitfenster")
        self.assertEqual([r["id"] for r in self.search("änderungen")], [word])
        self.assertEqual([r["id"] for r in self.search("a\u0308nderungen")], [word])
        self.assertEqual([r["id"] for r in self.search("anderungen")], [word])
        self.assertEqual([r["id"] for r in self.search("fenster*")], [word])
        self.assertEqual(self.search("fenster"), [])

    def test_late_excerpt_safe_highlight_and_bounded_huge_content(self):
        project = self.task()
        self.content("task_note_entries", project, "Einleitung " * 200 + '<script>alert(1)</script> Schreibschicht "& Ende')
        hit = self.search("Schreibschicht")[0]
        self.assertIn("Schreibschicht", hit["snippet"])
        self.assertIn("<mark>Schreibschicht</mark>", hit["snippet_html"])
        self.assertNotIn("<script>", hit["snippet_html"])
        self.assertIn("&lt;script&gt;", hit["snippet_html"])
        self.assertLessEqual(len(hit["snippet"]), 322)
        self.db.execute("UPDATE task_note_entries SET content = ?", ("&" * 10000 + " Schreibschicht " + "x" * 10000,))
        hit = self.search("Schreibschicht")[0]
        self.assertIn("<mark>Schreibschicht</mark>", hit["snippet_html"])
        self.assertLessEqual(len(hit["snippet"]), 322)
        self.assertLess(len(hit["snippet_html"]), 2000)

    def test_handoff_can_outrank_many_matching_projects_and_subtasks(self):
        for _ in range(25):
            project = self.task("needle " + "background " * 150)
            self.subtask(project, "needle " + "background " * 150)
        handoff = self.content("task_note_entries", project, "needle needle needle")
        hit = self.search("needle", limit=1)[0]
        self.assertEqual((hit["type"], hit["local_id"]), ("task_handoff", handoff))
        self.assertEqual(hit["handoff_id"], f"task:{handoff}")
        self.assertEqual(hit["user"], "author")

    def test_combined_project_type_since_filters_for_both_handoff_scopes(self):
        project = self.task("needle", created_at="2026-01-02 11:00:00")
        subtask = self.subtask(project)
        other = self.task()
        for table, target in (("task_note_entries", project), ("sub_task_note_entries", subtask)):
            self.content(table, target, "needle", created_at="2026-01-02 11:00:00")
            self.content(table, target, "needle old", created_at="2026-01-02 10:59:59")
        self.content("task_note_entries", other, "needle", created_at="2026-01-02 11:00:00")
        hits = self.search("needle", project_id=project, types=["handoff"], since="2026-01-02T12:00:00+01:00")
        self.assertEqual({r["type"] for r in hits}, {"task_handoff", "sub_task_handoff"})
        self.assertEqual(len(hits), 2)
        self.assertTrue(all(r["project_id"] == project and r["date"] == "2026-01-02T11:00:00Z" for r in hits))
        self.assertEqual([r["type"] for r in self.search("needle", project_id=project, types=["sub_task_handoff"], since="2026-01-02T11:00:00Z")], ["sub_task_handoff"])

    def test_note_dates_use_updated_at_and_other_sources_created_at(self):
        project = self.task("needle", created_at="2026-01-01 00:00:00")
        subtask = self.subtask(project, "needle", created_at="2026-01-01 00:00:00")
        for table, target in (("task_notes", project), ("sub_task_notes", subtask)):
            self.content(table, target, "needle", created_at="2026-01-01 00:00:00", updated_at="2026-02-01 00:00:00")
        for table, target in (("task_note_entries", project), ("sub_task_note_entries", subtask)):
            self.content(table, target, "needle", created_at="2026-01-01 00:00:00")
            self.db.execute(f"UPDATE {table} SET content='needle changed'")
        self.assertEqual({r["type"] for r in self.search("needle", since="2026-01-31T00:00:00Z")}, {"task_note", "sub_task_note"})

    def test_current_visibility_before_limit_and_revocation(self):
        for _ in range(10):
            self.task("secretneedle secretneedle")
        project = self.task("secretneedle public background")
        subtask = self.subtask(project, "secretneedle public background")
        for table, target in (("task_notes", project), ("sub_task_notes", subtask), ("task_note_entries", project), ("sub_task_note_entries", subtask)):
            self.content(table, target, "secretneedle public")
        def visible(**kwargs):
            return search_service.search_content(self.db, self.reader, "secretneedle", **kwargs)
        self.assertEqual(visible(), [])
        self.db.execute("INSERT INTO project_members(project_id, user_id) VALUES (?, 2)", (project,))
        self.assertEqual(len(visible()), 6)
        self.assertEqual(len(visible(limit=1)), 1)
        self.assertTrue(all(r["project_id"] == project for r in visible()))
        self.db.execute("DELETE FROM project_members WHERE user_id=2")
        self.assertEqual(visible(), [])
        self.db.execute("UPDATE sub_tasks SET assigned_to=2 WHERE id=?", (subtask,))
        self.assertEqual({r["type"] for r in visible()}, {"sub_task", "sub_task_note", "sub_task_handoff"})
        self.db.execute("UPDATE sub_tasks SET assigned_to=NULL")
        self.assertEqual(visible(), [])
        self.db.execute("UPDATE tasks SET assigned_to=2 WHERE id=?", (project,))
        self.assertEqual(len(visible()), 6)

    def test_runtime_parent_and_author_metadata_after_move(self):
        source = self.task()
        destination = self.task()
        subtask = self.subtask(source)
        self.content("sub_task_note_entries", subtask, "needle")
        self.db.execute("UPDATE sub_tasks SET project_id=? WHERE id=?", (destination, subtask))
        self.db.execute("UPDATE users SET username='renamed' WHERE id=3")
        self.assertEqual(self.search("needle", project_id=source), [])
        hit = self.search("needle", project_id=destination)[0]
        self.assertEqual(hit["user"], "renamed")
        self.assertEqual(hit["subtask_id"], subtask)

    def populate_sources(self, word="oldword"):
        project = self.task(word)
        subtask = self.subtask(project, word)
        for table, target in (("task_notes", project), ("sub_task_notes", subtask), ("task_note_entries", project), ("sub_task_note_entries", subtask)):
            self.content(table, target, word)
        return project, subtask

    def test_insert_update_delete_and_cascades_keep_every_type_current(self):
        project, subtask = self.populate_sources()
        self.assertEqual({r["type"] for r in self.search("oldword")}, set(search_service.SEARCH_TYPES))
        for table, _, _, body, *_ in search_service._SOURCES:
            self.db.execute(f"UPDATE {table} SET {body}='newword'")
        self.assertEqual(self.search("oldword"), [])
        self.assertEqual(len(self.search("newword")), 6)
        self.db.execute("DELETE FROM sub_tasks WHERE id=?", (subtask,))
        self.assertEqual({r["type"] for r in self.search("newword")}, {"task", "task_note", "task_handoff"})
        self.db.execute("DELETE FROM tasks WHERE id=?", (project,))
        self.assertEqual(self.search("newword"), [])
        self.db.execute("INSERT INTO search_fts(search_fts, rank) VALUES ('integrity-check', 1)")

    def test_author_delete_cascades_and_source_transaction_rollback(self):
        self.populate_sources("needle")
        self.db.commit()
        self.db.execute("DELETE FROM users WHERE id=3")
        self.assertEqual({r["type"] for r in self.search("needle")}, {"task", "sub_task"})
        self.db.rollback()
        self.assertEqual(len(self.search("needle")), 6)
        self.db.execute("INSERT INTO search_fts(search_fts, rank) VALUES ('integrity-check', 1)")

    def test_backfill_repeated_initialization_missing_trigger_repair_and_rebuild(self):
        self.populate_sources()
        for table, *_ in search_service._SOURCES:
            for event in ("ai", "au", "ad"):
                self.db.execute(f"DROP TRIGGER search_{table}_{event}")
        self.db.execute("DROP TABLE search_fts")
        self.db.execute("DROP VIEW search_documents")
        self.db.commit()
        database.init_db()
        database.init_db()
        self.assertEqual(len(self.search("oldword")), 6)
        self.db.execute("DROP TRIGGER search_tasks_au")
        self.db.execute("UPDATE tasks SET description='repairword'")
        self.db.commit()
        database.init_db()
        self.assertEqual(len(self.search("repairword")), 1)
        self.assertEqual(len(self.search("oldword")), 5)
        self.db.execute("INSERT INTO search_fts(search_fts) VALUES ('delete-all')")
        self.assertEqual(self.search("oldword"), [])
        search_service.rebuild_search_index(self.db)
        self.assertEqual(len(self.search("oldword")), 5)
        self.db.execute("INSERT INTO search_fts(search_fts, rank) VALUES ('integrity-check', 1)")

    def test_search_migration_rolls_back_as_one_unit_and_reports_missing_fts(self):
        class WithoutFTS(sqlite3.Connection):
            def execute(self, sql, *args, **kwargs):
                if sql.startswith("CREATE VIRTUAL TABLE search_fts"):
                    raise sqlite3.OperationalError("no such module: fts5")
                return super().execute(sql, *args, **kwargs)
        with closing(sqlite3.connect(":memory:", factory=WithoutFTS)) as db:
            with self.assertRaisesRegex(RuntimeError, "requires SQLite FTS5"):
                database._initialize_db(db)
            self.assertEqual(db.execute("SELECT name FROM sqlite_master WHERE name LIKE 'search_%'").fetchall(), [])

    def test_failed_rebuild_rolls_back_installation(self):
        with closing(sqlite3.connect(":memory:")) as db:
            with patch.object(search_service, "rebuild_search_index", side_effect=RuntimeError("simulated backfill failure")):
                with self.assertRaisesRegex(RuntimeError, "simulated backfill"):
                    database._initialize_db(db)
            self.assertEqual(db.execute("SELECT name FROM sqlite_master WHERE name LIKE 'search_%'").fetchall(), [])
            database._initialize_db(db)
            self.assertEqual(db.execute("SELECT version FROM search_index_meta").fetchone(), (1,))

    def test_query_and_filter_validation_has_stable_errors(self):
        self.assertEqual(self.search(" \t "), [])
        self.assertEqual(self.search("word", types=[]), [])
        for query in ('"unclosed', '""', '*word', 'wo*rd', 'word**', '"word"*', 'title:word', '(word)', '***', '"one"two', '"word\x00"', '\ud800', 'x' * 1001, 'word ' * 33):
            with self.subTest(query=query):
                with self.assertRaises(ApplicationError) as raised:
                    self.search(query)
                self.assertEqual((raised.exception.code, raised.exception.field), ("invalid_search_query", "query"))
        for value in ("2026-01-01", "2026-01-01T12:00:00", "yesterday", "2026-99-99T00:00:00Z"):
            with self.assertRaises(ApplicationError) as raised:
                self.search("word", since=value)
            self.assertEqual((raised.exception.code, raised.exception.field), ("invalid_search_since", "since"))
        for kwargs, code in (({"types": ["unknown"]}, "invalid_search_types"), ({"project_id": -1}, "invalid_project_id"), ({"limit": True}, "invalid_search_limit")):
            with self.assertRaises(ApplicationError) as raised:
                self.search("word", **kwargs)
            self.assertEqual(raised.exception.code, code)

    def test_stable_ties_and_limit_caps(self):
        for _ in range(205):
            self.task("needle", created_at="2026-01-01 00:00:00")
        first = self.search("needle", limit=500)
        self.assertEqual(len(first), 200)
        self.assertEqual(first, self.search("needle", limit=500))
        self.assertEqual([r["id"] for r in first], sorted(r["id"] for r in first))
        self.assertEqual(len(self.search("needle", limit=0)), 1)

    def test_audit_reason_migration_is_nullable_and_preserves_old_rows(self):
        self.db.execute("INSERT INTO audit_log(actor_type,entity_type,action) VALUES ('mcp','task','update')")
        self.db.execute("ALTER TABLE audit_log DROP COLUMN reason")
        self.db.commit()
        database.init_db()
        database.init_db()
        self.assertEqual(self.db.execute("SELECT reason FROM audit_log").fetchone()[0], None)

    def test_real_mcp_schema_filters_errors_and_note_upsert_updates_index(self):
        from fastmcp import Client
        from dashboard import mcp_server

        project = self.task("needle")
        subtask = self.subtask(project)
        self.content("task_note_entries", project, "needle", created_at="2026-01-02 11:00:00")
        handoff = self.content("sub_task_note_entries", subtask, "needle", created_at="2026-01-02 11:00:00")
        self.db.commit()
        token = mcp_server.current_mcp_user.set({**self.admin, "auth_source": "mcp"})
        self.addCleanup(mcp_server.current_mcp_user.reset, token)

        async def run():
            async with Client(mcp_server.mcp) as client:
                schema = next(tool for tool in await client.list_tools() if tool.name == "search").inputSchema
                self.assertEqual(schema["required"], ["query"])
                self.assertTrue({"limit", "types", "project_id", "since"} <= schema["properties"].keys())
                self.assertEqual(schema["properties"]["limit"]["default"], 20)
                result = await client.call_tool("search", {
                    "query": "needle", "limit": 1, "types": ["sub_task_handoff"],
                    "project_id": project, "since": "2026-01-02T12:00:00+01:00",
                })
                self.assertFalse(result.is_error)
                rows = json.loads(result.content[0].text)
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["handoff_id"], f"subtask:{handoff}")
                for arguments, code, field in (
                    ({"query": '"unclosed'}, "invalid_search_query", "query"),
                    ({"query": "needle", "since": "yesterday"}, "invalid_search_since", "since"),
                    ({"query": "needle", "types": ["unknown"]}, "invalid_search_types", "types"),
                    ({"query": "needle", "limit": "not an integer"}, "invalid_pagination", "limit"),
                ):
                    error = await client.call_tool("search", arguments, raise_on_error=False)
                    self.assertTrue(error.is_error)
                    self.assertEqual((error.structured_content["code"], error.structured_content["field"]), (code, field))
                # Both MCP note paths use INSERT ... ON CONFLICT DO UPDATE;
                # each replacement must remove the old token from FTS as well.
                for target in ({"task_id": project}, {"task_id": project, "subtask_id": subtask}):
                    await client.call_tool("note.write", {**target, "content": "olduniqueword"})
                    self.assertEqual(len(self.search("olduniqueword")), 1)
                    await client.call_tool("note.write", {**target, "content": "newuniqueword"})
                    self.assertEqual(self.search("olduniqueword"), [])
                self.assertEqual(len(self.search("newuniqueword")), 2)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
