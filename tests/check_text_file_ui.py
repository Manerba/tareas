"""Native Datei-UI im Chromium: lokale Assets, API-Mocks, echte Seiten-CSP."""

import hashlib
import json
import logging
import mimetypes
import sys
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dashboard.text_files import text_format

with patch('logging.handlers.RotatingFileHandler', return_value=logging.NullHandler()):
    from dashboard.app import app


def main():
    source = '# Vorschau\n\n**Fett**\n\n<script>window.injected = true</script>\n\n[Unsafe](javascript:alert(1))'
    files = {'readme.md': source, 'notes.txt': '# No heading\n<tag>literal</tag>\n',
             'scripts/run.PS1': '# PowerShell\nWrite-Host "Grüße"\n', 'run.sh': '#!/bin/sh\nprintf "%s" "<b>literal</b>"\n',
             'Plan.docx': 'office', 'large.txt': 'x'}
    state = {'office': False, 'write': True, 'fail_save': False, 'fail_load': False, 'transport_error': None}
    saved, errors, violations, office_requests = [], [], [], []
    # Kein Lifespan: Tests duerfen die produktive Datenbank nicht initialisieren.
    client = TestClient(app)
    with patch('dashboard.app._extract_user_from_request', return_value={'id': 1}):
        csp = client.get('/text-editor').headers['content-security-policy']
    client.close()
    html = '''<!doctype html><html><head>
        <link rel="stylesheet" href="/static/css/style.css">
        <link rel="stylesheet" href="/static/css/file_browser.css">
        <link rel="stylesheet" href="/static/css/markdown_editor.css">
        <link rel="stylesheet" href="/static/css/text_file_editor.css"></head>
        <body data-standalone="true"><div id="files"></div>
        <script src="/static/js/i18n.js"></script><script src="/static/js/app_core.js"></script>
        <script src="/static/js/vendor/marked.umd.js"></script><script src="/static/js/vendor/turndown.js"></script>
        <script src="/static/js/markdown_editor.js"></script><script src="/static/js/text_file_editor.js"></script>
        <script src="/static/js/file_browser.js"></script><script src="/static/js/onlyoffice_editor.js"></script>
        <script src="/test-init.js"></script></body></html>'''

    def revision(path):
        return hashlib.sha256(files[path].encode()).hexdigest()

    def serve(route):
        url = urlparse(route.request.url)
        path, query = url.path, parse_qs(url.query)
        if path == '/':
            route.fulfill(content_type='text/html', body=html)
            return
        if path == '/text-editor':
            route.fulfill(content_type='text/html', headers={'Content-Security-Policy': csp},
                          body=(ROOT / 'dashboard/templates/text_editor.html').read_text())
            return
        if path == '/test-init.js':
            route.fulfill(content_type='application/javascript', body="window.files = new FileBrowser('files', 1);")
            return
        if path.startswith('/static/'):
            file = ROOT / 'dashboard' / path.lstrip('/')
            route.fulfill(content_type=mimetypes.guess_type(file)[0] or 'application/octet-stream', body=file.read_bytes())
            return
        status, data = 200, {}
        if state['transport_error'] and path.startswith('/api/tasks/1/files'):
            status, key = state['transport_error']
            data = {'detail': key}
        elif path.endswith('/files/text'):
            name = query['path'][0]
            if route.request.method == 'PUT':
                assert route.request.headers['x-requested-with'] == 'XMLHttpRequest'
                body = route.request.post_data_json
                if not state['write']:
                    status, data = 403, {'detail': 'No write access'}
                elif state['fail_save']:
                    status, data = 503, {'detail': 'Try again'}
                elif body['revision'] != revision(name):
                    status, data = 409, {'detail': 'textFile.conflict'}
                else:
                    files[name] = body['content']
                    saved.append((name, body['content']))
                    data = {'revision': revision(name)}
            elif state['fail_load'] or name == 'large.txt':
                status, data = 413, {'detail': 'textFile.tooLarge'}
            else:
                data = {'content': files[name], 'format': text_format(name), 'can_write': state['write'], 'revision': revision(name)}
        elif path.endswith('/files'):
            prefix = query.get('path', [''])[0]
            prefix = prefix + '/' if prefix else ''
            entries = {}
            for name in files:
                if not name.startswith(prefix):
                    continue
                suffix = name[len(prefix):]
                item_name = suffix.split('/')[0]
                kind = 'directory' if '/' in suffix else 'file'
                entries[item_name] = dict(name=item_name, type=kind, size=20,
                                          text_format=text_format(item_name) if kind == 'file' else None)
            data = {'items': list(entries.values()), 'can_write': state['write'], 'onlyoffice_configured': state['office']}
        elif path.endswith('/files/edit'):
            office_requests.append(route.request.url)
        route.fulfill(status=status, content_type='application/json', body=json.dumps(data))

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        context = browser.new_context(viewport={'width': 1440, 'height': 1000})
        context.route('http://tareas.test/**', serve)
        context.add_init_script("""document.addEventListener('securitypolicyviolation', event =>
            window.reportCsp({directive: event.effectiveDirective, blocked: event.blockedURI}));""")
        context.expose_binding('reportCsp', lambda _source, data: violations.append(data))
        context.on('page', lambda new_page: new_page.on('pageerror', lambda error: errors.append(str(error))))
        page = context.new_page()
        page.goto('http://tareas.test/')
        dialog = page.locator('.text-file-shell')

        def open_file(name, view='tree'):
            item = page.locator(f'.fb-{view}-' + ('row' if view == 'tree' else 'item') + f'[data-name="{name}"]')
            item.dblclick()
            expect(dialog.locator('.markdown-editor')).to_be_visible()

        def edit(value):
            dialog.locator('[data-md-action="edit"]').click()
            dialog.locator('textarea').fill(value)

        def close():
            dialog.locator('[data-file-close]').click()
            expect(dialog).to_have_count(0)

        # Verbindungsfehler sind lesbar, erneutes Laden und Unterordner funktionieren danach.
        translations = json.loads((ROOT / 'dashboard/static/i18n/de.json').read_text())
        for view, failure in (('tree', (503, 'files.webdavConnectionError')), ('grid', (504, 'files.webdavTimeout'))):
            state['transport_error'] = failure
            page.evaluate('view => { files.viewMode = view; return files.loadDirectory(""); }', view)
            expect(page.locator('.fb-error')).to_have_text(translations[failure[1]])
            state['transport_error'] = None
            page.evaluate('files.loadDirectory("")')
            expect(page.locator('.fb-error')).to_have_count(0)
        page.evaluate('files.viewMode = "tree"; files.renderTree()')
        state['transport_error'] = (503, 'files.webdavConnectionError')
        page.locator('[data-toggle-path="scripts"]').click()
        expect(page.locator('.notification-error')).to_have_text(translations['files.webdavConnectionError'])
        assert not page.evaluate('files.expandedDirs.has("scripts") || Object.hasOwn(files.treeCache, "scripts")')
        state['transport_error'] = None
        page.locator('[data-toggle-path="scripts"]').click()
        expect(page.locator('.fb-tree-row[data-itempath="scripts/run.PS1"]')).to_be_visible()
        page.locator('[data-toggle-path="scripts"]').click()

        state['transport_error'] = (503, 'files.webdavConnectionError')
        page.locator('.fb-tree-row[data-name="readme.md"]').dblclick()
        expect(dialog.locator('.text-file-status')).to_have_text(translations['files.webdavConnectionError'])
        close()
        state['transport_error'] = None

        # Markdown: Vorschau, sicheres HTML, explizites Bearbeiten, Speichern und Abbrechen.
        open_file('readme.md')
        expect(dialog.locator('h1').last).to_have_text('Vorschau')
        expect(dialog.locator('strong')).to_have_text('Fett')
        expect(dialog.locator('textarea')).to_be_hidden()
        expect(dialog.locator('.markdown-body')).to_contain_text('<script>window.injected = true</script>')
        assert page.evaluate('window.injected') is None
        expect(dialog.locator('.markdown-body a[href]')).to_have_count(0)
        page.mouse.click(1, 1)
        expect(dialog).to_be_visible()
        edit('# Neuer Stand')
        page.once('dialog', lambda confirmation: confirmation.dismiss())
        dialog.locator('[data-file-close]').click()
        expect(dialog.locator('textarea')).to_have_value('# Neuer Stand')
        state['fail_save'] = True
        dialog.locator('[data-md-action="save"]').click()
        expect(dialog.locator('.markdown-error')).to_have_text('Try again')
        expect(dialog.locator('textarea')).to_have_value('# Neuer Stand')
        state['fail_save'] = False
        state['transport_error'] = (504, 'files.webdavTimeout')
        dialog.locator('[data-md-action="save"]').click()
        expect(dialog.locator('.markdown-error')).to_have_text(translations['files.webdavTimeout'])
        expect(dialog.locator('textarea')).to_have_value('# Neuer Stand')
        state['transport_error'] = None
        dialog.locator('[data-md-action="save"]').click()
        expect(dialog.locator('.markdown-body h1')).to_have_text('Neuer Stand')
        assert saved[-1] == ('readme.md', '# Neuer Stand')
        edit('discard')
        dialog.locator('[data-md-action="cancel"]').click()
        expect(dialog.locator('.markdown-body h1')).to_have_text('Neuer Stand')
        close()

        # Text/Scripts bleiben literaler Code, auch in expandierten Unterordnern.
        for name in ('notes.txt', 'run.sh', 'run.PS1'):
            if name == 'run.PS1':
                page.locator('[data-toggle-path="scripts"]').click()
            open_file(name)
            key = 'scripts/' + name if name == 'run.PS1' else name
            expect(dialog.locator('.text-file-plain')).to_have_text(files[key])
            expect(dialog.locator('.markdown-body h1, .markdown-body b, .markdown-body tag')).to_have_count(0)
            original = files[key]
            edit(original + '# saved\n')
            dialog.locator('[data-md-action="save"]').click()
            expect(dialog.locator('textarea')).to_be_hidden()
            assert saved[-1] == (key, original + '# saved\n')
            close()
            if name == 'run.PS1':
                expect(page.locator('.fb-tree-row[data-itempath="scripts/run.PS1"]')).to_be_visible()

        # Beide Ansichten: native Aktionen mit und ohne Office, Kontext und Icon.
        for office in (False, True):
            state['office'] = office
            for view in ('tree', 'grid'):
                page.evaluate('view => { files.viewMode = view; return files.loadDirectory(""); }', view)
                item = page.locator(f'.fb-{view}-' + ('row' if view == 'tree' else 'item') + '[data-name="readme.md"]')
                item.click(button='right')
                page.locator('.fb-ctx-item[data-action="edit"]').click()
                expect(dialog.locator('.markdown-body h1')).to_have_text('Neuer Stand')
                close()
                if view == 'grid':
                    item.locator('.fb-grid-edit').click()
                    expect(dialog.locator('.markdown-body h1')).to_have_text('Neuer Stand')
                    close()
                open_file('readme.md', view)
                close()
        assert not office_requests, office_requests

        # Nur Lesen, Ladefehler und echte Konflikte behalten den Entwurf.
        state['write'] = False
        open_file('readme.md', 'grid')
        expect(dialog.locator('[data-md-action]')).to_have_count(0)
        close()
        state['write'] = True
        page.locator('.fb-grid-item[data-name="large.txt"]').dblclick()
        expect(dialog.locator('.text-file-status')).to_contain_text('2 MiB')
        expect(dialog.locator('[data-file-download]')).to_be_visible()
        close()
        open_file('readme.md', 'grid')
        edit('# Retained draft')
        files['readme.md'] = '# External change'
        dialog.locator('[data-md-action="save"]').click()
        expect(dialog.locator('.markdown-error')).to_contain_text('zwischenzeitlich geändert')
        expect(dialog.locator('textarea')).to_have_value('# Retained draft')

        # Pop-up blockiert: Dialog und Entwurf bleiben unveraendert erhalten.
        page.evaluate('window.realOpen = window.open; window.open = () => null;')
        dialog.locator('[data-file-expand]').click()
        expect(dialog.locator('.text-file-status')).to_contain_text('Pop-ups')
        expect(dialog.locator('textarea')).to_have_value('# Retained draft')
        page.evaluate('window.open = window.realOpen;')

        # Ein Ladefehler im neuen Fenster darf den urspruenglichen Dialog nicht schliessen.
        state['fail_load'] = True
        with context.expect_page():
            dialog.locator('[data-file-expand]').click()
        expect(dialog.locator('.text-file-status')).to_contain_text('2 MiB')
        expect(dialog.locator('[data-file-expand]')).to_be_enabled()
        expect(dialog.locator('textarea')).to_have_value('# Retained draft')
        state['fail_load'] = False

        # Ausklappen uebertraegt einen ungespeicherten Entwurf, Cursor und alte Revision.
        dialog.locator('textarea').evaluate('(input) => input.setSelectionRange(3, 8)')
        with context.expect_page() as opened:
            dialog.locator('[data-file-expand]').click()
        popup = opened.value
        expect(popup.locator('textarea')).to_have_value('# Retained draft')
        expect(dialog).to_have_count(0)
        assert popup.locator('textarea').evaluate('input => [input.selectionStart, input.selectionEnd]') == [3, 8]
        assert popup.url.endswith('/text-editor?taskId=1&path=readme.md')
        popup.locator('[data-md-action="save"]').click()
        expect(popup.locator('.markdown-error')).to_contain_text('zwischenzeitlich geändert')
        expect(popup.locator('textarea')).to_be_visible()
        expect(popup.locator('textarea')).to_have_value('# Retained draft')
        expect(popup.locator('[data-md-action="save"]')).to_be_enabled()
        # Auch ein erneuter Speicherversuch darf die veraltete Revision nicht verwerfen.
        popup.locator('[data-md-action="save"]').click()
        expect(popup.locator('.markdown-error')).to_contain_text('zwischenzeitlich geändert')
        expect(popup.locator('textarea')).to_have_value('# Retained draft')
        assert files['readme.md'] == '# External change'
        popup.locator('[data-md-action="cancel"]').click()
        popup.close()

        # Erfolgreicher Transfer/Speichern im separaten Fenster, auch schmal/dunkel.
        open_file('readme.md', 'grid')
        edit('# Fenster-Entwurf')
        with context.expect_page() as opened:
            dialog.locator('[data-file-expand]').click()
        popup = opened.value
        expect(popup.locator('textarea')).to_have_value('# Fenster-Entwurf')
        expect(dialog).to_have_count(0)
        popup.set_viewport_size({'width': 390, 'height': 700})
        popup.evaluate('document.documentElement.dataset.colorScheme = "dark"')
        assert popup.evaluate('document.documentElement.scrollWidth <= innerWidth')
        popup.locator('[data-md-action="save"]').click()
        expect(popup.locator('.markdown-body h1')).to_have_text('Fenster-Entwurf')
        assert saved[-1] == ('readme.md', '# Fenster-Entwurf')
        with popup.expect_event('close'):
            popup.locator('[data-file-close]').click()

        # Auch Rechteentzug waehrend des Transfers darf den Entwurf nicht verlieren.
        open_file('readme.md', 'grid')
        edit('# Keep after revocation')
        state['write'] = False
        with context.expect_page() as opened:
            dialog.locator('[data-file-expand]').click()
        popup = opened.value
        expect(popup.locator('textarea')).to_have_value('# Keep after revocation')
        expect(popup.locator('[data-md-action="save"]')).to_have_count(0)
        expect(popup.locator('.text-file-status')).to_contain_text('Schreibrechte wurden entzogen')
        expect(dialog).to_have_count(0)
        popup.once('dialog', lambda confirmation: confirmation.accept())
        with popup.expect_event('close'):
            popup.locator('[data-file-close]').click()
        state['write'] = True

        page.set_viewport_size({'width': 390, 'height': 700})
        open_file('readme.md', 'grid')
        assert dialog.evaluate('element => element.getBoundingClientRect().right <= innerWidth')
        dialog.locator('[data-md-action="edit"]').click()
        expect(dialog.locator('textarea')).to_be_visible()
        dialog.locator('[data-md-action="cancel"]').click()
        close()
        direct = context.new_page()
        direct.goto('http://tareas.test/text-editor?taskId=1&path=run.sh')
        expect(direct.locator('.text-file-plain')).to_have_text(files['run.sh'])
        direct.goto('http://tareas.test/text-editor')
        expect(direct.locator('main')).to_contain_text('Parameter')
        assert errors == [], errors
        assert violations == [], violations
        browser.close()
    print('Native text browser checks passed: Markdown/plain code, save/cancel/errors/permissions, tree/grid, draft transfer, CSP, popup blocking and narrow layouts.')


if __name__ == '__main__':
    main()
