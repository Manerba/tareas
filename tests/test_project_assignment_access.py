"""Projektzuweisung vererbt Zugriff auf Teilaufgaben-Notizen und Handoffs."""

import json
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastmcp.exceptions import ToolError

from dashboard import api_tasks, database, mcp_server
from dashboard.auth import get_current_user


class ProjectAssignmentAccessTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='tareas-assignment-access-')
        self.addCleanup(temp.cleanup)
        for name, value in {'DB_DIR': Path(temp.name), 'DB_PATH': Path(temp.name) / 'test.db'}.items():
            patcher = patch.object(database, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        database.init_db()
        self.users = {}
        with closing(database.get_db()) as db, db:
            for name in ('assignee', 'subtask_assignee', 'reader', 'denied', 'outsider'):
                uid = db.execute(
                    "INSERT INTO users (username, password_hash, auth_source) VALUES (?, 'unused', 'mcp')", (name,),
                ).lastrowid
                self.users[name] = {'id': uid, 'username': name, 'auth_source': 'mcp', 'is_admin': False}
            self.project = db.execute(
                "INSERT INTO tasks (name, task_type, created_by, assigned_to) VALUES ('accessmarker project', 'projekt', 1, ?)",
                (self.users['assignee']['id'],),
            ).lastrowid
            self.other = db.execute(
                "INSERT INTO tasks (name, task_type, created_by) VALUES ('accessmarker other', 'projekt', 1)",
            ).lastrowid
            self.subtask = db.execute(
                "INSERT INTO sub_tasks (name, project_id, created_by) VALUES ('accessmarker unassigned', ?, 1)",
                (self.project,),
            ).lastrowid
            self.assigned_subtask = db.execute(
                "INSERT INTO sub_tasks (name, project_id, created_by, assigned_to) VALUES ('accessmarker assigned', ?, 1, ?)",
                (self.project, self.users['subtask_assignee']['id']),
            ).lastrowid
            self.other_subtask = db.execute(
                "INSERT INTO sub_tasks (name, project_id, created_by) VALUES ('accessmarker other', ?, 1)", (self.other,),
            ).lastrowid
            for name, can_read in (('reader', 1), ('denied', 0)):
                db.execute(
                    'INSERT INTO project_members (project_id, user_id, can_read, can_edit, can_create) VALUES (?, ?, ?, 0, 0)',
                    (self.project, self.users[name]['id'], can_read),
                )
            for project_id, subtask_id in ((self.project, None), (self.project, self.subtask),
                                          (self.project, self.assigned_subtask), (self.other, None),
                                          (self.other, self.other_subtask)):
                tables = ('sub_task_notes', 'sub_task_note_entries') if subtask_id else ('task_notes', 'task_note_entries')
                column = 'sub_task_id' if subtask_id else 'task_id'
                for table in tables:
                    db.execute(
                        f"INSERT INTO {table} ({column}, user_id, content) VALUES (?, 1, 'accessmarker admin context')",
                        (subtask_id or project_id,),
                    )
        self.user = self.users['assignee']
        token = mcp_server.current_mcp_user.set(self.user)
        self.addCleanup(mcp_server.current_mcp_user.reset, token)
        app = FastAPI()
        app.include_router(api_tasks.router)
        app.dependency_overrides[get_current_user] = lambda: self.user
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def login(self, name):
        self.user = self.users[name]
        mcp_server.current_mcp_user.set(self.user)

    def url(self, subtask_id=None, project_id=None):
        prefix = f'/api/tasks/{project_id or self.project}'
        return prefix + (f'/subtasks/{subtask_id}' if subtask_id else '')

    def test_project_assignee_reads_notes_handoffs_and_search_for_all_subtasks(self):
        for subtask_id in (None, self.subtask, self.assigned_subtask):
            with self.subTest(subtask_id=subtask_id):
                self.assertEqual(mcp_server.note_list(self.project, subtask_id)[0]['user_id'], 1)
                self.assertEqual(mcp_server.handoff_list(self.project, subtask_id)[0]['user_id'], 1)
                for endpoint in ('notes', 'note-entries'):
                    response = self.client.get(f'{self.url(subtask_id)}/{endpoint}')
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(len(response.json()['items']), 1)
        results = mcp_server.search('accessmarker', limit=200)
        self.assertEqual(len(results), 9)  # Project and two subtasks, each with a note and handoff.
        self.assertEqual({row['type'] for row in results}, {
            'task', 'sub_task', 'task_note', 'sub_task_note', 'task_handoff', 'sub_task_handoff',
        })
        with closing(database.get_db()) as db:
            self.assertTrue(mcp_server._can_read_subtask(db, self.project, self.subtask, self.user))
            self.assertFalse(mcp_server._can_read_subtask(db, self.other, self.other_subtask, self.user))

    def test_project_assignee_writes_and_deletes_only_own_annotations(self):
        for subtask_id in (None, self.subtask, self.assigned_subtask):
            with self.subTest(subtask_id=subtask_id):
                mcp_server.note_write(self.project, 'My note', subtask_id)
                response = self.client.put(f'{self.url(subtask_id)}/notes', json={'content': 'Updated own note'})
                self.assertEqual(response.status_code, 200, response.text)
                notes = {note['user_id']: note['content'] for note in mcp_server.note_list(self.project, subtask_id)}
                self.assertEqual(notes[self.user['id']], 'Updated own note')
                self.assertEqual(notes[1], 'accessmarker admin context')
                mcp_server.note_delete(self.project, subtask_id)
                self.assertEqual(len(mcp_server.note_list(self.project, subtask_id)), 1)
                for infer_scope in (False, True):
                    entry = mcp_server.handoff_add(self.project, 'My handoff', subtask_id)
                    deleted = mcp_server.handoff_delete(self.project, entry['handoff_id'], None if infer_scope else subtask_id)
                    self.assertTrue(deleted['deleted'])
                    self.assertEqual(len(mcp_server.handoff_list(self.project, subtask_id)), 1)
        with closing(database.get_db()) as db:
            audits = db.execute('SELECT actor_user_id, actor_type, changes_json FROM audit_log').fetchall()
        self.assertTrue(audits)
        self.assertTrue(all(row['actor_user_id'] == self.user['id'] and row['actor_type'] == 'mcp' for row in audits))
        self.assertEqual(json.loads(audits[-1]['changes_json'])['owner_user_id'], self.user['id'])

    def test_foreign_authors_and_wrong_project_scopes_remain_protected(self):
        for subtask_id in (None, self.subtask):
            entry = mcp_server.handoff_list(self.project, subtask_id)[0]
            with self.assertRaises(ToolError):
                mcp_server.handoff_delete(self.project, entry['handoff_id'])
            with self.assertRaises(ToolError):
                mcp_server.handoff_delete(self.project, entry['handoff_id'], subtask_id)
            with self.assertRaises(ToolError):
                mcp_server.note_update(self.project, 1, 'Forbidden', subtask_id)
            with self.assertRaises(ToolError):
                mcp_server.handoff_update(self.project, entry['handoff_id'], 'Forbidden', subtask_id)
            response = self.client.put(f'{self.url(subtask_id)}/notes/1', json={'content': 'Forbidden'})
            self.assertEqual(response.status_code, 403)
        for project_id, subtask_id in ((self.other, self.other_subtask), (self.project, self.other_subtask),
                                       (self.other, self.subtask), (self.project, 99999)):
            for operation in (mcp_server.note_list, mcp_server.handoff_list):
                with self.assertRaises(ToolError):
                    operation(project_id, subtask_id)
            for operation in (mcp_server.note_write, mcp_server.handoff_add):
                with self.assertRaises(ToolError):
                    operation(project_id, 'Forbidden', subtask_id)
            for endpoint in ('notes', 'note-entries'):
                response = self.client.get(f'{self.url(subtask_id, project_id)}/{endpoint}')
                self.assertIn(response.status_code, (403, 404))

    def test_individual_assignment_team_members_and_outsiders_keep_their_scope(self):
        for name, allowed in (('subtask_assignee', {self.assigned_subtask}),
                              ('reader', {None, self.subtask, self.assigned_subtask}),
                              ('denied', set()), ('outsider', set())):
            self.login(name)
            for subtask_id in (None, self.subtask, self.assigned_subtask):
                for operation in (mcp_server.note_list, mcp_server.handoff_list):
                    if subtask_id in allowed:
                        self.assertEqual(len(operation(self.project, subtask_id)), 1)
                    else:
                        with self.assertRaises(ToolError):
                            operation(self.project, subtask_id)
                for endpoint in ('notes', 'note-entries'):
                    self.assertEqual(self.client.get(f'{self.url(subtask_id)}/{endpoint}').status_code,
                                     200 if subtask_id in allowed else 403)
            self.assertEqual(len(mcp_server.search('accessmarker', limit=200)), 3 * len(allowed), name)

    def test_reassignment_revokes_inherited_access_without_changing_individual_assignments(self):
        mcp_server.note_write(self.project, 'Earlier contribution', self.subtask)
        with closing(database.get_db()) as db, db:
            db.execute('UPDATE tasks SET assigned_to = ? WHERE id = ?', (self.users['outsider']['id'], self.project))
        for operation in (mcp_server.note_list, mcp_server.handoff_list):
            with self.assertRaises(ToolError):
                operation(self.project, self.subtask)
        self.assertEqual(self.client.get(f'{self.url(self.subtask)}/notes').status_code, 403)
        self.assertEqual(mcp_server.search('accessmarker'), [])
        self.login('outsider')
        self.assertEqual(len(mcp_server.note_list(self.project, self.subtask)), 2)
        self.login('subtask_assignee')
        self.assertEqual(len(mcp_server.note_list(self.project, self.assigned_subtask)), 1)
        with self.assertRaises(ToolError):
            mcp_server.note_list(self.project, self.subtask)

    def test_project_assignment_grants_read_access_without_subtask_edit_or_create_rights(self):
        for with_membership in (False, True):
            if with_membership:
                with closing(database.get_db()) as db, db:
                    db.execute(
                        'INSERT INTO project_members (project_id, user_id, can_read, can_edit, can_create) VALUES (?, ?, 0, 0, 0)',
                        (self.project, self.user['id']),
                    )
            response = self.client.get(f'/api/tasks/{self.project}/subtasks')
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual({item['id'] for item in response.json()['items']}, {self.subtask, self.assigned_subtask})
            for item in response.json()['items']:
                self.assertEqual(item['permissions'], {'can_read': True, 'can_edit': False, 'can_create': False})
            self.assertEqual(len(mcp_server.note_list(self.project, self.subtask)), 1)
            self.assertEqual(self.client.put(f'/api/subtasks/{self.subtask}', json={'description': 'Forbidden'}).status_code, 403)
            self.assertEqual(self.client.post(f'/api/tasks/{self.project}/subtasks', json={'name': 'Forbidden'}).status_code, 403)


if __name__ == '__main__':
    unittest.main()
