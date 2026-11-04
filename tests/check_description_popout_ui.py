"""Description pop-outs with real pages, REST permissions and an isolated DB.

Run with the optional Playwright installation; no running Tareas instance needed.
"""

import logging
import sys
import tempfile
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlparse

from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dashboard import auth, database


def main():
    with tempfile.TemporaryDirectory(prefix='tareas-description-window-') as tmp, \
            patch.object(database, 'DB_DIR', Path(tmp)), \
            patch.object(database, 'DB_PATH', Path(tmp) / 'test.db'), \
            patch.object(auth, 'SECRET_KEY_PATH', Path(tmp) / 'secret.key'), \
            patch('logging.handlers.RotatingFileHandler', return_value=logging.NullHandler()):
        from dashboard.app import app
        database.init_db()
        with closing(database.get_db()) as db, db:
            reader = db.execute("INSERT INTO users (username, password_hash) VALUES ('reader', 'unused')").lastrowid
            assigned = db.execute("INSERT INTO users (username, password_hash) VALUES ('assigned', 'unused')").lastrowid
            project = db.execute("INSERT INTO tasks (name, task_type, created_by, description) VALUES ('Window project', 'projekt', 1, '# Project')").lastrowid
            subtask = db.execute("INSERT INTO sub_tasks (name, project_id, description, assigned_to) VALUES ('Window subtask', ?, '# Subtask', ?)", (project, assigned)).lastrowid
            db.execute('INSERT INTO project_members (project_id, user_id, can_read) VALUES (?, ?, 1)', (project, reader))
        client = TestClient(app)
        try:
            run(client, project, subtask, reader, assigned)
        finally:
            client.close()


def run(client, project, subtask, reader, assigned):
    errors, requests = [], []
    delayed = {'next': False, 'response': None}
    project_endpoint = f'/api/tasks/{project}/description'
    subtask_endpoint = f'/api/tasks/{project}/subtasks/{subtask}/description'

    def login(user_id):
        client.cookies.set(auth.COOKIE_NAME, auth.sign_token(auth.create_session(user_id)))

    def serve(route):
        request = route.request
        url = urlparse(request.url)
        path = url.path + ('?' + url.query if url.query else '')
        headers = {'X-Requested-With': request.headers.get('x-requested-with', '')}
        body = request.post_data_json if request.post_data else None
        response = client.request(request.method, path, headers=headers, json=body, follow_redirects=False)
        if url.path == '/description':
            assert "script-src 'self';" in response.headers['content-security-policy']
        if url.path.endswith('/description') and url.path.startswith('/api/'):
            requests.append((request.method, url.path, body, response.status_code))
            if request.method == 'GET' and delayed['next']:
                delayed.update(next=False, response=(route, response))
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
        page.locator(f'[data-row-id="{project}"] > .table-row > .expand-icon').click()
        project_editor = page.locator(f'#wysiwygEditor_{project}')
        expect(project_editor.locator('[data-md-expand]')).to_be_visible()
        page.locator(f'[data-subtask-id="{subtask}"] .subtask-expand-icon').click()
        subtask_editor = page.locator(f'#stWysiwyg_{subtask}')

        def rebuild():
            page.evaluate('async () => { await aufgabenTable.loadData(); }')
            page.locator(f'[data-row-id="{project}"] > .table-row > .expand-icon').click()
            page.locator(f'[data-subtask-id="{subtask}"] .subtask-expand-icon').click()

        def expand(inline):
            with page.expect_popup() as opened:
                inline.locator('[data-md-expand]').click()
            popup = opened.value
            expect(popup.locator('.markdown-editor')).to_be_visible()
            popup.wait_for_function('!location.hash')
            expect(inline.locator('[data-md-expand]')).to_be_enabled()
            assert popup.evaluate('!Object.values(localStorage).some(value => value.includes("PRIVATE DRAFT"))')
            assert 'PRIVATE' not in popup.url
            return popup

        # Project and nested subtask: transfer source, cursor, draft revision;
        # successful saves refresh the inline view and survive a row rebuild.
        for inline, endpoint, entity in ((project_editor, project_endpoint, 'Project'),
                                         (subtask_editor, subtask_endpoint, 'Subtask')):
            base_revision = client.get(endpoint).json()['revision']
            draft = f'## PRIVATE DRAFT {entity}\n\n**Unicode ✓** <script>alert(1)</script>'
            inline.locator('[data-md-action="edit"]').click()
            inline.locator('textarea').fill(draft)
            inline.locator('textarea').evaluate('(input) => input.setSelectionRange(4, 11)')
            popup = expand(inline)
            editor = popup.locator('.markdown-editor')
            expect(editor.locator('textarea')).to_have_value(draft)
            assert editor.locator('textarea').evaluate('(input) => [input.selectionStart, input.selectionEnd]') == [4, 11]
            expect(inline.locator('textarea')).to_be_hidden()
            editor.locator('[data-md-action="save"]').click()
            expect(editor.locator('textarea')).to_be_hidden()
            expect(inline.locator('.markdown-body')).to_contain_text('PRIVATE DRAFT ' + entity)
            assert client.get(endpoint).json()['content'] == draft
            assert next(body for method, path, body, _ in reversed(requests) if method == 'PUT' and path == endpoint)['revision'] == base_revision
            expect(editor.locator('.markdown-body script')).to_have_count(0)
            # A second save must use the revision of the first save.
            editor.locator('[data-md-action="edit"]').click()
            editor.locator('textarea').fill(f'## Saved {entity}')
            editor.locator('[data-md-action="save"]').click()
            expect(inline.locator('.markdown-body')).to_have_text('Saved ' + entity)
            popup.close()
        rebuild()
        expect(project_editor.locator('.markdown-body')).to_have_text('Saved Project')
        expect(subtask_editor.locator('.markdown-body')).to_have_text('Saved Subtask')

        # Popup blocking never clears the existing inline draft.
        project_editor.locator('[data-md-action="edit"]').click()
        project_editor.locator('textarea').fill('Blocked popup draft')
        page.evaluate('window.realOpen = window.open; window.open = () => null')
        project_editor.locator('[data-md-expand]').click()
        expect(project_editor.locator('.markdown-error')).to_be_visible()
        expect(project_editor.locator('textarea')).to_have_value('Blocked popup draft')
        expect(project_editor.locator('textarea')).to_be_enabled()
        page.evaluate('window.open = window.realOpen')
        project_editor.locator('[data-md-action="cancel"]').click()

        # While the popup loads, rebuilding the row must keep the transfer lock.
        # A forged acknowledgment cannot release that lock or clear the draft.
        project_editor.locator('[data-md-action="edit"]').click()
        project_editor.locator('textarea').fill('Transfer after rebuild')
        delayed['next'] = True
        with page.expect_popup() as opened:
            project_editor.locator('[data-md-expand]').click()
        popup = opened.value
        popup.wait_for_function('Boolean(document.getElementById("textFilePage"))')
        page.wait_for_function('Boolean(descriptionTransfers.size)')
        rebuild()
        expect(project_editor.locator('textarea')).to_be_disabled()
        page.evaluate('''() => {
            const popup = [...descriptionWindows.keys()].at(-1);
            window.dispatchEvent(new MessageEvent('message', {origin:location.origin, source:popup,
                data:{type:'text-file-loaded',token:'wrong-token'}}));
        }''')
        expect(project_editor.locator('textarea')).to_be_disabled()
        assert delayed['response'] is not None
        route, response = delayed['response']
        delayed['response'] = None
        route.fulfill(status=response.status_code, headers=dict(response.headers), body=response.content)
        expect(popup.locator('textarea')).to_have_value('Transfer after rebuild')
        expect(project_editor.locator('textarea')).to_be_hidden()
        popup.close()

        # An external save while a local draft is open must preserve its base
        # revision, including after row rebuilds; a retry is still a conflict.
        popup = expand(project_editor)
        project_editor.locator('[data-md-action="edit"]').click()
        project_editor.locator('textarea').fill('My concurrent draft')
        popup.locator('[data-md-action="edit"]').click()
        popup.locator('textarea').fill('Other window saved')
        popup.locator('[data-md-action="save"]').click()
        expect(popup.locator('textarea')).to_be_hidden()
        page.wait_for_function('(id) => aufgabenTable.data.find(row => row.id === id).description === "Other window saved"', arg=project)
        expect(project_editor.locator('textarea')).to_have_value('My concurrent draft')
        rebuild()
        project_editor.locator('[data-md-action="save"]').click()
        expect(project_editor.locator('.markdown-error')).to_be_visible()
        expect(project_editor.locator('textarea')).to_have_value('My concurrent draft')
        assert client.get(project_endpoint).json()['content'] == 'Other window saved'
        popup.close()
        # Transferring an already conflicting draft keeps the original revision.
        popup = expand(project_editor)
        popup.locator('[data-md-action="save"]').click()
        expect(popup.locator('.markdown-error')).to_contain_text('Entwurf bleibt erhalten')
        expect(popup.locator('textarea')).to_have_value('My concurrent draft')
        assert requests[-1][3] == 409
        popup.close()

        # Read-only users can open both kinds of description, but cannot edit.
        login(reader)
        page.reload()
        page.locator(f'[data-row-id="{project}"] > .table-row > .expand-icon').click()
        page.locator(f'[data-subtask-id="{subtask}"] .subtask-expand-icon').click()
        for inline in (project_editor, subtask_editor):
            expect(inline.locator('[data-md-action="edit"]')).to_have_count(0)
            popup = expand(inline)
            expect(popup.locator('[data-md-action="edit"]')).to_have_count(0)
            expect(popup.locator('.markdown-body')).to_be_visible()
            popup.close()

        # Revocation before a row rebuild hides editing controls, but an older
        # unsaved draft must still transfer and remain available for copying.
        with closing(database.get_db()) as db, db:
            db.execute('UPDATE project_members SET can_edit=1 WHERE user_id=?', (reader,))
        rebuild()
        project_editor.locator('[data-md-action="edit"]').click()
        project_editor.locator('textarea').fill('Draft retained after permission change')
        with closing(database.get_db()) as db, db:
            db.execute('UPDATE project_members SET can_edit=0 WHERE user_id=?', (reader,))
        rebuild()
        expect(project_editor.locator('[data-md-action="edit"]')).to_have_count(0)
        popup = expand(project_editor)
        expect(popup.locator('textarea')).to_have_value('Draft retained after permission change')
        expect(popup.locator('textarea')).to_be_visible()
        expect(popup.locator('[data-md-action="save"]')).to_have_count(0)
        expect(popup.locator('.text-file-status')).to_be_visible()
        popup.close()

        # A subtask assignee has a separate row without project read access.
        login(assigned)
        page.reload()
        page.locator(f'[data-row-id="{-subtask}"] > .table-row > .expand-icon').click()
        assigned_editor = page.locator(f'#stViewDescription_{subtask}')
        popup = expand(assigned_editor)
        expect(popup.locator('.text-file-title')).to_contain_text('Window subtask')
        popup.locator('[data-md-action="edit"]').click()
        popup.locator('textarea').fill('Assigned window edit')
        popup.locator('[data-md-action="save"]').click()
        expect(assigned_editor.locator('.markdown-body')).to_have_text('Assigned window edit')
        assert client.get(project_endpoint).status_code == 403
        popup.close()

        # Revocation after opening is checked on save; the draft stays copyable.
        popup = expand(assigned_editor)
        popup.locator('[data-md-action="edit"]').click()
        popup.locator('textarea').fill('Draft after revocation')
        with closing(database.get_db()) as db, db:
            db.execute('UPDATE sub_tasks SET assigned_to=NULL WHERE id=?', (subtask,))
        popup.locator('[data-md-action="save"]').click()
        expect(popup.locator('.markdown-error')).to_be_visible()
        expect(popup.locator('textarea')).to_have_value('Draft after revocation')
        assert requests[-1][3] == 403
        popup.close()

        # If even read access was revoked before opening, failed loading closes
        # the popup and leaves the complete draft in the originating view.
        assigned_editor.locator('[data-md-action="edit"]').click()
        assigned_editor.locator('textarea').fill('Keep inline on failed load')
        with page.expect_popup() as opened:
            assigned_editor.locator('[data-md-expand]').click()
        expect(assigned_editor.locator('.markdown-error')).to_be_visible()
        expect(assigned_editor.locator('textarea')).to_have_value('Keep inline on failed load')
        expect(assigned_editor.locator('textarea')).to_be_enabled()
        assert opened.value.is_closed()
        assert not errors, errors
        browser.close()
    print('Description pop-outs: project/subtask/assigned views, drafts, revisions, permissions and real CSP passed')


if __name__ == '__main__':
    main()
