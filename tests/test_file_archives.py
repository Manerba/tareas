"""ZIP-Inhalte, Rechte, Pfadgrenzen, WebDAV-Streams und temporaere Ressourcen."""

import asyncio
import io
import tempfile
import threading
import unittest
from contextlib import closing
from unittest.mock import Mock, patch
from zipfile import ZipFile

import httpx
from starlette.requests import ClientDisconnect

from dashboard import api_nextcloud, database, file_archives, webdav
import test_file_storage as storage_tests


class FileArchiveTests(unittest.TestCase):
    patch = storage_tests.FileStorageTests.patch
    ok = storage_tests.FileStorageTests.ok
    assign = storage_tests.FileStorageTests.assign

    def setUp(self):
        storage_tests.FileStorageTests.setUp(self)
        self.assign()
        self.files = self.root / 'files' / f'task-{self.task_id}'
        (self.files / 'docs' / 'nested' / 'empty').mkdir(parents=True)
        (self.files / 'docs' / 'nested' / 'Grüße 100% #&.txt').write_bytes('Grüße ✓\n'.encode())
        (self.files / 'docs' / 'readme.txt').write_bytes(b'docs')
        (self.files / 'readme.txt').write_bytes(b'root\x00\xff')
        self.archive_url = self.url + '/files/archive'
        self.temp_files = []
        real_tempfile = tempfile.TemporaryFile

        def tracked_tempfile():
            result = real_tempfile()
            self.temp_files.append(result)
            return result

        self.patch(api_nextcloud.tempfile, 'TemporaryFile', side_effect=tracked_tempfile)

    def download(self, paths, status=200):
        response = self.client.post(self.archive_url, json={'paths': paths})
        self.assertEqual(response.status_code, status, response.text[:100] if status != 200 else '')
        self.assertTrue(all(file.closed for file in self.temp_files))
        if status != 200:
            return response
        self.assertEqual(response.headers['content-type'], 'application/zip')
        self.assertEqual(int(response.headers['content-length']), len(response.content))
        self.assertEqual(response.headers['cache-control'], 'private, no-store')
        with ZipFile(io.BytesIO(response.content)) as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(len(archive.namelist()), len(set(archive.namelist())))
            return {name: archive.read(name) for name in archive.namelist()}

    def test_mixed_selection_preserves_structure_binary_unicode_and_empty_folders(self):
        entries = self.download(['docs/nested', 'docs', 'readme.txt', 'docs/'])
        self.assertEqual(entries, {
            'docs/': b'', 'docs/nested/': b'', 'docs/nested/empty/': b'',
            'docs/nested/Grüße 100% #&.txt': 'Grüße ✓\n'.encode(),
            'docs/readme.txt': b'docs', 'readme.txt': b'root\x00\xff',
        })
        self.assertEqual(self.download(['docs/nested']), {
            'nested/': b'', 'nested/empty/': b'', 'nested/Grüße 100% #&.txt': 'Grüße ✓\n'.encode(),
        })
        self.assertEqual(self.download(['docs/readme.txt']), {'readme.txt': b'docs'})
        self.assertEqual(self.download(['docs/nested/empty']), {'empty/': b''})
        self.assertEqual(entries, self.download(['', 'docs']))
        # Gleichnamige Dateien aus unterschiedlichen Verzeichnissen bleiben unterscheidbar.
        self.assertEqual(self.download(['readme.txt', 'docs/readme.txt']), {
            'readme.txt': b'root\x00\xff', 'docs/readme.txt': b'docs',
        })

    def test_empty_storage_and_dotfiles(self):
        (self.files / 'docs' / 'nested' / 'empty' / '.hidden').write_bytes(b'hidden')
        self.assertEqual(self.download(['docs/nested/empty']), {'empty/': b'', 'empty/.hidden': b'hidden'})
        self.assign('none')
        self.assign()
        self.assertEqual(self.download(['']), {})

    def test_all_readers_can_download_but_outsiders_and_revoked_users_cannot(self):
        for name in ('owner', 'admin', 'assignee', 'reader', 'editor'):
            self.user = self.users[name]
            self.assertEqual(self.download(['readme.txt']), {'readme.txt': b'root\x00\xff'})
        self.user = self.users['outsider']
        self.download([''], 403)
        self.user = self.users['reader']
        with closing(database.get_db()) as db, db:
            db.execute('DELETE FROM project_members WHERE project_id=? AND user_id=?', (self.task_id, self.user['id']))
        self.download([''], 403)

    def test_invalid_paths_missing_entries_and_symlinks_are_rejected(self):
        for path in ('../other', '/etc/passwd', 'docs/../../x', '%252e%252e/outside', 'docs\\readme.txt', 'bad\x00name'):
            with self.subTest(path=path):
                self.download([path], 400)
        self.download(['readme.txt', 'missing'], 404)
        external = self.root / 'private'
        external.write_bytes(b'NEVER INCLUDE')
        (self.files / 'link').symlink_to(external)
        self.download(['link'], 404)
        self.assertNotIn('link', self.download(['']))
        (self.files / 'loop').symlink_to(self.files, target_is_directory=True)
        self.download(['loop/readme.txt'], 400)
        self.assertNotIn('loop/', self.download(['']))
        (self.files / 'C:evil.txt').write_bytes(b'bad drive name')
        self.download(['C:evil.txt'], 400)
        for body in ({}, {'paths': []}, {'paths': [''] * 501}, {'paths': [1]}, {'paths': None}):
            self.assertEqual(self.client.post(self.archive_url, json=body).status_code, 422)

    def test_permission_revoked_while_building_prevents_delivery(self):
        self.user = self.users['reader']

        def build(storage, paths, output):
            filename = file_archives.build_archive(storage, paths, output)
            with closing(database.get_db()) as db, db:
                db.execute('DELETE FROM project_members WHERE project_id=? AND user_id=?', (self.task_id, self.user['id']))
            return filename

        with patch.object(api_nextcloud, 'build_archive', side_effect=build):
            self.download([''], 403)

    def test_limits_use_actual_bytes_and_close_temporary_files(self):
        for constant, limit in (('MAX_ARCHIVE_BYTES', 3), ('MAX_ARCHIVE_ENTRIES', 2), ('MAX_ARCHIVE_DEPTH', 1)):
            with self.subTest(constant=constant), patch.object(file_archives, constant, limit):
                response = self.download([''], 413)
                self.assertEqual(response.json()['detail'], 'files.archiveTooLarge')

    def mock_webdav(self, failure=None):
        self.assign('webdav', nextcloud_path='projects/demo')
        self.patch(webdav, '_get_config', return_value=None)
        listing = {
            'projects/demo': [{'name': 'folder', 'type': 'directory'}],
            'projects/demo/folder': [{'name': 'empty', 'type': 'directory'}, {'name': 'data.bin', 'type': 'file', 'size': 0}],
            'projects/demo/folder/empty': [],
        }
        self.patch(webdav, 'list_directory', side_effect=lambda path: listing[path])
        response, client = Mock(), Mock()
        if failure:
            response.iter_bytes.side_effect = failure
        else:
            response.iter_bytes.return_value = iter([b'first', b'second\x00\xff'])
        stream = self.patch(webdav, 'get_file_stream', return_value=(response, client, 'application/octet-stream', '0'))
        return listing, response, client, stream

    def test_webdav_archive_streams_files_and_retains_remote_base_and_empty_folders(self):
        _, response, client, stream = self.mock_webdav()
        self.assertEqual(self.download(['folder']), {
            'folder/': b'', 'folder/empty/': b'', 'folder/data.bin': b'firstsecond\x00\xff',
        })
        stream.assert_called_once_with('projects/demo/folder/data.bin')
        response.iter_bytes.assert_called_once_with(chunk_size=65536)
        response.close.assert_called_once()
        client.close.assert_called_once()

    def test_webdav_urls_keep_unicode_percent_hash_and_question_mark_in_the_path(self):
        config = {'server_url': 'https://dav.example.test', 'username': 'user@example.test',
                  'password': 'unused', 'base_path': 'Team #1', 'verify': True}
        self.patch(webdav, '_get_config', return_value=config)
        seen = []

        def serve(request):
            seen.append(request)
            return httpx.Response(200, content=b'remote bytes')

        real_client = httpx.Client
        with patch.object(webdav.httpx, 'Client', side_effect=lambda **kwargs: real_client(
                **kwargs, transport=httpx.MockTransport(serve))):
            response, client, _, _ = webdav.get_file_stream('projects/demo/Grüße 100% #&?.txt')
            try:
                self.assertEqual(b''.join(response.iter_bytes()), b'remote bytes')
            finally:
                response.close()
                client.close()
        self.assertEqual(seen[0].url.query, b'')
        self.assertEqual(seen[0].url.fragment, '')
        self.assertEqual(seen[0].url.path, '/remote.php/dav/files/user@example.test/Team #1/projects/demo/Grüße 100% #&?.txt')

    def test_webdav_failures_are_json_before_download_and_streams_close(self):
        for error, status, message in ((httpx.ConnectError('private hostname'), 503, 'files.webdavConnectionError'),
                                       (httpx.ReadTimeout('private timeout'), 504, 'files.webdavTimeout')):
            with self.subTest(error=error):
                _, response, client, _ = self.mock_webdav(error)
                with self.assertLogs('dashboard.api_nextcloud', level='WARNING'):
                    result = self.download(['folder'], status)
                self.assertEqual(result.json(), {'detail': message})
                response.close.assert_called_once()
                client.close.assert_called_once()

    def test_webdav_bad_listing_and_underreported_size_cannot_escape_limits(self):
        listing, response, client, stream = self.mock_webdav()
        for name in ('../secret', '/secret', '%252e%252e', 'bad/name', 'bad\\name'):
            listing['projects/demo/folder'] = [{'name': name, 'type': 'file'}]
            self.download(['folder'], 400)
        stream.assert_not_called()
        listing['projects/demo/folder'] = [{'name': 'large', 'type': 'file', 'size': 0}]
        with patch.object(file_archives, 'MAX_ARCHIVE_BYTES', 2):
            self.download(['folder'], 413)
        response.close.assert_called_once()
        client.close.assert_called_once()

    def test_response_closes_archive_on_disconnect_before_and_during_body(self):
        async def run(fail_at):
            file = tempfile.TemporaryFile()
            file.write(b'x' * 100000)
            response = api_nextcloud._ArchiveResponse(file, 'download.zip')
            calls = 0

            async def receive():
                return {'type': 'http.disconnect'}

            async def send(message):
                nonlocal calls
                calls += 1
                if calls == fail_at:
                    raise OSError('disconnected')

            with self.assertRaises(ClientDisconnect):
                await response({'asgi': {'spec_version': '2.4'}}, receive, send)
            self.assertTrue(file.closed)

        for fail_at in (1, 2, 3):
            asyncio.run(run(fail_at))

    def test_request_cancellation_waits_for_archive_worker_before_cleanup(self):
        started, finish = threading.Event(), threading.Event()

        def build(storage, paths, output):
            started.set()
            if not finish.wait(5):
                raise TimeoutError('test worker was not released')
            output.write(b'finished')
            return 'download.zip'

        async def run():
            task = asyncio.create_task(api_nextcloud.download_task_archive(
                self.task_id, api_nextcloud.ArchiveRequest(paths=['']), self.user))
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 5))
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertFalse(self.temp_files[-1].closed)
            finally:
                finish.set()
            for _ in range(100):
                if self.temp_files[-1].closed:
                    break
                await asyncio.sleep(0.01)
            self.assertTrue(self.temp_files[-1].closed)

        with patch.object(api_nextcloud, 'build_archive', side_effect=build):
            asyncio.run(run())


if __name__ == '__main__':
    unittest.main()
