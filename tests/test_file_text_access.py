"""Textausschnitte und Dateisuche: Grenzen, Cursor, echte MCP-Calls und WebDAV."""

import asyncio
import codecs
import json
import unittest
from contextlib import closing
from unittest.mock import patch

import httpx
from fastmcp import Client

from dashboard import database, file_storage, file_text_access as text_access, mcp_server as api, webdav
from dashboard.errors import ApplicationError
from dashboard.mcp_errors import MCPToolError
import test_mcp_files as fixtures


class MemoryStorage:
    def __init__(self, content, chunk_size=7):
        self.content, self.chunk_size = content, chunk_size
        self.closed, self.consumed = 0, 0

    def iter_file(self, path):
        try:
            for start in range(0, len(self.content), self.chunk_size):
                part = self.content[start:start + self.chunk_size]
                self.consumed += len(part)
                yield part
        finally:
            self.closed += 1


class TextAccessTests(unittest.TestCase):
    def test_unicode_boms_and_newline_boundaries(self):
        original = 'Äpfel 🍎\r\n東京\rEnde\n'
        for bom, codec in ((b'', 'utf-8'), (codecs.BOM_UTF8, 'utf-8'),
                           (codecs.BOM_UTF16_LE, 'utf-16-le'), (codecs.BOM_UTF16_BE, 'utf-16-be')):
            with self.subTest(codec=codec, bom=bom):
                storage = MemoryStorage(bom + original.encode(codec), 1)
                result = text_access.read_text_range(storage, '', start_line=2, line_count=1)
                self.assertEqual(result['content'], '東京\n')
                self.assertEqual(result['source_encoding'], codec)
                self.assertEqual((result['next_line'], result['next_column'], result['eof']), (3, 1, False))
                self.assertEqual(storage.closed, 1)

    def test_all_pages_reassemble_text_without_gaps_or_duplicates(self):
        original = '\nαβγ\r\n' + '😀ab' * 150 + '\rEND'
        expected = original.replace('\r\n', '\n').replace('\r', '\n')
        for chunk_size in (1, 8, 65536):
            for max_chars in (1, 5, 128):
                with self.subTest(chunk=chunk_size, limit=max_chars):
                    pieces, line, column = [], 1, 1
                    for _ in range(len(expected) + 1):
                        result = text_access.read_text_range(MemoryStorage(original.encode(), chunk_size), '',
                                                            start_line=line, start_column=column,
                                                            line_count=2, max_chars=max_chars)
                        self.assertLessEqual(len(result['content']), max_chars)
                        pieces.append(result['content'])
                        if result['eof']:
                            break
                        self.assertNotEqual((line, column), (result['next_line'], result['next_column']))
                        line, column = result['next_line'], result['next_column']
                    else:
                        self.fail('Cursor did not finish')
                    self.assertEqual(''.join(pieces), expected)

    def test_empty_files_blank_lines_eof_and_missing_final_newline(self):
        for content, expected in ((b'', ''), (codecs.BOM_UTF8, ''), (b'\n\n', '\n\n'), (b'end', 'end')):
            result = text_access.read_text_range(MemoryStorage(content), '')
            self.assertEqual(result['content'], expected)
            self.assertTrue(result['eof'])
            self.assertIsNone(result['next_line'])
        result = text_access.read_text_range(MemoryStorage(b'one\ntwo'), '', start_line=20)
        self.assertEqual(result['content'], '')
        self.assertTrue(result['eof'])
        for content in (b'a\nnext', b'a'):
            with self.assertRaises(ApplicationError):
                text_access.read_text_range(MemoryStorage(content), '', start_column=5)
        self.assertTrue(text_access.read_text_range(MemoryStorage(b'a'), '', start_column=2)['eof'])

    def test_early_close_does_not_download_the_whole_file(self):
        storage = MemoryStorage(b'short\n' + b'x' * (2 * 1024 * 1024), 65536)
        result = text_access.read_text_range(storage, '', line_count=1)
        self.assertEqual(result['content'], 'short\n')
        self.assertLessEqual(storage.consumed, 65536)
        self.assertEqual(storage.closed, 1)

    def test_literal_case_search_unicode_columns_and_context(self):
        storage = MemoryStorage('vor\r\nÄpfel [A.*] [A.*]\r\nnach\n[A.*]\nEnde'.encode(), 3)
        result = text_access.search_text_file(storage, '', '[a.*]', limit=1, context_lines=1)
        hit = result['matches'][0]
        self.assertEqual((hit['line'], hit['column'], hit['end_column']), (2, 7, 12))
        self.assertEqual([line['line'] for line in hit['context']], [1, 2, 3])
        self.assertEqual(result['next_line'], 3)
        self.assertFalse(result['complete'])
        second = text_access.search_text_file(storage, '', '[a.*]', start_line=result['next_line'])
        self.assertEqual([hit['line'] for hit in second['matches']], [4])
        self.assertTrue(second['complete'])
        self.assertEqual(text_access.search_text_file(storage, '', '[a.*]', case_sensitive=True)['matches'], [])
        self.assertEqual(storage.closed, 3)

    def test_search_long_line_boundary_match_and_bounded_preview(self):
        body = 'x' * 65534 + 'NÄDEL' + 'y' * 70000
        result = text_access.search_text_file(MemoryStorage(body.encode(), 65536), '', 'nädel')
        hit = result['matches'][0]
        self.assertEqual((hit['line'], hit['column']), (1, 65535))
        preview = hit['context'][0]
        self.assertIn('NÄDEL', preview['text'])
        self.assertTrue(preview['truncated_start'])
        self.assertTrue(preview['truncated_end'])
        self.assertLessEqual(len(preview['text']), 640)
        self.assertEqual(body[preview['start_column'] - 1:preview['start_column'] - 1 + len(preview['text'])], preview['text'])

    def test_search_budget_and_cursor_never_skip_matching_lines(self):
        content = ''.join('hit' + 'x' * 997 + '\n' for _ in range(60)).encode()
        start, found, pages = 1, [], 0
        while start is not None:
            result = text_access.search_text_file(MemoryStorage(content, 65536), '', 'hit',
                                                 start_line=start, context_lines=5, limit=50)
            self.assertLessEqual(result['returned_chars'], 16000)
            self.assertEqual(result['returned_chars'], sum(len(line['text']) for hit in result['matches'] for line in hit['context']))
            found.extend(hit['line'] for hit in result['matches'])
            start = result['next_line']
            pages += 1
            self.assertLess(pages, 61)
        self.assertEqual(found, list(range(1, 61)))
        self.assertGreater(pages, 1)

    def test_search_zero_context_one_match_per_line_and_empty_final_page(self):
        storage = MemoryStorage(b'hit hit\nhit')
        first = text_access.search_text_file(storage, '', 'hit', limit=2, context_lines=0)
        self.assertEqual([hit['line'] for hit in first['matches']], [1, 2])
        self.assertTrue(all(len(hit['context']) == 1 for hit in first['matches']))
        end = text_access.search_text_file(storage, '', 'hit', start_line=first['next_line'])
        self.assertEqual(end['matches'], [])
        self.assertTrue(end['complete'])

    def test_binary_invalid_encoding_and_unfinished_sequences(self):
        for content in (b'a\x00b', b'\xff\xfe\x00', codecs.BOM_UTF32_LE + b'A\x00\x00\x00',
                        b'a\xc3', b'%PDF-1.7', b'PK\x03\x04zip', b'abc\x07'):
            for operation in (lambda s: text_access.read_text_range(s, ''),
                              lambda s: text_access.search_text_file(s, '', 'a')):
                with self.subTest(content=content):
                    storage = MemoryStorage(content, 1)
                    with self.assertRaises(ApplicationError) as caught:
                        operation(storage)
                    self.assertEqual(caught.exception.code, 'unsupported_text_file')
                    self.assertEqual(storage.closed, 1)
        with self.assertRaises(ApplicationError) as caught:
            text_access.read_text_range(MemoryStorage(codecs.BOM_UTF16_LE + b'A\x00'), '', utf8_only=True)
        self.assertEqual(caught.exception.code, 'invalid_encoding')

    def test_scan_size_time_limits_and_cleanup(self):
        for operation in (lambda s: text_access.read_text_range(s, '', start_line=100),
                          lambda s: text_access.search_text_file(s, '', 'absent')):
            storage = MemoryStorage(b'123456789' * 10)
            with patch.object(text_access, 'MAX_SCAN_BYTES', 20), self.assertRaises(ApplicationError) as caught:
                operation(storage)
            self.assertEqual(caught.exception.code, 'file_scan_limit')
            self.assertEqual(storage.closed, 1)
            storage = MemoryStorage(b'text')
            with patch.object(text_access.time, 'monotonic', side_effect=[0, 31]), self.assertRaises(ApplicationError) as caught:
                operation(storage)
            self.assertEqual(caught.exception.code, 'file_scan_timeout')
            self.assertEqual(storage.closed, 1)


class MCPTextAccessTests(unittest.TestCase):
    setUp = fixtures.MCPFileTests.setUp
    patch = fixtures.MCPFileTests.patch
    login = fixtures.MCPFileTests.login

    def test_large_local_text_read_search_and_legacy_limit(self):
        content = 'prefix\n' + 'x' * (1024 * 1024 + 1) + '\nZiel ÄÖÜ\n'
        (self.files / 'large.txt').write_text(content)
        with self.assertRaises(MCPToolError) as caught:
            api.file_read(self.task, 'large.txt')
        self.assertEqual(caught.exception.code, 'file_too_large')
        part = api.file_read(self.task, 'large.txt', start_line=3, line_count=1)
        self.assertEqual(part['content'], 'Ziel ÄÖÜ\n')
        hit = api.file_search(self.task, 'large.txt', 'äöü', context_lines=0)['matches'][0]
        self.assertEqual((hit['line'], hit['column']), (3, 6))
        self.assertLess(len(json.dumps(hit)), 500)

    def test_permissions_traversal_symlinks_and_no_audit_contents(self):
        (self.files / 'readme.txt').write_text('secret body\nsecond')
        operations = (lambda path: api.file_read(self.task, path, start_line=1),
                      lambda path: api.file_search(self.task, path, 'secret'))
        for name in ('owner', 'reader', 'editor', 'assignee', 'admin'):
            self.login(name)
            for operation in operations:
                self.assertTrue(operation('readme.txt'))
        self.login('outsider')
        with patch.object(file_storage.TaskStorage, 'iter_file') as stream:
            for operation in operations:
                with self.assertRaises(MCPToolError) as caught:
                    operation('readme.txt')
                self.assertEqual(caught.exception.code, 'permission_denied')
            stream.assert_not_called()
        self.login('owner')
        (self.files / 'escape.txt').symlink_to(self.root / 'test.db')
        for path in ('../test.db', '%252e%252e/test.db', '/test.db', 'escape.txt'):
            for operation in operations:
                with self.assertRaises(MCPToolError) as caught:
                    operation(path)
                self.assertEqual(caught.exception.code, 'invalid_path')
        with closing(database.get_db()) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM audit_log').fetchone()[0], 0)

    def test_permissions_rechecked_after_streaming(self):
        self.login('reader')
        def stream(storage, path):
            yield b'visible\n'
            with closing(database.get_db()) as db, db:
                db.execute('DELETE FROM project_members WHERE project_id=? AND user_id=?',
                           (self.task, self.users['reader']['id']))
        for operation in (lambda: api.file_read(self.task, 'readme.txt', start_line=1),
                          lambda: api.file_search(self.task, 'readme.txt', 'visible')):
            with closing(database.get_db()) as db, db:
                db.execute('INSERT OR IGNORE INTO project_members(project_id,user_id,can_read) VALUES (?,?,1)',
                           (self.task, self.users['reader']['id']))
            with patch.object(file_storage.TaskStorage, 'iter_file', stream), self.assertRaises(MCPToolError) as caught:
                operation()
            self.assertEqual(caught.exception.code, 'permission_denied')

    def test_real_mcp_schema_strict_arguments_and_results(self):
        (self.files / 'data.txt').write_text('first\nneedle\nend')
        async def check():
            async with Client(api.mcp) as client:
                tools = {tool.name: tool for tool in await client.list_tools()}
                self.assertTrue(tools['file.search'].annotations.readOnlyHint)
                for field in ('start_line', 'line_count', 'start_column', 'max_chars'):
                    self.assertIn(field, tools['file.read'].inputSchema['properties'])
                for tool, extra in (('file.read', {'start_line': 2, 'line_count': 1}),
                                    ('file.search', {'query': 'needle'})):
                    result = await client.call_tool(tool, {'task_id': self.task, 'path': 'data.txt', **extra})
                    self.assertEqual(json.loads(result.content[0].text), result.structured_content)
                    self.assertIn('needle', json.dumps(result.structured_content))
                invalid = [('file.read', {key: value}) for key, value in
                           (('start_line', 0), ('start_line', True), ('start_column', 1.5), ('line_count', '2'),
                            ('line_count', 1001), ('max_chars', 32001), ('max_chars', 0))]
                invalid += [('file.read', {'start_line': 1, 'encoding': 'base64'}),
                            ('file.search', {'query': ''}), ('file.search', {'query': 'a\nb'}),
                            ('file.search', {'query': 'x' * 257}), ('file.search', {'query': 'a', 'limit': 51}),
                            ('file.search', {'query': 'a', 'context_lines': -1}),
                            ('file.search', {'query': 'a', 'case_sensitive': 'yes'})]
                for tool, extra in invalid:
                    with self.subTest(tool=tool, args=extra):
                        result = await client.call_tool(tool, {'task_id': self.task, 'path': 'data.txt', **extra}, raise_on_error=False)
                        self.assertTrue(result.is_error)
                        self.assertIn('field', result.structured_content)
                        self.assertEqual(json.loads(result.content[0].text), result.structured_content)
        asyncio.run(check())

    def test_webdav_streams_close_for_success_early_return_and_failures(self):
        with closing(database.get_db()) as db, db:
            db.execute("UPDATE tasks SET file_storage_type='webdav',nextcloud_path='projects/files' WHERE id=?", (self.task,))
        responses, clients = [], []
        real_client = httpx.Client
        def handle(request):
            self.assertTrue(request.url.path.startswith('/remote.php/dav/files/test/projects/files/'))
            if request.url.path.endswith('timeout.txt'):
                raise httpx.ReadTimeout('private backend')
            if request.url.path.endswith('offline.txt'):
                raise httpx.ConnectError('private backend')
            content = b'\x00' if request.url.path.endswith('binary.txt') else b'one\nneedle\n' + b'x' * 200000
            response = httpx.Response(404 if request.url.path.endswith('missing.txt') else 200, content=content)
            responses.append(response)
            return response
        def factory(**kwargs):
            client = real_client(transport=httpx.MockTransport(handle), **kwargs)
            clients.append(client)
            return client
        self.patch(webdav, '_get_config', return_value={'server_url': 'https://dav.example.test', 'username': 'test',
                                                      'password': 'placeholder', 'base_path': '', 'verify': True})
        self.patch(webdav.httpx, 'Client', side_effect=factory)
        self.assertEqual(api.file_read(self.task, 'ok.txt', line_count=1)['content'], 'one\n')
        self.assertEqual(api.file_search(self.task, 'ok.txt', 'needle', context_lines=0, limit=1)['matches'][0]['line'], 2)
        for path, code in (('binary.txt', 'unsupported_text_file'), ('missing.txt', 'file_not_found'),
                           ('timeout.txt', 'storage_timeout'), ('offline.txt', 'storage_unavailable')):
            with self.subTest(path=path), self.assertRaises(MCPToolError) as caught:
                with patch.object(api.logger, 'exception'):
                    api.file_search(self.task, path, 'x')
            self.assertEqual(caught.exception.code, code)
            self.assertNotIn('private backend', str(caught.exception))
        self.assertTrue(all(response.is_closed for response in responses))
        self.assertTrue(all(client.is_closed for client in clients))


if __name__ == '__main__':
    unittest.main()
