"""Markdown storage and non-destructive migration of legacy content."""

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from dashboard import api_tasks, database, mcp_server
from dashboard.auth import get_current_user


SOURCE = '# Scope\n\n- **Keep this**\n\n```html\n<div>Example & data</div>\n```\n'


class MarkdownContentTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='tareas-markdown-')
        self.addCleanup(temp.cleanup)
        for name, value in {'DB_DIR': Path(temp.name), 'DB_PATH': Path(temp.name) / 'test.db'}.items():
            patcher = patch.object(database, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        database.init_db()
        self.user = {'id': 1, 'username': 'admin', 'is_admin': True, 'auth_source': 'local'}
        app = FastAPI()
        app.include_router(api_tasks.router)
        app.dependency_overrides[get_current_user] = lambda: self.user
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        mail = patch.object(api_tasks, 'notify_event', return_value=None)
        mail.start()
        self.addCleanup(mail.stop)
        self.task_id = self.client.post('/api/tasks', json={
            'name': 'Markdown project', 'task_type': 'projekt', 'description': SOURCE,
        }).json()['id']
        self.subtask_id = self.client.post(f'/api/tasks/{self.task_id}/subtasks', json={
            'name': 'Markdown subtask', 'description': SOURCE,
        }).json()['id']

    def task(self):
        return next(row for row in self.client.get('/api/tasks').json()['items'] if row['id'] == self.task_id)

    def subtask(self):
        return self.client.get(f'/api/tasks/{self.task_id}/subtasks').json()['items'][0]

    def test_rest_round_trip_keeps_markdown_and_html_code_examples(self):
        for get_row, url in (
            (self.task, f'/api/tasks/{self.task_id}'),
            (self.subtask, f'/api/subtasks/{self.subtask_id}'),
        ):
            with self.subTest(url=url):
                self.assertEqual(get_row()['description'], SOURCE)
                self.assertEqual(get_row()['description_format'], 'markdown')
                updated = '<p>Literal HTML, not legacy markup</p>\n\n' + SOURCE
                response = self.client.put(url, json={'description': updated})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(get_row()['description'], updated)
                self.assertEqual(get_row()['description_format'], 'markdown')
                self.assertEqual(self.client.put(url, json={'description': ''}).status_code, 200)
                self.assertEqual(get_row()['description'], '')

    def test_upgrade_preserves_old_source_and_marks_only_existing_records(self):
        with closing(database.get_db()) as db, db:
            db.execute('ALTER TABLE tasks DROP COLUMN description_format')
            db.execute('ALTER TABLE sub_tasks DROP COLUMN description_format')
            db.execute('ALTER TABLE task_notes DROP COLUMN content_format')
            db.execute('ALTER TABLE sub_task_notes DROP COLUMN content_format')
            db.execute('ALTER TABLE task_note_entries DROP COLUMN content_format')
            db.execute('ALTER TABLE sub_task_note_entries DROP COLUMN content_format')
            db.execute("UPDATE tasks SET description = '<p>Old <b>bold</b></p>'")
            db.execute("INSERT INTO task_notes (task_id, user_id, content) VALUES (?, 1, '<p>Note</p>')", (self.task_id,))
            db.execute('INSERT INTO sub_task_notes (sub_task_id, user_id, content) VALUES (?, 1, ?)', (self.subtask_id, SOURCE))
            db.execute('INSERT INTO task_note_entries (task_id, user_id, content) VALUES (?, 1, ?)', (self.task_id, SOURCE))
            db.execute('INSERT INTO sub_task_note_entries (sub_task_id, user_id, content) VALUES (?, 1, ?)', (self.subtask_id, SOURCE))
        database.init_db()
        self.assertEqual(self.task()['description'], '<p>Old <b>bold</b></p>')
        self.assertEqual(self.task()['description_format'], 'legacy')
        self.assertEqual(self.subtask()['description'], SOURCE)
        self.assertEqual(self.subtask()['description_format'], 'legacy')
        note_urls = [
            f'/api/tasks/{self.task_id}/notes',
            f'/api/tasks/{self.task_id}/subtasks/{self.subtask_id}/notes',
            f'/api/tasks/{self.task_id}/note-entries',
            f'/api/tasks/{self.task_id}/subtasks/{self.subtask_id}/note-entries',
        ]
        for url in note_urls:
            self.assertEqual(self.client.get(url).json()['items'][0]['content_format'], 'legacy')
        self.client.put(f'/api/tasks/{self.task_id}', json={'name': 'Renamed'})
        self.assertEqual(self.task()['description_format'], 'legacy')
        self.client.put(f'/api/tasks/{self.task_id}', json={'description': SOURCE})
        self.assertEqual(self.task()['description_format'], 'markdown')
        for url in note_urls[:2]:
            self.assertEqual(self.client.put(url, json={'content': SOURCE}).status_code, 200)
            note = self.client.get(url).json()['items'][0]
            self.assertEqual((note['content'], note['content_format']), (SOURCE, 'markdown'))
        database.init_db()
        self.assertEqual(self.task()['description_format'], 'markdown')
        self.assertEqual(self.subtask()['description_format'], 'legacy')
        new_id = self.client.post('/api/tasks', json={'name': 'New', 'description': SOURCE}).json()['id']
        with closing(database.get_db()) as db:
            self.assertEqual(db.execute('SELECT description_format FROM tasks WHERE id = ?', (new_id,)).fetchone()[0], 'markdown')
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')

    def test_mcp_uses_markdown_for_descriptions_notes_and_handoffs(self):
        token = mcp_server.current_mcp_user.set(self.user)
        self.addCleanup(mcp_server.current_mcp_user.reset, token)
        with closing(database.get_db()) as db, db:
            db.execute("UPDATE tasks SET description_format = 'legacy'")
            db.execute("UPDATE sub_tasks SET description_format = 'legacy'")
        mcp_server.update_project(self.task_id, description=SOURCE)
        mcp_server.update_subtask(self.subtask_id, description=SOURCE)
        self.assertEqual(self.task()['description_format'], 'markdown')
        self.assertEqual(self.subtask()['description_format'], 'markdown')
        self.assertEqual(mcp_server.get_project(self.task_id)['description'], SOURCE)
        for subtask_id in (None, self.subtask_id):
            mcp_server.note_write(self.task_id, SOURCE, subtask_id=subtask_id)
            with closing(database.get_db()) as db, db:
                db.execute("UPDATE task_notes SET content_format = 'legacy'")
                db.execute("UPDATE sub_task_notes SET content_format = 'legacy'")
            mcp_server.note_write(self.task_id, SOURCE, subtask_id=subtask_id)
            note = mcp_server.note_list(self.task_id, subtask_id=subtask_id)[0]
            self.assertEqual((note['content'], note['content_format']), (SOURCE, 'markdown'))
            mcp_server.handoff_add(self.task_id, SOURCE, subtask_id=subtask_id)
            entry = mcp_server.handoff_list(self.task_id, subtask_id=subtask_id)[0]
            self.assertEqual((entry['content'], entry['content_format']), (SOURCE, 'markdown'))

    def test_failed_migration_rolls_back_format_column_and_can_retry(self):
        with closing(database.get_db()) as db:
            db.execute('ALTER TABLE tasks DROP COLUMN description_format')
        connect = sqlite3.connect

        class FailingConnection(sqlite3.Connection):
            def execute(self, sql, *args, **kwargs):
                if sql == "UPDATE tasks SET description_format = 'legacy'":
                    raise sqlite3.OperationalError('injected format migration failure')
                return super().execute(sql, *args, **kwargs)

        def failing_connect(*args, **kwargs):
            return connect(*args, factory=FailingConnection, **kwargs)

        with patch.object(database.sqlite3, 'connect', failing_connect):
            with self.assertRaisesRegex(sqlite3.OperationalError, 'injected format migration failure'):
                database.init_db()
        with closing(database.get_db()) as db:
            self.assertNotIn('description_format', {row[1] for row in db.execute('PRAGMA table_info(tasks)')})
            self.assertEqual(db.execute('SELECT description FROM tasks WHERE id = ?', (self.task_id,)).fetchone()[0], SOURCE)
        database.init_db()
        self.assertEqual(self.task()['description_format'], 'legacy')
        self.assertEqual(self.task()['description'], SOURCE)


if __name__ == '__main__':
    unittest.main()
