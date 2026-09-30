"""Handoff-Grid, gemeinsame Resize-Griffe und Dialog/Fenster mit echten Assets."""

import asyncio
import hashlib
import json
import logging
import mimetypes
import re
import sys
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlparse

from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dashboard.api_tasks import get_tasks_config

with patch('logging.handlers.RotatingFileHandler', return_value=logging.NullHandler()):
    from dashboard.app import app


def main():
    config = asyncio.run(get_tasks_config())
    project = dict(id=1, name='Handoff-Layout', task_type='projekt', created_by=1,
                   description=('## Projektbeschreibung\n\nEin Absatz mit **Markdown** und `Code`.\n\n' * 40),
                   description_format='markdown', status='offen', priority=50, deadline='',
                   assigned_to=None, file_storage_type='none', can_edit_status=True)
    subtasks = [dict(id=index, project_id=1, name=f'Teilaufgabe {index}', position_number=index-1,
                     created_by=1, assigned_to=None, status_percent=0, deadline='', priority=50,
                     description='## Beschreibung\n\n' + 'Ein Absatz.\n\n' * 60,
                     description_format='markdown', predecessor_ids=[])
                for index in (2, 3)]
    entries = [dict(id=index, user_id=7, user_name='Claude @ Kiara', created_at='29.09.2026 10:23',
                    content=f'## Ergebnis {index}\n\nDie ersten Wörter der Notiz bleiben sichtbar. ' + 'Weiterer Text. ' * 20,
                    content_format='markdown') for index in range(101, 113)]
    entries[1]['user_name'] = 'Agent mit einem sehr langen Namen <b>literal</b>'
    entries[1]['content'] = '<script>window.injected = true</script>\n\n**Sicherer Text**'
    project_entries = [dict(entry, content=f'## Projekt-Handoff {entry["id"]}\n\nEigene Projektnotiz mit mehr Text als einer Zeile. ' * 4) for entry in entries]
    state = {'admin': True, 'fail_save': False}
    errors, violations, saves = [], [], []
    client = TestClient(app)  # Kein Lifespan/keine produktiven Migrationen.
    with patch('dashboard.app._extract_user_from_request', return_value={'id': 1}):
        csp = client.get('/handoff').headers['content-security-policy']
    client.close()

    def revision(entry):
        return hashlib.sha256((entry['content_format'] + '\0' + entry['content']).encode()).hexdigest()

    def serve(route):
        path = urlparse(route.request.url).path
        if path in ('/', '/aufgaben', '/handoff'):
            file = ROOT / 'dashboard/templates' / ('text_editor.html' if path == '/handoff' else 'index.html')
            route.fulfill(content_type='text/html', body=file.read_text(),
                          headers={'Content-Security-Policy': csp} if path == '/handoff' else {})
            return
        if path.startswith('/static/'):
            file = ROOT / 'dashboard' / path.lstrip('/')
            route.fulfill(content_type=mimetypes.guess_type(file)[0] or 'application/octet-stream', body=file.read_bytes())
            return
        data, status = {'items': []}, 200
        match = re.fullmatch(r'/api/tasks/1(?:/subtasks/(\d+))?/note-entries/(\d+)', path)
        if match:
            scoped_entries = entries if match[1] else project_entries
            entry = next((entry for entry in scoped_entries if entry['id'] == int(match[2])), None)
            if not entry or match[1] not in (None, '2'):
                status, data = 404, {'detail': 'handoff.notFound'}
            elif route.request.method == 'PUT':
                assert route.request.headers['x-requested-with'] == 'XMLHttpRequest'
                payload = route.request.post_data_json
                if not state['admin']:
                    status, data = 403, {'detail': 'Read only'}
                elif state['fail_save']:
                    status, data = 503, {'detail': 'Save failed'}
                elif payload['revision'] != revision(entry):
                    status, data = 409, {'detail': 'handoff.conflict'}
                else:
                    entry.update(content=payload['content'], content_format='markdown')
                    saves.append((path, payload))
                    data = {'revision': revision(entry)}
            else:
                data = dict(entry, format=entry['content_format'], can_write=state['admin'], revision=revision(entry))
        elif path == '/api/tasks/config':
            data = config
        elif path == '/api/tasks':
            data = {'items': [project]}
        elif path == '/api/auth/me':
            data = {'user': {'id': 1, 'username': 'test', 'is_admin': state['admin']}}
        elif path == '/api/nextcloud/status':
            data = {'configured': False}
        elif path == '/api/tasks/1/subtasks':
            data = {'items': subtasks}
        elif path == '/api/tasks/1/subtasks/2/note-entries':
            data = {'items': entries}
        elif path == '/api/tasks/1/note-entries':
            data = {'items': project_entries}
        elif route.request.method == 'PUT':
            payload = route.request.post_data_json
            if path == '/api/tasks/1':
                project.update(payload)
            data = payload
        route.fulfill(status=status, content_type='application/json', body=json.dumps(data))

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        context = browser.new_context(viewport={'width': 1920, 'height': 1400})
        context.route('http://tareas.test/**', serve)
        context.expose_binding('reportCsp', lambda _source, data: violations.append(data))
        context.add_init_script("""document.addEventListener('securitypolicyviolation', event =>
            window.reportCsp({directive: event.effectiveDirective, blocked: event.blockedURI}));""")
        context.on('page', lambda page: page.on('pageerror', lambda error: errors.append(str(error))))
        page = context.new_page()
        page.goto('http://tareas.test/aufgaben')
        page.locator('[data-row-id="1"] > .table-row > .expand-icon').click()
        expect(page.locator('.subtask-row')).to_have_count(2)

        def settle():
            page.evaluate('''async () => {
                await Promise.all(document.getAnimations().map(animation => animation.finished.catch(() => {})));
                await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            }''')

        def dimensions(locator):
            rect = locator.bounding_box()
            return [rect['width'], rect['height']]

        def equal_size(before, after):
            assert all(abs(a-b) < 1 for a, b in zip(before, after)), (before, after)

        def drag(selector, dx=0, dy=0):
            handle = page.locator(selector)
            handle.scroll_into_view_if_needed()
            rect = handle.bounding_box()
            x, y = rect['x'] + rect['width']/2, rect['y'] + rect['height']/2
            page.mouse.move(x, y)
            page.mouse.down()
            page.mouse.move(x+dx, y+dy, steps=8)
            page.mouse.up()

        # Auch die nutzbare Textflaeche bleibt beim Moduswechsel gleich gross.
        for storage in ('none', 'local'):
            page.evaluate('''async storage => {
                const row = aufgabenTable.filteredData[0]; row.file_storage_type = storage;
                await onTaskRowExpanded(1, document.querySelector('.table-row-detail'));
            }''', storage)
            for width in (1920, 800, 390):
                page.set_viewport_size({'width': width, 'height': 1400})
                editor = page.locator('#wysiwygEditor_1')
                before, content = dimensions(editor), dimensions(editor.locator('[data-md-preview]'))
                editor.locator('[data-md-action="edit"]').click()
                equal_size(before, dimensions(editor))
                equal_size(content, dimensions(editor.locator('textarea')))
                editor.locator('[data-md-action="cancel"]').click()
                equal_size(before, dimensions(editor))
            page.set_viewport_size({'width': 1920, 'height': 1400})

        page.locator('.subtask-row[data-subtask-id="2"] .subtask-expand-icon').click()
        grid = page.locator('#stNoteEntriesContent_2')
        expect(grid.locator('.handoff-widget')).to_have_count(12)
        settle()
        left = page.locator('#stColumns_2 .project-detail-left')
        right = page.locator('#stColumns_2 .project-detail-right')
        assert right.bounding_box()['x'] > left.bounding_box()['x'] + left.bounding_box()['width']
        columns = lambda: grid.evaluate("el => getComputedStyle(el).gridTemplateColumns.split(' ').length")
        initial_columns = columns()
        assert initial_columns >= 2, initial_columns
        for widget in grid.locator('.handoff-widget').all():
            assert widget.bounding_box()['width'] >= 280
            expect(widget.locator('.handoff-timestamp')).to_have_text('29.09.2026 10:23')
        expect(grid.locator('.handoff-widget').first).to_contain_text('#101 Claude @ Kiara')
        expect(grid.locator('.note-entry-preview').first).to_contain_text('Die ersten Wörter')
        assert grid.evaluate('el => el.scrollWidth <= el.clientWidth')
        page.locator('#stSplit_2').focus()
        page.keyboard.press('Home')
        assert columns() > initial_columns
        page.screenshot(path='/tmp/tareas-handoff-grid.png', full_page=True)
        before = dimensions(page.locator('#stColumns_2'))
        drag('#stResize_2', dy=100)
        assert abs(dimensions(page.locator('#stColumns_2'))[1] - before[1] - 100) < 1
        equal_size([0, dimensions(left)[1]], [0, dimensions(right)[1]])
        before_width = dimensions(left)[0]
        drag('#stSplit_2', dx=70)
        assert abs(dimensions(left)[0] - before_width - 70) < 2
        saved_size = dimensions(left)
        subeditor = page.locator('#stWysiwyg_2')
        before = dimensions(subeditor)
        subeditor.locator('[data-md-action="edit"]').click()
        equal_size(before, dimensions(subeditor))
        subeditor.locator('[data-md-action="cancel"]').click()
        for _ in range(2):
            page.locator('.subtask-row[data-subtask-id="2"] .subtask-expand-icon').click()
            settle()
        equal_size(saved_size, dimensions(left))
        page.evaluate('loadSubTasks(1)')
        expect(grid.locator('.handoff-widget')).to_have_count(12)
        settle()
        equal_size(saved_size, dimensions(left))

        # Einzelklick -> Dialog; kein Inline-Aufklappen. Admin kann speichern.
        dialog = page.locator('.text-file-shell')
        grid.locator('[data-handoff-id="101"]').click()
        expect(dialog.locator('.markdown-body h2')).to_have_text('Ergebnis 101')
        expect(dialog.locator('.text-file-title')).to_have_text('#101 Claude @ Kiara')
        expect(dialog.locator('.text-file-meta')).to_have_text('29.09.2026 10:23')
        expect(dialog.locator('[data-file-download]')).to_have_count(0)
        page.mouse.click(1, 1)
        expect(dialog).to_be_visible()
        dialog.locator('[data-md-action="edit"]').click()
        dialog.locator('textarea').fill('## Korrigiert')
        state['fail_save'] = True
        dialog.locator('[data-md-action="save"]').click()
        expect(dialog.locator('.markdown-error')).to_have_text('Save failed')
        state['fail_save'] = False
        dialog.locator('[data-md-action="save"]').click()
        expect(dialog.locator('textarea')).to_be_hidden()
        expect(grid.locator('[data-handoff-id="101"] .note-entry-preview')).to_have_text('Korrigiert')

        # Ausklappen: Entwurf und Cursor uebernehmen, erst danach Dialog schliessen.
        dialog.locator('[data-md-action="edit"]').click()
        dialog.locator('textarea').fill('## Fensterentwurf')
        dialog.locator('textarea').evaluate('el => el.setSelectionRange(3, 8)')
        with context.expect_page() as opened:
            dialog.locator('[data-file-expand]').click()
        popup = opened.value
        expect(popup.locator('textarea')).to_have_value('## Fensterentwurf')
        expect(dialog).to_have_count(0)
        assert popup.locator('textarea').evaluate('el => [el.selectionStart, el.selectionEnd]') == [3, 8]
        popup.locator('[data-md-action="save"]').click()
        expect(popup.locator('.markdown-body h2')).to_have_text('Fensterentwurf')
        expect(grid.locator('[data-handoff-id="101"] .note-entry-preview')).to_have_text('Fensterentwurf')
        popup.close()

        # Doppelklick oeffnet direkt im Fenster; auch kein kurz aufblitzender Dialog.
        page.evaluate('''() => {
            window.overlayCount = 0;
            new MutationObserver(records => records.forEach(record => record.addedNodes.forEach(node => {
                if (node.classList?.contains('text-file-overlay')) window.overlayCount++;
            }))).observe(document.body, {childList: true});
        }''')
        with context.expect_page() as opened:
            grid.locator('[data-handoff-id="102"]').dblclick(delay=100)
        popup = opened.value
        expect(popup.locator('.markdown-body')).to_contain_text('<script>window.injected = true</script>')
        assert popup.evaluate('window.injected') is None
        assert page.evaluate('overlayCount') == 0
        popup.close()

        # Tastatur und Leserechte, auch bei direktem Fensteraufruf.
        state['admin'] = False
        grid.locator('[data-handoff-id="101"]').focus()
        page.keyboard.press('Enter')
        expect(dialog.locator('.markdown-body h2')).to_have_text('Fensterentwurf')
        expect(dialog.locator('[data-md-action]')).to_have_count(0)
        dialog.locator('[data-file-close]').click()
        with context.expect_page() as opened:
            grid.locator('[data-handoff-id="101"]').dblclick()
        popup = opened.value
        expect(popup.locator('.markdown-body h2')).to_have_text('Fensterentwurf')
        expect(popup.locator('[data-md-action]')).to_have_count(0)
        popup.close()
        state['admin'] = True

        # Schmale Ansicht stapelt die Bereiche, das Grid bleibt innerhalb seines Panels.
        page.set_viewport_size({'width': 800, 'height': 1000})
        expect(page.locator('#stSplit_2')).to_be_hidden()
        assert right.bounding_box()['y'] > left.bounding_box()['y']
        assert grid.evaluate('el => el.scrollWidth <= el.clientWidth')
        before = dimensions(page.locator('#stColumns_2'))
        drag('#stResize_2', dy=50)
        assert abs(dimensions(page.locator('#stColumns_2'))[1] - before[1] - 50) < 1
        page.set_viewport_size({'width': 390, 'height': 1000})
        assert columns() == 1
        assert grid.evaluate('el => el.scrollWidth <= el.clientWidth')
        page.evaluate('document.documentElement.dataset.colorScheme = "dark"')
        page.screenshot(path='/tmp/tareas-handoff-mobile.png', full_page=True)

        # Eigenstaendige Ansicht einer zugewiesenen Teilaufgabe nutzt dasselbe Layout.
        page.set_viewport_size({'width': 1440, 'height': 1100})
        page.evaluate('''async row => {
            const container = document.createElement('div'); container.id = 'assignedTest'; document.body.append(container);
            await onSubtaskViewExpanded({...row, id:-2, _subtask_id:2, _project_id:1,
                _project_name:'Projekt', permissions:{can_edit:true}}, container);
        }''', subtasks[0])
        expect(page.locator('#stViewNoteEntries_2 .handoff-widget')).to_have_count(12)
        expect(page.locator('#stViewSplit_2')).to_be_visible()

        # Projekt-Handoffs wachsen ohne Resize-Rahmen mit den Zeilen des Grids.
        project_grid = page.locator('#taskNoteEntries_1')
        expect(project_grid.locator('.handoff-widget')).to_have_count(12)
        expect(project_grid.locator('..').locator('.project-detail-resize')).to_have_count(0)
        assert project_grid.evaluate('el => !el.closest(".project-detail-columns, .subtask-handoff-panel")')
        initial_height = dimensions(project_grid)[1]
        assert project_grid.evaluate("el => getComputedStyle(el).gridTemplateColumns.split(' ').length") >= 2
        page.locator('#taskNoteEntries_1').screenshot(path='/tmp/tareas-project-handoffs.png')
        page.set_viewport_size({'width': 390, 'height': 1000})
        assert project_grid.evaluate("el => getComputedStyle(el).gridTemplateColumns.split(' ').length") == 1
        assert dimensions(project_grid)[1] > initial_height
        assert project_grid.evaluate('el => el.scrollHeight <= el.clientHeight && el.scrollWidth <= el.clientWidth')
        last = project_grid.locator('.handoff-widget').last.bounding_box()
        bounds = project_grid.bounding_box()
        assert last['y'] + last['height'] <= bounds['y'] + bounds['height'] + 1
        page.set_viewport_size({'width': 1440, 'height': 1100})

        # Dieselbe ID im Projekt und der Teilaufgabe bezeichnet verschiedene Handoffs.
        project_grid.locator('[data-handoff-id="101"]').click()
        expect(dialog.locator('.markdown-body h2').first).to_have_text('Projekt-Handoff 101')
        dialog.locator('[data-md-action="edit"]').click()
        dialog.locator('textarea').fill('## Projekt korrigiert')
        dialog.locator('[data-md-action="save"]').click()
        expect(project_grid.locator('[data-handoff-id="101"] .note-entry-preview')).to_have_text('Projekt korrigiert')
        expect(grid.locator('[data-handoff-id="101"] .note-entry-preview')).to_have_text('Fensterentwurf')
        assert saves[-1][0] == '/api/tasks/1/note-entries/101'
        dialog.locator('[data-md-action="edit"]').click()
        dialog.locator('textarea').fill('## Projekt im Fenster')
        with context.expect_page() as opened:
            dialog.locator('[data-file-expand]').click()
        popup = opened.value
        expect(popup.locator('textarea')).to_have_value('## Projekt im Fenster')
        expect(dialog).to_have_count(0)
        assert 'subtaskId' not in popup.url
        popup.locator('[data-md-action="save"]').click()
        expect(project_grid.locator('[data-handoff-id="101"] .note-entry-preview')).to_have_text('Projekt im Fenster')
        expect(grid.locator('[data-handoff-id="101"] .note-entry-preview')).to_have_text('Fensterentwurf')
        expect(page.locator('#stViewNoteEntries_2 [data-handoff-id="101"] .note-entry-preview')).to_have_text('Fensterentwurf')
        popup.close()

        state['admin'] = False
        page.evaluate('window.overlayCount = 0')
        with context.expect_page() as opened:
            project_grid.locator('[data-handoff-id="101"]').dblclick()
        popup = opened.value
        expect(popup.locator('.markdown-body h2')).to_have_text('Projekt im Fenster')
        expect(popup.locator('[data-md-action]')).to_have_count(0)
        assert page.evaluate('overlayCount') == 0
        assert 'subtaskId' not in popup.url
        popup.close()
        project_grid.locator('[data-handoff-id="101"]').click()
        expect(dialog.locator('.markdown-body h2')).to_have_text('Projekt im Fenster')
        expect(dialog.locator('[data-md-action]')).to_have_count(0)
        dialog.locator('[data-file-close]').click()
        project_entries.clear()
        page.evaluate('loadTaskNoteEntries(1)')
        expect(project_grid.locator('.handoff-widget')).to_have_count(0)
        expect(project_grid.locator('em')).to_be_visible()
        assert errors == [], errors
        assert violations == [], violations
        browser.close()
    print('Handoff browser checks passed: project grid with natural height, scoped project/subtask dialogs and windows, responsive grids, resize/state retention, stable Markdown dimensions, single/double click, popup drafts/saves, permissions, CSP and assigned-subtask view.')


if __name__ == '__main__':
    main()
