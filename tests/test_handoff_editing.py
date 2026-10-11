"""Own handoff corrections: server time, current rights, chronology and conflicts."""

import json
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from dashboard import database, mcp_server, note_service
from dashboard.mcp_errors import MCPToolError
import test_description_revisions as description_tests


class HandoffEditingTests(unittest.TestCase):
    setUp = description_tests.DescriptionRevisionTests.setUp
    call_mcp = description_tests.DescriptionRevisionTests.call_mcp

    def create(self, subtask=False, age=0):
        self.created = datetime(2026, 10, 11, 12, 0, 0, tzinfo=timezone.utc)
        self.now = self.created + timedelta(seconds=age)
        table, column, scope = ('sub_task_note_entries', 'sub_task_id', self.subtask) if subtask else ('task_note_entries', 'task_id', self.task)
        with closing(database.get_db()) as db, db:
            entry = db.execute(f"INSERT INTO {table} ({column}, user_id, content, created_at) VALUES (?, ?, 'Original', ?)",
                               (scope, self.user['id'], self.created.strftime('%Y-%m-%d %H:%M:%S'))).lastrowid
        self.entry, self.table = entry, table
        self.handoff_id = f"{'subtask' if subtask else 'task'}:{entry}"
        self.url = f'/api/tasks/{self.task}' + (f'/subtasks/{self.subtask}' if subtask else '') + f'/note-entries/{entry}'

    def row(self):
        with closing(database.get_db()) as db:
            return dict(db.execute(f'SELECT * FROM {self.table} WHERE id=?', (self.entry,)).fetchone())

    def test_server_time_boundaries_and_chronology_for_both_scopes(self):
        for subtask in (False, True):
            for age in (-0.001, 0, 299.999, 300, 300.001):
                with self.subTest(subtask=subtask, age=age):
                    self.create(subtask, age)
                    with patch.object(note_service, '_utc_now', return_value=self.now):
                        before = self.row()
                        data = self.client.get(self.url).json()
                        allowed = 0 <= age < 300
                        self.assertEqual(data['can_write'], allowed)
                        response = self.client.put(self.url, json={'content': '## Korrektur ü ✓', 'revision': data['revision']})
                        self.assertEqual(response.status_code, 200 if allowed else 403, response.text)
                        after = self.row()
                        for field in ('id', 'user_id', 'created_at'):
                            self.assertEqual(after[field], before[field])
                        self.assertEqual(after['content'], '## Korrektur ü ✓' if allowed else 'Original')
                        if allowed:
                            with closing(database.get_db()) as db:
                                audit = db.execute('SELECT * FROM audit_log ORDER BY id DESC LIMIT 1').fetchone()
                            self.assertEqual(audit['actor_user_id'], self.user['id'])
                            changes = json.loads(audit['changes_json'])
                            self.assertFalse(changes['edited_by_admin'])
                            self.assertEqual(changes['owner_user_id'], self.user['id'])
                            self.assertNotIn('content', changes)

    def test_foreign_revoked_read_only_and_admin(self):
        for subtask in (False, True):
            self.user = self.users['editor']
            self.create(subtask, 100)
            revision = note_service.handoff_revision('Original', 'markdown')
            with patch.object(note_service, '_utc_now', return_value=self.now):
                self.user = self.users['owner']
                self.assertFalse(self.client.get(self.url).json()['can_write'])
                self.assertEqual(self.client.put(self.url, json={'content': 'foreign', 'revision': revision}).status_code, 403)
                self.user = self.users['editor']
                with closing(database.get_db()) as db, db:
                    db.execute('UPDATE project_members SET can_edit=0 WHERE project_id=? AND user_id=?', (self.task, self.user['id']))
                self.assertFalse(self.client.get(self.url).json()['can_write'])
                self.assertEqual(self.client.put(self.url, json={'content': 'revoked', 'revision': revision}).status_code, 403)
                with closing(database.get_db()) as db, db:
                    db.execute('DELETE FROM project_members WHERE project_id=? AND user_id=?', (self.task, self.user['id']))
                self.assertEqual(self.client.get(self.url).status_code, 403)
                self.assertEqual(self.client.put(self.url, json={'content': 'revoked', 'revision': revision}).status_code, 403)
            self.user = self.admin
            self.assertTrue(self.client.get(self.url).json()['can_write'])
            self.assertEqual(self.client.put(self.url, json={'content': 'Admin legacy no revision'}).status_code, 200)
            with closing(database.get_db()) as db, db:
                db.execute('INSERT INTO project_members (project_id,user_id,can_read,can_edit) VALUES (?,?,1,1)', (self.task,self.users['editor']['id']))

    def test_revision_required_empty_content_and_mcp_error_codes(self):
        for subtask in (False, True):
            self.create(subtask, 299)
            with patch.object(note_service, '_utc_now', return_value=self.now):
                self.assertEqual(self.client.put(self.url, json={'content': 'no token'}).status_code, 422)
                data = self.call_mcp(mcp_server.handoff_get, self.task, self.handoff_id)
                self.assertEqual(data['revision'], note_service.handoff_revision('Original', 'markdown'))
                self.assertTrue(data['can_write'])
                for content, revision, code in (('no token', None, 'revision_required'), ('', data['revision'], 'empty_content'), ('stale', '0'*64, 'revision_conflict')):
                    with self.assertRaises(MCPToolError) as exc:
                        self.call_mcp(mcp_server.handoff_update, self.task, self.handoff_id, content, revision=revision)
                    self.assertEqual(exc.exception.code, code)
                self.assertEqual(self.row()['content'], 'Original')
            with patch.object(note_service, '_utc_now', return_value=self.created + timedelta(seconds=300)):
                with self.assertRaises(MCPToolError) as exc:
                    self.call_mcp(mcp_server.handoff_update, self.task, self.handoff_id, 'late', revision=data['revision'])
                self.assertEqual(exc.exception.code, 'handoff_edit_window_expired')

    def test_rest_mcp_competition_and_admin_revision(self):
        for subtask in (False, True):
            self.create(subtask, 10)
            revision = note_service.handoff_revision('Original', 'markdown')
            barrier = threading.Barrier(2)
            def rest():
                barrier.wait()
                return self.client.put(self.url, json={'content': 'REST', 'revision': revision}).status_code
            def mcp():
                barrier.wait()
                try:
                    self.call_mcp(mcp_server.handoff_update, self.task, self.handoff_id, 'MCP', revision=revision)
                    return 200
                except MCPToolError as exc:
                    self.assertEqual(exc.code, 'revision_conflict')
                    return 409
            with patch.object(note_service, '_utc_now', return_value=self.now), ThreadPoolExecutor(max_workers=2) as pool:
                a, b = pool.submit(rest), pool.submit(mcp)
                self.assertEqual(sorted([a.result(), b.result()]), [200, 409])
            self.assertIn(self.row()['content'], ('REST', 'MCP'))
            self.user = self.admin
            self.assertEqual(self.client.put(self.url, json={'content': 'stale admin', 'revision': revision}).status_code, 409)
            self.user = self.users['editor']


if __name__ == '__main__':
    unittest.main()
