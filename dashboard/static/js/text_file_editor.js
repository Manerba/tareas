/** Native Markdown-/Textdateien: derselbe Editor im Dialog und eigenen Fenster. */
let textFileSequence = 0;
let activeTextFileDialog = null;
const handoffWindows = new Map();

function textFileError(error) {
    return /^(textFile|handoff)\./.test(error.message || '') ? t(error.message) : (error.message || t('common.error'));
}

function handoffPageUrl(taskId, handoff) {
    const params = new URLSearchParams({ taskId, entryId: handoff.entryId });
    if (handoff.subtaskId !== null) params.set('subtaskId', handoff.subtaskId);
    return `/handoff?${params}`;
}

function handoffIdentity(taskId, handoff) {
    return { taskId: Number(taskId), subtaskId: handoff.subtaskId === null ? null : Number(handoff.subtaskId), entryId: Number(handoff.entryId) };
}

function watchHandoffWindow(popup, taskId, handoff) {
    for (const existing of handoffWindows.keys()) if (existing.closed) handoffWindows.delete(existing);
    if (popup) handoffWindows.set(popup, handoffIdentity(taskId, handoff));
}

window.addEventListener('message', event => {
    const expected = handoffWindows.get(event.source);
    if (event.origin !== location.origin || !expected || event.data?.type !== 'handoff-saved') return;
    if (!Object.keys(expected).every(key => expected[key] === event.data[key])) return;
    window.dispatchEvent(new CustomEvent('handoff-saved', { detail: expected }));
});

class TextFileEditor {
    constructor(shell, taskId, path, { overlay = null, onSaved = null, handoff = null } = {}) {
        this.shell = shell;
        this.taskId = taskId;
        this.path = path;
        this.overlay = overlay;
        this.onSaved = onSaved;
        this.handoff = handoff;
        this.editorId = `text-file-editor-${++textFileSequence}`;
        this.previousFocus = document.activeElement;
        this.controller = new AbortController();
        this.closed = false;
        this.busy = false;
        const titleId = `${this.editorId}-title`;
        shell.innerHTML = `
            <header class="text-file-header">
                <h1 class="text-file-title" id="${titleId}"></h1>
                ${handoff ? '' : `<a class="control-btn" data-file-download>${t('files.download')}</a>`}
                ${overlay ? `<button type="button" class="control-btn" data-file-expand disabled>${t('textFile.expand')}</button>` : ''}
                <button type="button" class="control-btn" data-file-close>${t('common.close')}</button>
            </header>
            <p class="text-file-meta" hidden></p>
            <p class="text-file-status" role="alert" hidden></p>
            <div class="text-file-content"><div id="${this.editorId}">${t('common.loading')}</div></div>`;
        shell.querySelector('.text-file-title').textContent = path;
        shell.setAttribute('aria-labelledby', titleId);
        if (overlay) {
            shell.setAttribute('role', 'dialog');
            shell.setAttribute('aria-modal', 'true');
        }
        this.status = shell.querySelector('.text-file-status');
        this.expandButton = shell.querySelector('[data-file-expand]');
        this.closeButton = shell.querySelector('[data-file-close]');
        const download = shell.querySelector('[data-file-download]');
        if (download) {
            download.href = `/api/tasks/${encodeURIComponent(taskId)}/files/download?path=${encodeURIComponent(path)}`;
            download.download = '';
        }
        this.closeButton.addEventListener('click', () => this.close());
        this.expandButton?.addEventListener('click', () => this.expand());
        this.beforeUnload = event => {
            if (this.isDirty() || this.editor?.saving) {
                event.preventDefault();
                event.returnValue = '';
            }
        };
        this.onKeyDown = event => {
            if (!overlay) return;
            if (event.key === 'Escape') {
                event.preventDefault();
                event.stopPropagation();
                this.close();
            }
            if (event.key === 'Tab') {
                const elements = [...shell.querySelectorAll('button, a[href], textarea')]
                    .filter(element => !element.disabled && element.getClientRects().length);
                const first = elements[0], last = elements.at(-1);
                if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
                else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
            }
        };
        window.addEventListener('beforeunload', this.beforeUnload);
        shell.addEventListener('keydown', this.onKeyDown);
        this.closeButton.focus();
    }

    endpoint() {
        if (this.handoff) {
            const subtask = this.handoff.subtaskId === null ? '' : `/subtasks/${encodeURIComponent(this.handoff.subtaskId)}`;
            return `/api/tasks/${encodeURIComponent(this.taskId)}${subtask}/note-entries/${encodeURIComponent(this.handoff.entryId)}`;
        }
        return `/api/tasks/${encodeURIComponent(this.taskId)}/files/text?path=${encodeURIComponent(this.path)}`;
    }

    async request(options = {}) {
        const response = await fetch(this.endpoint(), { ...options, signal: this.controller.signal });
        const data = await response.json();
        if (!response.ok) {
            throw new Error(typeof data.detail === 'string' ? data.detail : t('common.saveError'));
        }
        return data;
    }

    async load(snapshot = null) {
        try {
            const data = await this.request();
            if (this.closed) return false;
            if (this.handoff) {
                const title = `#${data.id} ${data.user_name || `User ${data.user_id}`}`;
                this.shell.querySelector('.text-file-title').textContent = title;
                const meta = this.shell.querySelector('.text-file-meta');
                meta.textContent = data.created_at || '';
                meta.hidden = !data.created_at;
                if (!this.overlay) document.title = `${title} – Tareas`;
            }
            // Die Revision des Entwurfs bleibt erhalten: fremde Aenderungen nicht ueberschreiben.
            const draft = snapshot?.editing ? snapshot : null;
            this.revision = draft ? draft.revision : data.revision;
            this.editor = new MarkdownEditor(this.editorId, draft ? draft.source : data.content, {
                label: data.can_write ? (this.handoff ? t('detail.noteHistory') : (data.format === 'markdown' ? 'Markdown' : t('textFile.text'))) : t('editor.readonly'),
                format: this.handoff && !draft ? data.format : 'markdown',
                plainText: !this.handoff && data.format !== 'markdown',
                readOnly: !data.can_write,
                saveErrorMessage: textFileError,
                onSave: async content => {
                    const result = await this.request({
                        method: 'PUT', headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ content, revision: this.revision }),
                    });
                    this.revision = result.revision;
                    this.onSaved?.();
                    if (this.handoff) {
                        const detail = handoffIdentity(this.taskId, this.handoff);
                        window.dispatchEvent(new CustomEvent('handoff-saved', { detail }));
                        window.opener?.postMessage({ type: 'handoff-saved', ...detail }, location.origin);
                    }
                },
            });
            // Preview-Links verlassen weder den Dialog noch einen offenen Entwurf.
            this.editor.preview.addEventListener('click', event => {
                const link = event.target.closest('a[href]');
                if (!link) return;
                event.preventDefault();
                window.open(link.href, '_blank', 'noopener,noreferrer');
            });
            if (draft) {
                this.editor.input.value = draft.content;
                this.editor.editing = true;
                MarkdownEditor.drafts.set(this.editorId, draft.content);
                this.editor.updateView();
                this.editor.input.focus();
                this.editor.input.setSelectionRange(draft.selectionStart, draft.selectionEnd);
                this.editor.input.scrollTop = draft.scrollTop;
                if (!data.can_write) this.showError(t('textFile.readOnlyDraft'));
            }
            if (this.expandButton) this.expandButton.disabled = false;
            return true;
        } catch (error) {
            if (!this.closed) {
                this.shell.querySelector('.text-file-content').replaceChildren();
                this.showError(textFileError(error));
            }
            return false;
        }
    }

    isDirty() {
        const normalize = text => text.replace(/\r\n?/g, '\n');
        return this.editor?.editing && normalize(this.editor.input.value) !== normalize(this.editor.source);
    }

    showError(message) {
        this.status.textContent = message;
        this.status.hidden = false;
    }

    close() {
        if (this.busy || this.editor?.saving) return false;
        if (this.isDirty() && !window.confirm(t('textFile.discard'))) return false;
        this.destroy();
        if (!this.overlay) window.close();
        return true;
    }

    destroy() {
        this.closed = true;
        this.controller.abort();
        MarkdownEditor.drafts.delete(this.editorId);
        window.removeEventListener('beforeunload', this.beforeUnload);
        this.shell.removeEventListener('keydown', this.onKeyDown);
        this.overlay?.remove();
        if (activeTextFileDialog === this) activeTextFileDialog = null;
        if (this.previousFocus?.isConnected) this.previousFocus.focus();
    }

    expand() {
        if (!this.editor || this.busy || this.editor.saving) return;
        const token = Array.from(crypto.getRandomValues(new Uint32Array(4)), value => value.toString(16)).join('-');
        const snapshot = {
            source: this.editor.source, content: this.editor.input.value, editing: this.editor.editing,
            revision: this.revision, selectionStart: this.editor.input.selectionStart,
            selectionEnd: this.editor.input.selectionEnd, scrollTop: this.editor.input.scrollTop,
        };
        // Entwuerfe nur im Speicher uebertragen, nie in URLs oder localStorage schreiben.
        const pageUrl = this.handoff ? handoffPageUrl(this.taskId, this.handoff)
            : `/text-editor?taskId=${encodeURIComponent(this.taskId)}&path=${encodeURIComponent(this.path)}`;
        const url = `${pageUrl}#${token}`;
        let popup;
        const finish = (success, message = '') => {
            clearTimeout(timeout);
            window.removeEventListener('message', receive);
            this.busy = false;
            this.editor.saving = false;
            this.editor.updateView();
            this.closeButton.disabled = false;
            this.expandButton.disabled = false;
            if (success) this.destroy();
            else {
                popup?.close();
                this.showError(message || t('textFile.popupFailed'));
            }
        };
        const receive = event => {
            if (event.origin !== location.origin || event.source !== popup || event.data?.token !== token) return;
            if (event.data.type === 'text-file-ready') popup.postMessage({ type: 'text-file-state', token, snapshot }, location.origin);
            if (event.data.type === 'text-file-loaded') finish(true);
            if (event.data.type === 'text-file-failed') finish(false, event.data.message);
        };
        window.addEventListener('message', receive);
        const timeout = setTimeout(() => finish(false), 30000);
        popup = window.open(url, '_blank', 'popup,width=1100,height=820');
        if (!popup) { finish(false); return; }
        if (this.handoff) watchHandoffWindow(popup, this.taskId, this.handoff);
        this.busy = true;
        this.editor.saving = true;
        this.editor.updateView();
        this.closeButton.disabled = true;
        this.expandButton.disabled = true;
        this.status.hidden = true;
    }
}

function openTextFileEditor(taskId, path, onSaved, handoff = null) {
    if (activeTextFileDialog && !activeTextFileDialog.close()) return;
    const overlay = document.createElement('div');
    overlay.className = 'text-file-overlay';
    const shell = document.createElement('section');
    shell.className = 'text-file-shell';
    overlay.appendChild(shell);
    document.body.appendChild(overlay);
    const editor = new TextFileEditor(shell, taskId, path, { overlay, onSaved, handoff });
    activeTextFileDialog = editor;
    editor.load();
}

function openHandoffEditor(taskId, subtaskId, entryId, separateWindow = false) {
    const handoff = { subtaskId, entryId };
    if (separateWindow) {
        const popup = window.open(handoffPageUrl(taskId, handoff), '_blank', 'popup,width=1100,height=820');
        if (popup) watchHandoffWindow(popup, taskId, handoff);
        else showNotification(t('handoff.popupFailed'), 'error');
    } else {
        openTextFileEditor(taskId, `#${entryId}`, null, handoff);
    }
}

document.addEventListener('DOMContentLoaded', async () => {
    const shell = document.getElementById('textFilePage');
    if (!shell) return;
    const params = new URLSearchParams(location.search);
    const taskId = params.get('taskId'), path = params.get('path');
    const handoff = location.pathname === '/handoff' ? { subtaskId: params.get('subtaskId'), entryId: params.get('entryId') } : null;
    const validHandoff = handoff && /^[1-9]\d*$/.test(handoff.entryId || '')
        && (handoff.subtaskId === null || /^[1-9]\d*$/.test(handoff.subtaskId));
    if (!/^[1-9]\d*$/.test(taskId || '') || (handoff ? !validHandoff : !path)) {
        shell.textContent = t('editor.missingParams');
        return;
    }
    const title = handoff ? `#${handoff.entryId}` : path.split('/').pop();
    document.title = `${title} – Tareas`;
    const editor = new TextFileEditor(shell, taskId, handoff ? title : path, { handoff });
    const token = location.hash.slice(1);
    if (token && window.opener) {
        const receive = async event => {
            if (event.origin !== location.origin || event.source !== window.opener || event.data?.token !== token || event.data.type !== 'text-file-state') return;
            clearTimeout(timeout);
            window.removeEventListener('message', receive);
            const loaded = await editor.load(event.data.snapshot);
            window.opener.postMessage({ type: loaded ? 'text-file-loaded' : 'text-file-failed', token,
                message: loaded ? '' : editor.status.textContent }, location.origin);
            history.replaceState(null, '', location.pathname + location.search);
        };
        window.addEventListener('message', receive);
        const timeout = setTimeout(() => {
            window.removeEventListener('message', receive);
            editor.showError(t('textFile.popupFailed'));
        }, 10000);
        window.opener.postMessage({ type: 'text-file-ready', token }, location.origin);
    } else {
        await editor.load();
    }
});
