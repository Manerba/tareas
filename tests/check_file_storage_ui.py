"""Browser checks with local assets and mocked APIs; requires Playwright/Chromium."""

import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main():
    task = dict(id=1, name="File storage project", task_type="projekt", created_by=1,
                assigned_to=None, description="## Documents", description_format="markdown",
                status="offen", deadline="", priority=50, can_edit_status=True,
                file_storage_type="none", nextcloud_path="")
    saved = []
    extra_tasks = []
    state = {"can_write": True, "fail_next": False}
    directory_name = "Team's <documents>"

    def serve(route):
        url = urlparse(route.request.url)
        path = url.path
        status = 200
        data = {"items": []}
        if path == "/":
            route.fulfill(content_type="text/html", body='<html><body data-admin-mode="true"><button id="toggleAllBtn" onclick="toggleAllExpand()"></button><div id="table"></div></body></html>')
            return
        if path == "/api/tasks/1" and route.request.method == "PUT":
            data = route.request.post_data_json
            if state["fail_next"]:
                state["fail_next"] = False
                status = 500
                data = {"detail": "Storage creation failed"}
            else:
                saved.append(data)
                task.update(data)
                if data.get("file_storage_type") != "webdav":
                    task["nextcloud_path"] = ""
        elif path == "/api/tasks":
            data = {"items": [task, *extra_tasks]}
        elif path == "/api/nextcloud/directories":
            current = parse_qs(url.query).get("path", [""])[0]
            data = {"directories": [] if current else [{"name": directory_name, "path": directory_name}]}
        elif path == "/api/tasks/1/files":
            data = {"items": [{"name": "Plan.txt", "type": "file", "size": 8, "last_modified": ""}],
                    "storage_type": task["file_storage_type"], "can_write": state["can_write"]}
        route.fulfill(status=status, content_type="application/json", body=json.dumps(data))

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.route("http://tareas.test/**", serve)
        page.goto("http://tareas.test/")
        translations = json.loads((ROOT / "dashboard/static/i18n/en.json").read_text())
        page.evaluate("""translations => window.t = (key, params = {}) => {
            let text = translations[key] || key;
            for (const [name, value] of Object.entries(params)) text = text.replaceAll('{' + name + '}', value);
            return text;
        }""", translations)
        for name in ("expandable_table.js", "wysiwyg_editor.js", "app_core.js", "vendor/marked.umd.js",
                     "vendor/turndown.js", "markdown_editor.js", "file_browser.js", "tab_aufgaben.js", "project_links.js"):
            page.add_script_tag(content=(ROOT / "dashboard/static/js" / name).read_text())
        for name in ("style.css", "expandable_table.css", "aufgaben.css", "file_browser.css", "markdown_editor.css"):
            page.add_style_tag(content=(ROOT / "dashboard/static/css" / name).read_text())
        page.add_style_tag(content="body { padding: 25px; } * { animation: none !important; transition: none !important; }")
        page.evaluate("""async () => {
            currentUser = {id: 1, is_admin: false};
            cachedAreas = []; cachedUsers = [];
            aufgabenTable = new ExpandableTable({id: 'filesTest', apiEndpoint: '/api/tasks', expandable: true, showHeader: true,
                gridTemplate: '30px 330px 60px 130px 90px 150px 160px', columns: [
                    {field: 'name', label: 'Name', sortable: true, align: 'left'}, {field: 'id', label: 'ID', align: 'left'},
                    {field: 'status', label: 'Status'}, {field: 'priority', label: 'Priority'},
                    {field: 'assigned_to_name', label: 'Assigned to'}, {field: 'deadline', label: 'Deadline'}
                ], detailFields: [], filters: [
                    {id: 'typeFilter', type: 'select', field: 'task_type'},
                    {id: 'searchFilter', type: 'input', field: 'name', wildcard: true},
                ]}, 'table', {
                    onRowExpanded: onTaskRowExpanded, onRowCollapsed: onTaskRowCollapsed,
                    onRendered: updateExpandAllButton,
                });
            tables.filesTest = aufgabenTable;
            await aufgabenTable.loadData();
        }""")

        def expand():
            page.locator(".table-row > .expand-icon").click()
            expect(page.locator(".detail-edit")).to_be_visible()

        def drag(selector, dx=0, dy=0):
            handle = page.locator(selector)
            handle.scroll_into_view_if_needed()
            box = handle.bounding_box()
            x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
            page.mouse.move(x, y)
            page.mouse.down()
            page.mouse.move(x + dx, y + dy, steps=8)
            page.mouse.up()
            assert page.evaluate('document.body.style.userSelect') == ''
            assert page.evaluate('document.body.style.cursor') == ''

        # Local creation is offered when Nextcloud is not configured.
        expand()
        # Description height changes in both reading/editing modes without storage.
        columns = page.locator("#pdColumns_1")
        initial_height = columns.bounding_box()["height"]
        expect(page.locator("#pdSplit_1")).to_have_count(0)
        drag("#pdResize_1", dy=100)
        assert abs(columns.bounding_box()["height"] - initial_height - 100) < 2
        editor = page.locator("#wysiwygEditor_1")
        assert abs(editor.bounding_box()["height"] - columns.bounding_box()["height"]) < 2
        editor.get_by_role("button", name="Edit", exact=True).click()
        source_height = editor.locator("textarea").bounding_box()["height"]
        drag("#pdResize_1", dy=60)
        assert abs(editor.locator("textarea").bounding_box()["height"] - source_height - 60) < 2
        editor.get_by_role("button", name="Cancel", exact=True).click()
        saved_height = columns.bounding_box()["height"]
        page.locator(".table-row > .expand-icon").click()
        expand()
        assert abs(columns.bounding_box()["height"] - saved_height) < 2

        page.get_by_role("button", name="File Storage", exact=True).click()
        expect(page.locator("#fileStorageType")).to_have_value("local")
        expect(page.locator('#fileStorageType option[value="webdav"]')).to_be_disabled()
        expect(page.locator("#webdavStorageBrowser")).to_be_hidden()
        expect(page.locator("#localStorageSave")).to_be_visible()
        state["fail_next"] = True
        page.locator("#localStorageSave").click()
        expect(page.locator("#localStorageSave")).to_be_enabled()
        expect(page.locator(".modal-overlay")).to_be_visible()
        page.locator("#localStorageSave").click()
        expect(page.locator(".modal-overlay")).to_have_count(0)
        assert saved[-1] == {"file_storage_type": "local"}
        expand()
        expect(page.locator("#fileBrowserContainer_1")).to_be_visible()
        expect(page.locator("#fb-upload-1")).to_be_visible()
        expect(page.locator('.fb-header #fb-configure-1')).to_be_visible()
        expect(page.locator('button.nc-active')).to_have_count(0)
        expect(page.locator(".fb-tree-name")).to_have_text("Plan.txt")

        # The divider changes both widths; both panes still share the same height.
        left = page.locator(".project-detail-left")
        right = page.locator(".project-detail-right")
        before_left, before_right = left.bounding_box(), right.bounding_box()
        drag("#pdSplit_1", dx=120)
        assert abs(left.bounding_box()["width"] - before_left["width"] - 120) < 2
        assert abs(right.bounding_box()["width"] - before_right["width"] + 120) < 2
        saved_width = left.bounding_box()["width"]
        page.locator(".table-row > .expand-icon").click()
        expand()
        assert abs(left.bounding_box()["width"] - saved_width) < 2
        drag("#pdResize_1", dy=-80)
        assert abs(columns.bounding_box()["height"] - saved_height + 80) < 2
        assert abs(left.bounding_box()["height"] - right.bounding_box()["height"]) < 2
        page.locator("#pdSplit_1").focus()
        page.keyboard.press("ArrowLeft")
        assert left.bounding_box()["width"] < saved_width

        # Narrow screens stack the panes and retain a working height handle.
        page.set_viewport_size({"width": 800, "height": 1000})
        expect(page.locator("#pdSplit_1")).to_be_hidden()
        assert right.bounding_box()["y"] > left.bounding_box()["y"]
        before_height = columns.bounding_box()["height"]
        drag("#pdResize_1", dy=50)
        assert abs(columns.bounding_box()["height"] - before_height - 50) < 2
        page.set_viewport_size({"width": 1440, "height": 1000})

        # WebDAV browsing handles names containing quotes and HTML as plain text.
        page.evaluate("ncConfigured = true")
        page.locator('.fb-header').get_by_role('button', name='Configure file storage').click()
        before_remove = len(saved)
        page.locator('#storageRemove').click()
        expect(page.locator('.modal-body')).to_contain_text('including all files and subfolders')
        assert len(saved) == before_remove
        page.locator('#storageDeleteCancel').click()
        expect(page.locator('#fileStorageType')).to_have_value('local')
        assert len(saved) == before_remove
        page.locator("#fileStorageType").select_option("webdav")
        expect(page.locator("#localStorageSave")).to_be_hidden()
        page.locator(".nc-dir-name").click()
        expect(page.locator("#ncBrowseBc")).to_contain_text(directory_name)
        expect(page.locator(".nc-select-btn")).to_be_visible()
        page.locator("#fileStorageType").select_option("local")
        expect(page.locator(".nc-select-btn")).to_have_count(0)
        page.locator("#fileStorageType").select_option("webdav")
        page.locator(".nc-dir-select-btn").click()
        expect(page.locator(".modal-overlay")).to_have_count(0)
        assert saved[-1] == {"file_storage_type": "webdav", "nextcloud_path": directory_name}
        expand()
        expect(page.locator("#fileBrowserContainer_1")).to_be_visible()
        page.locator('.fb-header').get_by_role('button', name='Configure file storage').click()
        page.locator("#storageRemove").click()
        expect(page.locator(".modal-overlay")).to_have_count(0)
        assert saved[-1] == {"file_storage_type": "none"}
        expand()
        expect(page.locator("#fileBrowserContainer_1")).to_have_count(0)

        # Ordinary tasks also offer local storage without Nextcloud.
        task["task_type"] = "aufgabe"
        page.evaluate("async () => { ncConfigured = false; await aufgabenTable.loadData(); }")
        expand()
        page.get_by_role("button", name="File Storage", exact=True).click()
        page.locator("#localStorageSave").click()
        expect(page.locator(".modal-overlay")).to_have_count(0)
        expand()
        expect(page.locator("#fileBrowserContainer_1")).to_be_visible()

        # Local removal requires an explicit confirmation and supports retrying errors.
        page.locator('.fb-header').get_by_role('button', name='Configure file storage').click()
        page.locator('#storageRemove').click()
        state['fail_next'] = True
        page.locator('#storageDeleteConfirm').click()
        expect(page.locator('#storageDeleteConfirm')).to_be_enabled()
        expect(page.locator('.modal-overlay')).to_be_visible()
        page.locator('#storageDeleteConfirm').click()
        expect(page.locator('.modal-overlay')).to_have_count(0)
        assert saved[-1] == {'file_storage_type': 'none'}
        expand()
        expect(page.locator('#fileBrowserContainer_1')).to_have_count(0)
        page.get_by_role('button', name='File Storage', exact=True).click()
        page.locator('#localStorageSave').click()
        expect(page.locator('.modal-overlay')).to_have_count(0)

        # Read-only users see files, but no upload, rename, delete or mapping actions.
        state["can_write"] = False
        task.update(created_by=2, is_team_member=True, can_edit_status=False)
        page.evaluate("async () => { await aufgabenTable.loadData(); }")
        expand()
        expect(page.locator(".fb-tree-name")).to_have_text("Plan.txt")
        expect(page.locator("#fb-upload-1")).to_be_hidden()
        expect(page.locator("#fb-mkdir-1")).to_be_hidden()
        expect(page.locator('#fb-configure-1')).to_have_count(0)
        expect(page.locator("button.nc-active")).to_have_count(0)
        page.locator(".fb-tree-name").click(button="right")
        expect(page.locator('.fb-ctx-item[data-action="download"]')).to_be_visible()
        expect(page.locator('.fb-ctx-item[data-action="rename"]')).to_have_count(0)
        expect(page.locator('.fb-ctx-item[data-action="delete"]')).to_have_count(0)
        page.mouse.click(5, 5)

        # An open project shows only its own row/details, retaining its subtask header.
        task.update(task_type='projekt', created_by=1, is_team_member=False, can_edit_status=True)
        extra_tasks.extend([
            dict(task, id=2, name='Second project', file_storage_type='none'),
            dict(task, id=3, name='Ordinary task', task_type='aufgabe', file_storage_type='none'),
        ])
        page.evaluate('async () => { await aufgabenTable.loadData(); }')
        header = page.locator('#table > .table-header')
        visible_rows = page.locator('#table > .table-body > .table-row-wrapper:visible')
        toggle_all = page.locator('#toggleAllBtn')

        def toggle_row(task_id):
            page.locator(f'#table [data-row-id="{task_id}"] > .table-row > .expand-icon').click()

        expect(header).to_be_visible()
        expect(visible_rows).to_have_count(3)
        toggle_row(1)
        expect(header).to_be_hidden()
        expect(visible_rows).to_have_count(1)
        expect(visible_rows).to_have_attribute('data-row-id', '1')
        expect(page.locator('#subtaskContainer_1 thead')).to_be_visible()
        expect(page.locator('#fileBrowserContainer_1')).to_be_visible()
        expect(toggle_all).to_have_text('Collapse all')
        toggle_row(1)
        expect(header).to_be_visible()
        expect(visible_rows).to_have_count(3)
        expect(toggle_all).to_have_text('Expand all')

        # Ordinary tasks leave the list visible; another project can then be opened.
        toggle_row(3)
        expect(header).to_be_visible()
        expect(visible_rows).to_have_count(3)
        toggle_row(3)
        toggle_row(2)
        expect(visible_rows).to_have_count(1)
        expect(visible_rows).to_have_attribute('data-row-id', '2')
        expect(header).to_be_hidden()
        toggle_row(2)

        # Returning to the list preserves filtering and sorting.
        page.evaluate("() => { aufgabenTable.setFilter('typeFilter', 'projekt'); aufgabenTable.sortBy('name'); aufgabenTable.sortBy('name'); }")
        assert visible_rows.evaluate_all('(rows) => rows.map(row => row.dataset.rowId)') == ['2', '1']
        toggle_row(1)
        toggle_row(1)
        expect(header).to_be_visible()
        assert visible_rows.evaluate_all('(rows) => rows.map(row => row.dataset.rowId)') == ['2', '1']

        # Filtering, empty results and reloads clear the focused view and toolbar state.
        toggle_row(2)
        page.evaluate("applySearchFilter('no matching task')")
        expect(visible_rows).to_have_count(0)
        expect(page.locator('.table-no-data')).to_be_visible()
        expect(toggle_all).to_have_text('Expand all')
        page.evaluate("applySearchFilter('File storage')")
        expect(header).to_be_visible()
        expect(visible_rows).to_have_count(1)
        toggle_row(1)
        page.evaluate('async () => { await aufgabenTable.loadData(); }')
        expect(header).to_be_visible()
        expect(toggle_all).to_have_text('Expand all')
        page.evaluate("() => { applySearchFilter(''); aufgabenTable.setFilter('typeFilter', ''); }")

        # Bulk expansion also shows exactly one project; collapsing restores all rows.
        toggle_all.click()
        expect(visible_rows).to_have_count(1)
        expect(header).to_be_hidden()
        expect(page.locator('.project-focus.expanded')).to_have_count(1)
        expect(toggle_all).to_have_text('Collapse all')
        toggle_all.click()
        expect(header).to_be_visible()
        expect(visible_rows).to_have_count(3)
        expect(page.locator('#table .table-row-wrapper.expanded')).to_have_count(0)
        expect(toggle_all).to_have_text('Expand all')
        assert errors == [], errors
        browser.close()
    print("File storage browser checks passed: local/WebDAV configuration, deletion confirmation/cancel/retry, permissions, resizing, narrow screens and project focus with list restoration, filters, sorting, reloads and bulk expansion.")


if __name__ == "__main__":
    main()
