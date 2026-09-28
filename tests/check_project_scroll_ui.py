"""Browserpruefung: Fester Tabellenkopf und sichtbare Projektzeilen beim Zuklappen.

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
    projects = [dict(id=i, name=f'Projekt {i:02d}', task_type='projekt', created_by=1,
                     assigned_to=None, description='Projektbeschreibung', description_format='markdown',
                     status='offen', priority=50, deadline='', can_edit_status=True, _category='mcp')
                for i in range(1, 51)]
    subtasks = [dict(id=100+i, name=f'Teilaufgabe {i}', position_number=i,
                    description='', status_percent=0, priority=50, deadline='', predecessor_ids=[])
               for i in range(1, 16)]

    def serve(route):
        path = urlparse(route.request.url).path
        if route.request.resource_type == 'document':
            route.fulfill(content_type='text/html', body=(ROOT / 'dashboard/templates/index.html').read_text())
            return
        if path.startswith('/static/'):
            file = ROOT / 'dashboard' / path.lstrip('/')
            route.fulfill(content_type=mimetypes.guess_type(file)[0] or 'application/octet-stream', body=file.read_bytes())
            return
        data = {'items': []}
        if path == '/api/tasks/config':
            data = config
        elif path == '/api/tasks':
            data = {'items': projects}
        elif path == '/api/auth/me':
            data = {'user': {'id': 1, 'is_admin': True, 'username': 'test'}}
        elif path == '/api/nextcloud/status':
            data = {'configured': False}
        elif path.startswith('/api/tasks/') and path.endswith('/subtasks'):
            project_id = int(path.split('/')[3])
            data = {'items': [{**subtask, 'project_id': project_id} for subtask in subtasks]}
        route.fulfill(content_type='application/json', body=json.dumps(data))

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for width, height in ((1440, 900), (640, 700)):
            page = browser.new_page(viewport={'width': width, 'height': height})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.route('http://tareas.test/**', serve)
            page.goto('http://tareas.test/aufgaben')
            expect(page.locator('.table-row-wrapper')).to_have_count(len(projects))

            def settle():
                page.evaluate('''() => new Promise(resolve => requestAnimationFrame(() =>
                    requestAnimationFrame(() => requestAnimationFrame(resolve))))''')

            def check_sticky_header():
                samples = []
                for scroll_y in (0, 5, 15, 60, 400, 100000, 15, 5, 0):
                    page.evaluate('y => window.scrollTo({top: y, behavior: "instant"})', scroll_y)
                    settle()
                    samples.append(page.evaluate('''() => {
                        const header = document.querySelector('#aufgabenTableContainer > .table-header').getBoundingClientRect();
                        const filter = document.getElementById('filterBar').getBoundingClientRect();
                        return {top: header.top, filterBottom: filter.bottom, scrollY};
                    }'''))
                assert max(sample['scrollY'] for sample in samples) > 400, (width, samples)
                assert max(sample['top'] for sample in samples) - min(sample['top'] for sample in samples) < 1, (width, samples)
                assert all(abs(sample['top'] - sample['filterBottom']) < 1 for sample in samples), (width, samples)

            check_sticky_header()

            def check_collapse(task_id):
                icon = page.locator(f'[data-row-id="{task_id}"] > .table-row > .expand-icon')
                icon.click()
                expect(page.locator(f'#subtaskContainer_{task_id} .subtask-row')).to_have_count(len(subtasks))
                expect(page.locator('#filterBar')).to_be_hidden()
                page.evaluate('window.scrollTo(0, 0)')
                settle()
                box = icon.bounding_box()
                # A physical click cannot auto-scroll the row after the collapse.
                page.mouse.click(box['x'] + box['width']/2, box['y'] + box['height']/2)
                expect(page.locator('#filterBar')).to_be_visible()
                settle()
                metrics = page.evaluate('''id => {
                    const wrapper = document.querySelector(`[data-row-id="${id}"]`);
                    const row = wrapper.querySelector(':scope > .table-row').getBoundingClientRect();
                    const headers = ['.main-app > .header', '#filterBar', '#aufgabenTableContainer > .table-header']
                        .map(selector => document.querySelector(selector).getBoundingClientRect());
                    return {top: row.top, bottom: row.bottom, visibleTop: Math.max(...headers.map(rect => rect.bottom)),
                        filterHeight: headers[1].height, scrollY, maxScroll: document.documentElement.scrollHeight - innerHeight};
                }''', str(task_id))
                context = (width, task_id, metrics)
                assert metrics['top'] >= metrics['visibleTop'] + 3, context
                assert metrics['bottom'] <= height, context
                if metrics['scrollY'] < metrics['maxScroll'] - 1:
                    assert metrics['top'] <= metrics['visibleTop'] + 6, context
                return metrics

            # Top, middle and last project, including repeated return from the focused view.
            for task_id in (50, 25, 1):
                for _ in range(2):
                    metrics = check_collapse(task_id)
            if width == 640:
                assert metrics['filterHeight'] > 51, metrics  # Wrapped filters need a larger offset.

            # Sorting and filtering still apply when the full list returns.
            page.evaluate("() => { aufgabenTable.setFilter('typeFilter', 'projekt'); aufgabenTable.sortBy('id'); }")
            check_collapse(25)
            assert page.locator('.table-row-wrapper').evaluate_all('(rows) => rows.map(row => Number(row.dataset.rowId))') == list(range(1, 51))
            assert page.evaluate("aufgabenTable.filterValues.typeFilter") == 'projekt'
            check_sticky_header()

            # Re-measure wrapped filters when the viewport changes while scrolled down.
            page.evaluate('window.scrollTo(0, 400)')
            page.set_viewport_size({'width': 640 if width == 1440 else 1440, 'height': height})
            check_sticky_header()
            page.set_viewport_size({'width': width, 'height': height})

            # At a short list's natural end the browser keeps the whole project row visible.
            page.evaluate("applySearchFilter('Projekt 25')")
            expect(page.locator('.table-row-wrapper')).to_have_count(1)
            check_collapse(25)
            assert errors == [], errors
            page.close()
        browser.close()
    print('Project scroll checks passed: stationary header from the first scroll step, viewport resizing, first/middle/last rows, repeated collapse, wrapped filters, sorting, filtering and short lists at two viewport sizes.')


if __name__ == '__main__':
    main()
