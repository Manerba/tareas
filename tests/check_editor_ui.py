"""Echte Browser-CSP fuer Editor-Start und Dateiaktionen, lokale Assets/gemockte API."""

import json
import logging
import mimetypes
import sys
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
with patch('logging.handlers.RotatingFileHandler', return_value=logging.NullHandler()):
    from dashboard.app import app


@contextmanager
def download_server():
    """Browser-Downloads umgehen Playwright-Routing und brauchen einen HTTP-Server."""
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            url = urlparse(self.path)
            if not url.path.endswith('/files/download'):
                self.send_error(404)
                return
            name = parse_qs(url.query)['path'][0].rsplit('/', 1)[-1]
            content = b'# Projekt'
            self.send_response(200)
            self.send_header('Content-Type', mimetypes.guess_type(name)[0] or 'application/octet-stream')
            self.send_header('Content-Disposition', f'attachment; filename="{name}"')
            self.send_header('Content-Security-Policy', 'sandbox')
            self.send_header('Content-Length', str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, *_args):
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
    policies = {}
    # Ohne Context-Manager: produktiven Lifespan/DB-Migrationen nicht starten.
    client = TestClient(app)
    for configured in (False, True):
        with patch('dashboard.app.get_onlyoffice_origin', return_value='https://office.test' if configured else None):
            policies[configured] = client.get('/editor', follow_redirects=False).headers['content-security-policy']
    client.close()
    state = {'configured': False, 'edit_status': 400, 'api_failed': False, 'can_edit': True}
    requests = []
    violations = []
    errors = []
    translations = json.loads((ROOT / 'dashboard/static/i18n/de.json').read_text())
    items = [dict(name=name, type='file', size=12, last_modified='') for name in ('readme.md', 'Plan.docx', 'photo.png')]
    html = '''<html><body data-admin-mode="true"><div id="files"></div>
        <script src="/static/js/i18n.js"></script><script src="/static/js/app_core.js"></script>
        <script src="/static/js/file_browser.js"></script><script src="/static/js/onlyoffice_editor.js"></script>
        <script src="/test-init.js"></script></body></html>'''

    def serve(route):
        url = urlparse(route.request.url)
        path = url.path
        if url.netloc == 'office.test':
            if state['api_failed']:
                route.abort()
            else:
                route.fulfill(content_type='application/javascript', body='''window.DocsAPI = {DocEditor: function(id, config) {
                    window.editorConfig = config;
                    document.getElementById(id).textContent = 'Office started';
                }};''')
            return
        if path == '/editor':
            route.fulfill(content_type='text/html', headers={'Content-Security-Policy': policies[state['configured']]},
                          body=(ROOT / 'dashboard/templates/editor.html').read_text())
            return
        if path == '/':
            route.fulfill(content_type='text/html', body=html)
            return
        if path == '/test-init.js':
            route.fulfill(content_type='application/javascript', body="window.files = new FileBrowser('files', 1);")
            return
        if path.startswith('/static/'):
            file = ROOT / 'dashboard' / path.lstrip('/')
            route.fulfill(content_type=mimetypes.guess_type(file)[0] or 'application/octet-stream', body=file.read_bytes())
            return
        if path.endswith('/files/edit'):
            requests.append(route.request.url)
            data = {'detail': 'ONLYOFFICE ist nicht konfiguriert'} if state['edit_status'] == 400 else {
                'editor_url': 'https://office.test', 'editor_config': {'document': {'title': 'Plan.docx'}},
                'token': 'test-placeholder', 'can_edit': state['can_edit'],
            }
            route.fulfill(status=state['edit_status'], content_type='application/json', body=json.dumps(data))
            return
        if path.endswith('/files/download'):
            file_path = parse_qs(url.query)['path'][0]
            disposition = 'inline' if parse_qs(url.query).get('inline') == ['true'] else 'attachment'
            content_type = 'text/plain' if file_path.endswith('.png') else mimetypes.guess_type(file_path)[0]
            route.fulfill(content_type=content_type or 'application/octet-stream', body='# Projekt', headers={
                'Content-Disposition': f'{disposition}; filename="{file_path.rsplit("/", 1)[-1]}"',
                'Content-Security-Policy': 'sandbox',
            })
            return
        data = {'items': items, 'can_write': True, 'storage_type': 'local', 'onlyoffice_configured': state['configured']}
        route.fulfill(content_type='application/json', body=json.dumps(data))

    with download_server() as base_url, sync_playwright() as pw:
        browser = pw.chromium.launch()
        context = browser.new_context()
        context.route('**/*', serve)
        context.add_init_script("""window.cspViolations = []; document.addEventListener('securitypolicyviolation', e =>
            cspViolations.push({directive:e.effectiveDirective, blocked:e.blockedURI}));""")
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))

        # Strict CSP must allow startup and show the real API error, rather than a spinner.
        page.goto(f'{base_url}/editor?taskId=1&path=readme.md')
        expect(page.locator('#ooEditorBody')).to_contain_text('ONLYOFFICE ist nicht konfiguriert')
        expect(page.locator('.spinner')).to_have_count(0)
        expect(page.locator('#editorTitle')).to_have_text('readme.md')
        assert len(requests) == 1
        violations.extend(page.evaluate('cspViolations'))
        page.goto(f'{base_url}/editor')
        expect(page.locator('#ooEditorBody')).to_contain_text(translations['editor.missingParams'])

        # Configured Office still starts its external API, with read/edit badges.
        state.update(configured=True, edit_status=200)
        for can_edit in (True, False):
            state['can_edit'] = can_edit
            page.goto(f'{base_url}/editor?taskId=1&path=Plan.docx')
            expect(page.locator('#ooEditorContainer')).to_have_text('Office started')
            expect(page.locator('#editorTitle')).to_have_text('Plan.docx')
            expect(page.locator('.oo-badge')).to_have_text(translations['editor.edit' if can_edit else 'editor.readonly'])
            violations.extend(page.evaluate('cspViolations'))
        state['api_failed'] = True
        page.reload()
        expect(page.locator('#ooEditorBody')).to_contain_text(translations['editor.apiError'])
        state.update(configured=False, edit_status=400, api_failed=False)

        # Both views and all entry points must fall back without Office.
        page.goto(f'{base_url}/')
        for view in ('tree', 'grid'):
            page.evaluate('view => { files.viewMode = view; files.currentPath = "Unterlagen"; return files.loadDirectory("Unterlagen"); }', view)
            item = page.locator(f'.fb-{view}-' + ('row' if view == 'tree' else 'item') + '[data-name="readme.md"]')
            expect(item).to_be_visible()
            expect(page.locator('.fb-grid-edit')).to_have_count(0)
            item.click(button='right')
            expect(page.locator('.fb-ctx-item[data-action="edit"]')).to_have_count(0)
            with page.expect_download() as download:
                page.locator('.fb-ctx-item[data-action="download"]').click()
            assert download.value.suggested_filename == 'readme.md', download.value.suggested_filename
            assert Path(download.value.path()).read_text() == '# Projekt'
            before = len(requests)
            with page.expect_download() as download:
                item.dblclick()
            assert download.value.suggested_filename == 'readme.md', download.value.suggested_filename
            assert Path(download.value.path()).read_text() == '# Projekt'
            assert len(requests) == before

        # New directory data reflects setup/removal without a page reload.
        state.update(configured=True, edit_status=200, can_edit=True)
        page.evaluate('files.loadDirectory("Unterlagen")')
        expect(page.locator('.fb-grid-edit')).to_have_count(2)
        item = page.locator('.fb-grid-item[data-name="Plan.docx"]')
        for action in ('icon', 'context', 'double'):
            with context.expect_page() as opened:
                if action == 'icon':
                    item.locator('.fb-grid-edit').click()
                elif action == 'context':
                    item.click(button='right')
                    page.locator('.fb-ctx-item[data-action="edit"]').click()
                else:
                    item.dblclick()
            editor = opened.value
            expect(editor.locator('#ooEditorContainer')).to_have_text('Office started')
            assert parse_qs(urlparse(editor.url).query)['path'] == ['Unterlagen/Plan.docx']
            violations.extend(editor.evaluate('cspViolations'))
            editor.close()
        state.update(configured=False, edit_status=400)
        page.evaluate('files.loadDirectory("Unterlagen")')
        expect(page.locator('.fb-grid-edit')).to_have_count(0)
        with context.expect_page() as opened:
            page.locator('.fb-grid-item[data-name="photo.png"]').dblclick()
        expect(opened.value.locator('body')).to_contain_text('# Projekt')
        assert 'inline=true' in opened.value.url
        opened.value.close()
        assert violations == [], violations
        assert errors == [], errors
        browser.close()
    print('Editor browser checks passed: strict CSP, missing configuration, Office startup/errors, filenames, tree/grid download fallback and editor actions.')


if __name__ == '__main__':
    main()
