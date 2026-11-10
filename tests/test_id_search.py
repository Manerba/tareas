"""Exact UI ID lookups respect the same visibility as tasks and handoffs."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from dashboard import api_tasks, database, search_service
from dashboard.auth import get_current_user


class IdSearchTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='tareas-id-search-')
        self.addCleanup(temp.cleanup)
        for name, value in {'DB_DIR': Path(temp.name), 'DB_PATH': Path(temp.name) / 'test.db'}.items():
            patcher = patch.object(database, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        database.init_db()
        self.db = database.get_db()
        self.addCleanup(self.db.close)
        self.db.execute("INSERT INTO users (id, username, password_hash) VALUES (2, 'reader', 'unused')")
        for entity_id in (11, 12, 13, 14, 111):
            self.db.execute("INSERT INTO tasks (id, name, task_type, created_by, description) VALUES (?, '#11 in name', 'projekt', 1, '#11 in content')", (entity_id,))
        self.db.execute("INSERT INTO sub_tasks (id, project_id, name, description, position_number) VALUES (11, 13, 'Subtask', '', 1), (12, 14, 'Content #11', '#11', 11)")
        self.db.execute("INSERT INTO task_note_entries (id, task_id, user_id, content) VALUES (11, 14, 1, ''), (12, 12, 1, 'Text #11')")
        self.db.execute("INSERT INTO sub_task_note_entries (id, sub_task_id, user_id, content) VALUES (11, 12, 1, '')")
        self.db.execute("INSERT INTO task_notes (id, task_id, user_id, content) VALUES (11, 12, 1, '#11')")
        self.db.execute("INSERT INTO sub_task_notes (id, sub_task_id, user_id, content) VALUES (11, 12, 1, '#11')")
        self.db.commit()
        self.admin = {'id': 1, 'is_admin': True, 'auth_source': 'local'}
        self.reader = {'id': 2, 'is_admin': False, 'auth_source': 'local'}
        self.user = self.admin

    def lookup(self, query='#11'):
        return search_service.search_ids(self.db, self.user, query)

    def test_exact_ids_in_four_namespaces_ignore_contents_notes_and_positions(self):
        self.assertEqual(self.lookup(), [
            {'type': 'task', 'id': 11, 'task_id': 11, 'subtask_id': None},
            {'type': 'sub_task', 'id': 11, 'task_id': 13, 'subtask_id': 11},
            {'type': 'task_handoff', 'id': 11, 'task_id': 14, 'subtask_id': None},
            {'type': 'sub_task_handoff', 'id': 11, 'task_id': 14, 'subtask_id': 12},
        ])
        for query in ('#011', '  #11  ', '# 11'):
            self.assertEqual(self.lookup(query), self.lookup())
        self.assertEqual(self.lookup('#1'), [])

    def test_incomplete_malformed_and_out_of_range_queries_never_search_contents(self):
        for query in ('', '11', '#', '# ', '#0', '#000', '#-11', '#11x', '#11 12', '##11', '#11*',
                      '#11 OR 1=1', '#11.0', '#１１', '#9223372036854775808', '#' + '1' * 1001):
            with self.subTest(query=query[:25]):
                self.assertEqual(self.lookup(query), [])
        self.assertEqual(self.lookup('#9223372036854775807'), [])

    def test_current_permissions_assignments_sharing_and_revocation(self):
        self.user = self.reader
        self.assertEqual(self.lookup(), [])
        self.db.execute('UPDATE sub_tasks SET assigned_to=2 WHERE id IN (11,12)')
        self.assertEqual({hit['type'] for hit in self.lookup()}, {'sub_task', 'sub_task_handoff'})
        self.db.execute('UPDATE sub_tasks SET assigned_to=NULL')
        self.db.execute('INSERT INTO project_members (project_id, user_id, can_read) VALUES (14, 2, 1)')
        self.assertEqual({hit['type'] for hit in self.lookup()}, {'task_handoff', 'sub_task_handoff'})
        self.db.execute('DELETE FROM project_members WHERE user_id=2')
        self.assertEqual(self.lookup(), [])
        self.db.execute('UPDATE tasks SET assigned_to=2 WHERE id IN (11,13,14)')
        self.assertEqual(len(self.lookup()), 4)
        self.db.execute('UPDATE tasks SET assigned_to=NULL')
        self.db.execute('UPDATE tasks SET created_by=2 WHERE id=11')
        self.assertEqual([hit['type'] for hit in self.lookup()], ['task'])

    def test_read_visibility_includes_edit_and_create_shares_and_legacy_tasks(self):
        self.user = self.reader
        self.db.execute('INSERT INTO project_members (project_id, user_id, can_read, can_edit, can_create) VALUES (11,2,0,1,0), (13,2,0,0,1)')
        self.assertEqual({hit['type'] for hit in self.lookup()}, {'task', 'sub_task'})
        self.db.execute('UPDATE tasks SET created_by=NULL WHERE id=14')
        self.assertEqual(len(self.lookup()), 4)

    def test_move_and_delete_update_results_without_search_index(self):
        self.db.execute('UPDATE sub_tasks SET project_id=12 WHERE id=12')
        hit = next(hit for hit in self.lookup() if hit['type'] == 'sub_task_handoff')
        self.assertEqual(hit['task_id'], 12)
        self.db.execute('DELETE FROM task_note_entries WHERE id=11')
        self.db.execute('DROP TABLE search_fts')
        self.assertNotIn('task_handoff', {hit['type'] for hit in self.lookup()})

    def test_rest_returns_only_authorized_metadata_and_requires_session(self):
        app = FastAPI()
        app.include_router(api_tasks.router)
        app.dependency_overrides[get_current_user] = lambda: self.user
        with TestClient(app) as client:
            response = client.get('/api/tasks/id-search', params={'query': '#11'})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json(), {'items': self.lookup()})
            self.assertNotIn('content', response.text)
            self.assertNotIn('description', response.text)
            self.user = self.reader
            self.assertEqual(client.get('/api/tasks/id-search', params={'query': '#11'}).json(), {'items': []})
            del app.dependency_overrides[get_current_user]
            self.assertEqual(client.get('/api/tasks/id-search', params={'query': '#11'}).status_code, 401)


if __name__ == '__main__':
    unittest.main()
