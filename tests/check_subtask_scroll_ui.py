"""Browserpruefung: Teilaufgaben animieren, ohne die Zeile zu verschieben.

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
    project = dict(id=1, name='Scrollposition', task_type='projekt', created_by=1,
                   assigned_to=None, description='', status='offen', priority=50,
                   deadline='', can_edit_status=True)
    subtasks = [dict(id=index, name=f'Teilaufgabe {index}', project_id=1,
                     position_number=index - 100, status_percent=0, priority=50,
                     deadline='', predecessor_ids=[], description_format='markdown',
                     description='\n\n'.join(f'Absatz {line} der Beschreibung.' for line in range(30)))
                for index in range(101, 129)]
    subtasks[3]['name'] = 'Eine Teilaufgabe mit einem langen Namen, der sich bei schmaler Tabelle ueber mehrere Zeilen verteilt'

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
            data = {'items': [project, {**project, 'id': 2, 'name': 'Weiteres Projekt'}]}
        elif path == '/api/auth/me':
            data = {'user': {'id': 1, 'is_admin': True, 'username': 'test'}}
        elif path == '/api/nextcloud/status':
            data = {'configured': False}
        elif path == '/api/tasks/1/subtasks':
            data = {'items': subtasks}
        route.fulfill(content_type='application/json', body=json.dumps(data))

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for width, height in [(1440, 900), (800, 700)]:
            page = browser.new_page(viewport={'width': width, 'height': height})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.route('http://tareas.test/**', serve)
            page.goto('http://tareas.test/aufgaben')
            project_row = page.locator('[data-row-id="1"]')
            project_row.locator(':scope > .table-row > .expand-icon').click()
            expect(page.locator('.subtask-row')).to_have_count(len(subtasks))

            def settle():
                page.evaluate('''async () => {
                    await Promise.all([...document.querySelectorAll('.subtask-detail-reveal')]
                        .flatMap(el => el.getAnimations()).map(animation => animation.finished.catch(() => {})));
                    await new Promise(resolve => requestAnimationFrame(() =>
                        requestAnimationFrame(() => requestAnimationFrame(resolve))));
                }''')

            def row(subtask_id):
                return page.locator(f'tr[data-subtask-id="{subtask_id}"]')

            def expand(subtask_id):
                row(subtask_id).locator('.subtask-expand-icon').click()
                expect(page.locator(f'#stWysiwyg_{subtask_id} .markdown-body')).to_be_visible()
                page.wait_for_function('''id => {
                    const notes = document.getElementById(`stAllNotesContent_${id}`);
                    return notes._loaded && !notes.textContent.includes('Laden');
                }''', arg=subtask_id)
                settle()

            def collapse_at(subtask_id, top=180):
                # Position before clicking; mouse.click cannot auto-scroll like locator.click.
                row(subtask_id).evaluate('(el, top) => window.scrollBy(0, el.getBoundingClientRect().top - top)', top)
                settle()
                before = row(subtask_id).bounding_box()['y']
                initial_height = page.locator(f'tr[data-subtask-detail-id="{subtask_id}"]').bounding_box()['height']
                icon = row(subtask_id).locator('.subtask-expand-icon').bounding_box()
                page.evaluate('''id => {
                    const row = document.querySelector(`tr[data-subtask-id="${id}"]`);
                    const detail = document.querySelector(`tr[data-subtask-detail-id="${id}"]`);
                    window.collapseSamples = new Promise(resolve => {
                        const samples = [];
                        const sample = () => {
                            samples.push({top: row.getBoundingClientRect().top, height: detail.getBoundingClientRect().height});
                            if (!detail.classList.contains('visible') || samples.length >= 120) resolve(samples);
                            else requestAnimationFrame(sample);
                        };
                        requestAnimationFrame(sample);
                    });
                }''', subtask_id)
                page.mouse.click(icon['x'] + icon['width'] / 2, icon['y'] + icon['height'] / 2)
                expect(page.locator(f'tr[data-subtask-detail-id="{subtask_id}"]')).to_be_hidden()
                settle()
                after = row(subtask_id).bounding_box()['y']
                assert abs(after - before) < 1, (width, subtask_id, before, after)
                samples = page.evaluate('collapseSamples')
                assert any(2 < sample['height'] < initial_height - 2 for sample in samples), samples
                assert all(abs(sample['top'] - before) < 1 for sample in samples), (width, before, samples)

            # Opening grows through intermediate heights, including on the first lazy load.
            opening_heights = page.evaluate('''async () => {
                toggleSubTaskDetail(1, 101);
                const reveal = document.querySelector('[data-subtask-detail-id="101"] .subtask-detail-reveal');
                const heights = [reveal.getBoundingClientRect().height];
                let done = false;
                Promise.all(reveal.getAnimations().map(animation => animation.finished)).then(() => done = true);
                do {
                    await new Promise(requestAnimationFrame);
                    heights.push(reveal.getBoundingClientRect().height);
                } while (!done && heights.length < 120);
                return heights;
            }''')
            assert any(2 < value < opening_heights[-1] - 2 for value in opening_heights), opening_heights
            collapse_at(101)

            # Near the end, the viewport needs space below the project after collapse.
            expand(128)
            collapse_at(128)
            assert project_row.bounding_box()['y'] + project_row.bounding_box()['height'] < height - 100
            first_height = page.evaluate('document.documentElement.scrollHeight')
            for _ in range(3):
                expand(128)
                collapse_at(128)
                assert abs(page.evaluate('document.documentElement.scrollHeight') - first_height) < 2
            expand(128)
            collapse_at(128, top=height - 200)

            # Several open rows can be collapsed in succession without accumulating space.
            expand(127)
            expand(128)
            collapse_at(128)
            collapse_at(127)

            # Scrolling up releases the extra space; an earlier row needs no extra space.
            page.evaluate('window.scrollTo(0, 0)')
            settle()
            body = page.locator('#aufgabenTableContainer > .table-body')
            assert abs(body.bounding_box()['height'] - project_row.bounding_box()['height']) < 2
            expand(101)
            collapse_at(101)
            assert abs(body.bounding_box()['height'] - project_row.bounding_box()['height']) < 2

            # Fast repeated clicks reverse the current animation and keep the final intent.
            page.evaluate('''async () => {
                toggleSubTaskDetail(1, 128);
                await new Promise(resolve => setTimeout(resolve, 70));
                toggleSubTaskDetail(1, 128);
                await new Promise(resolve => setTimeout(resolve, 40));
                toggleSubTaskDetail(1, 128);
            }''')
            settle()
            expect(row(128)).to_have_class('subtask-row expanded')
            expect(page.locator('[data-subtask-detail-id="128"]')).to_be_visible()
            collapse_at(128)

            # Rebuilding data preserves open rows directly; reduced motion disables transitions.
            expand(128)
            page.evaluate('loadSubTasks(1)')
            expect(row(128)).to_have_class('subtask-row expanded')
            assert page.locator('.subtask-detail-reveal').evaluate_all('(items) => items.flatMap(el => el.getAnimations()).length') == 0
            page.emulate_media(reduced_motion='reduce')
            page.evaluate('toggleSubTaskDetail(1, 128)')
            expect(page.locator('[data-subtask-detail-id="128"]')).to_be_hidden()
            page.evaluate('toggleSubTaskDetail(1, 128)')
            expect(page.locator('[data-subtask-detail-id="128"]')).to_be_visible()
            assert page.locator('.subtask-detail-reveal').evaluate_all('(items) => items.flatMap(el => el.getAnimations()).length') == 0
            page.evaluate('toggleSubTaskDetail(1, 128)')
            page.emulate_media(reduced_motion='no-preference')

            # Leaving the project during a subtask animation must not restore stale positions.
            expand(128)
            page.evaluate('''() => {
                toggleSubTaskDetail(1, 128);
                aufgabenTable.toggleRow('1');
                window.scrollTo(0, 0);
            }''')
            settle()
            assert page.evaluate('window.scrollY') == 0
            assert page.evaluate('document.documentElement.scrollHeight') == height
            project_row.locator(':scope > .table-row > .expand-icon').click()
            expect(page.locator('.subtask-row')).to_have_count(len(subtasks))

            # Closing the project and navigating home both restore the ordinary list height.
            expand(128)
            collapse_at(128)
            page.evaluate('aufgabenTable.toggleRow("1")')
            settle()
            assert page.evaluate('document.documentElement.scrollHeight') == height
            project_row.locator(':scope > .table-row > .expand-icon').click()
            expect(page.locator('.subtask-row')).to_have_count(len(subtasks))
            settle()
            assert abs(body.bounding_box()['height'] - project_row.bounding_box()['height']) < 2
            expand(128)
            collapse_at(128)
            page.locator('.header-home').click()
            expect(page.locator('.project-focus.expanded')).to_have_count(0)
            settle()
            assert page.evaluate('document.documentElement.scrollHeight') == height
            assert errors == [], errors
            page.close()
        browser.close()
    print('Subtask animation/scroll checks passed: intermediate heights, stable rows throughout collapse, fast reversals, reduced motion, reloads and project/list navigation at two viewport sizes.')


if __name__ == '__main__':
    main()
