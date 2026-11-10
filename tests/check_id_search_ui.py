"""ID search in the real UI, with isolated SQLite and real REST permissions."""

import logging
import sys
import tempfile
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dashboard import auth, database


def main():
    with tempfile.TemporaryDirectory(prefix='tareas-id-search-browser-') as tmp, \
            patch.object(database, 'DB_DIR', Path(tmp)), \
            patch.object(database, 'DB_PATH', Path(tmp) / 'test.db'), \
            patch.object(auth, 'SECRET_KEY_PATH', Path(tmp) / 'secret.key'), \
            patch('logging.handlers.RotatingFileHandler', return_value=logging.NullHandler()):
        from dashboard.app import app
        database.init_db()
        with closing(database.get_db()) as db, db:
            db.execute("INSERT INTO users (id, username, password_hash) VALUES (2, 'assigned', 'unused')")
            for entity_id in (11, 12, 13, 14, 111):
                db.execute("INSERT INTO tasks (id, name, task_type, created_by, description) VALUES (?, 'Content #11', ?, 1, 'Content #11')",
                           (entity_id, 'aufgabe' if entity_id == 11 else 'projekt'))
            db.execute("INSERT INTO sub_tasks (id, project_id, name, description, position_number, assigned_to) VALUES (11, 13, 'Direct subtask', '', 1, 2), (12, 14, 'Content #11', '#11', 11, 2)")
            db.execute("INSERT INTO task_note_entries (id, task_id, user_id, content) VALUES (11, 14, 1, 'Project handoff'), (12, 12, 1, 'Content #11')")
            db.execute("INSERT INTO sub_task_note_entries (id, sub_task_id, user_id, content) VALUES (11, 12, 1, 'Subtask handoff')")
            db.execute("INSERT INTO task_notes (id, task_id, user_id, content) VALUES (11, 12, 1, '#11')")
        client = TestClient(app)
        try:
            run(client)
        finally:
            client.close()


def run(client):
    errors, queries = [], []
    delayed = {'query': None, 'response': None}
    failure = {'next': False}

    def login(user_id):
        client.cookies.set(auth.COOKIE_NAME, auth.sign_token(auth.create_session(user_id)))

    def serve(route):
        request = route.request
        url = urlparse(request.url)
        path = url.path + ('?' + url.query if url.query else '')
        response = client.request(request.method, path, follow_redirects=False,
                                  headers={'X-Requested-With': request.headers.get('x-requested-with', '')},
                                  json=request.post_data_json if request.post_data else None)
        if url.path == '/api/tasks/id-search':
            query = parse_qs(url.query)['query'][0]
            queries.append(query)
            if failure['next']:
                failure['next'] = False
                route.fulfill(status=503, json={'detail': 'Temporary test failure'})
                return
            if query == delayed['query']:
                delayed.update(query=None, response=(route, response))
                return
        route.fulfill(status=response.status_code, headers=dict(response.headers), body=response.content)

    login(1)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        context = browser.new_context(viewport={'width': 1600, 'height': 1000})
        context.route('http://tareas.test/**', serve)
        context.on('page', lambda page: page.on('pageerror', lambda error: errors.append(str(error))))
        page = context.new_page()
        page.goto('http://tareas.test/aufgaben')
        search = page.locator('#searchFilterInput')

        def expect_rows(ids):
            page.wait_for_function('(ids) => JSON.stringify(aufgabenTable.filteredData.map(row => row.id).sort((a,b) => a-b)) === JSON.stringify(ids)', arg=sorted(ids))
            expect(page.locator('#aufgabenTableContainer > .table-body > .table-row-wrapper')).to_have_count(len(ids))

        def search_for(query, ids):
            search.fill(query)
            expect_rows(ids)
            expect(page.locator('#aufgabenTableContainer')).to_have_attribute('aria-busy', 'false')

        # Without #, substring matching in names/descriptions retains its behavior.
        search_for('11', [11, 12, 13, 14, 111])
        assert queries == []
        search_for('#11', [11, 13, 14])
        expect(page.locator('.task-id-search-link')).to_have_count(4)
        expect(page.locator('[data-row-id="14"] .task-id-search-matches')).to_contain_text('Teilaufgabe #12')
        for query in ('#', '#11x', '#0', '#1', '#1111', '#11 OR 1=1'):
            search_for(query, [])
        search_for('  #0011  ', [11, 13, 14])
        search_for('#11', [11, 13, 14])

        # Existing type/status filters still intersect with the ID results.
        page.locator('#typeFilterSelect').select_option('aufgabe')
        expect_rows([11])
        page.locator('#statusFilterSelect').select_option('erledigt')
        expect_rows([])
        page.locator('#statusFilterSelect').select_option('')
        page.locator('#typeFilterSelect').select_option('projekt')
        expect_rows([13, 14])
        page.locator('#typeFilterSelect').select_option('')
        expect_rows([11, 13, 14])

        # Each hit is navigable; equal handoff IDs keep their task/subtask scope.
        page.get_by_role('button', name='Aufgabe #11', exact=True).click()
        expect(page.locator('#wysiwygEditor_11')).to_be_visible()
        page.locator('[data-row-id="11"] > .table-row > .expand-icon').click()
        page.get_by_role('button', name='Teilaufgabe #11', exact=True).click()
        expect(page.locator('[data-subtask-detail-id="11"]')).to_be_visible()
        page.locator('[data-row-id="13"] > .table-row > .expand-icon').click()
        expect(search).to_have_value('#11')
        for label, content in (('Handoff #11', 'Project handoff'), ('Handoff #11 · Teilaufgabe #12', 'Subtask handoff')):
            page.get_by_role('button', name=label, exact=True).click()
            expect(page.locator('.text-file-overlay .markdown-body')).to_have_text(content)
            page.locator('.text-file-overlay [data-file-close]').click()
        search_for('Content', [11, 12, 13, 14, 111])
        expect(page.locator('.task-id-search-link')).to_have_count(0)

        # Simulate a transport that cannot cancel an old request. Its late reply
        # must not override a newer ID query or a return to normal text search.
        page.evaluate('''() => {
            const original = window.fetch;
            window.fetch = (url, options = {}) => original(url,
                String(url).startsWith('/api/tasks/id-search') ? {...options, signal: undefined} : options);
        }''')
        for replacement, expected in (('#12', [12, 14]), ('Content', [11, 12, 13, 14, 111])):
            delayed['query'] = '#11'
            with page.expect_request('**/api/tasks/id-search?query=%2311'):
                search.fill('#11')
            page.wait_for_function('document.getElementById("aufgabenTableContainer").getAttribute("aria-busy") === "true"')
            search_for(replacement, expected)
            assert delayed['response'] is not None
            route, response = delayed['response']
            delayed['response'] = None
            with page.expect_response('**/api/tasks/id-search?query=%2311'):
                route.fulfill(status=response.status_code, headers=dict(response.headers), body=response.content)
            # Drain the fetch response handler before asserting stable results.
            page.evaluate('() => new Promise(resolve => setTimeout(resolve, 50))')
            expect_rows(expected)
            expect(search).to_have_value(replacement)
        failure['next'] = True
        search_for('#11', [])
        expect(page.locator('.notification')).to_contain_text('Fehler')
        search_for('Content', [11, 12, 13, 14, 111])

        # Current rights on every lookup, and assigned subtasks without parent
        # read access still resolve to their existing separate list entries.
        login(2)
        page.reload()
        expect_rows([-11, -12])
        search_for('#11', [-11, -12])
        page.get_by_role('button', name='Teilaufgabe #11', exact=True).click()
        expect(page.locator('#stViewDescription_11')).to_be_visible()
        page.locator('[data-row-id="-11"] > .table-row > .expand-icon').click()
        page.get_by_role('button', name='Handoff #11 · Teilaufgabe #12', exact=True).click()
        expect(page.locator('.text-file-overlay .markdown-body')).to_have_text('Subtask handoff')
        page.locator('.text-file-overlay [data-file-close]').click()
        with closing(database.get_db()) as db, db:
            db.execute('UPDATE sub_tasks SET assigned_to=NULL WHERE id=12')
        search_for('', [-11, -12])
        search_for('#11', [-11])
        page.evaluate('async () => { await aufgabenTable.loadData(); }')
        expect_rows([-11])
        expect(page.locator('#aufgabenTableContainer')).to_have_attribute('aria-busy', 'false')
        page.set_viewport_size({'width': 540, 'height': 900})
        expect(page.get_by_role('button', name='Teilaufgabe #11', exact=True)).to_be_visible()
        assert not errors, errors
        browser.close()
    print('ID search UI: exact IDs, scoped navigation, normal search, filters, late replies, errors and current permissions passed')


if __name__ == '__main__':
    main()
