"""Mehrfachauswahl in Chromium mit echten Assets und echten ZIP-Dateien."""

import io
import json
import mimetypes
import sys
import tempfile
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, quote, urlparse
from zipfile import ZipFile

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dashboard import database
from dashboard.file_archives import build_archive
from dashboard.file_storage import TaskStorage
from dashboard.text_files import text_format


@contextmanager
def download_server(storage):
    # Chromium kann native Downloads ausserhalb der Playwright-Routen abwickeln.
    # Deren echte HTTP-Antwort und Dateibytes ueber einen lokalen Server pruefen.
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            url = urlparse(self.path)
            if url.path != '/api/tasks/1/files/download':
                self.send_error(404)
                return
            path = parse_qs(url.query)['path'][0]
            content, mime_type = storage.get_file(path)
            self.send_response(200)
            self.send_header('Content-Type', mime_type)
            self.send_header('Content-Length', str(len(content)))
            self.send_header('Content-Disposition', f"attachment; filename*=UTF-8''{quote(Path(path).name, safe='')}")
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}'
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def main():
    with tempfile.TemporaryDirectory(prefix='tareas-download-ui-') as temp, patch.object(database, 'DB_DIR', Path(temp)):
        root = Path(temp) / 'files/task-1'
        folder = "Team's <files>"
        special = '100% + & #.txt'
        (root / folder / 'empty').mkdir(parents=True)
        (root / 'empty').mkdir()
        (root / folder / 'nested.txt').write_bytes(b'nested')
        (root / 'Alpha.txt').write_bytes(b'alpha')
        (root / 'Beta.bin').write_bytes(b'\x00\xffbinary')
        (root / special).write_bytes('Grüße ✓'.encode())
        storage = TaskStorage(1, 'local', '', False)
        requests, errors = [], []
        state = {'failure': False}
        html = '''<!doctype html><html><head><meta charset="utf-8">
            <link rel="stylesheet" href="/static/css/style.css">
            <link rel="stylesheet" href="/static/css/file_browser.css"></head>
            <body data-standalone="true"><div id="files"></div>
            <script src="/static/js/i18n.js"></script><script src="/static/js/app_core.js"></script>
            <script src="/static/js/file_browser.js"></script><script src="/test-init.js"></script></body></html>'''

        def serve(route):
            url = urlparse(route.request.url)
            path = url.path
            if path == '/':
                route.fulfill(content_type='text/html', body=html)
            elif path == '/test-init.js':
                route.fulfill(content_type='application/javascript', body="""
                    window.openedEditors = [];
                    window.openTextFileEditor = (...args) => window.openedEditors.push(args);
                    window.files = new FileBrowser('files', 1);
                """)
            elif path.startswith('/static/'):
                file = ROOT / 'dashboard' / path.lstrip('/')
                route.fulfill(content_type=mimetypes.guess_type(file)[0] or 'application/octet-stream', body=file.read_bytes())
            elif path == '/api/tasks/1/files':
                directory = parse_qs(url.query).get('path', [''])[0]
                entries = storage.list_directory(directory)
                for entry in entries:
                    entry['text_format'] = text_format(entry['name']) if entry['type'] == 'file' else None
                route.fulfill(json={'items': entries, 'can_write': False, 'onlyoffice_configured': False})
            elif path == '/api/tasks/1/files/archive':
                assert route.request.headers['x-requested-with'] == 'XMLHttpRequest'
                paths = route.request.post_data_json['paths']
                requests.append(paths)
                if state['failure']:
                    route.fulfill(status=503, json={'detail': 'files.webdavConnectionError'})
                else:
                    output = io.BytesIO()
                    filename = build_archive(storage, paths, output)
                    route.fulfill(content_type='application/zip', body=output.getvalue(),
                                  headers={'Content-Disposition': f"attachment; filename*=UTF-8''{quote(filename, safe='')}"})
            elif path == '/api/tasks/1/files/download':
                route.continue_()
            else:
                route.fulfill(json={})

        with download_server(storage) as base_url, sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={'width': 1000, 'height': 850}, accept_downloads=True)
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.context.route(base_url + '/**', serve)
            page.goto(base_url + '/')
            expect(page.locator('.fb-tree-row')).to_have_count(5)
            expect(page.locator('#fb-upload-1')).to_be_hidden()
            expect(page.locator('#fb-download-folder-1')).to_be_enabled()
            expect(page.locator('#fb-download-selection-1')).to_be_disabled()

            def row(path):
                value = json.dumps(path, ensure_ascii=False)
                return page.locator(f'.fb-tree-row[data-itempath={value}], .fb-grid-item[data-itempath={value}]')

            def selected():
                return page.evaluate('[...files.selection.keys()]')

            def download(action):
                with page.expect_download() as event:
                    action()
                result = event.value
                assert result.failure() is None
                with ZipFile(result.path()) as archive:
                    assert archive.testzip() is None
                    return result.suggested_filename, {name: archive.read(name) for name in archive.namelist()}

            # Checkboxen waehlen ohne Navigation; Eltern/Kind-Auswahl dedupliziert das ZIP.
            row(folder).locator('.fb-select').check()
            row(folder).locator('.fb-tree-expand').click()
            expect(row(f'{folder}/nested.txt')).to_be_visible()
            row(f'{folder}/nested.txt').locator('.fb-select').check()
            row('Beta.bin').locator('.fb-select').check()
            assert len(selected()) == 3
            row('Beta.bin').click(button='right')
            expect(page.locator('.fb-ctx-item')).to_have_count(1)
            _, contents = download(lambda: page.locator('[data-action="download-selection"]').click())
            assert contents == {f'{folder}/': b'', f'{folder}/empty/': b'', f'{folder}/nested.txt': b'nested', 'Beta.bin': b'\x00\xffbinary'}
            assert len(selected()) == 3

            # Rechte Maustaste auf einen anderen Ordner waehlt ihn allein und bietet ZIP an.
            row('empty').click(button='right')
            filename, contents = download(lambda: page.locator('[data-action="archive"]').click())
            assert filename == 'empty.zip' and contents == {'empty/': b''}

            # Strg/Cmd, Bereichsauswahl und Tastatur beziehen sich auf sichtbare Eintraege.
            page.locator('#fb-clear-selection-1').click()
            row(special).click()
            row('Beta.bin').click(modifiers=['Shift'])
            assert set(selected()) == {special, 'Alpha.txt', 'Beta.bin'}
            row('Alpha.txt').click(modifiers=['Control'])
            assert set(selected()) == {special, 'Beta.bin'}
            row('Alpha.txt').click(modifiers=['Meta'])
            assert len(selected()) == 3
            row('Alpha.txt').focus()
            page.keyboard.press('Control+a')
            assert len(selected()) == 7
            page.keyboard.press('Escape')
            assert not selected()
            page.keyboard.press('Space')
            assert selected() == ['Alpha.txt']
            page.keyboard.press('Enter')
            assert page.evaluate('openedEditors[0][1]') == 'Alpha.txt'
            page.locator('#fb-clear-selection-1').click()

            # Auswahl bleibt beim Wechsel Baum/Grid erhalten; Checkbox-Doppelklick oeffnet nichts.
            row(special).locator('.fb-select').check()
            row('Beta.bin').locator('.fb-select').check()
            page.locator('#fb-toggle-1').click()
            expect(page.locator('.fb-grid-item.fb-selected')).to_have_count(2)
            _, contents = download(lambda: page.locator('#fb-download-selection-1').click())
            assert contents == {special: 'Grüße ✓'.encode(), 'Beta.bin': b'\x00\xffbinary'}
            row('Alpha.txt').locator('.fb-select').dblclick()
            assert page.evaluate('openedEditors.length') == 1

            # Fehler erhaelt die Auswahl und hebt die Download-Sperre wieder auf.
            state['failure'] = True
            page.locator('#fb-download-selection-1').click()
            expect(page.locator('.notification').last).to_contain_text('Nextcloud/WebDAV ist nicht erreichbar')
            expect(page.locator('#fb-download-selection-1')).to_be_enabled()
            assert len(selected()) == 2
            state['failure'] = False
            before = len(requests)
            download(lambda: page.evaluate("() => { files.downloadSelection(); files.downloadSelection(); }"))
            assert len(requests) == before + 1

            # Einzeldatei bleibt ein normaler Download mit unveraendertem Dateinamen/Inhalt.
            row(special).click()
            assert selected() == [special], selected()
            with page.expect_download() as event:
                page.locator('#fb-download-selection-1').click()
            assert event.value.suggested_filename == special, (event.value.suggested_filename, special)
            assert Path(event.value.path()).read_bytes() == 'Grüße ✓'.encode()

            # Verzeichniswechsel loescht die Auswahl, ZIP-Button nutzt den aktuellen Ordner.
            row(folder).dblclick()
            expect(page.locator('.fb-grid-item')).to_have_count(2)
            assert not selected()
            filename, contents = download(lambda: page.locator('#fb-download-folder-1').click())
            # Chromium ersetzt plattformuebergreifend unzulaessige Zeichen im Downloadnamen.
            assert filename == folder.replace('<', '_').replace('>', '_') + '.zip', filename
            assert contents == {f'{folder}/': b'', f'{folder}/empty/': b'', f'{folder}/nested.txt': b'nested'}
            page.locator('.fb-bc-root').click()
            expect(page.locator('.fb-grid-item')).to_have_count(5)
            page.locator('#fb-select-all-1').check()
            assert len(selected()) == 5
            page.locator('#fb-select-all-1').uncheck()
            assert not selected()
            _, contents = download(lambda: page.locator('#fb-download-folder-1').click())
            assert len(contents) == 7

            # Auch auf schmalen Displays bleiben Checkboxen und Download erreichbar.
            page.set_viewport_size({'width': 390, 'height': 850})
            row(folder).locator('.fb-select').check()
            row('Beta.bin').locator('.fb-select').check()
            assert page.locator('.fb-wrapper').evaluate('el => el.scrollWidth <= el.clientWidth')
            page.screenshot(path='/tmp/tareas-file-download-mobile.png')
            download(lambda: page.locator('#fb-download-selection-1').click())
            assert errors == [], errors
            browser.close()
    print('File download browser checks passed: tree/grid selection, checkboxes, Ctrl/Cmd/Shift, keyboard, readonly access, folder/mixed/root/empty ZIPs, binary/Unicode filenames, errors/retry, duplicate-click guard, navigation and mobile layout.')


if __name__ == '__main__':
    main()
