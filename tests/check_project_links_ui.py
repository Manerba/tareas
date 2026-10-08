"""Browser-Integration mit echten REST-Routen und isolierter SQLite-Datenbank.

Benutzt die optionale Playwright-Installation, keine laufende Tareas-Instanz.
"""

import mimetypes
import sys
import tempfile
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlparse

from fastapi import FastAPI
from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dashboard import api_tasks, database, mcp_server as mcp
from dashboard.auth import get_current_user


def main():
    with tempfile.TemporaryDirectory(prefix='tareas-link-browser-') as tmp, \
            patch.object(database, 'DB_DIR', Path(tmp)), \
            patch.object(database, 'DB_PATH', Path(tmp) / 'test.db'), \
            patch.object(api_tasks, 'notify_event', return_value=None):
        database.init_db()
        user = dict(id=1, username='admin', is_admin=True, auth_source='local')
        token = mcp.current_mcp_user.set(user)
        try:
            run(user)
        finally:
            mcp.current_mcp_user.reset(token)


def run(user):
    parent = mcp.create_project('Parent project')['id']
    subtask = mcp.create_subtask(parent, 'Expandable scope', status_percent=75)['id']
    child = mcp.create_project('Existing child')['id']
    mcp.create_subtask(child, 'Done', status_percent=100)
    leaf = mcp.create_subtask(child, 'Pending')['id']
    app = FastAPI()
    app.include_router(api_tasks.router)
    app.dependency_overrides[get_current_user] = lambda: user
    writes = []

    with TestClient(app) as client, sync_playwright() as pw:
        def serve(route):
            path = urlparse(route.request.url).path
            if route.request.resource_type == 'document':
                route.fulfill(content_type='text/html', body=(ROOT / 'dashboard/templates/index.html').read_text())
            elif path.startswith('/static/'):
                file = ROOT / 'dashboard' / path.lstrip('/')
                route.fulfill(content_type=mimetypes.guess_type(file)[0] or 'application/octet-stream', body=file.read_bytes())
            elif path == '/api/auth/me':
                route.fulfill(json={'user': user})
            elif path == '/api/nextcloud/status':
                route.fulfill(json={'configured': False})
            elif path.startswith(('/api/tasks', '/api/subtasks', '/api/areas', '/api/users/list')):
                method = route.request.method
                body = route.request.post_data_json if route.request.post_data else None
                if method in ('POST', 'PUT', 'DELETE'):
                    writes.append((path, body))
                response = client.request(method, path, json=body)
                route.fulfill(status=response.status_code, content_type='application/json', body=response.content)
            else:
                route.fulfill(json={'items': []})

        browser = pw.chromium.launch()
        page = browser.new_page(viewport={'width': 1600, 'height': 1000})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.route('http://tareas.test/**', serve)
        page.goto('http://tareas.test/aufgaben')
        expect(page.locator('.table-header')).to_contain_text('PID')
        page.locator(f'[data-row-id="{parent}"] > .table-row > .expand-icon').click()
        page.locator(f'[data-subtask-id="{subtask}"] .subtask-expand-icon').click()
        detail = page.locator(f'[data-subtask-detail-id="{subtask}"]')
        detail.locator('.project-link-bar .control-btn').click()
        page.locator('#childProjectSelect').select_option(str(child))
        expect(page.locator('#childProjectNameField')).to_be_hidden()
        page.locator('#projectLinkSave').click()
        expect(detail.locator('.project-link-bar')).to_contain_text(f'#{child}')
        expect(page.locator(f'#stEditStatus_{subtask}')).to_have_count(0)
        expect(page.locator(f'[data-subtask-id="{subtask}"]')).to_contain_text('50%')
        page.locator(f'#stEditName_{subtask}').fill('Renamed parent subtask')
        page.locator(f'#stEditName_{subtask}').press('Tab')
        page.wait_for_function('(sid) => document.getElementById("subtaskContainer_' + str(parent) + '")._subtasksData.find(s => s.id === sid).name === "Renamed parent subtask"', arg=subtask)
        payload = next(body for path, body in reversed(writes) if path == f'/api/subtasks/{subtask}')
        assert 'status_percent' not in payload, payload
        assert mcp.get_subtask(subtask)['name'] == 'Renamed parent subtask'

        # Child edits update both the parent's percentage and its project status.
        detail.locator('.project-link-button').click()
        page.locator(f'[data-subtask-id="{leaf}"] .subtask-expand-icon').click()
        page.locator(f'#stEditStatus_{leaf}').fill('100')
        page.locator(f'#stEditStatus_{leaf}').press('Tab')
        page.wait_for_function('(pid) => aufgabenTable.data.find(t => t.id === pid).status === "erledigt"', arg=parent)
        pid = page.locator(f'[data-row-id="{child}"] > .table-row .project-link-button')
        expect(pid).to_have_text(str(subtask))
        pid.click()
        expect(page.locator(f'[data-subtask-id="{subtask}"]')).to_contain_text('100%')
        expect(page.locator(f'#stEditStatus_{subtask}')).to_have_count(0)

        # Detach retains progress and restores manual editing.
        detail.locator('.project-link-bar .control-btn').click()
        page.locator('#projectLinkSave').click()
        expect(page.locator(f'#stEditStatus_{subtask}')).to_have_value('100')
        assert mcp.get_project(child)['parent_subtask_id'] is None

        # New-project path works at narrow width and immediately takes ownership of progress.
        page.set_viewport_size({'width': 540, 'height': 900})
        detail.locator('.project-link-bar .control-btn').click()
        page.locator('#childProjectName').fill('New child from scope')
        page.locator('#projectLinkSave').click()
        expect(page.locator(f'#stEditStatus_{subtask}')).to_have_count(0)
        expect(page.locator(f'[data-subtask-id="{subtask}"]')).to_contain_text('0%')
        new_child = mcp.get_subtask(subtask)['child_project_id']
        assert mcp.get_project(new_child)['name'] == 'New child from scope'

        # Assigned-subtask pseudo rows must obey the same lock, including full edit saves.
        with closing(database.get_db()) as db, db:
            owner = db.execute("INSERT INTO users (username, password_hash) VALUES ('owner', 'unused')").lastrowid
            db.execute('UPDATE tasks SET created_by = ? WHERE id = ?', (owner, parent))
        page.set_viewport_size({'width': 1600, 'height': 1000})
        page.evaluate('async () => { await aufgabenTable.loadData(); }')
        page.locator(f'[data-row-id="{-subtask}"] > .table-row > .expand-icon').click()
        expect(page.locator(f'#stViewStatus_{subtask}')).to_be_disabled()
        page.locator(f'#stViewName_{subtask}').fill('Updated via assignment')
        with page.expect_response(lambda response: response.url.endswith(f'/api/subtasks/{subtask}') and response.request.method == 'PUT') as saved:
            page.locator(f'#stViewName_{subtask}').press('Tab')
        assert saved.value.status == 200
        payload = next(body for path, body in reversed(writes) if path == f'/api/subtasks/{subtask}')
        assert 'status_percent' not in payload, payload
        assert mcp.get_subtask(subtask)['name'] == 'Updated via assignment'
        assert mcp.get_subtask(subtask)['progress_automatic'] is True
        assert not errors, errors
        browser.close()
        print('Project links browser integration: passed')


if __name__ == '__main__':
    main()
