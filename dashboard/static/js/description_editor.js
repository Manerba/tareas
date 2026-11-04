/** Beschreibungen mit Entwurf/Revision in ein eigenes Fenster uebergeben. */
const descriptionEditors = new Map();
const descriptionWindows = new Map();
const descriptionTransfers = new Set();
const descriptionRefreshPending = new Set();
const descriptionRefreshSequence = new Map();

function descriptionIdentity(taskId, description) {
    return { taskId: Number(taskId), subtaskId: description.subtaskId == null ? null : Number(description.subtaskId) };
}

function descriptionKey(target) {
    return `${target.taskId}:${target.subtaskId ?? ''}`;
}

function descriptionEndpoint(target) {
    return `/api/tasks/${target.taskId}${target.subtaskId === null ? '' : `/subtasks/${target.subtaskId}`}/description`;
}

function registerDescriptionEditor(editor, target, model) {
    target = descriptionIdentity(target.taskId, target);
    descriptionEditors.set(editor.containerId, { editor, target, model });
    editor.transferring = descriptionTransfers.has(editor.containerId);
    editor.options.onSaveSettled = () => {
        if (descriptionRefreshPending.delete(editor.containerId)) refreshDescription(target);
    };
    editor.updateView();
}

function updateDescriptionModel(model, data) {
    if (!model) return;
    model.description = data.content;
    model.description_format = data.format;
    model.description_revision = data.revision;
}

async function refreshDescription(target) {
    const key = descriptionKey(target);
    const sequence = (descriptionRefreshSequence.get(key) || 0) + 1;
    descriptionRefreshSequence.set(key, sequence);
    try {
        const response = await fetch(descriptionEndpoint(target));
        if (!response.ok) throw new Error(t('common.loadError'));
        const data = await response.json();
        if (descriptionRefreshSequence.get(key) !== sequence) return;
        // Auch Modelle nicht aufgeklappter Zeilen aktualisieren.
        const rows = typeof aufgabenTable === 'undefined' ? [] : (aufgabenTable?.data || []);
        for (const row of rows) {
            const matches = target.subtaskId === null ? row.id === target.taskId
                : row._subtask_id === target.subtaskId && row._project_id === target.taskId;
            if (matches) updateDescriptionModel(row, data);
        }
        const subtasks = document.getElementById(`subtaskContainer_${target.taskId}`)?._subtasksData || [];
        if (target.subtaskId !== null) updateDescriptionModel(subtasks.find(row => row.id === target.subtaskId), data);
        for (const { editor, target: registered, model } of descriptionEditors.values()) {
            if (descriptionKey(registered) !== key) continue;
            updateDescriptionModel(model, data);
            if (editor.saving || editor.transferring) {
                descriptionRefreshPending.add(editor.containerId);
                continue;
            }
            editor.source = markdownSource(data.content, data.format);
            editor.options.revision = data.revision;
            // Offene Entwuerfe behalten ihre Basisrevision und ihren Inhalt.
            if (!editor.editing && !MarkdownEditor.drafts.has(editor.containerId)) {
                editor.revision = data.revision;
                editor.input.value = editor.source;
            }
            editor.updateView();
        }
    } catch (error) {
        showNotification(error.message || t('common.loadError'), 'error');
    }
}

window.addEventListener('message', event => {
    const target = descriptionWindows.get(event.source);
    if (event.origin !== location.origin || !target || event.data?.type !== 'description-saved') return;
    if (!Object.keys(target).every(key => target[key] === event.data[key])) return;
    refreshDescription(target);
});

function expandDescription(editor, target) {
    if (editor.saving || editor.transferring) return;
    target = descriptionIdentity(target.taskId, target);
    const containerId = editor.containerId;
    const token = Array.from(crypto.getRandomValues(new Uint32Array(4)), value => value.toString(16)).join('-');
    const snapshot = {
        source: editor.source, content: editor.input.value,
        editing: editor.editing || MarkdownEditor.drafts.has(containerId),
        revision: editor.revision, selectionStart: editor.input.selectionStart,
        selectionEnd: editor.input.selectionEnd, scrollTop: editor.input.scrollTop,
    };
    const params = new URLSearchParams({ taskId: target.taskId });
    if (target.subtaskId !== null) params.set('subtaskId', target.subtaskId);
    let popup, finished = false;
    const finish = (success, message = '') => {
        if (finished) return;
        finished = true;
        clearTimeout(timeout);
        window.removeEventListener('message', receive);
        descriptionTransfers.delete(containerId);
        const current = descriptionEditors.get(containerId)?.editor || editor;
        current.transferring = false;
        if (success) {
            // Erst nach bestaetigter Uebernahme den inline gehaltenen Entwurf freigeben.
            if (!MarkdownEditor.drafts.has(containerId) || MarkdownEditor.drafts.get(containerId) === snapshot.content) {
                MarkdownEditor.drafts.delete(containerId);
                MarkdownEditor.draftRevisions.delete(containerId);
                current.input.value = current.source;
                current.editing = false;
                current.error.hidden = true;
            }
        } else {
            descriptionWindows.delete(popup);
            popup?.close();
            current.error.textContent = message || t('textFile.popupFailed');
            current.error.hidden = false;
        }
        current.updateView();
        if (descriptionRefreshPending.delete(containerId)) refreshDescription(target);
    };
    const receive = event => {
        if (event.origin !== location.origin || event.source !== popup || event.data?.token !== token) return;
        if (event.data.type === 'text-file-ready') popup.postMessage({ type: 'text-file-state', token, snapshot }, location.origin);
        if (event.data.type === 'text-file-loaded') finish(true);
        if (event.data.type === 'text-file-failed') finish(false, event.data.message);
    };
    window.addEventListener('message', receive);
    const timeout = setTimeout(() => finish(false), 30000);
    popup = window.open(`/description?${params}#${token}`, '_blank', 'popup,width=1100,height=820');
    if (!popup) { finish(false); return; }
    for (const existing of descriptionWindows.keys()) if (existing.closed) descriptionWindows.delete(existing);
    descriptionWindows.set(popup, target);
    descriptionTransfers.add(containerId);
    editor.transferring = true;
    editor.error.hidden = true;
    editor.updateView();
}
