"""Abhaengigkeitsregeln ueber REST und MCP mit isolierter Datenbank."""

import itertools
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from fastmcp.exceptions import ToolError

from dashboard import api_tasks, database, mcp_server
from dashboard.auth import get_current_user
from dashboard.db_utils import db_transaction
from dashboard.dependency_service import (
    add_subtask_dependency, dependency_ancestors, dependency_path,
    load_dependency_graph, redundant_dependencies,
)


class DependencyTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="tareas-dependencies-")
        self.addCleanup(temp.cleanup)
        for name, value in {"DB_DIR": Path(temp.name), "DB_PATH": Path(temp.name) / "test.db"}.items():
            patcher = patch.object(database, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        database.init_db()
        self.user = {"id": 1, "username": "admin", "is_admin": True, "auth_source": "local"}
        token = mcp_server.current_mcp_user.set(self.user)
        self.addCleanup(mcp_server.current_mcp_user.reset, token)
        with closing(database.get_db()) as db, db:
            self.project = db.execute("INSERT INTO tasks (name, task_type) VALUES ('Project', 'projekt')").lastrowid
            other = db.execute("INSERT INTO tasks (name, task_type) VALUES ('Other', 'projekt')").lastrowid
            ids = [db.execute(
                "INSERT INTO sub_tasks (project_id, name, position_number) VALUES (?, ?, ?)",
                (self.project, name, index),
            ).lastrowid for index, name in enumerate('ABCDE', 1)]
            self.a, self.b, self.c, self.d, self.e = ids
            self.foreign = db.execute(
                "INSERT INTO sub_tasks (project_id, name) VALUES (?, 'Foreign')", (other,)
            ).lastrowid
        app = FastAPI()
        app.include_router(api_tasks.router)
        app.dependency_overrides[get_current_user] = lambda: self.user
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def seed(self, edges):
        with closing(database.get_db()) as db, db:
            db.execute("DELETE FROM sub_task_dependencies")
            db.execute("UPDATE sub_tasks SET depends_on_project = 0")
            db.executemany("INSERT INTO sub_task_dependencies (sub_task_id, depends_on_id) VALUES (?, ?)", edges)

    def graph(self):
        with closing(database.get_db()) as db:
            return load_dependency_graph(db, self.project)

    def snapshot(self):
        with closing(database.get_db()) as db:
            return [[tuple(row) for row in db.execute(sql)] for sql in (
                "SELECT * FROM sub_tasks ORDER BY id",
                "SELECT * FROM sub_task_dependencies ORDER BY id",
                "SELECT * FROM audit_log ORDER BY id",
            )]

    def add_rest(self, child, parent):
        return self.client.post(f"/api/tasks/{self.project}/subtasks/add-dependency", json={
            "from_id": parent, "to_id": child,
        })

    def reject_rest(self, response, before, message=None):
        self.assertEqual(response.status_code, 400, response.text)
        if message:
            self.assertIn(message, response.json()["detail"])
        self.assertEqual(self.snapshot(), before)

    def test_shortcut_rejected_by_rest_and_mcp_add_and_update(self):
        self.seed([(self.b, self.a), (self.c, self.b)])
        before = self.snapshot()
        self.reject_rest(self.add_rest(self.c, self.a), before, "redundante")
        self.reject_rest(self.client.put(f"/api/subtasks/{self.c}", json={
            "name": "Must roll back", "predecessor_ids": [self.a, self.b],
        }), before, "redundante")
        for operation in (
            lambda: mcp_server.add_dependency(self.c, self.a),
            lambda: mcp_server.update_subtask(self.c, name="Must roll back", predecessor_ids=[self.a, self.b]),
        ):
            with self.assertRaisesRegex(ToolError, "redundante"):
                operation()
            self.assertEqual(self.snapshot(), before)

    def test_redundant_initial_predecessors_roll_back_creation(self):
        self.seed([(self.b, self.a)])
        before = self.snapshot()
        self.reject_rest(self.client.post(f"/api/tasks/{self.project}/subtasks", json={
            "name": "Invalid", "predecessor_ids": [self.a, self.b],
        }), before, "redundante")
        with self.assertRaisesRegex(ToolError, "redundante"):
            mcp_server.create_subtask(self.project, "Invalid", predecessor_ids=[self.a, self.b])
        self.assertEqual(self.snapshot(), before)

    def test_every_triangle_creation_order_rejects_the_last_edge(self):
        triangle = [(self.b, self.a), (self.c, self.b), (self.c, self.a)]
        for surface, order in itertools.product(('rest', 'mcp'), itertools.permutations(triangle)):
            with self.subTest(surface=surface, order=order):
                self.seed([])
                for child, parent in order[:2]:
                    if surface == 'rest':
                        response = self.add_rest(child, parent)
                        self.assertEqual(response.status_code, 200, response.text)
                    else:
                        mcp_server.add_dependency(child, parent)
                before = self.snapshot()
                child, parent = order[-1]
                if surface == 'rest':
                    self.reject_rest(self.add_rest(child, parent), before, "redundante")
                else:
                    with self.assertRaisesRegex(ToolError, "redundante"):
                        mcp_server.add_dependency(child, parent)
                    self.assertEqual(self.snapshot(), before)

    def test_new_bridge_must_not_make_another_tasks_edge_redundant(self):
        self.seed([(self.b, self.a), (self.c, self.b), (self.e, self.a), (self.e, self.d)])
        before = self.snapshot()
        response = self.client.put(f"/api/subtasks/{self.d}", json={"predecessor_ids": [self.c]})
        self.reject_rest(response, before, f"#{self.e}")
        with self.assertRaisesRegex(ToolError, "redundante"):
            mcp_server.update_subtask(self.d, predecessor_ids=[self.c])
        self.assertEqual(self.snapshot(), before)
        # Removing the shortcut first permits the new, longer path.
        response = self.client.put(f"/api/subtasks/{self.e}", json={"predecessor_ids": [self.d]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.add_rest(self.d, self.c).status_code, 200)
        self.assertFalse(redundant_dependencies(self.graph()))

    def test_replacement_validates_the_final_graph_and_allows_a_diamond(self):
        self.seed([(self.b, self.a), (self.c, self.a)])
        response = self.client.put(f"/api/subtasks/{self.c}", json={"predecessor_ids": [self.b]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.graph()[self.c], {self.b})
        mcp_server.update_subtask(self.c, predecessor_ids=[self.a])
        mcp_server.update_subtask(self.d, predecessor_ids=[self.b, self.c])
        created = self.client.post(f"/api/tasks/{self.project}/subtasks", json={
            "name": "Independent predecessors", "predecessor_ids": [self.d, self.e],
        })
        self.assertEqual(created.status_code, 200, created.text)
        self.assertFalse(redundant_dependencies(self.graph()))
        # The existing MCP add operation stays idempotent.
        mcp_server.add_dependency(self.d, self.b)
        self.assertEqual(self.graph()[self.d], {self.b, self.c})

    def test_cycles_self_references_and_foreign_or_missing_predecessors_are_rejected(self):
        self.seed([(self.b, self.a), (self.c, self.b)])
        for child, parent in ((self.a, self.c), (self.a, self.a), (self.a, self.foreign), (self.a, 99999), (self.a, -1)):
            with self.subTest(child=child, parent=parent):
                before = self.snapshot()
                self.reject_rest(self.add_rest(child, parent), before)
                self.reject_rest(self.client.put(f"/api/subtasks/{child}", json={"predecessor_ids": [parent]}), before)
                for operation in (
                    lambda: mcp_server.add_dependency(child, parent),
                    lambda: mcp_server.update_subtask(child, predecessor_ids=[parent]),
                ):
                    with self.assertRaises(ToolError):
                        operation()
                    self.assertEqual(self.snapshot(), before)
        before = self.snapshot()
        for invalid in (self.foreign, 99999, -1):
            self.reject_rest(self.client.post(f"/api/tasks/{self.project}/subtasks", json={
                "name": "Invalid", "predecessor_ids": [invalid],
            }), before)
            with self.assertRaises(ToolError):
                mcp_server.create_subtask(self.project, "Invalid", predecessor_ids=[invalid])
            self.assertEqual(self.snapshot(), before)

    def test_project_node_works_on_creation_and_cannot_be_mixed_with_subtasks(self):
        response = self.client.post(f"/api/tasks/{self.project}/subtasks", json={"name": "Root", "predecessor_ids": [0]})
        self.assertEqual(response.status_code, 200, response.text)
        root = response.json()['id']
        self.assertEqual(self.graph()[root], {0})
        for kwargs in ({'depends_on_project': True}, {'predecessor_ids': [0]}):
            result = mcp_server.create_subtask(self.project, 'MCP root', **kwargs)
            self.assertTrue(result['depends_on_project'])
            self.assertEqual(self.graph()[result['id']], {0})
        before = self.snapshot()
        self.reject_rest(self.add_rest(root, self.a), before)
        with self.assertRaises(ToolError):
            mcp_server.add_dependency(root, self.a)
        self.assertEqual(self.snapshot(), before)
        self.reject_rest(self.client.post(f"/api/tasks/{self.project}/subtasks", json={
            "name": "Mixed", "predecessor_ids": [0, self.a],
        }), before)
        with self.assertRaises(ToolError):
            mcp_server.create_subtask(self.project, 'Mixed', depends_on_project=True, predecessor_ids=[self.a])
        self.assertEqual(self.snapshot(), before)
        mcp_server.update_subtask(root, predecessor_ids=[self.a])
        self.assertEqual(self.graph()[root], {self.a})

    def test_legacy_redundancies_can_be_removed_stepwise_without_blocking_other_fields(self):
        self.seed([(self.b, self.a), (self.c, self.a), (self.c, self.b), (self.d, self.a), (self.d, self.c)])
        response = self.client.put(f"/api/subtasks/{self.c}", json={
            "status_percent": 42, "predecessor_ids": [self.a, self.b],
        })
        self.assertEqual(response.status_code, 200, response.text)
        mcp_server.update_subtask(self.c, predecessor_ids=[self.b])
        self.assertEqual(redundant_dependencies(self.graph()), {(self.d, self.a)})
        before = self.snapshot()
        with self.assertRaises(ToolError):
            mcp_server.update_subtask(self.e, predecessor_ids=[self.d, self.a])
        self.assertEqual(self.snapshot(), before)
        response = self.client.put(f"/api/subtasks/{self.d}", json={"predecessor_ids": [self.c]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(redundant_dependencies(self.graph()))

    def test_redundant_edge_removal_preserves_every_transitive_dependency(self):
        graph = {0: set(), 1: set(), 2: {1}, 3: {1, 2}, 4: {1, 2, 3}, 5: {3, 4}}
        before = dependency_ancestors(graph)
        for child, parent in sorted(redundant_dependencies(graph)):
            self.assertGreaterEqual(len(dependency_path(graph, child, parent)), 3)
            graph[child].remove(parent)
        self.assertEqual(dependency_ancestors(graph), before)
        self.assertFalse(redundant_dependencies(graph))

    def test_concurrent_additions_cannot_jointly_create_a_redundancy_or_cycle(self):
        for initial, additions in (
            ([(self.c, self.a)], [(self.b, self.a), (self.c, self.b)]),
            ([], [(self.a, self.b), (self.b, self.a)]),
        ):
            with self.subTest(additions=additions):
                self.seed(initial)
                barrier = threading.Barrier(2)

                def add(edge):
                    barrier.wait(timeout=5)
                    try:
                        with db_transaction() as db:
                            add_subtask_dependency(db, *edge)
                        return 200
                    except HTTPException as error:
                        return error.status_code

                with ThreadPoolExecutor(max_workers=2) as executor:
                    results = list(executor.map(add, additions))
                self.assertEqual(sorted(results), [200, 400])
                graph = self.graph()
                self.assertFalse(redundant_dependencies(graph))
                self.assertTrue(all(node not in ancestors for node, ancestors in dependency_ancestors(graph).items()))


if __name__ == '__main__':
    unittest.main()
