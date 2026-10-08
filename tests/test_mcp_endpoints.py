"""Beide HTTP-MCP-Zugaenge mit echten Sessions/Tokens und isolierter SQLite-DB."""

import asyncio
import json
import logging
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from starlette import formparsers

from dashboard import api_nextcloud, auth, database, file_storage
from dashboard.api_admin_mcp import MCP_TOOLS
from dashboard.agent_guide import build_agent_metadata, _with_port

with patch('logging.handlers.RotatingFileHandler', return_value=logging.NullHandler()):
    from dashboard.app import app as legacy_app
from dashboard.mcp_app import app as dedicated_app


class MCPEndpointTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='tareas-mcp-endpoints-')
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        for module, name, value in ((database, 'DB_DIR', root), (database, 'DB_PATH', root / 'test.db'),
                                    (auth, 'SECRET_KEY_PATH', root / 'secret.key')):
            patcher = patch.object(module, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        database.init_db()
        self.tokens, self.token_ids, self.users = {}, {}, {}
        for name in ('owner', 'reader'):
            self.tokens[name], self.token_ids[name] = auth.create_mcp_token(name, 1)
        with closing(database.get_db()) as db, db:
            for name, token_id in self.token_ids.items():
                self.users[name] = db.execute('SELECT user_id FROM mcp_tokens WHERE id=?', (token_id,)).fetchone()[0]
            self.project = db.execute(
                "INSERT INTO tasks (name,task_type,created_by,file_storage_type) VALUES ('Endpoint test','projekt',?,'local')",
                (self.users['owner'],),
            ).lastrowid
            db.execute('INSERT INTO project_members (project_id,user_id,can_read,can_edit) VALUES (?,?,1,0)',
                       (self.project, self.users['reader']))
            db.execute("INSERT OR REPLACE INTO app_config (id,server_address) VALUES (1,'tareas.test:8504')")
        file_storage.ensure_local_directory(self.project)
        self.url = f'/api/tasks/{self.project}/files'

    def headers(self, name='owner'):
        return {'Authorization': f'Bearer {self.tokens[name]}', 'Accept': 'application/json, text/event-stream'}

    def streamed_request(self, app, method, path, headers, chunks):
        """Echte App mit einzelnen ASGI-Body-Chunks; ungelesene Chunks bleiben sichtbar."""
        consumed, sent = [], []

        async def receive():
            self.assertLess(len(consumed), len(chunks), 'App read beyond the supplied body chunks')
            chunk = chunks[len(consumed)]
            consumed.append(chunk)
            return {'type': 'http.request', 'body': chunk, 'more_body': len(consumed) < len(chunks)}

        async def send(message):
            sent.append(message)

        async def run():
            await app({
                'type': 'http', 'asgi': {'version': '3.0', 'spec_version': '2.4'},
                'http_version': '1.1', 'method': method, 'scheme': 'http',
                'path': path, 'raw_path': path.encode(), 'root_path': '', 'query_string': b'',
                'headers': [(key.lower().encode(), value.encode()) for key, value in headers.items()],
                'client': ('127.0.0.1', 12345), 'server': ('tareas.test', 8506),
            }, receive, send)

        asyncio.run(run())
        status = next(message['status'] for message in sent if message['type'] == 'http.response.start')
        body = b''.join(message.get('body', b'') for message in sent if message['type'] == 'http.response.body')
        return status, json.loads(body), consumed

    def rpc(self, client, method, params, headers):
        response = client.post('/mcp/', headers=headers,
                               json={'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params})
        self.assertEqual(response.status_code, 200, response.text[:500])
        if response.headers['content-type'].startswith('text/event-stream'):
            body = next(json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: '))
        else:
            body = response.json()
        self.assertNotIn('error', body, body)
        return response, body['result']

    def initialize(self, client, name='owner'):
        headers = self.headers(name)
        response, result = self.rpc(client, 'initialize', {
            'protocolVersion': '2025-11-25', 'capabilities': {},
            'clientInfo': {'name': 'tareas-test', 'version': '1'},
        }, headers)
        headers.update({'Mcp-Session-Id': response.headers['mcp-session-id'], 'MCP-Protocol-Version': result['protocolVersion']})
        ready = client.post('/mcp/', headers=headers, json={'jsonrpc': '2.0', 'method': 'notifications/initialized'})
        self.assertEqual(ready.status_code, 202, ready.text)
        return headers

    def call(self, client, headers, name, arguments=None, *, error=False):
        _, result = self.rpc(client, 'tools/call', {'name': name, 'arguments': arguments or {}}, headers)
        self.assertEqual(bool(result.get('isError')), error, result)
        if error:
            return result
        return result.get('structuredContent') or json.loads(result['content'][0]['text'])

    def test_both_endpoints_share_all_tools_identity_data_and_file_api(self):
        for app, port in ((legacy_app, 8504), (dedicated_app, 8506)):
            with self.subTest(port=port), TestClient(app, base_url=f'http://tareas.test:{port}') as client:
                headers = self.initialize(client)
                _, tools = self.rpc(client, 'tools/list', {}, headers)
                self.assertEqual({tool['name'] for tool in tools['tools']}, set(MCP_TOOLS))
                self.assertEqual(self.call(client, headers, 'whoami')['id'], self.users['owner'])
                self.assertEqual(self.call(client, headers, 'get_project', {'project_id': self.project})['id'], self.project)
                self.call(client, headers, 'file.write', {'task_id': self.project, 'path': 'readme.md', 'content': f'# Port {port}'})
                download = client.get(self.url + '/download', params={'path': 'readme.md'}, headers=headers)
                self.assertEqual(download.status_code, 200)
                self.assertEqual(download.text, f'# Port {port}')
                response = client.post(self.url + '/upload', headers={**headers, 'X-Requested-With': 'XMLHttpRequest'},
                                       files={'file': ('readme.md', f'# REST {port}'.encode(), 'text/markdown')})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(self.call(client, headers, 'file.read', {'task_id': self.project, 'path': 'readme.md'})['content'], f'# REST {port}')
                guide = self.call(client, headers, 'get_agent_guide')
                self.assertIn(f'http://tareas.test:{port}/mcp/', json.dumps(guide))
                self.assertIn(f'http://tareas.test:{port}/api/tasks/', guide['markdown'])
        with closing(database.get_db()) as db:
            logs = db.execute('SELECT actor_type,actor_user_id FROM audit_log').fetchall()
        self.assertTrue(logs)
        self.assertTrue(all(row['actor_type'] == 'mcp' and row['actor_user_id'] == self.users['owner'] for row in logs))

    def test_feedback_features_work_together_on_both_endpoints(self):
        for app, port in ((legacy_app, 8504), (dedicated_app, 8506)):
            with self.subTest(port=port), TestClient(app, base_url=f'http://tareas.test:{port}') as client:
                headers = self.initialize(client)
                _, catalog = self.rpc(client, 'tools/list', {}, headers)
                tools = {tool['name']: tool for tool in catalog['tools']}
                self.assertIn('list_projects_page', tools)
                progress_schema = tools['create_subtask']['inputSchema']['properties']['status_percent']
                self.assertEqual((progress_schema['minimum'], progress_schema['maximum']), (0, 100))

                bad_page = self.call(client, headers, 'list_projects_page', {'limit': 501}, error=True)
                self.assertEqual(bad_page['structuredContent']['code'], 'invalid_pagination')
                self.assertEqual(bad_page['structuredContent']['field'], 'limit')

                project = self.call(client, headers, 'create_project', {
                    'name': f'Feedback {port}', 'description': 'Long description ' * 1000,
                })
                pid = project['id']
                invalid = self.call(client, headers, 'update_project', {
                    'project_id': pid, 'name': 'Must not be saved', 'status': 'invalid-secret-value',
                }, error=True)
                self.assertEqual(invalid['structuredContent']['code'], 'invalid_status')
                self.assertEqual(invalid['structuredContent']['field'], 'status')
                self.assertNotIn('invalid-secret-value', json.dumps(invalid))
                self.assertEqual(self.call(client, headers, 'get_project', {'project_id': pid})['name'], project['name'])

                subtasks = [self.call(client, headers, 'create_subtask', {
                    'project_id': pid, 'name': f'Part {index}', 'status_percent': 100,
                }) for index in range(4)]
                invalid = self.call(client, headers, 'update_subtask', {
                    'subtask_id': subtasks[0]['id'], 'status_percent': 101,
                }, error=True)
                self.assertEqual(invalid['structuredContent']['code'], 'invalid_progress')
                self.assertEqual(invalid['structuredContent']['field'], 'status_percent')
                self.assertEqual(self.call(client, headers, 'get_project', {'project_id': pid})['status'], 'erledigt')

                page = self.call(client, headers, 'list_projects_page', {'status': 'erledigt', 'limit': 1})
                self.assertEqual(page['items'][0]['id'], pid)
                self.assertNotIn('description', page['items'][0])
                self.assertIn('next_offset', page)
                self.assertLess(len(json.dumps(page)), len(project['description']))
                legacy = self.call(client, headers, 'list_projects', {'status': 'erledigt'})
                legacy = legacy['result'] if isinstance(legacy, dict) else legacy
                self.assertIsInstance(legacy, list)
                self.assertEqual(next(item for item in legacy if item['id'] == pid)['description'], project['description'])

                self.call(client, headers, 'add_dependency', {
                    'subtask_id': subtasks[1]['id'], 'depends_on_id': subtasks[0]['id'],
                })
                cycle = self.call(client, headers, 'add_dependency', {
                    'subtask_id': subtasks[0]['id'], 'depends_on_id': subtasks[1]['id'],
                }, error=True)
                self.assertEqual(cycle['structuredContent']['code'], 'dependency_cycle')
                self.assertEqual(self.call(client, headers, 'get_subtask', {
                    'subtask_id': subtasks[0]['id'],
                })['predecessor_ids'], [])

                missing = self.call(client, headers, 'file.list', {'task_id': pid}, error=True)
                self.assertEqual(missing['structuredContent']['code'], 'storage_not_configured')
                self.assertEqual(missing['structuredContent']['field'], 'file_storage_type')
                self.assertIn('WebDAV', missing['structuredContent']['message'])

                self.call(client, headers, 'update_project', {'project_id': pid, 'status': 'abgebrochen'})
                self.call(client, headers, 'update_subtask', {'subtask_id': subtasks[0]['id'], 'status_percent': 50})
                self.assertEqual(self.call(client, headers, 'get_project', {'project_id': pid})['status'], 'abgebrochen')
                resumed = self.call(client, headers, 'update_project', {'project_id': pid, 'status': 'offen'})
                self.assertEqual(resumed['status'], 'in_arbeit')

    def test_project_links_and_progress_lock_on_both_endpoints(self):
        for app, port in ((legacy_app, 8504), (dedicated_app, 8506)):
            with self.subTest(port=port), TestClient(app, base_url=f'http://tareas.test:{port}') as client:
                headers = self.initialize(client)
                parent = self.call(client, headers, 'create_project', {'name': f'Parent {port}'})
                subtask = self.call(client, headers, 'create_subtask', {'project_id': parent['id'], 'name': 'Parent subtask'})
                child = self.call(client, headers, 'create_project', {
                    'name': 'Child', 'parent_subtask_id': subtask['id'],
                })
                self.assertEqual(child['parent_subtask_id'], subtask['id'])
                self.call(client, headers, 'create_subtask', {
                    'project_id': child['id'], 'name': 'Done', 'status_percent': 100,
                })
                self.call(client, headers, 'create_subtask', {'project_id': child['id'], 'name': 'Open'})
                read = self.call(client, headers, 'get_subtask', {'subtask_id': subtask['id']})
                self.assertEqual(read['status_percent'], 50)
                self.assertEqual(read['child_project_id'], child['id'])
                rejected = self.call(client, headers, 'update_subtask', {
                    'subtask_id': subtask['id'], 'status_percent': 100,
                }, error=True)
                self.assertEqual(rejected['structuredContent']['code'], 'linked_project_progress_readonly')
                self.assertEqual(rejected['structuredContent']['field'], 'status_percent')
                self.call(client, headers, 'update_project', {'project_id': child['id'], 'parent_subtask_id': 0})
                read = self.call(client, headers, 'update_subtask', {'subtask_id': subtask['id'], 'status_percent': 25})
                self.assertEqual(read['status_percent'], 25)
                self.assertFalse(read['progress_automatic'])

    def test_auth_revocation_kill_switch_and_read_only_rights_on_both(self):
        for app in (legacy_app, dedicated_app):
            auth.set_mcp_enabled(True)
            with self.subTest(app=app.title), TestClient(app) as client:
                for path in ('/mcp', '/mcp/'):
                    self.assertEqual(client.post(path, json={}, follow_redirects=False).status_code, 401)
                    self.assertEqual(client.post(path, json={}, headers={'Authorization': 'Bearer invalid'}, follow_redirects=False).status_code, 401)
                headers = self.initialize(client, 'reader')
                self.assertEqual(self.call(client, headers, 'whoami')['id'], self.users['reader'])
                self.call(client, headers, 'get_project', {'project_id': self.project})
                self.call(client, headers, 'update_project', {'project_id': self.project, 'name': 'Forbidden'}, error=True)
                self.assertEqual(client.post(self.url + '/upload', headers={**headers, 'X-Requested-With': 'XMLHttpRequest'},
                                             files={'file': ('bad.txt', b'bad')}).status_code, 403)
                auth.set_mcp_enabled(False)
                self.assertEqual(client.post('/mcp/', json={}, headers=headers).status_code, 401)
                self.assertEqual(client.get(self.url, headers=headers).status_code, 403)
                auth.set_mcp_enabled(True)
        auth.revoke_mcp_token(self.token_ids['reader'])
        for app in (legacy_app, dedicated_app):
            with TestClient(app) as client:
                self.assertEqual(client.post('/mcp/', headers=self.headers('reader'), json={}).status_code, 401)
                self.assertEqual(client.get(self.url, headers=self.headers('reader')).status_code, 401)

    def test_dedicated_service_exposes_only_agent_routes_and_rejects_cookies(self):
        with TestClient(dedicated_app) as client:
            client.cookies.set(auth.COOKIE_NAME, auth.sign_token(auth.create_session(1)))
            for path in ('/', '/login', '/static/js/app_core.js', '/api/tasks', '/api/auth/me', '/api/nextcloud/status',
                         '/api/nextcloud/directories', '/api/admin/mcp/tokens', self.url + '/edit', '/editor', '/docs', '/openapi.json'):
                self.assertEqual(client.get(path, headers=self.headers()).status_code, 404, path)
            self.assertEqual(client.get(self.url).status_code, 401)
            self.assertEqual(client.get(self.url, headers=self.headers()).status_code, 200)
            self.assertEqual(client.post(self.url + '/upload', headers=self.headers(), files={'file': ('a.txt', b'a')}).status_code, 403)
            self.assertEqual(client.get('/agent-guide.md').status_code, 200)

    def test_dedicated_file_auth_rejects_before_reading_any_body(self):
        auth.revoke_mcp_token(self.token_ids['reader'])
        cookie = f'{auth.COOKIE_NAME}={auth.sign_token(auth.create_session(1))}'
        operations = (('POST', '/upload'), ('POST', '/mkdir'), ('PUT', '/move'),
                      ('PUT', '/text'), ('DELETE', ''), ('GET', ''))
        for name, credentials, enabled, expected in (
            ('missing', {}, True, 401),
            ('cookie only', {'Cookie': cookie}, True, 401),
            ('invalid', {'Authorization': 'Bearer invalid', 'Cookie': cookie}, True, 401),
            ('revoked', self.headers('reader'), True, 401),
            ('disabled', self.headers(), False, 403),
        ):
            auth.set_mcp_enabled(enabled)
            for method, suffix in operations:
                with self.subTest(auth=name, method=method, path=suffix), patch.object(
                    formparsers, 'SpooledTemporaryFile', wraps=tempfile.SpooledTemporaryFile,
                ) as spool:
                    status, body, consumed = self.streamed_request(dedicated_app, method, self.url + suffix, {
                        **credentials, 'X-Requested-With': 'XMLHttpRequest',
                        'Content-Type': 'multipart/form-data; boundary=upload',
                        'Content-Length': str(1024 * 1024 * 1024), 'Expect': '100-continue',
                    }, [])
                    self.assertEqual(status, expected, body)
                    self.assertIn('detail', body)
                    self.assertEqual(consumed, [])
                    spool.assert_not_called()

    def test_upload_limit_stops_the_stream_and_closes_temporary_files_on_both_ports(self):
        limit = 16
        prefix = (b'--upload\r\nContent-Disposition: form-data; name="file"; filename="stream.bin"\r\n'
                  b'Content-Type: application/octet-stream\r\n\r\n')
        suffix = b'\r\n--upload--\r\n'
        target = file_storage.local_path(self.project, 'stream.bin')
        for app in (legacy_app, dedicated_app):
            for declared_length in (False, True):
                for size in (0, limit, limit + 1):
                    with self.subTest(app=app.title, content_length=declared_length, size=size):
                        target.write_bytes(b'original')
                        chunks = [prefix, b'x' * min(size, limit)]
                        if size > limit:
                            chunks.append(b'x')
                        chunks.append(suffix)
                        headers = {**self.headers(), 'X-Requested-With': 'XMLHttpRequest',
                                   'Content-Type': 'multipart/form-data; boundary=upload'}
                        if declared_length:
                            headers['Content-Length'] = str(sum(map(len, chunks)))
                        spooled = []

                        def record_spool(*args, **kwargs):
                            file = tempfile.SpooledTemporaryFile(*args, **kwargs)
                            spooled.append(file)
                            return file

                        with closing(database.get_db()) as db:
                            audit_before = db.execute('SELECT COUNT(*) FROM audit_log').fetchone()[0]
                        with patch.object(api_nextcloud, 'MAX_UPLOAD_SIZE', limit), \
                                patch.object(formparsers.MultiPartParser, 'spool_max_size', 4), \
                                patch.object(formparsers, 'SpooledTemporaryFile', record_spool):
                            status, body, consumed = self.streamed_request(
                                app, 'POST', self.url + '/upload', headers, chunks)
                        self.assertEqual(len(spooled), 1)
                        self.assertTrue(spooled[0].closed)
                        if size > limit:
                            self.assertEqual(status, 413, body)
                            self.assertEqual(body['detail'], 'Datei zu gross (max. 500 MB)')
                            self.assertEqual(consumed, chunks[:-1])
                            self.assertTrue(spooled[0]._rolled)
                            self.assertEqual(target.read_bytes(), b'original')
                            with closing(database.get_db()) as db:
                                self.assertEqual(db.execute('SELECT COUNT(*) FROM audit_log').fetchone()[0], audit_before)
                        else:
                            self.assertEqual(status, 200, body)
                            self.assertEqual(consumed, chunks)
                            self.assertEqual(target.read_bytes(), b'x' * size)

    def test_incomplete_upload_closes_temporary_file(self):
        chunks = [b'--upload\r\nContent-Disposition: form-data; name="file"; filename="partial.bin"\r\n\r\n',
                  b'unfinished file']
        spooled = []

        def record_spool(*args, **kwargs):
            file = tempfile.SpooledTemporaryFile(*args, **kwargs)
            spooled.append(file)
            return file

        with patch.object(formparsers, 'SpooledTemporaryFile', record_spool):
            status, _, _ = self.streamed_request(dedicated_app, 'POST', self.url + '/upload', {
                **self.headers(), 'X-Requested-With': 'XMLHttpRequest',
                'Content-Type': 'multipart/form-data; boundary=upload',
            }, chunks)
        self.assertEqual(status, 422)
        self.assertEqual(len(spooled), 1)
        self.assertTrue(spooled[0].closed)
        self.assertFalse(file_storage.local_path(self.project, 'partial.bin').exists())

    def test_malformed_multipart_upload_returns_client_error(self):
        with TestClient(dedicated_app) as client:
            response = client.post(self.url + '/upload', headers={
                **self.headers(), 'X-Requested-With': 'XMLHttpRequest',
                'Content-Type': 'multipart/form-data; boundary=upload',
            }, content=b'not a multipart body')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['detail'], 'There was an error parsing the body')

    def test_session_id_does_not_transfer_identity_between_tokens(self):
        for app in (legacy_app, dedicated_app):
            with self.subTest(app=app.title), TestClient(app) as client:
                owner = self.initialize(client)
                reader = self.initialize(client, 'reader')
                self.assertEqual(self.call(client, reader, 'whoami')['id'], self.users['reader'])
                self.assertEqual(self.call(client, owner, 'whoami')['id'], self.users['owner'])
                changed_token = {**owner, **self.headers('reader')}
                response = client.post('/mcp/', headers=changed_token,
                                       json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                                             'params': {'name': 'whoami', 'arguments': {}}})
                if response.status_code == 200:
                    result = self.call(client, changed_token, 'whoami')
                    self.assertEqual(result['id'], self.users['reader'])
                    self.call(client, changed_token, 'update_project',
                              {'project_id': self.project, 'name': 'Forbidden'}, error=True)
                    self.assertEqual(self.call(client, owner, 'whoami')['id'], self.users['owner'])
                else:
                    self.assertIn(response.status_code, (403, 404))

    def test_role_changes_take_effect_in_existing_sessions(self):
        for app in (legacy_app, dedicated_app):
            with self.subTest(app=app.title), TestClient(app) as client:
                with closing(database.get_db()) as db, db:
                    db.execute("UPDATE users SET is_admin=1 WHERE id=?", (self.users['reader'],))
                headers = self.initialize(client, 'reader')
                self.call(client, headers, 'update_project',
                          {'project_id': self.project, 'name': 'Admin change'})
                with closing(database.get_db()) as db, db:
                    db.execute("UPDATE users SET is_admin=0 WHERE id=?", (self.users['reader'],))
                self.call(client, headers, 'update_project',
                          {'project_id': self.project, 'name': 'Forbidden'}, error=True)

    def test_metadata_preserves_legacy_url_and_uses_dedicated_origin_for_files(self):
        self.assertEqual(_with_port('https://[::1]:8504', 8506), 'https://[::1]:8506')
        for app, port in ((legacy_app, 8504), (dedicated_app, 8506)):
            with TestClient(app, base_url=f'http://tareas.test:{port}') as client:
                metadata = client.get('/.well-known/tareas-agent.json').json()
            self.assertEqual(metadata['mcp']['endpoint'], f'http://tareas.test:{port}/mcp/')
            self.assertEqual(metadata['mcp']['legacy_endpoint'], 'http://tareas.test:8504/mcp/')
            self.assertEqual(metadata['mcp']['dedicated_endpoint'], 'http://tareas.test:8506/mcp/')
            self.assertEqual(metadata['web_ui'], 'http://tareas.test:8504/')
            self.assertEqual(metadata['file_api_base_url'], f'http://tareas.test:{port}')


if __name__ == '__main__':
    unittest.main()
