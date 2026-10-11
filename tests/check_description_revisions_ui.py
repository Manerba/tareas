"""Real editor assets with isolated mock APIs: revisions and retained conflict drafts."""

import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def revision(text):
    return hashlib.sha256(('markdown\0' + text).encode()).hexdigest()


def main():
    stored = {'/api/tasks/1': '# Project', '/api/subtasks/2': '# Subtask'}
    requests = []
    handoff = {'content': 'Own handoff', 'can_write': True, 'expired': False}
    errors = []
    delayed = {'next': False, 'response': None}

    def serve(route):
        path = urlparse(route.request.url).path
        if path == '/':
            route.fulfill(content_type='text/html', body='<div id="project"></div><div id="subtask"></div>')
            return
        data, status = {}, 200
        if path in stored and route.request.method == 'PUT':
            body = route.request.post_data_json
            requests.append((path, body))
            if body.get('description_revision') != revision(stored[path]):
                data, status = {'detail': 'description.conflict'}, 409
            else:
                stored[path] = body['description']
                data = {'description_revision': revision(stored[path])}
        elif path == '/api/tasks/1/note-entries/3':
            if route.request.method == 'PUT':
                body = route.request.post_data_json
                requests.append((path, body))
                if handoff['expired']:
                    data, status = {'detail': 'handoff.editWindowExpired'}, 403
                elif body['revision'] != revision(handoff['content']):
                    data, status = {'detail': 'handoff.conflict'}, 409
                else:
                    handoff['content'] = body['content']
                    data = {'revision': revision(handoff['content'])}
            else:
                data = dict(id=3, task_id=1, user_id=2, user_name='Author', created_at='11.10.2026 12:00',
                            format='markdown', content=handoff['content'], revision=revision(handoff['content']),
                            can_write=handoff['can_write'])
        if delayed['next'] and path in stored and route.request.method == 'PUT':
            delayed.update(next=False, response=(route, status, data))
            return
        route.fulfill(status=status, content_type='application/json', body=json.dumps(data))

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.route('http://tareas.test/**', serve)
        page.goto('http://tareas.test/')
        page.evaluate('(translations) => window.t = key => translations[key] || key', json.loads((ROOT/'dashboard/static/i18n/en.json').read_text()))
        for name in ['app_core.js', 'vendor/marked.umd.js', 'vendor/turndown.js', 'markdown_editor.js', 'text_file_editor.js', 'tab_aufgaben.js']:
            page.add_script_tag(content=(ROOT/'dashboard/static/js'/name).read_text())
        for name in ['style.css', 'markdown_editor.css', 'text_file_editor.css']:
            page.add_style_tag(content=(ROOT/'dashboard/static/css'/name).read_text())

        def mount(target, url, *, source=None, replace_node=False, reuse_model=False):
            source = stored[url] if source is None else source
            page.evaluate('''({target, url, text, revision, replaceNode, reuseModel}) => {
                if (replaceNode) {
                    const fresh = document.createElement('div');
                    fresh.id = target;
                    document.getElementById(target).replaceWith(fresh);
                }
                window.testEditors ??= {};
                window.testModels ??= {};
                const model = reuseModel ? window.testModels[target] : {
                    description: text, description_format:'markdown', description_revision:revision
                };
                window.testModels[target] = model;
                window.testEditors[target] = createMarkdownField(target, model.description, {
                    url, model
                });
            }''', dict(target=target, url=url, text=source, revision=revision(source),
                       replaceNode=replace_node, reuseModel=reuse_model))

        # The server commits before its delayed response arrives. Rebuilding the
        # row from cached data must transfer the eventual successful revision to
        # the currently displayed editor, not only to its detached predecessor.
        for target, url in (('project', '/api/tasks/1'), ('subtask', '/api/subtasks/2')):
            for replace_node, edit_source in ((True, True), (False, True), (True, False), (False, False)):
                base = stored[url]
                first = f'{target} first save; replaced node={replace_node}' if edit_source else base
                second = first + '\nFurther edit after acknowledgment'
                mount(target, url)
                editor = page.locator('#' + target)
                editor.get_by_role('button', name='Edit', exact=True).click()
                # Saving unchanged source has no input event or cached draft.
                if edit_source:
                    editor.locator('textarea').fill(first)
                page.evaluate('target => window.previousSavingEditor = window.testEditors[target]', target)
                delayed['next'] = True
                with page.expect_request(lambda request: urlparse(request.url).path == url and request.method == 'PUT'):
                    editor.get_by_role('button', name='Save', exact=True).click()
                # A browser turn ensures the intercepted route callback ran.
                page.evaluate('() => true')
                assert delayed['response'] is not None
                mount(target, url, source=base, replace_node=replace_node)
                expect(editor.locator('textarea')).to_have_value(first)
                expect(editor.locator('textarea')).to_be_disabled()
                expect(editor.get_by_role('button', name='Save', exact=True)).to_be_disabled()
                expect(editor.get_by_role('button', name='Cancel', exact=True)).to_be_disabled()
                route, status, data = delayed['response']
                delayed['response'] = None
                assert status == 200
                route.fulfill(status=status, content_type='application/json', body=json.dumps(data))
                page.wait_for_function('() => !window.previousSavingEditor.saving')
                expect(editor.locator('textarea')).to_be_hidden()
                expect(editor.locator('.markdown-body')).to_have_text(first)
                assert page.evaluate('target => window.testEditors[target].revision', target) == revision(first)
                # The remounted editor uses a NEW row model. The response must
                # update that model too, or a later local row rebuild can revive
                # the old revision after the temporary pending state is gone.
                self_model = page.evaluate('target => window.testModels[target]', target)
                assert self_model['description'] == first
                assert self_model['description_revision'] == revision(first)
                mount(target, url, replace_node=replace_node, reuse_model=True)
                expect(editor.locator('textarea')).to_have_value(first)
                if editor.locator('textarea').is_hidden():
                    editor.get_by_role('button', name='Edit', exact=True).click()
                editor.locator('textarea').fill(second)
                with page.expect_response(lambda response: urlparse(response.url).path == url and response.request.method == 'PUT') as saved:
                    editor.get_by_role('button', name='Save', exact=True).click()
                assert saved.value.status == 200, (
                    f'{target}, replace_node={replace_node}, edit_source={edit_source}: remounted editor sent '
                    f'{requests[-1][1]["description_revision"]} instead of {revision(first)}; '
                    f'false HTTP {saved.value.status} after its own successful save'
                )
                expect(editor.locator('textarea')).to_be_hidden()
                assert stored[url] == second

                # A real competing writer still rejects the pending request.
                # The error belongs to the current editor; its draft keeps the
                # original revision even if the remount fetched the newer base.
                original_revision = revision(second)
                draft = f'{target} local conflicting draft; replaced node={replace_node}'
                foreign = f'{target} external update; replaced node={replace_node}'
                editor.get_by_role('button', name='Edit', exact=True).click()
                editor.locator('textarea').fill(draft)
                stored[url] = foreign
                page.evaluate('target => window.previousSavingEditor = window.testEditors[target]', target)
                delayed['next'] = True
                with page.expect_request(lambda request: urlparse(request.url).path == url and request.method == 'PUT'):
                    editor.get_by_role('button', name='Save', exact=True).click()
                page.evaluate('() => true')
                assert delayed['response'] is not None
                mount(target, url, replace_node=replace_node)
                expect(editor.locator('textarea')).to_have_value(draft)
                expect(editor.locator('textarea')).to_be_disabled()
                route, status, data = delayed['response']
                delayed['response'] = None
                assert status == 409
                route.fulfill(status=status, content_type='application/json', body=json.dumps(data))
                page.wait_for_function('() => !window.previousSavingEditor.saving')
                expect(editor.locator('.markdown-error')).to_contain_text('Your draft is retained')
                expect(editor.locator('textarea')).to_have_value(draft)
                expect(editor.locator('textarea')).to_be_enabled()
                expect(editor.get_by_role('button', name='Save', exact=True)).to_be_enabled()
                assert stored[url] == foreign
                with page.expect_response(lambda response: urlparse(response.url).path == url and response.request.method == 'PUT') as rejected:
                    editor.get_by_role('button', name='Save', exact=True).click()
                assert rejected.value.status == 409
                assert requests[-1][1]['description_revision'] == original_revision
                expect(editor.locator('textarea')).to_have_value(draft)
                # Start the next scenario from an explicitly discarded draft.
                editor.get_by_role('button', name='Cancel', exact=True).click()

        for target, url in (('project','/api/tasks/1'), ('subtask','/api/subtasks/2')):
            mount(target, url)
            editor = page.locator('#' + target)
            initial = revision(stored[url])
            editor.get_by_role('button', name='Edit', exact=True).click()
            editor.locator('textarea').fill('## My draft')
            stored[url] = 'Concurrent change'
            editor.get_by_role('button', name='Save', exact=True).click()
            expect(editor.locator('.markdown-error')).to_contain_text('Your draft is retained')
            expect(editor.locator('textarea')).to_have_value('## My draft')
            assert requests[-1][1]['description_revision'] == initial
            assert stored[url] == 'Concurrent change'
            # Rebuilding a row after a reload must preserve the draft's old revision.
            mount(target, url)
            expect(editor.locator('textarea')).to_have_value('## My draft')
            editor.get_by_role('button', name='Save', exact=True).click()
            expect(editor.locator('.markdown-error')).to_be_visible()
            assert requests[-1][1]['description_revision'] == initial
            # Explicit discard picks up the loaded current base and its revision.
            editor.get_by_role('button', name='Cancel', exact=True).click()
            editor.get_by_role('button', name='Edit', exact=True).click()
            editor.locator('textarea').fill('Resolved')
            editor.get_by_role('button', name='Save', exact=True).click()
            expect(editor.locator('textarea')).to_be_hidden()
            assert stored[url] == 'Resolved'
            editor.get_by_role('button', name='Edit', exact=True).click()
            editor.locator('textarea').fill('Second save')
            editor.get_by_role('button', name='Save', exact=True).click()
            expect(editor.locator('textarea')).to_be_hidden()
            assert stored[url] == 'Second save'

        page.evaluate('openHandoffEditor(1, null, 3)')
        dialog = page.locator('.text-file-overlay')
        editor = dialog.locator('.markdown-editor')
        editor.get_by_role('button', name='Edit', exact=True).click()
        editor.locator('textarea').fill('Correct own handoff')
        editor.get_by_role('button', name='Save', exact=True).click()
        expect(editor.locator('textarea')).to_be_hidden()
        assert handoff['content'] == 'Correct own handoff'
        editor.get_by_role('button', name='Edit', exact=True).click()
        editor.locator('textarea').fill('Draft at timeout')
        handoff['expired'] = True
        editor.get_by_role('button', name='Save', exact=True).click()
        expect(editor.locator('.markdown-error')).to_contain_text('five-minute window')
        expect(editor.locator('textarea')).to_have_value('Draft at timeout')
        assert handoff['content'] == 'Correct own handoff'
        assert not errors, errors
        browser.close()
    print('Description project/subtask pending saves across same/replaced DOM nodes, pending error/draft retention, real revision conflicts, repeat saves and own-handoff expiry: OK')


if __name__ == '__main__':
    main()
