"""Native Textdateien: echte API, temporaere Ablagen, Rechte und WebDAV."""

import codecs
import json
import multiprocessing
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from dashboard import api_nextcloud, database, file_storage, text_files, webdav
from dashboard.auth import get_file_user


def _save_in_process(db_dir, task, user, revision, content, operation, ready, start, done, results,
                     paused=None, resume=None):
    """Eigener Dienstprozess; bei Bedarf direkt vor dem atomaren Schreiben pausieren."""
    database.DB_DIR = Path(db_dir)
    database.DB_PATH = database.DB_DIR / "test.db"
    app = FastAPI()
    app.include_router(api_nextcloud.file_operations_router)
    app.dependency_overrides[get_file_user] = lambda: user
    replace = file_storage.os.replace

    def pause_before_replace(*args):
        if paused is not None:
            paused.set()
            if not resume.wait(15):
                raise TimeoutError("Test did not release the pending save")
        return replace(*args)

    try:
        # MCP-Import abschliessen, bevor der Prozess seine Bereitschaft meldet.
        if operation == "mcp":
            from dashboard import mcp_server
        with TestClient(app) as client, patch.object(file_storage.os, "replace", pause_before_replace):
            ready.set()
            if not start.wait(15):
                raise TimeoutError("Test did not start the save")
            if operation == "mcp":
                token = mcp_server.current_mcp_user.set(user)
                try:
                    mcp_server.file_write(task, "readme.md", content)
                finally:
                    mcp_server.current_mcp_user.reset(token)
                status, detail = 200, None
            elif operation == "upload":
                response = client.post(f"/api/tasks/{task}/files/upload",
                                       files={"file": ("readme.md", content.encode(), "text/markdown")})
                status, detail = response.status_code, response.json().get("detail")
            else:
                response = client.put(f"/api/tasks/{task}/files/text", params={"path": "readme.md"},
                                      json={"content": content, "revision": revision})
                status, detail = response.status_code, response.json().get("detail")
            results.put((content, status, detail))
    except Exception as exc:
        results.put((content, "error", repr(exc)))
    finally:
        done.set()


class TextFileTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="tareas-text-")
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        for name, value in (("DB_DIR", root), ("DB_PATH", root / "test.db")):
            patcher = patch.object(database, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        database.init_db()
        self.users = {"admin": {"id": 1, "username": "admin", "is_admin": True}}
        with closing(database.get_db()) as db, db:
            for name in ("owner", "reader", "editor", "outsider"):
                uid = db.execute("INSERT INTO users (username, password_hash) VALUES (?, 'unused')", (name,)).lastrowid
                self.users[name] = {"id": uid, "username": name, "is_admin": False}
            self.task = db.execute(
                "INSERT INTO tasks (name, created_by, file_storage_type) VALUES ('Files', ?, 'local')",
                (self.users["owner"]["id"],),
            ).lastrowid
            for name, edit in (("reader", 0), ("editor", 1)):
                db.execute("INSERT INTO project_members (project_id, user_id, can_read, can_edit) VALUES (?, ?, 1, ?)",
                           (self.task, self.users[name]["id"], edit))
        file_storage.ensure_local_directory(self.task)
        self.root = file_storage.local_path(self.task)
        self.user = self.users["owner"]
        app = FastAPI()
        app.include_router(api_nextcloud.files_router)
        app.dependency_overrides[get_file_user] = lambda: self.user
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.url = f"/api/tasks/{self.task}/files/text"

    def read(self, path="readme.md"):
        response = self.client.get(self.url, params={"path": path})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def save(self, revision, content, path="readme.md"):
        return self.client.put(self.url, params={"path": path}, json={"revision": revision, "content": content})

    def test_formats_and_local_round_trip_audited_without_contents(self):
        source = "# Grüße\n\n<script>alert(1)</script>\n"
        for name in ("readme.md", "SCRIPT.PS1", "run.sh", "notes.txt", "config.yaml", ".env", "Dockerfile"):
            (self.root / name).write_text(source)
        (self.root / "folder.md").mkdir()
        (self.root / "office.docx").write_bytes(b"office")
        listing = self.client.get(f"/api/tasks/{self.task}/files").json()
        formats = {item["name"]: item["text_format"] for item in listing["items"]}
        self.assertEqual(formats["readme.md"], "markdown")
        self.assertIsNone(formats["folder.md"])
        self.assertIsNone(formats["office.docx"])
        self.assertTrue(all(formats[name] == "text" for name in ("SCRIPT.PS1", "run.sh", "notes.txt", "config.yaml", ".env", "Dockerfile")))
        loaded = self.read()
        self.assertEqual(loaded["content"], source)
        self.assertTrue(loaded["can_write"])
        content = "# Update\nsecret-file-content\n"
        saved = self.save(loaded["revision"], content)
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual((self.root / "readme.md").read_text(), content)
        self.assertEqual(self.read()["revision"], saved.json()["revision"])
        with closing(database.get_db()) as db:
            entries = [dict(row) for row in db.execute("SELECT * FROM audit_log WHERE action='file_edit'")]
        self.assertEqual(len(entries), 1)
        self.assertNotIn("secret-file-content", json.dumps(entries))

    def test_encodings_bom_crlf_and_unchanged_mixed_line_endings(self):
        for codec, bom in (("utf-8", b""), ("utf-8", codecs.BOM_UTF8),
                           ("utf-16-le", codecs.BOM_UTF16_LE), ("utf-16-be", codecs.BOM_UTF16_BE)):
            with self.subTest(codec=codec, bom=bom):
                original = bom + "# Grüße\r\nWrite-Host 'alt'\r\n".encode(codec)
                target = self.root / "script.ps1"
                target.write_bytes(original)
                data = self.read("script.ps1")
                saved = self.save(data["revision"], "# Grüße\nWrite-Host 'neu'\n", "script.ps1")
                self.assertEqual(saved.status_code, 200, saved.text)
                self.assertEqual(target.read_bytes(), bom + "# Grüße\r\nWrite-Host 'neu'\r\n".encode(codec))
        target.write_bytes(b"one\r\ntwo\nthree\rfour")
        data = self.read("script.ps1")
        self.assertEqual(self.save(data["revision"], "one\ntwo\nthree\nfour", "script.ps1").status_code, 200)
        self.assertEqual(target.read_bytes(), b"one\r\ntwo\nthree\rfour")

    def test_permissions_and_revocation_at_save(self):
        (self.root / "readme.md").write_text("original")
        for name in ("owner", "editor", "admin"):
            self.user = self.users[name]
            data = self.read()
            self.assertTrue(data["can_write"])
            self.assertEqual(self.save(data["revision"], name).status_code, 200)
        self.user = self.users["reader"]
        data = self.read()
        self.assertFalse(data["can_write"])
        self.assertEqual(self.save(data["revision"], "forbidden").status_code, 403)
        self.user = self.users["outsider"]
        self.assertEqual(self.client.get(self.url, params={"path": "readme.md"}).status_code, 403)
        self.user = self.users["editor"]
        data = self.read()
        with closing(database.get_db()) as db, db:
            db.execute("DELETE FROM project_members WHERE user_id=?", (self.user["id"],))
        self.assertEqual(self.save(data["revision"], "forbidden").status_code, 403)
        self.assertEqual((self.root / "readme.md").read_text(), "admin")

    def test_conflicts_missing_file_and_concurrent_native_saves(self):
        target = self.root / "readme.md"
        target.write_text("original")
        revision = self.read()["revision"]
        target.write_text("external edit")
        self.assertEqual(self.save(revision, "stale").status_code, 409)
        self.assertEqual(target.read_text(), "external edit")
        revision = self.read()["revision"]
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda value: self.save(revision, value).status_code, ["first", "second"]))
        self.assertEqual(sorted(results), [200, 409])
        revision = self.read()["revision"]
        target.unlink()
        self.assertEqual(self.save(revision, "do not recreate").status_code, 404)
        self.assertFalse(target.exists())

    def start_save_process(self, content, revision, *, operation="text", pause=False):
        ctx = multiprocessing.get_context("spawn")
        ready, start, done, paused, resume = (ctx.Event() for _ in range(5))
        results = ctx.Queue()
        self.addCleanup(results.close)
        process = ctx.Process(target=_save_in_process, args=(
            str(database.DB_DIR), self.task, self.user, revision, content, operation,
            ready, start, done, results, paused if pause else None, resume,
        ))
        process.start()

        def cleanup():
            if process.is_alive():
                process.kill()
            process.join(timeout=5)
            process.close()

        self.addCleanup(cleanup)
        self.assertTrue(ready.wait(15), "Save process did not become ready")
        return process, start, done, paused, resume, results

    def test_native_save_conflicts_across_processes_with_text_upload_and_mcp_writes(self):
        target = self.root / "readme.md"
        for operation in ("text", "upload", "mcp"):
            with self.subTest(first_writer=operation):
                target.write_text("original")
                revision = self.read()["revision"]
                first, start_first, done_first, paused, resume, results_first = self.start_save_process(
                    "first writer", revision, operation=operation, pause=True)
                second, start_second, done_second, _, _, results_second = self.start_save_process(
                    "browser draft", revision)
                start_first.set()
                self.assertTrue(paused.wait(10), "First save did not reach the write step")
                try:
                    start_second.set()
                    # Ohne gemeinsame Prozesssperre wuerde der zweite Request
                    # hier bereits HTTP 200 liefern und anschliessend verloren gehen.
                    completed_too_early = done_second.wait(1)
                finally:
                    resume.set()
                self.assertTrue(done_first.wait(10))
                self.assertTrue(done_second.wait(10))
                first.join(timeout=5)
                second.join(timeout=5)
                self.assertEqual((first.exitcode, second.exitcode), (0, 0))
                self.assertEqual(results_first.get(timeout=2), ("first writer", 200, None))
                self.assertEqual(results_second.get(timeout=2), ("browser draft", 409, "textFile.conflict"))
                self.assertFalse(completed_too_early, "Second process bypassed the pending write")
                self.assertEqual(target.read_text(), "first writer")

    def test_process_death_releases_file_lock(self):
        target = self.root / "readme.md"
        target.write_text("original")
        revision = self.read()["revision"]
        first, start_first, _, paused, _, _ = self.start_save_process("interrupted", revision, pause=True)
        second, start_second, done, _, _, results = self.start_save_process("recovered", revision)
        start_first.set()
        self.assertTrue(paused.wait(10))
        first.kill()
        first.join(timeout=5)
        self.assertEqual(target.read_text(), "original")
        start_second.set()
        self.assertTrue(done.wait(10), "File stayed locked after process death")
        second.join(timeout=5)
        self.assertEqual(second.exitcode, 0)
        self.assertEqual(results.get(timeout=2), ("recovered", 200, None))
        self.assertEqual(target.read_text(), "recovered")

    def test_path_binary_encoding_and_size_rejections_preserve_file(self):
        target = self.root / "readme.md"
        for invalid in (b"zero\x00byte", b"invalid\xff", codecs.BOM_UTF32_LE + b"\x00" * 4):
            target.write_bytes(invalid)
            self.assertEqual(self.client.get(self.url, params={"path": "readme.md"}).status_code, 415)
        for path in ("../readme.md", "%252e%252e/readme.md", "/etc/passwd", "a\\b.txt"):
            self.assertEqual(self.client.get(self.url, params={"path": path}).status_code, 400)
        (self.root / "link.txt").symlink_to(target)
        self.assertEqual(self.client.get(self.url, params={"path": "link.txt"}).status_code, 400)
        (self.root / "image.png").write_bytes(b"plain text")
        self.assertEqual(self.client.get(self.url, params={"path": "image.png"}).status_code, 415)
        target.write_bytes(b"x" * (text_files.MAX_TEXT_BYTES + 1))
        self.assertEqual(self.client.get(self.url, params={"path": "readme.md"}).status_code, 413)
        target.write_text("keep")
        revision = self.read()["revision"]
        self.assertEqual(self.save(revision, "ü" * text_files.MAX_TEXT_BYTES).status_code, 413)
        self.assertEqual(self.save(revision, "binary\x00").status_code, 415)
        self.assertEqual(target.read_text(), "keep")

    def test_webdav_uses_bounded_stream_preserves_encoding_and_closes_resources(self):
        with closing(database.get_db()) as db, db:
            db.execute("UPDATE tasks SET file_storage_type=NULL, nextcloud_path='Project' WHERE id=?", (self.task,))
        content = [codecs.BOM_UTF8 + b"# Original\r\n"]
        responses, clients = [], []

        def stream(path):
            self.assertEqual(path, "Project/readme.md")
            response, client = Mock(), Mock()
            response.iter_bytes.return_value = iter([content[0]])
            responses.append(response)
            clients.append(client)
            return response, client, "text/markdown", str(len(content[0]))

        def upload(path, data, content_type):
            self.assertEqual((path, content_type), ("Project/readme.md", "text/markdown"))
            content[0] = data

        with patch.object(webdav, "get_file_stream", side_effect=stream), patch.object(webdav, "upload_file", side_effect=upload) as put:
            data = self.read()
            self.assertEqual(self.save(data["revision"], "# Updated\n").status_code, 200)
            self.assertEqual(content[0], codecs.BOM_UTF8 + b"# Updated\r\n")
            self.assertEqual(self.save(data["revision"], "stale").status_code, 409)
            put.assert_called_once()
            content[0] = b"x" * (text_files.MAX_TEXT_BYTES + 1)
            self.assertEqual(self.client.get(self.url, params={"path": "readme.md"}).status_code, 413)
        for response, client in zip(responses, clients):
            response.close.assert_called_once()
            client.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
