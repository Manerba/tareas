/** Projekte an Teilaufgaben haengen; Fortschritt bleibt serverseitig berechnet. */
function renderParentId(value, col, row) {
    if (!value) return '–';
    return `<button class="project-link-button" title="${escapeAttr(t('projectLink.parent'))}"
        onclick="event.stopPropagation(); openLinkedProject(${row.parent_project_id}, ${value})">${value}</button>`;
}

function renderChildProjectLink(projectId, subtask, canEdit) {
    const childId = subtask.child_project_id;
    const child = aufgabenTable.data.find(row => row.id === childId);
    return `<div class="project-link-bar">
        ${childId ? `<span>${t('projectLink.child')}:
            <button class="project-link-button" onclick="openLinkedProject(${childId})">#${childId}${child ? ' · ' + escapeHtml(child.name) : ''}</button></span>
            <span class="project-link-hint">${t('projectLink.automatic')}</span>` : ''}
        ${canEdit ? `<button class="control-btn" onclick="openChildProjectDialog(${projectId}, ${subtask.id || subtask._subtask_id})">${t(childId ? 'projectLink.manage' : 'projectLink.link')}</button>` : ''}
    </div>`;
}

async function openLinkedProject(projectId, subtaskId = null) {
    try {
        if (!aufgabenTable.data.some(row => row.id === projectId)) {
            const response = await fetch('/api/tasks');
            if (!response.ok) throw new Error(t('common.loadError'));
            const items = (await response.json()).items;
            if (!items.some(row => row.id === projectId)) throw new Error(t('projectLink.unavailable'));
            aufgabenTable.data = items;
            buildCategoryButtons();
            aufgabenTable.applyFiltersAndRender();
        }
        if (!aufgabenTable.data.some(row => row.id === projectId)) {
            throw new Error(t('projectLink.unavailable'));
        }
        if (!aufgabenTable.filteredData.some(row => row.id === projectId)) {
            aufgabenTable.filterValues = {};
            aufgabenTable.activeFilters = {};
            ['typeFilterSelect', 'statusFilterSelect', 'searchFilterInput'].forEach(id => {
                const input = document.getElementById(id);
                if (input) input.value = '';
            });
            buildCategoryButtons();
            aufgabenTable.applyFiltersAndRender();
        }
        const wrapper = document.querySelector(`[data-row-id="${projectId}"]`);
        if (!wrapper) throw new Error(t('projectLink.unavailable'));
        if (!wrapper.classList.contains('expanded')) {
            wrapper.classList.add('expanded');
            await onTaskRowExpanded(projectId, wrapper.querySelector('.table-row-detail'));
        }
        const subtaskRow = subtaskId && wrapper.querySelector(`[data-subtask-id="${subtaskId}"]`);
        if (subtaskRow && !subtaskRow.classList.contains('expanded')) {
            toggleSubTaskDetail(projectId, subtaskId, false);
        }
        (subtaskRow || wrapper).scrollIntoView({block: 'center'});
    } catch (error) {
        showNotification(error.message || t('common.loadError'), 'error');
    }
}

async function openChildProjectDialog(projectId, subtaskId) {
    const overlay = createModal({
        title: t('projectLink.manage'),
        body: `<div id="projectLinkContent">${t('common.loading')}</div>
            <p id="projectLinkError" class="task-settings-error" role="alert" hidden></p>`,
        footer: `<button type="button" class="action-btn" id="projectLinkCancel">${t('common.cancel')}</button>
            <button type="button" class="action-btn primary" id="projectLinkSave" disabled>${t('common.save')}</button>`,
        closeOnBackdrop: false,
    });
    const content = overlay.querySelector('#projectLinkContent');
    const save = overlay.querySelector('#projectLinkSave');
    const error = overlay.querySelector('#projectLinkError');
    overlay.querySelector('#projectLinkCancel').addEventListener('click', closeModal);
    try {
        const [tasksResponse, subtasksResponse] = await Promise.all([
            fetch('/api/tasks'), fetch(`/api/tasks/${projectId}/subtasks`),
        ]);
        if (!tasksResponse.ok || !subtasksResponse.ok) throw new Error(t('common.loadError'));
        const tasks = (await tasksResponse.json()).items;
        const subtask = (await subtasksResponse.json()).items.find(row => row.id === subtaskId);
        if (!subtask) throw new Error(t('projectLink.unavailable'));
        if (!overlay.isConnected) return;
        const child = tasks.find(row => row.id === subtask.child_project_id);
        if (subtask.child_project_id) {
            content.innerHTML = `<p>${t('projectLink.child')}: #${subtask.child_project_id}${child ? ' · ' + escapeHtml(child.name) : ''}</p>
                <p>${t('projectLink.automatic')}</p><p>${t('projectLink.detachHint')}</p>`;
            save.textContent = t('projectLink.detach');
            save.disabled = !child || !getTaskPermissions(child).canEdit;
        } else {
            const ancestors = new Set();
            let ancestor = projectId;
            while (ancestor && !ancestors.has(ancestor)) {
                ancestors.add(ancestor);
                ancestor = tasks.find(row => row.id === ancestor)?.parent_project_id;
            }
            const candidates = tasks.filter(row => row.task_type === 'projekt' && !row.parent_subtask_id
                && !ancestors.has(row.id) && getTaskPermissions(row).canEdit);
            content.innerHTML = `<div class="modal-field">
                <label for="childProjectSelect">${t('projectLink.child')}</label>
                <select id="childProjectSelect"><option value="new">${t('projectLink.create')}</option>
                    ${candidates.map(row => `<option value="${row.id}">#${row.id} · ${escapeHtml(row.name)}</option>`).join('')}
                </select></div>
                <div class="modal-field" id="childProjectNameField">
                    <label for="childProjectName">${t('tasks.name')}</label>
                    <input id="childProjectName" value="${escapeAttr(subtask.name)}" required>
                </div><p>${t('projectLink.automatic')}</p>`;
            const select = content.querySelector('#childProjectSelect');
            select.addEventListener('change', () => {
                content.querySelector('#childProjectNameField').hidden = select.value !== 'new';
            });
            save.disabled = false;
        }
        save.addEventListener('click', async () => {
            const selected = content.querySelector('#childProjectSelect')?.value;
            const name = content.querySelector('#childProjectName')?.value.trim();
            if (selected === 'new' && !name) {
                error.textContent = t('tasks.nameRequired'); error.hidden = false; return;
            }
            save.disabled = true;
            error.hidden = true;
            try {
                const detach = !!subtask.child_project_id;
                const create = selected === 'new';
                const id = detach ? subtask.child_project_id : Number(selected);
                const response = await fetch(create ? '/api/tasks' : `/api/tasks/${id}`, {
                    method: create ? 'POST' : 'PUT', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(create
                        ? {name, task_type: 'projekt', parent_subtask_id: subtaskId}
                        : {parent_subtask_id: detach ? null : subtaskId}),
                });
                if (!response.ok) {
                    const data = await response.json();
                    throw new Error(typeof data.detail === 'string' ? data.detail : t('common.saveError'));
                }
                closeModal();
                await aufgabenTable.loadData();
                await openLinkedProject(projectId, subtaskId);
            } catch (err) {
                error.textContent = err.message || t('common.saveError'); error.hidden = false;
                save.disabled = false;
            }
        });
    } catch (err) {
        error.textContent = err.message || t('common.loadError'); error.hidden = false;
    }
}

// Nur abgeleitete Felder erneuern, damit offene Beschreibungsentwuerfe erhalten bleiben.
async function refreshTaskProgress() {
    try {
        const response = await fetch('/api/tasks');
        if (!response.ok) return;
        const items = (await response.json()).items;
        const keys = ['status', 'subtask_total', 'subtask_done', 'progress_percent',
            'parent_subtask_id', 'parent_project_id', '_status_percent', 'child_project_id', 'progress_automatic'];
        for (const fresh of items) {
            const row = aufgabenTable.data.find(item => item.id === fresh.id);
            if (!row) continue;
            keys.forEach(key => { row[key] = fresh[key]; });
            const wrapper = document.querySelector(`[data-row-id="${row.id}"]`);
            const statusInput = wrapper?.querySelector(`#inlineStatus_${row.id}`);
            if (statusInput && row.task_type === 'projekt') {
                const status = row.subtask_total > 0 && row.subtask_done >= row.subtask_total
                    ? 'erledigt' : row.subtask_done > 0 ? 'in_arbeit' : 'offen';
                statusInput.innerHTML = `<option value="${status}">${t(`status.${status}`)} (${row.subtask_done}/${row.subtask_total})</option>
                    <option value="abgebrochen">${t('status.abgebrochen')}</option>`;
                statusInput.value = row.status;
            } else if (wrapper) {
                const index = aufgabenTable.config.columns.findIndex(col => col.field === 'status');
                const cell = wrapper.querySelectorAll(':scope > .table-row > .table-cell')[index];
                if (cell) cell.innerHTML = renderAufgabenBadge(row.status, {field: 'status'}, row);
            }
        }
    } catch (error) {
        console.warn('Projektfortschritt konnte nicht aktualisiert werden:', error);
    }
}
