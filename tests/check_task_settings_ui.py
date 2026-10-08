"""Browser checks for settings drafts and role-specific task controls; mocked API."""

import copy
import json
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main():
    task = dict(id=1, name='Example task', task_type='aufgabe', created_by=1, assigned_to=None,
                description='# Scope', description_format='markdown', priority=50, status='offen', deadline='')
    users = [dict(id=uid, username=name, vorname=name, nachname='', email=f'{name}@example.test',
                  is_admin=uid == 4, is_active=True) for uid, name in
             [(1, 'Owner'), (2, 'Editor'), (3, 'Reader'), (4, 'Admin'), (5, 'Alice'), (6, 'Bob')]]
    config = dict(task=task, users=users, revision='initial', items=[
        dict(user_id=2, can_read=True, can_edit=True, can_create=False),
        dict(user_id=3, can_read=True, can_edit=False, can_create=False),
    ])
    writes = []
    fail_save = [0]
    deny_load = [False]
    rights = dict(can_read=True, can_edit=True, can_manage=True, can_create=False, can_structure=True, can_edit_status=True)

    def serve(route):
        path = urlparse(route.request.url).path
        status, data = 200, {}
        if path == '/':
            route.fulfill(content_type='text/html', body='<html><body data-admin-mode="true"><div id="table"></div></body></html>')
            return
        if path == '/api/tasks/1/members':
            if route.request.method == 'PUT':
                writes.append(route.request.post_data_json)
                status = fail_save[0] or 200
                fail_save[0] = 0
                if status == 200:
                    config['items'] = copy.deepcopy(writes[-1]['members'])
                    config['revision'] += '-saved'
            else:
                status = 403 if deny_load[0] else 200
                data = config
        elif path == '/api/tasks/1/subtasks':
            data = {'items': [], 'permissions': rights}
        elif path.endswith('/notes') or path.endswith('/note-entries'):
            data = {'items': []}
        route.fulfill(status=status, content_type='application/json', body=json.dumps(data))

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={'width': 1300, 'height': 950})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.route('http://tareas.test/**', serve)
        page.goto('http://tareas.test/')
        page.evaluate('data => window.t = key => data[key] || key', json.loads((ROOT / 'dashboard/static/i18n/en.json').read_text()))
        for name in ('expandable_table.js', 'app_core.js', 'vendor/marked.umd.js', 'vendor/turndown.js',
                     'markdown_editor.js', 'tab_aufgaben.js', 'project_links.js', 'task_settings.js'):
            page.add_script_tag(content=(ROOT / 'dashboard/static/js' / name).read_text())
        for name in ('style.css', 'expandable_table.css', 'aufgaben.css', 'markdown_editor.css', 'task_settings.css'):
            page.add_style_tag(content=(ROOT / 'dashboard/static/css' / name).read_text())
        page.add_style_tag(content='* { animation: none !important; transition: none !important; }')
        page.evaluate('''task => {
            currentUser = {id: 1, is_admin: false};
            cachedUsers = []; cachedAreas = [];
            aufgabenTable = new ExpandableTable({id: 'settingsTest', expandable: true,
                gridTemplate: '30px 260px 100px 80px 130px 140px 76px', columns: [
                    {field: 'name'}, {field: 'status'}, {field: 'priority'},
                    {field: 'assigned_to_name'}, {field: 'deadline'}, {field: '_actions', renderer: 'deleteAction'}
                ], detailFields: []}, 'table', {onRowExpanded: onTaskRowExpanded});
            aufgabenTable.renderers.deleteAction = renderDeleteAction;
            aufgabenTable.filteredData = [task]; tables.settingsTest = aufgabenTable;
            aufgabenTable.loadData = async () => { window.reloads = (window.reloads || 0) + 1; };
            window.renderSettingsRow = () => document.getElementById('table').innerHTML = aufgabenTable.renderRow(task, 0);
            renderSettingsRow();
        }''', dict(task, permissions=rights))
        page.locator('.row-settings-btn').click()
        expect(page.get_by_role('dialog')).to_be_visible()
        expect(page.locator('#taskSettingsSave')).to_be_disabled()
        expect(page.locator('[data-permission="create"]')).to_have_count(0)
        expect(page.locator('#permissionRows tr')).to_have_count(2)
        expect(page.locator('.permissions-fixed')).to_contain_text('Owner')
        expect(page.locator('.permissions-fixed')).to_contain_text('Administrators')
        page.screenshot(path='/tmp/tareas-task-settings-desktop.png')
        # Opening settings must not expand/collapse the task underneath.
        expect(page.locator('.table-row-wrapper.expanded')).to_have_count(0)
        page.locator('#permissionUserSearch').fill('alice')
        expect(page.locator('#permissionUserSelect option')).to_have_count(2)
        page.locator('#permissionUserSelect').select_option('5')
        page.locator('#permissionAdd').click()
        page.locator('[data-user-id="5"] [data-permission="access"]').select_option('edit')
        page.locator('[data-user-id="2"] [data-remove]').click()
        assert writes == []
        page.locator('#taskSettingsCancel').click()
        assert writes == []
        page.locator('.row-settings-btn').click()
        expect(page.locator('#permissionRows tr')).to_have_count(2)
        expect(page.locator('[data-user-id="5"]')).to_have_count(0)

        # Save sends one complete, normalized draft. Failed saves retain it.
        page.locator('[data-user-id="3"] [data-permission="access"]').select_option('edit')
        fail_save[0] = 503
        page.locator('#taskSettingsSave').click()
        expect(page.locator('#taskSettingsError')).to_be_visible()
        expect(page.locator('[data-user-id="3"] [data-permission="access"]')).to_have_value('edit')
        page.locator('#taskSettingsSave').click()
        expect(page.get_by_role('dialog')).to_have_count(0)
        assert writes[-1]['members'][1] == dict(user_id=3, can_read=True, can_edit=True, can_create=False)
        assert page.evaluate('window.reloads') == 1

        # Project settings preserve separate creation rights and explain inheritance.
        config['task']['task_type'] = 'projekt'
        page.locator('.row-settings-btn').click()
        expect(page.locator('[data-permission="create"]')).to_have_count(2)
        expect(page.locator('#taskSettingsContent')).to_contain_text('all subtasks')
        page.locator('[data-user-id="3"] [data-permission="access"]').select_option('read')
        page.locator('[data-user-id="3"] [data-permission="create"]').check()
        fail_save[0] = 409
        page.locator('#taskSettingsSave').click()
        expect(page.locator('#taskSettingsError')).to_contain_text('Permissions have changed')
        expect(page.locator('[data-user-id="3"] [data-permission="create"]')).to_be_checked()
        page.locator('#taskSettingsCancel').click()
        # The server still has the previous grant because the conflict failed.
        assert config['items'][1]['can_create'] is False

        # Small screens keep all controls reachable; admin opens a foreign task.
        page.set_viewport_size({'width': 520, 'height': 900})
        page.evaluate('currentUser = {id: 4, is_admin: true}')
        page.locator('.row-settings-btn').click()
        expect(page.locator('#permissionUserSearch')).to_be_visible()
        expect(page.locator('#taskSettingsCancel')).to_be_visible()
        for button in page.locator('[data-remove]').all():
            box = button.bounding_box()
            assert box['x'] >= 0 and box['x'] + box['width'] <= 520, box
        page.screenshot(path='/tmp/tareas-task-settings-mobile.png')
        page.locator('#taskSettingsCancel').click()
        page.set_viewport_size({'width': 1300, 'height': 950})

        # Actual task controls follow effective rights, including empty projects.
        for editable in (False, True):
            current_rights = dict(rights, can_edit=editable, can_edit_status=editable,
                                  can_manage=False, can_structure=editable, can_create=False)
            rights.update(current_rights)
            page.evaluate('''async rights => {
                currentUser = {id: 3, is_admin: false};
                const row = aufgabenTable.filteredData[0];
                Object.assign(row, {task_type: 'projekt', permissions: rights, is_team_member: true});
                renderSettingsRow();
                await onTaskRowExpanded(1, document.querySelector('.table-row-detail'));
            }''', current_rights)
            expect(page.locator('.row-settings-btn')).to_have_count(0)
            expect(page.locator('#inlineAssigned_1')).to_have_count(0)
            expect(page.locator('#inlineName_1')).to_have_count(1 if editable else 0)
            expect(page.locator('#wysiwygEditor_1 [data-md-action="edit"]')).to_have_count(1 if editable else 0)
            expect(page.locator('.subtask-add-row')).to_have_count(0)

        deny_load[0] = True
        page.evaluate('openTaskSettings(1)')
        expect(page.locator('#taskSettingsError')).to_contain_text('Only creators')
        expect(page.locator('#taskSettingsSave')).to_be_disabled()
        page.locator('#taskSettingsCancel').click()
        assert errors == [], errors
        browser.close()
    print('Task settings browser checks passed: drafts, save/cancel, conflicts, failure retry, roles, mobile layout.')


if __name__ == '__main__':
    main()
