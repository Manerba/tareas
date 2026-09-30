"""Editor-CSP und ONLYOFFICE-Verfuegbarkeit mit echten App-Routen."""

import logging
import re
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from dashboard import auth, database, file_storage


class EditorPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        loggers = [logging.getLogger(name) for name in ("", "uvicorn", "uvicorn.access", "uvicorn.error")]
        saved = [(logger, list(logger.handlers), logger.level, logger.propagate) for logger in loggers]

        def restore_logging():
            for logger, handlers, level, propagate in saved:
                logger.handlers[:] = handlers
                logger.setLevel(level)
                logger.propagate = propagate

        cls.addClassCleanup(restore_logging)
        with patch("logging.handlers.RotatingFileHandler", return_value=logging.NullHandler()):
            from dashboard.app import app
        cls.app = app

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="tareas-editor-")
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        for obj, name, value in ((database, "DB_DIR", root), (database, "DB_PATH", root / "test.db"),
                                 (auth, "SECRET_KEY_PATH", root / "secret.key")):
            patcher = patch.object(obj, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        database.init_db()
        with closing(database.get_db()) as db, db:
            self.task = db.execute(
                "INSERT INTO tasks (name, task_type, created_by, file_storage_type) VALUES ('Files', 'projekt', 1, 'local')",
            ).lastrowid
        file_storage.ensure_local_directory(self.task)
        (file_storage.local_path(self.task) / "readme.md").write_text("# Projekt\n")
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.client.cookies.set(auth.COOKIE_NAME, auth.sign_token(auth.create_session(1)))
        self.url = f"/api/tasks/{self.task}/files"

    def set_office(self, url):
        # Wie ein separater Admin-Prozess: DB aendern, keinen lokalen Cache invalidieren.
        with closing(database.get_db()) as db, db:
            db.execute("DELETE FROM onlyoffice_config")
            if url:
                db.execute("INSERT INTO onlyoffice_config (id, server_url, jwt_secret) VALUES (1, ?, 'private-test-value')", (url,))

    def test_unconfigured_editor_bootstrap_is_external_and_error_endpoint_is_reachable(self):
        response = self.client.get(f"/editor?taskId={self.task}&path=readme.md")
        self.assertEqual(response.status_code, 200)
        self.assertIn("script-src 'self';", response.headers["content-security-policy"])
        self.assertNotRegex(response.text, re.compile(r"<script\b(?![^>]*\bsrc=)[^>]*>", re.I))
        self.assertIn('/static/js/editor_page.js', response.text)
        self.assertEqual(self.client.get('/static/js/editor_page.js').status_code, 200)
        error = self.client.get(self.url + "/edit", params={"path": "readme.md"})
        self.assertEqual(error.status_code, 400)
        self.assertEqual(error.json()["detail"], "ONLYOFFICE ist nicht konfiguriert")
        download = self.client.get(self.url + "/download", params={"path": "readme.md"})
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.text, "# Projekt\n")
        self.assertIn("attachment", download.headers["content-disposition"])
        self.assertEqual(download.headers["content-security-policy"], "sandbox")

    def test_file_status_and_editor_csp_follow_configuration_across_processes(self):
        for url in (None, "https://office.example.test:8443/path", "https://second.example.test", None):
            self.set_office(url)
            listing = self.client.get(self.url)
            self.assertEqual(listing.status_code, 200)
            self.assertIs(listing.json()["onlyoffice_configured"], bool(url))
            self.assertNotIn("private-test-value", listing.text)
            self.assertNotIn("server_url", listing.text)
            csp = self.client.get('/editor').headers["content-security-policy"]
            if url:
                origin = url.removesuffix('/path')
                self.assertIn(f"frame-src {origin};", csp)
            else:
                self.assertIn("script-src 'self';", csp)
                self.assertNotIn("example.test", csp)

    def test_editor_and_file_status_require_authentication(self):
        self.client.cookies.clear()
        self.assertEqual(self.client.get('/editor', follow_redirects=False).status_code, 302)
        self.assertEqual(self.client.get(self.url).status_code, 401)
        self.assertEqual(self.client.get(self.url + '/edit', params={"path": "readme.md"}).status_code, 401)

    def test_native_text_page_strict_csp_and_file_csrf(self):
        response = self.client.get('/text-editor')
        self.assertEqual(response.status_code, 200)
        self.assertIn("script-src 'self';", response.headers['content-security-policy'])
        self.assertNotRegex(response.text, re.compile(r"<script\b(?![^>]*\bsrc=)[^>]*>", re.I))
        data = self.client.get(self.url + '/text', params={'path': 'readme.md'}).json()
        params = dict(params={'path': 'readme.md'}, json={'content': '# Updated', 'revision': data['revision']})
        self.assertEqual(self.client.put(self.url + '/text', **params).status_code, 403)
        self.assertEqual(self.client.put(self.url + '/text', headers={'X-Requested-With': 'XMLHttpRequest'}, **params).status_code, 200)
        self.client.cookies.clear()
        self.assertEqual(self.client.get('/text-editor', follow_redirects=False).status_code, 302)
        self.assertEqual(self.client.get(self.url + '/text', params={'path': 'readme.md'}).status_code, 401)

    def test_handoff_window_authentication_and_strict_csp(self):
        for query in ('taskId=1&entryId=3', 'taskId=1&subtaskId=2&entryId=3'):
            response = self.client.get('/handoff?' + query)
            self.assertEqual(response.status_code, 200)
            self.assertIn("script-src 'self';", response.headers['content-security-policy'])
            self.assertNotRegex(response.text, re.compile(r"<script\b(?![^>]*\bsrc=)[^>]*>", re.I))
        self.client.cookies.clear()
        self.assertEqual(self.client.get('/handoff', follow_redirects=False).status_code, 302)
        self.assertEqual(self.client.get('/api/tasks/1/subtasks/2/note-entries/3').status_code, 401)
        self.assertEqual(self.client.get('/api/tasks/1/note-entries/3').status_code, 401)


if __name__ == '__main__':
    unittest.main()
