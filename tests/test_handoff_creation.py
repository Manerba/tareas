"""Handoffs aus der UI: Beitragsrechte, neue Eintraege und MCP-Kompatibilitaet."""

import json
import unittest
from contextlib import closing

from dashboard import database, mcp_server, note_service
from dashboard.mcp_errors import MCPToolError
import test_description_revisions as description_tests


class HandoffCreationTests(unittest.TestCase):
    setUp = description_tests.DescriptionRevisionTests.setUp
    call_mcp = description_tests.DescriptionRevisionTests.call_mcp

    def url(self, task_id=None, subtask_id=None):
        path = f'/api/tasks/{task_id or self.task}'
        return path + (f'/subtasks/{subtask_id}' if subtask_id else '') + '/note-entries'

    def test_creation_for_tasks_projects_and_subtasks_preserves_text_and_audits_author(self):
        with closing(database.get_db()) as db, db:
            task = db.execute("INSERT INTO tasks (name, created_by) VALUES ('Task', ?)",
                              (self.user['id'],)).lastrowid
        content = '  ## Handoffcreationmarker ü ✓\n\n**Ergebnis** & <script>alert(1)</script>\n'
        for task_id, subtask_id in ((task, None), (self.task, None), (self.task, self.subtask)):
            with self.subTest(task_id=task_id, subtask_id=subtask_id):
                url = self.url(task_id, subtask_id)
                self.client.put(url.replace('/note-entries', '/notes'), json={'content': 'Meine Notiz'})
                created_ids = []
                for _ in range(2):
                    response = self.client.post(url, json={'content': content, 'user_id': 1, 'created_at': '1900-01-01'})
                    self.assertEqual(response.status_code, 201, response.text)
                    entry = response.json()
                    created_ids.append(entry['id'])
                    self.assertEqual(entry['content'], content)
                    self.assertEqual(entry['user_id'], self.user['id'])
                    self.assertEqual(entry['task_id'], task_id)
                    self.assertEqual(entry['subtask_id'], subtask_id)
                    self.assertEqual(entry['format'], 'markdown')
                    self.assertTrue(entry['can_write'])
                    self.assertRegex(entry['created_at'], r'^\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}$')
                    self.assertEqual(entry['revision'], note_service.handoff_revision(content, 'markdown'))
                    self.assertEqual(self.client.get(f'{url}/{entry["id"]}').json(), entry)
                    with closing(database.get_db()) as db:
                        audit = db.execute('SELECT * FROM audit_log ORDER BY id DESC LIMIT 1').fetchone()
                    self.assertEqual(audit['action'], 'create')
                    self.assertEqual(audit['entity_type'], 'sub_task_handoff' if subtask_id else 'task_handoff')
                    self.assertEqual(audit['entity_id'], subtask_id or task_id)
                    self.assertEqual(audit['actor_user_id'], self.user['id'])
                    self.assertEqual(json.loads(audit['changes_json']), {
                        'handoff_id': f'{"subtask" if subtask_id else "task"}:{entry["id"]}',
                        'local_id': entry['id'], 'owner_user_id': self.user['id'], 'content_len': len(content),
                    })
                self.assertNotEqual(*created_ids)
                listing = self.client.get(url).json()
                self.assertTrue(listing['can_create'])
                self.assertEqual([item['id'] for item in listing['items']], created_ids[::-1])
                notes = self.client.get(url.replace('/note-entries', '/notes')).json()['items']
                self.assertEqual([note['content'] for note in notes], ['Meine Notiz'])
                edited = self.client.put(f'{url}/{entry["id"]}', json={'content': 'Korrektur', 'revision': entry['revision']})
                self.assertEqual(edited.status_code, 200, edited.text)
                self.assertEqual(len(self.client.get(url).json()['items']), 2)
        hits = self.call_mcp(mcp_server.search, 'handoffcreationmarker')
        self.assertEqual(len(hits), 3)

    def test_contributors_and_read_only_users_receive_matching_capabilities(self):
        for user in (self.admin, *self.users.values()):
            self.user = user
            for subtask_id in (None, self.subtask):
                with self.subTest(user=user['username'], subtask_id=subtask_id):
                    allowed = user['username'] in ('admin', 'owner', 'editor', 'assignee')
                    listing = self.client.get(self.url(subtask_id=subtask_id))
                    if user['username'] == 'outsider':
                        self.assertEqual(listing.status_code, 403)
                    else:
                        self.assertEqual(listing.status_code, 200)
                        self.assertEqual(listing.json()['can_create'], allowed)
                    response = self.client.post(self.url(subtask_id=subtask_id), json={'content': 'UI handoff'})
                    self.assertEqual(response.status_code, 201 if allowed else 403, response.text)

    def test_subtask_assignment_does_not_grant_project_contributions_and_create_is_not_contribute(self):
        self.user = self.users['outsider']
        with closing(database.get_db()) as db, db:
            db.execute('UPDATE sub_tasks SET assigned_to = ? WHERE id = ?', (self.user['id'], self.subtask))
            db.execute('INSERT INTO project_members (project_id, user_id, can_read, can_edit, can_create) VALUES (?, ?, 1, 0, 1)',
                       (self.task, self.user['id']))
        self.assertFalse(self.client.get(self.url()).json()['can_create'])
        self.assertEqual(self.client.post(self.url(), json={'content': 'Forbidden'}).status_code, 403)
        self.assertTrue(self.client.get(self.url(subtask_id=self.subtask)).json()['can_create'])
        self.assertEqual(self.client.post(self.url(subtask_id=self.subtask), json={'content': 'Assigned'}).status_code, 201)
        with closing(database.get_db()) as db, db:
            db.execute('UPDATE sub_tasks SET assigned_to = NULL WHERE id = ?', (self.subtask,))
        self.assertFalse(self.client.get(self.url(subtask_id=self.subtask)).json()['can_create'])
        self.assertEqual(self.client.post(self.url(subtask_id=self.subtask), json={'content': 'Revoked'}).status_code, 403)

    def test_invalid_content_target_and_revoked_permission_do_not_create_entries(self):
        for subtask_id in (None, self.subtask):
            url = self.url(subtask_id=subtask_id)
            for body in ({}, {'content': None}, {'content': 42}, {'content': ''}, {'content': ' \n\t '}):
                self.assertEqual(self.client.post(url, json=body).status_code, 422)
            self.assertTrue(self.client.get(url).json()['can_create'])
        with closing(database.get_db()) as db, db:
            other = db.execute("INSERT INTO tasks (name, task_type, created_by) VALUES ('Other', 'projekt', ?)",
                               (self.user['id'],)).lastrowid
            db.execute('UPDATE project_members SET can_edit=0 WHERE project_id=? AND user_id=?', (self.task, self.user['id']))
        for subtask_id in (None, self.subtask):
            self.assertEqual(self.client.post(self.url(subtask_id=subtask_id), json={'content': 'Revoked'}).status_code, 403)
        for url in (self.url(999999), self.url(other, self.subtask), self.url(subtask_id=999999)):
            self.assertEqual(self.client.post(url, json={'content': 'Wrong target'}).status_code, 404)
        with closing(database.get_db()) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM task_note_entries').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM sub_task_note_entries').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM audit_log').fetchone()[0], 0)

    def test_mcp_creation_keeps_response_and_structured_errors(self):
        for subtask_id in (None, self.subtask):
            result = self.call_mcp(mcp_server.handoff_add, self.task, 'MCP handoff', subtask_id)
            self.assertEqual(result, {
                'saved': True, 'task_id': self.task, 'subtask_id': subtask_id,
                'local_id': result['local_id'],
                'handoff_id': f'{"subtask" if subtask_id else "task"}:{result["local_id"]}',
            })
            self.assertEqual(self.client.get(self.url(subtask_id=subtask_id)).json()['items'][0]['id'], result['local_id'])
            with self.assertRaises(MCPToolError) as error:
                self.call_mcp(mcp_server.handoff_add, self.task, ' \t ', subtask_id)
            self.assertEqual(error.exception.code, 'empty_content')
            self.assertEqual(error.exception.field, 'content')


if __name__ == '__main__':
    unittest.main()
