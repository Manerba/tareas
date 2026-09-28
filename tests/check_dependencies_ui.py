"""Browserpruefung fuer Vorgaenger-Auswahl, Netzplan und Validierungsfehler.

Nutzt lokale Assets, gemockte APIs und die optionale Playwright-Installation.
"""

import asyncio
import json
import mimetypes
import sys
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dashboard.api_tasks import get_tasks_config


def main():
    config = asyncio.run(get_tasks_config())
    task = dict(id=1, name='Dependencies', task_type='projekt', created_by=1, assigned_to=None,
                description='', status='offen', priority=50, deadline='', can_edit_status=True)
    subtasks = [dict(id=index, name=name, project_id=1, position_number=index - 10,
                     description='', status_percent=0, priority=50, deadline='', predecessor_ids=[])
                for index, name in enumerate('ABCDE', 11)]
    error_message = 'Transitiv redundante Abhaengigkeit: #13 waere bereits indirekt von #14 abhaengig.'
    reject_changes = [True]

    def seed(edges):
        for st in subtasks:
            st['predecessor_ids'] = [parent for child, parent in edges if child == st['id']]

    seed([(12, 11), (13, 12)])

    def serve(route):
        path = urlparse(route.request.url).path
        if route.request.resource_type == 'document':
            route.fulfill(content_type='text/html', body=(ROOT / 'dashboard/templates/index.html').read_text())
            return
        if path.startswith('/static/'):
            file = ROOT / 'dashboard' / path.lstrip('/')
            route.fulfill(content_type=mimetypes.guess_type(file)[0] or 'application/octet-stream', body=file.read_bytes())
            return
        status, data = 200, {'items': []}
        if path == '/api/tasks/config':
            data = config
        elif path == '/api/tasks':
            data = {'items': [task]}
        elif path == '/api/auth/me':
            data = {'user': {'id': 1, 'is_admin': True, 'username': 'test'}}
        elif path == '/api/nextcloud/status':
            data = {'configured': False}
        elif path == '/api/tasks/1/subtasks':
            data = {'items': subtasks}
        elif (path.startswith('/api/subtasks/') and route.request.method == 'PUT') or path.endswith('-dependency'):
            if reject_changes[0]:
                status, data = 400, {'detail': error_message}
            elif path.endswith('-dependency'):
                body = route.request.post_data_json
                target = next(st for st in subtasks if st['id'] == body['to_id'])
                if path.endswith('/add-dependency'):
                    target['predecessor_ids'].append(body['from_id'])
                else:
                    target['predecessor_ids'].remove(body['from_id'])
        route.fulfill(status=status, content_type='application/json', body=json.dumps(data))

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={'width': 1440, 'height': 1100})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.route('http://tareas.test/**', serve)
        page.goto('http://tareas.test/aufgaben')
        page.locator('[data-row-id="1"] > .table-row > .expand-icon').click()
        expect(page.locator('.subtask-row')).to_have_count(5)

        def options(child):
            return page.locator(f'#predSelect_{child} option').evaluate_all('(options) => options.map(o => o.value)')

        def targets(parent):
            return page.evaluate('parent => [..._computeValidNetzplanTargets(parent, document.getElementById("subtaskContainer_1")._subtasksData)]', parent)

        def reload(edges):
            seed(edges)
            page.evaluate('async () => { await loadSubTasks(1); }')

        # A -> B -> C: no shortcut A -> C and no cycle C -> A.
        assert '11' not in options(13)
        assert '13' not in options(11)
        assert 13 not in targets(11)
        assert 11 not in targets(13)
        assert '14' in options(13)
        page.locator('tr[data-subtask-id="13"] .subtask-expand-icon').click()
        # A stale selection may still be refused by the server; show its reason.
        page.locator('#predSelect_13').select_option('14')
        expect(page.locator('.notification')).to_contain_text(error_message)
        expect(page.locator('#predChips_13 [data-pred-id="14"]')).to_have_count(0)

        # The shortcut exists first: completing either leg of the longer path is invalid.
        reload([(12, 11), (13, 11)])
        assert '12' not in options(13)
        assert 13 not in targets(12)
        reload([(13, 11), (13, 12)])
        assert '11' not in options(12)
        assert 12 not in targets(11)
        # A longer bridge would make another subtask's direct predecessor redundant.
        reload([(12, 11), (13, 12), (15, 11), (15, 14)])
        assert '13' not in options(14)
        assert 14 not in targets(13)
        # Independent prerequisites remain available (diamond graph).
        reload([(12, 11), (13, 11), (14, 12)])
        assert '13' in options(14)
        assert 14 in targets(13)

        # Exercise real network event handlers and rejected undo/redo requests.
        reject_changes[0] = False
        page.evaluate('openNetzplan(1)')
        page.evaluate("netzplanNetwork.emit('click', {nodes: [13], edges: []})")
        page.wait_for_function('_netzplanLinkState?.sourceId === 13')
        page.evaluate("netzplanNetwork.emit('click', {nodes: [14], edges: []})")
        expect(page.locator('#netzplanUndoBtn')).to_be_enabled()
        expect(page.locator('#netzplanRedoBtn')).to_be_disabled()
        reject_changes[0] = True
        page.locator('#netzplanUndoBtn').click()
        expect(page.locator('.notification')).to_contain_text(error_message)
        expect(page.locator('#netzplanUndoBtn')).to_be_enabled()
        expect(page.locator('#netzplanRedoBtn')).to_be_disabled()
        reject_changes[0] = False
        page.locator('#netzplanUndoBtn').click()
        expect(page.locator('#netzplanUndoBtn')).to_be_disabled()
        expect(page.locator('#netzplanRedoBtn')).to_be_enabled()
        reject_changes[0] = True
        page.locator('#netzplanRedoBtn').click()
        expect(page.locator('.notification')).to_contain_text(error_message)
        expect(page.locator('#netzplanUndoBtn')).to_be_disabled()
        expect(page.locator('#netzplanRedoBtn')).to_be_enabled()
        assert errors == [], errors
        browser.close()
    print('Dependency browser checks passed: dropdowns, cycles, shortcuts, new indirect paths, valid diamonds, server errors and network undo/redo.')


if __name__ == '__main__':
    main()
