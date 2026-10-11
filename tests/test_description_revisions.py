"""REST/MCP description edits share revisions, permissions and a write transaction."""

import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastmcp.exceptions import ToolError

from dashboard import api_tasks, database, mcp_server
from dashboard.auth import get_current_user
from dashboard.description_service import description_revision, patch_description


SOURCE = '# Übersicht\n\n**Prüfung** & <xml> ✓\nEin Ziel.\n'


class DescriptionRevisionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='tareas-description-')
        self.addCleanup(temp.cleanup)
        for name, value in {'DB_DIR': Path(temp.name), 'DB_PATH': Path(temp.name) / 'test.db'}.items():
            patcher = patch.object(database, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        database.init_db()
        self.admin = {'id': 1, 'is_admin': True, 'username': 'admin'}
        with closing(database.get_db()) as db, db:
            self.users = {}
            for name in ('owner', 'editor', 'reader', 'assignee', 'outsider'):
                uid = db.execute("INSERT INTO users (username, password_hash) VALUES (?, 'unused')", (name,)).lastrowid
                self.users[name] = {'id': uid, 'username': name, 'is_admin': False, 'auth_source': 'local'}
            self.task = db.execute("INSERT INTO tasks (name, task_type, created_by, assigned_to, description) VALUES ('Project', 'projekt', ?, ?, ?)",
                                   (self.users['owner']['id'], self.users['assignee']['id'], SOURCE)).lastrowid
            self.subtask = db.execute("INSERT INTO sub_tasks (name, project_id, description) VALUES ('Subtask', ?, ?)", (self.task, SOURCE)).lastrowid
            for name in ('editor', 'reader'):
                db.execute('INSERT INTO project_members (project_id, user_id, can_read, can_edit) VALUES (?, ?, 1, ?)',
                           (self.task, self.users[name]['id'], name == 'editor'))
        self.user = self.users['editor']
        app = FastAPI()
        app.include_router(api_tasks.router)
        app.dependency_overrides[get_current_user] = lambda: self.user
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        mail = patch.object(api_tasks, 'notify_event', return_value=None)
        mail.start()
        self.addCleanup(mail.stop)

    def row(self, subtask=False):
        url = f'/api/tasks/{self.task}/subtasks' if subtask else '/api/tasks'
        return self.client.get(url).json()['items'][0]

    def url(self, subtask=False):
        return f'/api/subtasks/{self.subtask}' if subtask else f'/api/tasks/{self.task}'

    def call_mcp(self, function, *args, **kwargs):
        token = mcp_server.current_mcp_user.set(self.user)
        try:
            return function(*args, **kwargs)
        finally:
            mcp_server.current_mcp_user.reset(token)

    def test_rest_revisions_full_update_legacy_and_independent_status(self):
        for subtask in (False, True):
            revision = self.row(subtask)['description_revision']
            self.assertEqual(revision, description_revision(SOURCE, 'markdown'))
            changed = self.client.put(self.url(subtask), json={'description': SOURCE + 'Änderung', 'description_revision': revision})
            self.assertEqual(changed.status_code, 200, changed.text)
            self.assertEqual(changed.json()['description_revision'], self.row(subtask)['description_revision'])
            self.assertEqual(self.client.put(self.url(subtask), json={'description': 'lost', 'description_revision': revision}).status_code, 409)
            status = {'status_percent': 25} if subtask else {'priority': 19}
            self.assertEqual(self.client.put(self.url(subtask), json={**status, 'description_revision': revision}).status_code, 200)
            self.assertEqual(self.row(subtask)['description'], SOURCE + 'Änderung')
            self.assertEqual(self.client.put(self.url(subtask), json={'description': 'Legacy unconditional'}).status_code, 200)
            self.assertEqual(self.row(subtask)['description'], 'Legacy unconditional')

    def test_patch_exact_unicode_missing_ambiguous_and_empty_targets(self):
        for subtask in (False, True):
            url = self.url(subtask) + '/description'
            for body in ({'operation': 'replace', 'text': 'X', 'old_text': 'missing'},
                         {'operation': 'replace', 'text': 'X', 'old_text': ''},
                         {'operation': 'append', 'text': 'X', 'old_text': 'Ein Ziel.'}):
                response = self.client.patch(url, json={**body, 'revision': self.row(subtask)['description_revision']})
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(self.row(subtask)['description'], SOURCE)
            revision = self.row(subtask)['description_revision']
            result = self.client.patch(url, json={'operation': 'replace', 'text': '`$<&>`\n🧭', 'old_text': 'Ein Ziel.', 'revision': revision})
            self.assertEqual(result.status_code, 200, result.text)
            self.assertNotIn('description', result.json())
            self.assertEqual(self.row(subtask)['description'], SOURCE.replace('Ein Ziel.', '`$<&>`\n🧭'))
            self.client.put(self.url(subtask), json={'description': 'aaaa'})
            for old in ('a', 'aaa'):
                response = self.client.patch(url, json={'operation': 'replace', 'text': 'x', 'old_text': old, 'revision': self.row(subtask)['description_revision']})
                self.assertEqual(response.status_code, 422)
                self.assertEqual(self.row(subtask)['description'], 'aaaa')
            self.assertEqual(self.client.patch(url, json={'operation': 'append', 'text': 'X'}).status_code, 422)

    def test_patch_preserves_legacy_format_and_exact_newlines(self):
        for subtask in (False, True):
            with closing(database.get_db()) as db, db:
                sql = 'UPDATE sub_tasks SET description_format = ? WHERE id = ?' if subtask else 'UPDATE tasks SET description_format = ? WHERE id = ?'
                db.execute(sql, ('legacy', self.subtask if subtask else self.task))
            revision = self.row(subtask)['description_revision']
            result = self.client.patch(self.url(subtask) + '/description', json={'operation': 'append', 'text': '\r\n<p>Ä</p>', 'revision': revision})
            self.assertEqual(result.status_code, 200)
            self.assertEqual(self.row(subtask)['description'], SOURCE + '\r\n<p>Ä</p>')
            self.assertEqual(self.row(subtask)['description_format'], 'legacy')
            self.assertNotEqual(result.json()['description_revision'], revision)
            empty = self.client.patch(self.url(subtask) + '/description', json={'operation': 'append', 'text': '', 'revision': result.json()['description_revision']})
            self.assertEqual(empty.json()['description_revision'], result.json()['description_revision'])

    def test_permissions_and_revocation(self):
        revision = description_revision(SOURCE, 'markdown')
        for role in ('reader', 'assignee', 'outsider'):
            self.user = self.users[role]
            for subtask in (False, True):
                self.assertEqual(self.client.patch(self.url(subtask) + '/description', json={'operation': 'append', 'text': 'no', 'revision': revision}).status_code, 403)
                # Existing task PUT silently ignores description for a status-only assignee.
                expected = 200 if role == 'assignee' and not subtask else 403
                self.assertEqual(self.client.put(self.url(subtask), json={'description': 'no', 'description_revision': revision}).status_code, expected)
                with self.assertRaises(ToolError) as error:
                    self.call_mcp(mcp_server.patch_description, self.task, revision, 'append', 'no',
                                  subtask_id=self.subtask if subtask else None)
                self.assertEqual(error.exception.code, 'permission_denied')
        self.user = self.users['editor']
        with closing(database.get_db()) as db, db:
            db.execute('UPDATE project_members SET can_edit=0 WHERE project_id=?', (self.task,))
        self.assertEqual(self.client.patch(self.url() + '/description', json={'operation': 'append', 'text': 'no', 'revision': revision}).status_code, 403)

    def test_mcp_patch_exact_result_and_project_scope(self):
        for subtask in (False, True):
            revision = self.row(subtask)['description_revision']
            result = self.call_mcp(mcp_server.patch_description, self.task, revision, 'append', '\n🧭',
                                   subtask_id=self.subtask if subtask else None)
            self.assertNotIn('description', result)
            self.assertEqual(result['description_length'], len(SOURCE + '\n🧭'))
            result = self.call_mcp(mcp_server.patch_description, self.task, result['description_revision'],
                                   'replace', '✓', '🧭', self.subtask if subtask else None)
            self.assertEqual(self.row(subtask)['description'], SOURCE + '\n✓')
            self.assertEqual(result['description_revision'], self.row(subtask)['description_revision'])
        with self.assertRaises(ToolError) as error:
            self.call_mcp(mcp_server.patch_description, self.task + 100, revision, 'append', 'wrong scope', subtask_id=self.subtask)
        self.assertEqual(error.exception.code, 'subtask_not_found')

    def test_mcp_and_rest_competing_writers_have_one_winner(self):
        for subtask in (False, True):
            revision = self.row(subtask)['description_revision']
            barrier = threading.Barrier(2)
            def rest():
                barrier.wait()
                return self.client.put(self.url(subtask), json={'description': 'REST', 'description_revision': revision}).status_code
            def mcp():
                barrier.wait()
                try:
                    self.call_mcp(mcp_server.patch_description, self.task, revision, 'append', 'MCP', subtask_id=self.subtask if subtask else None)
                    return 200
                except ToolError as exc:
                    self.assertEqual(exc.code, 'revision_conflict')
                    return 409
            with ThreadPoolExecutor(max_workers=2) as pool:
                a, b = pool.submit(rest), pool.submit(mcp)
                self.assertEqual(sorted([a.result(), b.result()]), [200, 409])
            self.assertIn(self.row(subtask)['description'], ('REST', SOURCE + 'MCP'))

    def test_mcp_full_updates_honor_rest_revision_and_errors(self):
        for subtask in (False, True):
            revision = self.row(subtask)['description_revision']
            update = mcp_server.update_subtask if subtask else mcp_server.update_project
            entity_id = self.subtask if subtask else self.task
            self.call_mcp(update, entity_id, description='MCP', description_revision=revision)
            with self.assertRaises(ToolError) as error:
                self.call_mcp(update, entity_id, description='stale', description_revision=revision)
            self.assertEqual(error.exception.code, 'revision_conflict')
            for old, code in (('missing', 'description_target_not_found'), ('', 'invalid_description_target')):
                with self.assertRaises(ToolError) as error:
                    self.call_mcp(mcp_server.patch_description, self.task, self.row(subtask)['description_revision'], 'replace', 'x', old, self.subtask if subtask else None)
                self.assertEqual(error.exception.code, code)

    def test_transaction_rolls_back_all_fields_and_no_success_audit(self):
        revision = self.row()['description_revision']
        with closing(database.get_db()) as db, db:
            db.execute("CREATE TRIGGER reject_description BEFORE UPDATE OF description ON tasks BEGIN SELECT RAISE(ABORT, 'injected failure'); END")
            audit_before = db.execute('SELECT COUNT(*) FROM audit_log').fetchone()[0]
        with self.assertRaisesRegex(Exception, 'injected failure'):
            patch_description(self.task, self.user, revision=revision, operation='append', text='X')
        self.assertEqual(self.row()['description'], SOURCE)
        with closing(database.get_db()) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM audit_log').fetchone()[0], audit_before)


if __name__ == '__main__':
    unittest.main()
