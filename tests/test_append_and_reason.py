"""Projekt 75: atomare UTF-8-Ergaenzungen und separate Audit-Begruendungen."""
import asyncio
import json
import multiprocessing
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastmcp import Client

from dashboard import database, file_storage, mcp_server as mcp, webdav
from dashboard.api_admin_mcp import router, MCP_TOOLS
from dashboard.auth import get_admin_user
from dashboard.mcp_errors import MCPToolError


def _append_worker(root, task_id, text, start, ready, results):
    database.DB_DIR = Path(root)
    storage = file_storage.TaskStorage(task_id, 'local', '', True)
    ready.set()
    try:
        if not start.wait(15):
            raise TimeoutError('start')
        storage.append_text('log.txt', text, max_bytes=1024)
        results.put(None)
    except Exception as exc:
        results.put(str(exc))


class AppendAndReasonTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='tareas-append-reason-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for key, value in {'DB_DIR': self.root, 'DB_PATH': self.root / 'test.db'}.items():
            patcher = patch.object(database, key, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        database.init_db()
        self.user = {'id': 1, 'is_admin': True, 'auth_source': 'mcp'}
        token = mcp.current_mcp_user.set(self.user)
        self.addCleanup(mcp.current_mcp_user.reset, token)
        self.pid = mcp.create_project('Protokoll')['id']
        self.sid = mcp.create_subtask(self.pid, 'Port 443 pruefen')['id']
        with closing(database.get_db()) as db, db:
            db.execute("UPDATE tasks SET file_storage_type='local' WHERE id=?", (self.pid,))
            self.reader_id = db.execute("INSERT INTO users(username,password_hash) VALUES('reader','unused')").lastrowid
            db.execute('INSERT INTO project_members(project_id,user_id,can_read,can_edit) VALUES(?,?,1,0)', (self.pid, self.reader_id))
        file_storage.ensure_local_directory(self.pid)
        self.path = file_storage.local_path(self.pid, 'log.txt')
        self.path.write_bytes(b'original\r\n')

    def logs(self):
        with closing(database.get_db()) as db:
            return [dict(row) for row in db.execute('SELECT * FROM audit_log ORDER BY id')]

    def test_exact_utf8_bom_empty_and_compact_audit(self):
        self.path.write_bytes(b'\xef\xbb\xbfGr\xc3\xbc\xc3\x9fe\r\n')
        before = self.path.read_bytes()
        result = mcp.file_append(self.pid, 'log.txt', 'Ergänzung\n')
        self.assertEqual(self.path.read_bytes(), before + 'Ergänzung\n'.encode())
        self.assertEqual(result['appended_bytes'], len('Ergänzung\n'.encode()))
        self.assertNotIn('content', result)
        self.assertEqual(mcp.file_append(self.pid, 'log.txt', '')['appended_bytes'], 0)
        self.assertEqual(self.path.read_bytes(), before + 'Ergänzung\n'.encode())
        entry = self.logs()[-2]
        self.assertEqual((entry['actor_user_id'], entry['entity_id'], entry['action']), (1, self.pid, 'file_append'))
        self.assertNotIn('Ergänzung', entry['changes_json'])

    def test_failed_appends_preserve_file_and_audit(self):
        cases = [('missing.txt', 'x', 'file_not_found'), ('../escape', 'x', 'invalid_path'),
                 ('', 'x', 'path_required'), ('log.txt', '\x00', 'invalid_content'),
                 ('log.txt', '\ud800', 'invalid_content')]
        for path, content, code in cases:
            with self.subTest(code=code):
                before = self.path.read_bytes()
                audit = len(self.logs())
                with self.assertRaises(MCPToolError) as raised:
                    mcp.file_append(self.pid, path, content)
                self.assertEqual(raised.exception.code, code)
                self.assertEqual(self.path.read_bytes(), before)
                self.assertEqual(len(self.logs()), audit)
        for raw, text, code in ((b'\xff\xfea\x00', 'x', 'invalid_encoding'),
                                (b'x'*16, 'ö', 'file_too_large'), (b'x'*17, '', 'file_too_large')):
            self.path.write_bytes(raw)
            with patch.object(mcp, 'MCP_FILE_MAX_BYTES', 16), self.assertRaises(MCPToolError) as raised:
                mcp.file_append(self.pid, 'log.txt', text)
            self.assertEqual(raised.exception.code, code)
            self.assertEqual(self.path.read_bytes(), raw)

    def test_append_permissions_and_revocation(self):
        reader = {'id': self.reader_id, 'is_admin': False, 'auth_source': 'mcp'}
        mcp.current_mcp_user.set(reader)
        with self.assertRaises(MCPToolError) as raised:
            mcp.file_append(self.pid, 'log.txt', 'denied')
        self.assertEqual(raised.exception.code, 'permission_denied')
        with closing(database.get_db()) as db, db:
            db.execute('UPDATE project_members SET can_edit=1 WHERE user_id=?', (self.reader_id,))
        mcp.file_append(self.pid, 'log.txt', 'allowed')
        with closing(database.get_db()) as db, db:
            db.execute('DELETE FROM project_members WHERE user_id=?', (self.reader_id,))
        with self.assertRaises(MCPToolError):
            mcp.file_append(self.pid, 'log.txt', 'revoked')
        self.assertEqual(self.path.read_bytes(), b'original\r\nallowed')

    def test_parallel_process_appends_keep_both_additions(self):
        ctx = multiprocessing.get_context('spawn')
        start, results = ctx.Event(), ctx.Queue()
        self.addCleanup(results.close)
        workers = []
        try:
            for text in ('\nÄpfel', '\nÖl'):
                ready = ctx.Event()
                process = ctx.Process(target=_append_worker, args=(str(self.root), self.pid, text, start, ready, results))
                process.start()
                workers.append(process)
                self.assertTrue(ready.wait(15))
            start.set()
            for worker in workers:
                worker.join(15)
                self.assertEqual(worker.exitcode, 0)
            self.assertEqual([results.get(timeout=2) for _ in workers], [None, None])
            data = self.path.read_text()
            self.assertTrue(data.startswith('original\n'))
            self.assertEqual(data.count('Äpfel'), 1)
            self.assertEqual(data.count('Öl'), 1)
        finally:
            for worker in workers:
                if worker.is_alive():
                    worker.kill()
                worker.join()
                worker.close()

    def test_append_holds_same_lock_as_upload(self):
        first = file_storage.TaskStorage(self.pid, 'local', '', True)
        second = file_storage.TaskStorage(self.pid, 'local', '', True)
        entered, release, uploading, uploaded = [threading.Event() for _ in range(4)]
        original = first.get_file
        def paused_read(*args, **kwargs):
            result = original(*args, **kwargs)
            entered.set()
            if not release.wait(10):
                raise TimeoutError('release')
            return result
        def upload():
            uploading.set()
            second.upload_file('log.txt', b'replacement')
            uploaded.set()
        with patch.object(first, 'get_file', side_effect=paused_read), ThreadPoolExecutor(2) as pool:
            append = pool.submit(first.append_text, 'log.txt', 'append', max_bytes=1024)
            try:
                self.assertTrue(entered.wait(10))
                write = pool.submit(upload)
                self.assertTrue(uploading.wait(10))
                self.assertFalse(uploaded.wait(.1))
            finally:
                release.set()
            append.result(10)
            write.result(10)
        self.assertEqual(self.path.read_bytes(), b'replacement')

    def test_webdav_exact_append_and_limits_without_put_on_failure(self):
        with closing(database.get_db()) as db, db:
            db.execute("UPDATE tasks SET file_storage_type='webdav',nextcloud_path='shared' WHERE id=?", (self.pid,))
        data = {'raw': b'\xef\xbb\xbfremote\r\n'}
        puts = []
        real_client = httpx.Client
        def handle(request):
            self.assertEqual(request.url.path, '/remote.php/dav/files/test/shared/log.txt')
            if request.method == 'GET':
                return httpx.Response(200, content=data['raw'], headers={'Content-Type': 'text/plain'})
            self.assertEqual(request.method, 'PUT')
            data['raw'] = request.content
            puts.append(request.content)
            return httpx.Response(204)
        def client(**kwargs):
            return real_client(transport=httpx.MockTransport(handle), **kwargs)
        config = {'server_url': 'https://dav.test', 'username': 'test', 'password': 'dummy', 'base_path': '', 'verify': True}
        with patch.object(webdav, '_get_config', return_value=config), patch.object(webdav.httpx, 'Client', side_effect=client):
            mcp.file_append(self.pid, 'log.txt', 'Ä\n')
            self.assertEqual(data['raw'], b'\xef\xbb\xbfremote\r\n'+'Ä\n'.encode())
            mcp.file_append(self.pid, 'log.txt', '')
            count = len(puts)
            with patch.object(mcp, 'MCP_FILE_MAX_BYTES', len(data['raw'])), self.assertRaises(MCPToolError):
                mcp.file_append(self.pid, 'log.txt', 'X')
            self.assertEqual(len(puts), count)
            data['raw'] = b'\xff'
            with self.assertRaises(MCPToolError):
                mcp.file_append(self.pid, 'log.txt', 'X')
            self.assertEqual(len(puts), count)

    def test_parallel_webdav_appends_share_lock_across_project_mappings(self):
        other = mcp.create_project('Zweiter Zugriff auf dieselbe Ablage')['id']
        with closing(database.get_db()) as db, db:
            db.execute("UPDATE tasks SET file_storage_type='webdav',nextcloud_path='shared' WHERE id=?", (self.pid,))
            db.execute("UPDATE tasks SET file_storage_type='webdav',nextcloud_path='shared/' WHERE id=?", (other,))
        data = {'raw': b'remote\r\n'}
        requests, clients = [], []
        entered, release, second_started, second_read = [threading.Event() for _ in range(4)]
        real_client = httpx.Client

        def handle(request):
            self.assertEqual(request.url.path, '/remote.php/dav/files/test/shared/log.txt')
            requests.append(request.method)
            if request.method == 'GET':
                snapshot = data['raw']
                if requests.count('GET') == 1:
                    entered.set()
                    if not release.wait(10):
                        raise TimeoutError('first read was not released')
                else:
                    second_read.set()
                return httpx.Response(200, content=snapshot, headers={'Content-Type': 'text/plain'})
            self.assertEqual(request.method, 'PUT')
            data['raw'] = request.content
            return httpx.Response(204)

        def client(**kwargs):
            instance = real_client(transport=httpx.MockTransport(handle), **kwargs)
            clients.append(instance)
            return instance

        def append(pid, text):
            token = mcp.current_mcp_user.set(self.user)
            try:
                if pid == other:
                    second_started.set()
                return mcp.file_append(pid, 'log.txt', text)
            finally:
                mcp.current_mcp_user.reset(token)

        config = {'server_url': 'https://dav.test', 'username': 'test', 'password': 'dummy', 'base_path': '', 'verify': True}
        with patch.object(webdav, '_get_config', return_value=config), patch.object(webdav.httpx, 'Client', side_effect=client), \
                ThreadPoolExecutor(2) as pool:
            first = pool.submit(append, self.pid, 'Äpfel\n')
            try:
                self.assertTrue(entered.wait(10))
                second = pool.submit(append, other, 'Öl\n')
                self.assertTrue(second_started.wait(10))
                self.assertFalse(second_read.wait(.1), 'Second mapping read the old content while first append held its lock')
            finally:
                release.set()
            self.assertEqual(first.result(10)['appended_bytes'], len('Äpfel\n'.encode()))
            self.assertEqual(second.result(10)['appended_bytes'], len('Öl\n'.encode()))
        self.assertEqual(requests, ['GET', 'PUT', 'GET', 'PUT'])
        self.assertEqual(data['raw'], b'remote\r\n' + 'Äpfel\nÖl\n'.encode())
        self.assertTrue(all(client.is_closed for client in clients))
        appended = [entry for entry in self.logs() if entry['action'] == 'file_append']
        self.assertEqual({entry['entity_id'] for entry in appended}, {self.pid, other})

    def test_webdav_missing_and_failed_writes_release_locks_without_success_audit(self):
        with closing(database.get_db()) as db, db:
            db.execute("UPDATE tasks SET file_storage_type='webdav',nextcloud_path='shared' WHERE id=?", (self.pid,))
        state = {'mode': 'missing', 'raw': b'unchanged'}
        requests, clients = [], []
        real_client = httpx.Client

        def handle(request):
            requests.append(request.method)
            if request.method == 'GET':
                return httpx.Response(404 if state['mode'] == 'missing' else 200, content=state['raw'])
            self.assertEqual(request.method, 'PUT')
            if state['mode'] == 'failed_put':
                return httpx.Response(500)
            state['raw'] = request.content
            return httpx.Response(204)

        def client(**kwargs):
            instance = real_client(transport=httpx.MockTransport(handle), **kwargs)
            clients.append(instance)
            return instance

        config = {'server_url': 'https://dav.test', 'username': 'test', 'password': 'dummy', 'base_path': '', 'verify': True}
        audit_count = len(self.logs())
        with patch.object(webdav, '_get_config', return_value=config), patch.object(webdav.httpx, 'Client', side_effect=client):
            with self.assertRaises(MCPToolError) as missing:
                mcp.file_append(self.pid, 'log.txt', 'X')
            self.assertEqual(missing.exception.code, 'file_not_found')
            self.assertEqual(requests, ['GET'])
            self.assertEqual(len(self.logs()), audit_count)
            state['mode'] = 'failed_put'
            with self.assertLogs(mcp.logger, level='ERROR'), self.assertRaises(MCPToolError) as failed:
                mcp.file_append(self.pid, 'log.txt', 'X')
            self.assertEqual(failed.exception.code, 'file_operation_failed')
            self.assertEqual(state['raw'], b'unchanged')
            self.assertEqual(len(self.logs()), audit_count)
            state['mode'] = 'success'
            result = mcp.file_append(self.pid, 'log.txt', 'Ö')
            self.assertEqual(result['size'], len(b'unchanged') + len('Ö'.encode()))
            self.assertEqual(state['raw'], b'unchanged' + 'Ö'.encode())
        self.assertTrue(all(client.is_closed for client in clients))
        self.assertEqual(len(self.logs()), audit_count + 1)

    def test_exact_byte_limit_and_invalid_unicode_reason_before_mutation(self):
        self.path.write_bytes(b'\xef\xbb\xbfA')
        with patch.object(mcp, 'MCP_FILE_MAX_BYTES', 6):
            result = mcp.file_append(self.pid, 'log.txt', 'Ö')
            self.assertEqual(result['size'], 6)
            self.assertEqual(self.path.read_bytes(), b'\xef\xbb\xbfA' + 'Ö'.encode())
        audit_count = len(self.logs())
        for operation in (
            lambda: mcp.update_project(self.pid, name='Unstored title', reason='\ud800'),
            lambda: mcp.update_subtask(self.sid, name='Unstored title', reason='\udfff'),
        ):
            with self.assertRaises(MCPToolError) as failed:
                operation()
            self.assertEqual(failed.exception.code, 'invalid_reason')
        self.assertEqual(mcp.get_project(self.pid)['name'], 'Protokoll')
        self.assertEqual(mcp.get_subtask(self.sid)['name'], 'Port 443 pruefen')
        self.assertEqual(len(self.logs()), audit_count)

    def test_reason_metadata_compatibility_validation_and_admin_output(self):
        before = mcp.get_project(self.pid)
        mcp.update_project(self.pid, priority=70, reason='  Freigabe – Größe geprüft 🧪  ')
        entry = self.logs()[-1]
        self.assertEqual(entry['reason'], 'Freigabe – Größe geprüft 🧪')
        self.assertEqual((entry['entity_type'], entry['entity_id'], entry['actor_user_id']), ('task', self.pid, 1))
        self.assertNotIn('reason', json.loads(entry['changes_json']))
        self.assertEqual(mcp.get_project(self.pid)['description'], before['description'])
        mcp.update_subtask(self.sid, status_percent=50, reason='Zwischenergebnis')
        self.assertEqual(self.logs()[-1]['reason'], 'Zwischenergebnis')
        mcp.update_subtask(self.sid, priority=60, reason=' \n ')
        self.assertIsNone(self.logs()[-1]['reason'])
        mcp.update_project(self.pid, priority=71)
        self.assertIsNone(self.logs()[-1]['reason'])
        audit_count = len(self.logs())
        for operation in (lambda: mcp.update_project(self.pid, name='NO', reason='x'*1001),
                          lambda: mcp.update_subtask(self.sid, name='NO', reason='x'*1001),
                          lambda: mcp.update_subtask(self.sid, status_percent=101, reason='failed')):
            with self.assertRaises(MCPToolError):
                operation()
        self.assertEqual(len(self.logs()), audit_count)
        self.assertEqual(mcp.get_project(self.pid)['name'], 'Protokoll')
        self.assertEqual(mcp.get_subtask(self.sid)['name'], 'Port 443 pruefen')
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_admin_user] = lambda: self.user
        with TestClient(app) as client:
            result = client.get('/api/admin/mcp/audit').json()
            self.assertTrue(any(item['reason'] == entry['reason'] for item in result['items']))

    def test_wire_schema_and_append_result(self):
        async def check():
            async with Client(mcp.mcp) as client:
                schemas = {tool.name: tool.inputSchema for tool in await client.list_tools()}
                self.assertIn('file.append', MCP_TOOLS)
                self.assertIn('reason', schemas['update_project']['properties'])
                self.assertIn('reason', schemas['update_subtask']['properties'])
                result = await client.call_tool('file.append', {'task_id': self.pid, 'path': 'log.txt', 'content': 'wire'})
                self.assertTrue(result.structured_content['appended'])
                result = await client.call_tool('update_project', {'project_id': self.pid, 'priority': 80, 'reason': 'x'*1001}, raise_on_error=False)
                self.assertEqual(result.structured_content['code'], 'invalid_reason')
        asyncio.run(check())


if __name__ == '__main__':
    unittest.main()
