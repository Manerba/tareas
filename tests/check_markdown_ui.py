"""Browser regression checks against local JS/CSS and mocked API responses.

Requires the optional Playwright test dependency and its Chromium browser:
    python -m pip install playwright
    python -m playwright install chromium
    python tests/check_markdown_ui.py
No running Tareas instance or production data is used.
"""

import json
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SOURCE = '# Project scope\n\n**Bold** and `code`.\n\n- First\n- Second\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n```html\n<div>Example</div>\n```\n'


def main():
    task = dict(id=1, name='Markdown project', task_type='projekt', created_by=1,
                assigned_to=None, description=SOURCE, description_format='markdown',
                status='offen', deadline='', priority=50, can_edit_status=True)
    subtask = dict(id=2, project_id=1, name='Markdown subtask', position_number=1,
                   assigned_to=None, description='## Subtask\n\n- [x] Done',
                   description_format='markdown', status_percent=42, deadline='', priority=50)
    note = dict(user_id=1, user_name='Tester', content='## Notes\n\nA note.', content_format='markdown')
    saved = []
    fail_next = [False]

    def serve(route):
        path = urlparse(route.request.url).path
        data = {}
        status = 200
        if route.request.method == 'PUT':
            data = route.request.post_data_json
            if fail_next[0]:
                fail_next[0] = False
                status = 503
            else:
                saved.append((path, data))
                if 'description' in data:
                    model = subtask if path == '/api/subtasks/2' else task
                    model.update(data, description_format='markdown')
                if 'content' in data:
                    note.update(data, content_format='markdown')
        elif path == '/api/tasks/1/subtasks':
            data = {'items': [subtask]}
        elif path.endswith('/notes'):
            data = {'items': [note]}
        elif path.endswith('/note-entries'):
            data = {'items': [dict(note, id=1)]}
        elif path == '/':
            route.fulfill(content_type='text/html', body='<html><body data-admin-mode="true"><div id="table"></div><div id="sandbox"></div></body></html>')
            return
        route.fulfill(status=status, content_type='application/json', body=json.dumps(data))

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={'width': 1440, 'height': 1100})
        errors = []
        page.on('pageerror', lambda exc: errors.append(str(exc)))
        page.route('http://tareas.test/**', serve)
        page.goto('http://tareas.test/')
        translations = json.loads((ROOT / 'dashboard/static/i18n/en.json').read_text())
        page.evaluate('(translations) => window.t = key => translations[key] || key', translations)
        for name in ['expandable_table.js', 'wysiwyg_editor.js', 'app_core.js',
                     'vendor/marked.umd.js', 'vendor/turndown.js', 'markdown_editor.js', 'tab_aufgaben.js']:
            page.add_script_tag(content=(ROOT / 'dashboard/static/js' / name).read_text())
        for name in ['style.css', 'expandable_table.css', 'aufgaben.css', 'file_browser.css', 'markdown_editor.css']:
            page.add_style_tag(content=(ROOT / 'dashboard/static/css' / name).read_text())
        page.add_style_tag(content='body { padding: 25px; } * { animation: none !important; transition: none !important; }')
        page.evaluate('''task => {
            currentUser = {id: 1, is_admin: true};
            cachedAreas = [];
            cachedUsers = [{id: 1, vorname: 'Admin', nachname: ''}, {id: 3, vorname: 'Assignee', nachname: ''}];
            aufgabenTable = new ExpandableTable({id: 'mdTest', expandable: true,
                gridTemplate: '30px 330px 60px 130px 90px 150px 160px', columns: [
                    {field: 'name', align: 'left'}, {field: 'id', align: 'left'},
                    {field: 'status'}, {field: 'priority'}, {field: 'assigned_to_name'}, {field: 'deadline'}
                ], detailFields: []}, 'table', {
                    onRowExpanded: onTaskRowExpanded,
                    onRowCollapsed: deactivateInlineEditing,
                });
            aufgabenTable.filteredData = [task];
            tables.mdTest = aufgabenTable;
            document.getElementById('table').innerHTML = aufgabenTable.renderRow(task, 0);
        }''', task)
        toggle = page.locator('.table-row > .expand-icon')
        toggle.click()
        editor = page.locator('#wysiwygEditor_1')
        expect(editor.locator('.markdown-body h1')).to_have_text('Project scope')
        expect(editor.locator('.markdown-source')).to_be_hidden()
        expect(editor.locator('table')).to_have_count(1)
        expect(editor.locator('pre code')).to_have_text('<div>Example</div>\n')
        expect(page.locator('.wysiwyg-toolbar')).to_have_count(0)

        # Store source verbatim; literal HTML cannot trigger legacy conversion.
        editor.get_by_role('button', name='Edit', exact=True).click()
        expect(editor.locator('textarea')).to_have_value(SOURCE)
        changed = '<p>Literal HTML</p>\n\n' + SOURCE.replace('Project scope', 'Updated scope')
        editor.locator('textarea').fill(changed)
        editor.get_by_role('button', name='Save', exact=True).click()
        expect(editor.locator('textarea')).to_be_hidden()
        assert saved[-1] == ('/api/tasks/1', {'description': changed})
        expect(editor.locator('.markdown-body')).to_contain_text('<p>Literal HTML</p>')
        toggle.click()
        toggle.click()
        expect(editor.locator('.markdown-body h1')).to_have_text('Updated scope')

        # Cancel and row reconstruction must not lose or persist drafts.
        editor.get_by_role('button', name='Edit', exact=True).click()
        editor.locator('textarea').fill('Unsaved draft')
        toggle.click()
        toggle.click()
        expect(editor.locator('textarea')).to_have_value('Unsaved draft')
        editor.get_by_role('button', name='Cancel', exact=True).click()
        expect(editor.locator('.markdown-body h1')).to_have_text('Updated scope')
        assert len(saved) == 1

        # A failed save keeps the source editable and allows retrying.
        editor.get_by_role('button', name='Edit', exact=True).click()
        editor.locator('textarea').fill('# Retry retained')
        fail_next[0] = True
        editor.get_by_role('button', name='Save', exact=True).click()
        expect(editor.locator('[role="alert"]')).to_be_visible()
        expect(editor.locator('textarea')).to_have_value('# Retry retained')
        editor.get_by_role('button', name='Save', exact=True).click()
        expect(editor.locator('.markdown-body h1')).to_have_text('Retry retained')
        expect(editor.locator('textarea')).to_be_hidden()

        # Real subtask editor and notes/handoff renderers use the same Markdown.
        page.locator('tr.subtask-row .subtask-id-cell').click()
        subeditor = page.locator('#stWysiwyg_2')
        expect(subeditor.locator('.markdown-body h2')).to_have_text('Subtask')
        expect(subeditor.locator('.markdown-body')).to_contain_text('☑')
        subeditor.get_by_role('button', name='Edit', exact=True).click()
        subeditor.locator('textarea').fill('### Edited subtask')
        subeditor.get_by_role('button', name='Save', exact=True).click()
        expect(subeditor.locator('.markdown-body h3')).to_have_text('Edited subtask')
        assert saved[-1] == ('/api/subtasks/2', {'description': '### Edited subtask'})
        expect(page.locator('#stAllNotesContent_2 h2')).to_have_text('Notes')
        expect(page.locator('#taskNoteEntries_1 .note-entry-preview')).to_contain_text('Notes')

        # Admins edit assigned own/foreign projects even with read-only membership.
        note.update(user_id=9, user_name='Original author')
        subtask.update(assigned_to=3, permissions={'can_read': True, 'can_edit': False, 'can_create': False})
        for creator, assignee in [(1, 3), (2, 3), (2, 1)]:
            page.evaluate('''async ([creator, assignee]) => {
                const row = aufgabenTable.filteredData[0];
                Object.assign(row, {created_by: creator, assigned_to: assignee, is_team_member: true, can_edit_status: false});
                await onTaskRowExpanded(1, document.querySelector('.table-row-detail'));
            }''', [creator, assignee])
            for field in ('Name', 'Status', 'Priority', 'Assigned', 'Deadline'):
                expect(page.locator(f'#inline{field}_1')).to_be_enabled()
            expect(editor.locator('[data-md-action="edit"]')).to_be_visible()
            expect(page.locator('.team-btn')).to_be_visible()
        with page.expect_response(lambda r: r.url.endswith('/api/tasks/1') and r.request.method == 'PUT'):
            page.locator('#inlineName_1').fill('Admin renamed project')
            page.locator('#inlineName_1').press('Tab')
        assert saved[-1][1]['name'] == 'Admin renamed project'
        editor.get_by_role('button', name='Edit', exact=True).click()
        editor.get_by_role('button', name='Save', exact=True).click()
        expect(editor.locator('textarea')).to_be_hidden()
        assert saved[-1] == ('/api/tasks/1', {'description': '# Retry retained'})

        page.locator('tr.subtask-row .subtask-id-cell').click()
        for field in ('Name', 'Status', 'Priority', 'Assigned', 'Deadline'):
            expect(page.locator(f'#stEdit{field}_2')).to_be_enabled()
        expect(page.locator('#stWysiwyg_2 [data-md-action="edit"]')).to_be_visible()
        # Notes and handoffs keep their author label and save to a scoped endpoint.
        for container, editor_id, endpoint, handoff in [
            ('taskAllNotes_1', 9, '/api/tasks/1/notes/9', False),
            ('taskNoteEntries_1', 1, '/api/tasks/1/note-entries/1', True),
            ('stAllNotesContent_2', 9, '/api/tasks/1/subtasks/2/notes/9', False),
            ('stNoteEntriesContent_2', 1, '/api/tasks/1/subtasks/2/note-entries/1', True),
        ]:
            card = page.locator(f'#{container}')
            expect(card.locator('.subtask-note-meta strong')).to_have_text('Original author')
            if handoff:
                card.locator('summary').click()
            note_editor = page.locator(f'#{container}_editor_{editor_id}')
            expect(note_editor.locator('textarea')).to_be_hidden()
            note_editor.get_by_role('button', name='Edit', exact=True).click()
            note_editor.locator('textarea').fill('## Admin correction')
            note_editor.get_by_role('button', name='Save', exact=True).click()
            expect(note_editor.locator('textarea')).to_be_hidden()
            expect(note_editor.locator('.markdown-body h2')).to_have_text('Admin correction')
            expect(card.locator('.subtask-note-meta strong')).to_have_text('Original author')
            assert saved[-1] == (endpoint, {'content': '## Admin correction'})
            if handoff:
                expect(card.locator('.note-entry-preview')).to_have_text('Admin correction')

        # Read-only viewers, including the network detail dialog.
        page.evaluate('''() => _openNetzplanNodeDetails(0, aufgabenTable.filteredData[0], [], 'Project')''')
        expect(page.locator('.modal-content .markdown-body h1')).to_have_text('Retry retained')
        page.evaluate('closeModal()')
        page.evaluate('''async () => {
            const row = aufgabenTable.filteredData[0];
            currentUser.is_admin = false;
            row.created_by = 2; row.assigned_to = 1;
            await onTaskRowExpanded(1, document.querySelector('.table-row-detail'));
        }''')
        expect(page.locator('.description-readonly h1')).to_have_text('Retry retained')
        expect(page.locator('#taskNoteEntries_1 [data-md-action="edit"]')).to_have_count(0)
        notes = page.locator('#notesEditor_1')
        expect(notes.locator('textarea')).to_be_hidden()
        notes.get_by_role('button', name='Edit', exact=True).click()
        notes.locator('textarea').fill('## Saved notes')
        notes.get_by_role('button', name='Save', exact=True).click()
        expect(notes.locator('.markdown-body h2')).to_have_text('Saved notes')
        assert saved[-1] == ('/api/tasks/1/notes', {'content': '## Saved notes'})

        # Assigned subtasks have their own notes endpoint and read-only description.
        page.evaluate('''async subtask => {
            await onSubtaskViewExpanded({...subtask, id: -2, _type: 'assigned_subtask',
                _subtask_id: 2, _project_id: 1, _project_name: 'Project', _status_percent: 42},
                document.querySelector('.table-row-detail'));
        }''', subtask)
        expect(page.locator('.description-readonly h3')).to_have_text('Edited subtask')
        assigned_notes = page.locator('#stViewNotes_2')
        assigned_notes.get_by_role('button', name='Edit', exact=True).click()
        assigned_notes.locator('textarea').fill('## Assigned subtask notes')
        assigned_notes.get_by_role('button', name='Save', exact=True).click()
        expect(assigned_notes.locator('.markdown-body h2')).to_have_text('Assigned subtask notes')
        assert saved[-1] == ('/api/tasks/1/subtasks/2/notes', {'content': '## Assigned subtask notes'})

        # Admins can also edit the separate assigned-subtask view.
        page.evaluate('''async subtask => {
            currentUser.is_admin = true;
            await onSubtaskViewExpanded({...subtask, id: -2, _type: 'assigned_subtask',
                _subtask_id: 2, _project_id: 1, _project_name: 'Project', _status_percent: 42},
                document.querySelector('.table-row-detail'));
        }''', subtask)
        for field in ('Name', 'Status', 'Priority', 'Assigned', 'Deadline'):
            expect(page.locator(f'#stView{field}_2')).to_be_enabled()
        expect(page.locator('#stViewDescription_2 [data-md-action="edit"]')).to_be_visible()
        with page.expect_response(lambda r: r.url.endswith('/api/subtasks/2') and r.request.method == 'PUT'):
            page.locator('#stViewName_2').fill('Admin renamed assigned subtask')
            page.locator('#stViewName_2').press('Tab')
        assert saved[-1][1]['name'] == 'Admin renamed assigned subtask'
        expect(page.locator('#stViewAllNotes_2 [data-md-action="edit"]')).to_be_visible()

        # Team readers must not receive an edit button merely because they can view.
        page.evaluate('''async () => {
            currentUser.is_admin = false;
            const row = aufgabenTable.filteredData[0];
            row.created_by = 2; row.assigned_to = null;
            row.is_team_member = true; row.can_edit_status = false;
            await onTaskRowExpanded(1, document.querySelector('.table-row-detail'));
        }''')
        expect(editor.locator('[data-md-action="edit"]')).to_have_count(0)
        expect(editor.locator('.markdown-body h1')).to_have_text('Retry retained')
        page.locator('tr.subtask-row .subtask-id-cell').click()
        expect(page.locator('#stAllNotesContent_2 [data-md-action="edit"]')).to_have_count(0)
        expect(page.locator('#stNoteEntriesContent_2 [data-md-action="edit"]')).to_have_count(0)

        # Legacy compatibility is explicit: new Markdown never runs through Turndown.
        legacy = '<h2>Old title</h2><p><b>Bold</b> and text</p><p># Already Markdown</p>'
        page.evaluate('''legacy => new MarkdownEditor('sandbox', legacy, {
            format: 'legacy', onSave: async () => {},
        })''', legacy)
        old = page.locator('#sandbox')
        expect(old.locator('h2')).to_have_text('Old title')
        expect(old.locator('h1')).to_have_text('Already Markdown')
        old.get_by_role('button', name='Edit', exact=True).click()
        legacy_source = old.locator('textarea').input_value()
        assert '## Old title' in legacy_source and '**Bold**' in legacy_source
        assert '<h2>' not in legacy_source
        assert page.evaluate('source => markdownSource(source, "markdown")', changed) == changed
        assert page.evaluate('source => markdownSource(source, "legacy")', SOURCE) == SOURCE

        # Rendering never executes raw HTML or dangerous links; code examples survive.
        unsafe = '# Safe\n\n<script>window.mdXss = true</script>\n\n<img src=x onerror="window.mdXss=true">\n\n[bad](javascript:alert(1))\n\n[good](https://example.invalid)\n\n```html\n<script>example</script>\n```'
        page.evaluate('source => document.getElementById("sandbox").innerHTML = renderMarkdown(source)', unsafe)
        expect(old.locator('script')).to_have_count(0)
        expect(old.locator('[onerror]')).to_have_count(0)
        expect(old.locator('a[href^="javascript:"]')).to_have_count(0)
        expect(old.locator('a[href="https://example.invalid"]')).to_have_count(1)
        expect(old.locator('pre code')).to_contain_text('<script>example</script>')
        assert page.evaluate('window.mdXss === undefined')
        sanitized = page.evaluate('''() => sanitizeHtml('<a href="java&#x09;script:alert(1)" onclick="alert(1)">bad</a><img src="data:image/svg+xml,evil" onerror="alert(1)">')''')
        assert 'href=' not in sanitized and 'src=' not in sanitized and 'onclick=' not in sanitized and 'onerror=' not in sanitized
        expect(page.locator('.wysiwyg-toolbar')).to_have_count(0)
        assert errors == [], errors
        browser.close()
    print('Markdown browser checks passed: view/edit/save/cancel, drafts, failed saves, subtasks, notes/handoffs, admin/reader permissions, legacy HTML and XSS.')


if __name__ == '__main__':
    main()
