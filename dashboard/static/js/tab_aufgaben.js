/**
 * Tareas - Aufgaben-Tab
 * Init, Control-Area, Renderer, Detail-Callbacks, Modal, SubTask-Verwaltung
 * Sprint B: Aufgabenzuweisung, Sichtbarkeit, Notizen
 */

let aufgabenTable = null;
let aufgabenConfig = null;
let allExpanded = false;
let cachedAreas = null;
let cachedUsers = [];
let ncConfigured = false;
let ncDirectories = [];

// Auto-Save: Debounce-Timer pro Feld
const _autoSaveTimers = {};
function autoSaveDebounced(key, fn, delay = 1500) {
    clearTimeout(_autoSaveTimers[key]);
    _autoSaveTimers[key] = setTimeout(fn, delay);
}

// ========================================
// Tab Initialisierung
// ========================================

async function initAufgabenTab() {
    const container = document.getElementById('contentContainer');
    if (!container) return;

    container.innerHTML = '<div class="table-loading"><div class="spinner"></div></div>';

    try {
        // Config, Areas, Users und Nextcloud-Status parallel laden
        const [configResp, areasResp, usersResp, ncStatusResp] = await Promise.all([
            fetch('/api/tasks/config'),
            fetch('/api/areas'),
            fetch('/api/users/list'),
            fetch('/api/nextcloud/status').catch(() => ({ ok: false })),
        ]);
        aufgabenConfig = await configResp.json();
        const areasData = await areasResp.json();
        cachedAreas = areasData.items || [];
        const usersData = await usersResp.json();
        cachedUsers = usersData.items || [];

        // Nextcloud-Status
        if (ncStatusResp.ok) {
            const ncData = await ncStatusResp.json();
            ncConfigured = ncData.configured || false;
        } else {
            ncConfigured = false;
        }

        // Control-Bar aufbauen
        buildControlBar();

        // Container fuer Tabelle
        container.innerHTML = '<div id="aufgabenTableContainer"></div>';

        // Tabelle erstellen
        aufgabenTable = new ExpandableTable(aufgabenConfig, 'aufgabenTableContainer', {
            onRowExpanded: onTaskRowExpanded,
            onRowCollapsed: (rowId) => deactivateInlineEditing(rowId),
            onDataLoaded: () => buildCategoryButtons(),
        });

        // Custom Renderer registrieren
        aufgabenTable.renderers['badge'] = renderAufgabenBadge;
        aufgabenTable.renderers['priority'] = renderPriority;
        aufgabenTable.renderers['taskId'] = renderTaskId;
        aufgabenTable.renderers['deleteAction'] = renderDeleteAction;

        // Prioritaet-Spalte Renderer zuweisen
        const prioCol = aufgabenConfig.columns.find(c => c.field === 'priority');
        if (prioCol) prioCol.renderer = 'priority';

        window.tables['aufgaben'] = aufgabenTable;

        // Daten laden
        await aufgabenTable.loadData();
    } catch (error) {
        console.error('Aufgaben-Tab Fehler:', error);
        container.innerHTML = `<div class="table-error">${t('tasks.loadError')}</div>`;
    }
}

// ========================================
// Control Bar
// ========================================

function buildControlBar() {
    const filterBar = document.getElementById('filterBar');
    if (!filterBar) return;

    filterBar.innerHTML = `
        <div class="control-bar">
            <button class="control-btn primary" onclick="openNewTaskModal()">${t('tasks.newTask')}</button>
            <button class="control-btn" id="toggleAllBtn" onclick="toggleAllExpand()">${t('tasks.expandAll')}</button>
            <span class="separator"></span>
            <div class="filter-group">
                <label class="filter-label">${t('tasks.typeLabel')}</label>
                <select class="filter-select" id="typeFilterSelect" onchange="applyControlFilter()">
                    <option value="">${t('common.all')}</option>
                    <option value="aufgabe">${t('type.aufgabe')}</option>
                    <option value="projekt">${t('type.projekt')}</option>
                </select>
            </div>
            <div class="filter-group">
                <label class="filter-label">${t('tasks.statusLabel')}</label>
                <select class="filter-select" id="statusFilterSelect" onchange="applyControlFilter()">
                    <option value="">${t('common.all')}</option>
                    <option value="offen">${t('status.offen')}</option>
                    <option value="in_arbeit">${t('status.in_arbeit')}</option>
                    <option value="erledigt">${t('status.erledigt')}</option>
                    <option value="abgebrochen">${t('status.abgebrochen')}</option>
                </select>
            </div>
            <span class="spacer"></span>
            <input type="text" class="control-search" id="searchFilterInput"
                   placeholder="${t('common.search')}" oninput="applySearchFilter(this.value)">
        </div>
    `;
}

function applyControlFilter() {
    if (!aufgabenTable) return;
    const typeVal = document.getElementById('typeFilterSelect')?.value || '';
    const statusVal = document.getElementById('statusFilterSelect')?.value || '';
    aufgabenTable.setFilter('typeFilter', typeVal);
    aufgabenTable.setFilter('statusFilter', statusVal);
}

function applySearchFilter(value) {
    if (!aufgabenTable) return;
    aufgabenTable.setFilter('searchFilter', value);
}

function toggleAllExpand() {
    if (!aufgabenTable) return;
    allExpanded = !allExpanded;
    aufgabenTable.toggleAllRows(allExpanded);

    const btn = document.getElementById('toggleAllBtn');
    if (btn) {
        btn.textContent = allExpanded ? t('tasks.collapseAll') : t('tasks.expandAll');
        btn.classList.toggle('active', allExpanded);
    }
}

// ========================================
// Category Filter Buttons
// ========================================

function _getCategoryLabel(cat) {
    const labels = {
        'team': t('category.team'),
        'zugewiesene': t('category.zugewiesene'),
        'vergebene': t('category.vergebene'),
        'eigene': t('category.eigene'),
        'mcp': t('category.mcp'),
    };
    return labels[cat] || cat;
}

function buildCategoryButtons() {
    if (!aufgabenTable) return;

    // Vorhandene Kategorien zaehlen
    const counts = {};
    (aufgabenTable.data || []).forEach(item => {
        const cat = item._category;
        if (cat) counts[cat] = (counts[cat] || 0) + 1;
    });

    // Container finden oder erstellen
    let container = document.getElementById('categoryFilterGroup');
    if (!container) {
        const controlBar = document.querySelector('.control-bar');
        if (!controlBar) return;
        // Nach den Dropdowns einfuegen (stabile Position)
        const filterGroups = controlBar.querySelectorAll('.filter-group');
        const lastFilterGroup = filterGroups[filterGroups.length - 1];
        if (!lastFilterGroup) return;
        container = document.createElement('div');
        container.id = 'categoryFilterGroup';
        container.className = 'category-filter-group';
        lastFilterGroup.after(container);
    }

    // Aktive Filter merken
    const activeSet = aufgabenTable.activeFilters['_category'] || new Set();

    // Buttons nur fuer vorhandene Kategorien erzeugen (feste Reihenfolge)
    const order = ['team', 'zugewiesene', 'vergebene', 'eigene', 'mcp'];
    let html = '';
    order.forEach(cat => {
        if (!counts[cat]) return;
        const isActive = activeSet.has(cat);
        const label = _getCategoryLabel(cat);
        html += `<button class="control-btn category-btn${isActive ? ' active' : ''}" data-category="${escapeAttr(cat)}" onclick="toggleCategoryFilter('${escapeAttr(cat)}')">${label} (${counts[cat]})</button>`;
    });

    container.innerHTML = html;
}

function toggleCategoryFilter(category) {
    if (!aufgabenTable) return;

    // activeFilters initialisieren falls noetig
    if (!aufgabenTable.activeFilters['_category']) {
        aufgabenTable.activeFilters['_category'] = new Set();
    }

    aufgabenTable.toggleButtonFilter('_category', category);

    // Button-Styling aktualisieren
    const container = document.getElementById('categoryFilterGroup');
    if (container) {
        const activeSet = aufgabenTable.activeFilters['_category'];
        container.querySelectorAll('.category-btn').forEach(btn => {
            const cat = btn.dataset.category;
            btn.classList.toggle('active', activeSet.has(cat));
        });
    }
}

// ========================================
// Inline-Editing (Haupt-Tabelle)
// ========================================

function activateInlineEditing(rowId) {
    const wrapper = document.querySelector(`[data-row-id="${rowId}"]`);
    if (!wrapper) return;

    const row = aufgabenTable.filteredData.find(r => String(r.id) === String(rowId));
    if (!row) return;

    // Skip assigned_subtask pseudo-rows
    if (row._type === 'assigned_subtask' || row.id < 0) return;

    const perm = getTaskPermissions(row);
    const tableRow = wrapper.querySelector('.table-row');
    if (!tableRow) return;

    const cells = tableRow.querySelectorAll('.table-cell');
    if (!cells.length) return;
    const cellForField = (field) => {
        const index = aufgabenTable.config.columns.findIndex(col => col.field === field);
        return index >= 0 ? cells[index] : null;
    };

    const deadlineISO = convertToISO(row.deadline);

    // Name
    const nameCell = cellForField('name');
    if (nameCell && (perm.isAdmin || (!perm.isAssignee && !(perm.isCreator && perm.isAssigned)))) {
        nameCell._originalHTML = nameCell.innerHTML;
        nameCell.innerHTML = `<input type="text" class="inline-edit-input" id="inlineName_${row.id}" value="${escapeAttr(row.name)}" onclick="event.stopPropagation()">`;
    }

    // Projekte koennen zwischen berechnetem Fortschritt und Abgebrochen wechseln.
    const statusCell = cellForField('status');
    if (statusCell) {
        const canEditStatus = perm.isAdmin || (row.can_edit_status ?? (perm.isCreator || perm.isAssignee || perm.isLegacy));
        if (canEditStatus) {
            const isProject = row.task_type === 'projekt';
            const progressStatus = row.subtask_total > 0 && row.subtask_done >= row.subtask_total
                ? 'erledigt' : row.subtask_done > 0 ? 'in_arbeit' : 'offen';
            const statuses = isProject
                ? [progressStatus, 'abgebrochen']
                : ['offen', 'in_arbeit', 'erledigt', 'abgebrochen'];
            statusCell._originalHTML = statusCell.innerHTML;
            statusCell.innerHTML = `<select class="inline-edit-select" id="inlineStatus_${row.id}" onclick="event.stopPropagation()">
                ${statuses.map(status => {
                    const progress = isProject && status !== 'abgebrochen' && row.subtask_total > 0
                        ? ` (${row.subtask_done}/${row.subtask_total})` : '';
                    return `<option value="${status}" ${row.status === status ? 'selected' : ''}>${t(`status.${status}`)}${progress}</option>`;
                }).join('')}
            </select>`;
        }
    }

    // Prioritaet
    const priorityCell = cellForField('priority');
    if (priorityCell && (perm.isAdmin || !perm.isAssignee)) {
        priorityCell._originalHTML = priorityCell.innerHTML;
        priorityCell.innerHTML = `<input type="number" class="inline-edit-input" id="inlinePriority_${row.id}" value="${row.priority}" min="1" max="100" onclick="event.stopPropagation()">`;
    }

    // Zugewiesen an (Admin/Ersteller/Legacy)
    const assignedCell = cellForField('assigned_to_name');
    if (assignedCell && (perm.isAdmin || perm.isCreator || perm.isOwnTask || perm.isLegacy)) {
        assignedCell._originalHTML = assignedCell.innerHTML;
        assignedCell.innerHTML = `<select class="inline-edit-select" id="inlineAssigned_${row.id}" onclick="event.stopPropagation()">
            ${buildUserOptions(row.assigned_to)}
        </select>`;
    }

    // Deadline
    const deadlineCell = cellForField('deadline');
    if (deadlineCell && (perm.isAdmin || !perm.isAssignee)) {
        deadlineCell._originalHTML = deadlineCell.innerHTML;
        deadlineCell.innerHTML = `<input type="date" class="inline-edit-input" id="inlineDeadline_${row.id}" value="${deadlineISO}" onclick="event.stopPropagation()">`;
    }

    // Auto-Save auf alle inline inputs wiren
    ['inlineName', 'inlineStatus', 'inlinePriority', 'inlineAssigned', 'inlineDeadline'].forEach(prefix => {
        const el = document.getElementById(`${prefix}_${row.id}`);
        if (el) {
            el.addEventListener('change', () => saveTaskFromInline(row.id));
        }
    });
}

function deactivateInlineEditing(rowId) {
    const wrapper = document.querySelector(`[data-row-id="${rowId}"]`);
    if (!wrapper) return;

    const row = aufgabenTable.filteredData.find(r => String(r.id) === String(rowId));
    const tableRow = wrapper.querySelector('.table-row');
    if (!tableRow) return;

    const cells = tableRow.querySelectorAll('.table-cell');
    const columns = aufgabenTable.config.columns;

    cells.forEach((cell, index) => {
        if (cell._originalHTML !== undefined) {
            if (row && columns[index]) {
                // Zelle aus aktuellen (ggf. optimistisch aktualisierten) Daten neu rendern
                cell.innerHTML = aufgabenTable.renderCell(row[columns[index].field], columns[index], row);
            } else {
                cell.innerHTML = cell._originalHTML;
            }
            delete cell._originalHTML;
        }
    });
}

async function saveTaskFromInline(taskId) {
    const row = aufgabenTable.filteredData.find(r => String(r.id) === String(taskId));
    if (!row) return;

    const body = {};

    const nameInput = document.getElementById(`inlineName_${taskId}`);
    if (nameInput) body.name = nameInput.value;

    const statusInput = document.getElementById(`inlineStatus_${taskId}`);
    if (statusInput && statusInput.value !== row.status) body.status = statusInput.value;

    const prioInput = document.getElementById(`inlinePriority_${taskId}`);
    if (prioInput) body.priority = parseInt(prioInput.value) || 50;

    const assignInput = document.getElementById(`inlineAssigned_${taskId}`);
    if (assignInput) {
        const val = assignInput.value;
        body.assigned_to = val ? parseInt(val) : 0;
    }

    const dlInput = document.getElementById(`inlineDeadline_${taskId}`);
    if (dlInput) body.deadline = dlInput.value || '';

    // Optimistisches Update der lokalen Daten (vor await, damit deactivateInlineEditing die neuen Werte sieht)
    const oldValues = {};
    if (body.name !== undefined) { oldValues.name = row.name; row.name = body.name; }
    if (body.status !== undefined) { oldValues.status = row.status; row.status = body.status; }
    if (body.priority !== undefined) { oldValues.priority = row.priority; row.priority = body.priority; }
    if (body.assigned_to !== undefined) {
        oldValues.assigned_to = row.assigned_to;
        oldValues.assigned_to_name = row.assigned_to_name;
        row.assigned_to = body.assigned_to === 0 ? null : body.assigned_to;
        if (body.assigned_to === 0) {
            row.assigned_to_name = '';
        } else {
            const user = cachedUsers.find(u => u.id === body.assigned_to);
            row.assigned_to_name = user ? `${user.vorname} ${user.nachname}`.trim() : '';
        }
    }
    if (body.deadline !== undefined) {
        oldValues.deadline = row.deadline;
        if (body.deadline) {
            const parts = body.deadline.split('-');
            row.deadline = parts.length === 3 ? `${parts[2]}.${parts[1]}.${parts[0]}` : body.deadline;
        } else {
            row.deadline = '';
        }
    }

    try {
        const resp = await fetch(`/api/tasks/${taskId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (!resp.ok) throw new Error(t('common.saveError'));
    } catch (error) {
        // Bei Fehler: lokale Daten zuruecksetzen
        for (const [key, value] of Object.entries(oldValues)) {
            row[key] = value;
        }
        console.error('Inline-Save fehlgeschlagen:', error);
        showNotification(t('common.saveError'), 'error');
    }
}

// ========================================
// Custom Renderer
// ========================================

function renderTaskId(value, col, row) {
    const displayId = row._type === 'assigned_subtask' ? row._subtask_id : value;
    return escapeHtml(displayId ?? '-');
}

function renderSubtaskProgress(value) {
    const percent = parseInt(value, 10) || 0;
    const cssClass = percent >= 100 ? 'erledigt' : percent > 0 ? 'in_arbeit' : 'offen';
    return `<span class="badge badge-${cssClass}">${percent}%</span>`;
}

function renderAufgabenBadge(value, col, row) {
    if (col.field === 'status' && row._type === 'assigned_subtask') {
        return renderSubtaskProgress(row._status_percent);
    }
    if (!value) return '-';

    // Abgebrochene Projekte behalten ihren Status statt der Fortschrittsanzeige.
    if (col.field === 'status' && row.task_type === 'projekt' && value !== 'abgebrochen' && row.subtask_total > 0) {
        const done = row.subtask_done;
        const total = row.subtask_total;
        const cssClass = done === total ? 'erledigt' : done > 0 ? 'in_arbeit' : 'offen';
        return `<span class="badge badge-${cssClass}">${done}/${total}</span>`;
    }

    const cssClass = value.toLowerCase().replace(/\s+/g, '_');
    const labelMap = {
        'offen': t('status.offen'),
        'in_arbeit': t('status.in_arbeit'),
        'erledigt': t('status.erledigt'),
        'abgebrochen': t('status.abgebrochen'),
        'aufgabe': t('type.aufgabe'),
        'projekt': t('type.projekt'),
    };
    const label = labelMap[value] || value;
    return `<span class="badge badge-${cssClass}">${label}</span>`;
}

function renderPriority(value, col, row) {
    if (value === null || value === undefined) return '-';
    const num = parseInt(value);
    let dotClass = 'priority-medium';
    if (num <= 30) dotClass = 'priority-high';
    else if (num >= 70) dotClass = 'priority-low';
    return `<span class="priority-display"><span class="priority-dot ${dotClass}"></span>${num}</span>`;
}

function renderDeleteAction(value, col, row) {
    const perm = getTaskPermissions(row);
    if (!perm.isCreator && !perm.isOwnTask && !perm.isLegacy && !perm.isAdmin) return '';
    const taskId = row.id;
    return `<button class="row-delete-btn" onclick="event.stopPropagation(); deleteTask(${taskId})" title="${t('common.delete')}">&times;</button>`;
}

// ========================================
// Berechtigungslogik
// ========================================

function getTaskPermissions(row) {
    const userId = currentUser ? currentUser.id : null;
    const isCreator = row.created_by === userId;
    const isAssignee = row.assigned_to === userId;
    const isAssigned = !!row.assigned_to;
    const isDesktop = document.body.classList.contains('desktop-mode');
    const isOwnTask = isCreator && (!isAssigned || (isDesktop && isAssignee));
    // Altdaten ohne created_by: alles editierbar
    const isLegacy = !row.created_by;
    const isTeamMember = !!row.is_team_member;
    const isAdmin = !!(currentUser && currentUser.is_admin);

    return { isCreator, isAssignee, isAssigned, isOwnTask, isLegacy, isTeamMember, isAdmin };
}

// ========================================
// Benutzer-Dropdown HTML
// ========================================

function buildUserOptions(selectedId) {
    let html = `<option value="">${t('tasks.unassigned')}</option>`;
    cachedUsers.forEach(u => {
        const name = `${u.vorname} ${u.nachname}`.trim() || `User ${u.id}`;
        const sel = u.id === selectedId ? 'selected' : '';
        html += `<option value="${u.id}" ${sel}>${escapeHtml(name)}</option>`;
    });
    return html;
}

// ========================================
// Detail-Bereich: Aufgabe/Projekt aufgeklappt
// ========================================

function createMarkdownField(containerId, content, options) {
    const field = options.field || 'description';
    return new MarkdownEditor(containerId, content, {
        format: options.model?.[`${field}_format`] || 'markdown',
        label: options.label,
        readOnly: options.readOnly,
        onSave: async source => {
            const response = await fetch(options.url, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ [field]: source }),
            });
            if (!response.ok) throw new Error(t('common.saveError'));
            if (options.model) {
                options.model[field] = source;
                options.model[`${field}_format`] = 'markdown';
            }
            options.onSaved?.(source);
        },
    });
}

async function onTaskRowExpanded(rowId, detailElement) {
    if (!detailElement || !aufgabenTable) return;

    const row = aufgabenTable.filteredData.find(r => String(r.id) === String(rowId));
    if (!row) return;

    // Zugewiesene Subtask-Ansicht (negative ID = Pseudo-Task fuer zugewiesene Subtask)
    if (row._type === 'assigned_subtask' || row.id < 0) {
        return onSubtaskViewExpanded(row, detailElement);
    }

    const perm = getTaskPermissions(row);

    // Inline-Editing in der Tabellenzeile aktivieren
    activateInlineEditing(rowId);

    let html = `<div class="detail-edit" data-task-id="${row.id}">`;

    // Beschreibung + Dateiablage: Zwei-Spalten-Layout wenn NC-Verzeichnis zugeordnet
    const hasNcPath = !!row.nextcloud_path && ncConfigured;

    if (hasNcPath) {
        html += `<div class="project-detail-columns">`;
        html += `<div class="project-detail-left">`;
    }

    // Beschreibung: Markdown-Ansicht, Bearbeiten nach bestehenden Berechtigungen
    if (!perm.isAdmin && perm.isAssignee && !perm.isCreator) {
        html += `<div class="notes-section">
            <h5>${t('detail.descriptionFrom', { name: escapeHtml(row.created_by_name || 'Ersteller') })}</h5>
            <div class="description-readonly markdown-body">${renderMarkdown(row.description, row.description_format) || `<em>${t('detail.noDescription')}</em>`}</div>
        </div>`;
        html += `<div class="notes-section">
            <h5>${t('detail.myNotes')}</h5>
            <div id="notesEditor_${row.id}"></div>
        </div>`;
    } else if (!perm.isAdmin && perm.isCreator && perm.isAssigned && !perm.isOwnTask) {
        html += `<div class="notes-section">
            <h5>${t('detail.description')}</h5>
            <div class="description-readonly markdown-body">${renderMarkdown(row.description, row.description_format) || `<em>${t('detail.noDescription')}</em>`}</div>
        </div>`;
        html += `<div class="notes-section" id="assigneeNotes_${row.id}">
            <h5>${t('detail.notesFrom', { name: escapeHtml(row.assigned_to_name || 'Zugewiesenem') })}</h5>
            <div class="description-readonly markdown-body" id="assigneeNotesContent_${row.id}"><em>${t('common.loading')}</em></div>
        </div>`;
    } else {
        html += `<div id="wysiwygEditor_${row.id}"></div>`;
    }

    if (hasNcPath) {
        html += `</div>`; // project-detail-left
        html += `<div class="project-detail-right">
            <div id="fileBrowserContainer_${row.id}"></div>
        </div>`;
        html += `</div>`; // project-detail-columns
        html += `<div class="project-detail-resize" id="pdResize_${row.id}"></div>`;
    }

    if (perm.isAdmin) {
        html += `<div class="notes-section subtask-notes-list">
            <h5>${t('detail.notes')}</h5>
            <div class="subtask-notes-content" id="taskAllNotes_${row.id}"><em>${t('common.loading')}</em></div>
        </div>`;
    }

    html += `<div class="notes-section note-entries-list">
        <h5>${t('detail.noteHistory')}</h5>
        <div class="subtask-notes-content" id="taskNoteEntries_${row.id}"><em>${t('common.loading')}</em></div>
    </div>`;

    // Dateiablage-Button fuer Aufgaben (nicht-Projekte)
    if (row.task_type !== 'projekt' && ncConfigured && (perm.isAdmin || perm.isCreator || perm.isLegacy)) {
        html += `<div class="subtask-section-actions" style="margin-top:8px;display:flex;justify-content:flex-end">
            <button class="control-btn${row.nextcloud_path ? ' nc-active' : ''}" onclick="event.stopPropagation(); openNcDirDialog(${row.id}, '${escapeAttr(row.nextcloud_path || '')}')">${row.nextcloud_path ? '&#128194; ' + escapeHtml(row.nextcloud_path) : t('nc.fileStorage')}</button>
        </div>`;
    }

    // SubTask-Bereich fuer Projekte
    if (row.task_type === 'projekt') {
        html += `<div class="subtask-section" id="subtaskSection_${row.id}">
            <div class="subtask-section-header">
                <h4>${t('subtask.title')}</h4>
                <div class="subtask-section-actions">
                    <button class="control-btn" onclick="event.stopPropagation(); openNetzplan(${row.id})">${t('netzplan.title')}</button>
                    ${(perm.isAdmin || perm.isCreator) ? `<button class="team-btn" onclick="event.stopPropagation(); openTeamDialog(${row.id})">${t('team.title')}</button>` : ''}
                    ${ncConfigured && (perm.isAdmin || perm.isCreator || perm.isLegacy) ? `<button class="control-btn${row.nextcloud_path ? ' nc-active' : ''}" onclick="event.stopPropagation(); openNcDirDialog(${row.id}, '${escapeAttr(row.nextcloud_path || '')}')">${row.nextcloud_path ? '&#128194; ' + escapeHtml(row.nextcloud_path) : t('nc.fileStorage')}</button>` : ''}
                </div>
            </div>
            <div id="subtaskContainer_${row.id}">
                <div class="table-loading"><div class="spinner"></div></div>
            </div>
        </div>`;
    }

    html += `</div>`;

    detailElement.innerHTML = html;

    // Editoren initialisieren
    if (!perm.isAdmin && perm.isAssignee && !perm.isCreator) {
        // Notizen-Editor fuer Zugewiesenen: Notiz laden und Editor initialisieren
        try {
            const notesResp = await fetch(`/api/tasks/${row.id}/notes`);
            if (!notesResp.ok) throw new Error(`HTTP ${notesResp.status}`);
            const notesData = await notesResp.json();
            const myNote = (notesData.items || []).find(n => n.user_id === currentUser.id);
            createMarkdownField(`notesEditor_${row.id}`, myNote?.content || '', {
                url: `/api/tasks/${row.id}/notes`, field: 'content', model: myNote, label: t('detail.myNotes'),
            });
        } catch (e) {
            document.getElementById(`notesEditor_${row.id}`).textContent = t('detail.loadError');
        }
    } else if (!perm.isAdmin && perm.isCreator && perm.isAssigned && !perm.isOwnTask) {
        // Notizen des Zugewiesenen laden
        try {
            const notesResp = await fetch(`/api/tasks/${row.id}/notes`);
            if (!notesResp.ok) throw new Error(`HTTP ${notesResp.status}`);
            const notesData = await notesResp.json();
            const assigneeNote = (notesData.items || []).find(n => n.user_id === row.assigned_to);
            const contentEl = document.getElementById(`assigneeNotesContent_${row.id}`);
            if (contentEl) {
                contentEl.innerHTML = assigneeNote && assigneeNote.content
                    ? renderMarkdown(assigneeNote.content, assigneeNote.content_format)
                    : `<em>${t('detail.noNotes')}</em>`;
            }
        } catch (e) {
            const contentEl = document.getElementById(`assigneeNotesContent_${row.id}`);
            if (contentEl) contentEl.innerHTML = `<em>${t('detail.loadError')}</em>`;
        }
    } else {
        // Markdown startet in der formatierten Ansicht
        createMarkdownField(`wysiwygEditor_${row.id}`, row.description || '', {
            url: `/api/tasks/${row.id}`, model: row,
            readOnly: !(perm.isAdmin || perm.isOwnTask || perm.isLegacy || (perm.isTeamMember && row.can_edit_status)),
        });
    }

    if (perm.isAdmin) {
        loadAdminNotes(`/api/tasks/${row.id}/notes`, `taskAllNotes_${row.id}`);
    }

    // Auto-Save: WYSIWYG-Editor mit Debounce
    const wysiwygContent = document.querySelector(`#wysiwygEditor_${row.id} .wysiwyg-content`);
    if (wysiwygContent) {
        wysiwygContent.addEventListener('input', () => {
            autoSaveDebounced(`task_${row.id}`, () => saveTask(row.id, true));
        });
    }

    // Auto-Save: Notizen-Editor mit Debounce
    const notesContent = document.querySelector(`#notesEditor_${row.id} .wysiwyg-content`);
    if (notesContent) {
        notesContent.addEventListener('input', () => {
            autoSaveDebounced(`taskNotes_${row.id}`, () => saveTask(row.id, true));
        });
    }

    loadTaskNoteEntries(row.id);

    // File Browser initialisieren wenn Verzeichnis zugeordnet
    if (row.nextcloud_path && ncConfigured) {
        const fbContainer = document.getElementById(`fileBrowserContainer_${row.id}`);
        if (fbContainer) {
            new FileBrowser(`fileBrowserContainer_${row.id}`, row.id);
        }
        initColumnResize(row.id);
    }

    // SubTasks laden wenn Projekt
    if (row.task_type === 'projekt') {
        loadSubTasks(row.id);
    }
}


/**
 * Resize-Handle fuer Zwei-Spalten-Layout (Editor + Dateiablage).
 * Zieht man den Handle nach unten/oben, aendert sich die Hoehe beider Spalten synchron.
 */
function initColumnResize(taskId) {
    const handle = document.getElementById(`pdResize_${taskId}`);
    if (!handle) return;
    const columns = handle.previousElementSibling;
    if (!columns || !columns.classList.contains('project-detail-columns')) return;

    let startY = 0;
    let startH = 0;

    function onMouseMove(e) {
        const newH = Math.max(200, startH + (e.clientY - startY));
        columns.style.height = newH + 'px';
    }
    function onMouseUp() {
        document.removeEventListener('mousemove', onMouseMove);
        document.removeEventListener('mouseup', onMouseUp);
        document.body.style.userSelect = '';
        document.body.style.cursor = '';
    }
    handle.addEventListener('mousedown', (e) => {
        e.preventDefault();
        startY = e.clientY;
        startH = columns.offsetHeight;
        document.body.style.userSelect = 'none';
        document.body.style.cursor = 'ns-resize';
        document.addEventListener('mousemove', onMouseMove);
        document.addEventListener('mouseup', onMouseUp);
    });
}

// ========================================
// Task CRUD
// ========================================

async function saveTask(taskId, silent = false) {
    const row = aufgabenTable.filteredData.find(r => String(r.id) === String(taskId));

    // Subtask-View: an eigene Speicherfunktion weiterleiten (negative ID = Pseudo-Task)
    if (row && (row._type === 'assigned_subtask' || row.id < 0)) {
        return saveSubtaskView(row, silent);
    }

    const perm = row ? getTaskPermissions(row) : { isOwnTask: true, isLegacy: true, isAssignee: false, isCreator: true, isAssigned: false };

    const body = {};

    // Inline-Felder lesen (aus Tabellenzeile)
    const nameInput = document.getElementById(`inlineName_${taskId}`);
    if (nameInput) body.name = nameInput.value;

    const statusInput = document.getElementById(`inlineStatus_${taskId}`);
    if (statusInput && statusInput.value !== row?.status) body.status = statusInput.value;

    const prioInput = document.getElementById(`inlinePriority_${taskId}`);
    if (prioInput) body.priority = parseInt(prioInput.value) || 50;

    const dlInput = document.getElementById(`inlineDeadline_${taskId}`);
    if (dlInput) body.deadline = dlInput.value || '';

    const assignSelect = document.getElementById(`inlineAssigned_${taskId}`);
    if (assignSelect) {
        const val = assignSelect.value;
        body.assigned_to = val ? parseInt(val) : 0;
    }

    // WYSIWYG-Content (aus Detail-Bereich)
    if (perm.isOwnTask || perm.isLegacy) {
        const editorContainer = document.getElementById(`wysiwygEditor_${taskId}`);
        const editorContent = editorContainer?.querySelector('.wysiwyg-content');
        if (editorContent) body.description = sanitizeHtml(editorContent.innerHTML);
    }

    try {
        const resp = await fetch(`/api/tasks/${taskId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });

        if (!resp.ok) throw new Error(t('common.saveError'));

        // Notizen separat speichern (fuer Zugewiesene)
        if (!perm.isAdmin && perm.isAssignee && !perm.isCreator) {
            const notesContainer = document.getElementById(`notesEditor_${taskId}`);
            const notesContent = notesContainer?.querySelector('.wysiwyg-content');
            if (notesContent) {
                await fetch(`/api/tasks/${taskId}/notes`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ content: sanitizeHtml(notesContent.innerHTML) }),
                });
            }
        }

        if (!silent) {
            showNotification(t('tasks.saved'), 'success');
            await aufgabenTable.loadData();
        }
    } catch (error) {
        console.error('Speichern fehlgeschlagen:', error);
        showNotification(t('common.saveError'), 'error');
    }
}

// ========================================
// Subtask-View (zugewiesene Teilaufgabe als Aufgabe)
// ========================================

async function onSubtaskViewExpanded(row, detailElement) {
    const stId = row._subtask_id || Math.abs(row.id);
    const projectId = row._project_id;
    const deadlineISO = convertToISO(row.deadline);
    const isAdmin = !!currentUser?.is_admin;
    const fieldAccess = isAdmin ? '' : 'disabled class="field-readonly"';

    let html = `<div class="detail-edit" data-task-id="${row.id}">`;

    // Info-Hinweis
    html += `<div class="subtask-view-info">${t('subtask.info', { name: escapeHtml(row._project_name) })}</div>`;

    // Admins bearbeiten auch zugewiesene Teilaufgaben vollstaendig.
    html += `<div class="detail-edit-header">
        <div class="detail-edit-field flex-grow">
            <label>${t('subtask.col.name')}</label>
            <input type="text" id="stViewName_${stId}" value="${escapeAttr(row.name)}" ${fieldAccess}>
        </div>
        <div class="detail-edit-field">
            <label>${t('subtask.col.deadline')}</label>
            <input type="date" id="stViewDeadline_${stId}" value="${deadlineISO}" ${fieldAccess}>
        </div>
        <div class="detail-edit-field">
            <label>${t('subtask.col.priority')}</label>
            <input type="number" id="stViewPriority_${stId}" value="${row.priority}" min="1" max="100" ${fieldAccess} style="width:70px">
        </div>
        <div class="detail-edit-field">
            <label>${t('subtask.col.status')}</label>
            <input type="number" id="stViewStatus_${stId}" value="${escapeAttr(row._status_percent ?? 0)}" min="0" max="100" step="1" required style="width:70px">
        </div>
        ${isAdmin ? `<div class="detail-edit-field">
            <label>${t('subtask.col.assignedTo')}</label>
            <select id="stViewAssigned_${stId}">${buildUserOptions(row.assigned_to)}</select>
        </div>` : ''}
    </div>`;

    html += isAdmin ? `<div id="stViewDescription_${stId}"></div>` : `<div class="notes-section">
        <h5>${t('detail.descriptionFrom', { name: escapeHtml(row.created_by_name || 'Ersteller') })}</h5>
        <div class="description-readonly markdown-body">${renderMarkdown(row.description, row.description_format) || `<em>${t('detail.noDescription')}</em>`}</div>
    </div>`;

    // Eigene Notizen (Markdown)
    html += `<div class="notes-section">
        <h5>${t('detail.myNotes')}</h5>
        <div id="stViewNotes_${stId}"></div>
    </div>`;

    if (isAdmin) {
        html += `<div class="notes-section subtask-notes-list">
            <h5>${t('detail.notes')}</h5>
            <div class="subtask-notes-content" id="stViewAllNotes_${stId}"></div>
        </div>`;
    }

    html += `<div class="notes-section note-entries-list">
        <h5>${t('detail.noteHistory')}</h5>
        <div class="subtask-notes-content" id="stViewNoteEntries_${stId}"><em>${t('common.loading')}</em></div>
    </div>`;

    html += `</div>`;
    detailElement.innerHTML = html;

    if (isAdmin) {
        createMarkdownField(`stViewDescription_${stId}`, row.description || '', {
            url: `/api/subtasks/${stId}`, model: row,
        });
        if (projectId) loadAdminNotes(`/api/tasks/${projectId}/subtasks/${stId}/notes`, `stViewAllNotes_${stId}`, currentUser.id);
    }

    // Notizen laden und Editor initialisieren
    if (projectId) {
        try {
            const notesResp = await fetch(`/api/tasks/${projectId}/subtasks/${stId}/notes`);
            if (!notesResp.ok) throw new Error(`HTTP ${notesResp.status}`);
            const notesData = await notesResp.json();
            const myNote = (notesData.items || []).find(n => n.user_id === currentUser.id);
            createMarkdownField(`stViewNotes_${stId}`, myNote?.content || '', {
                url: `/api/tasks/${projectId}/subtasks/${stId}/notes`, field: 'content', model: myNote, label: t('detail.myNotes'),
            });
        } catch (e) {
            document.getElementById(`stViewNotes_${stId}`).textContent = t('detail.loadError');
        }
    } else {
        document.getElementById(`stViewNotes_${stId}`).textContent = t('detail.loadError');
    }

    if (projectId) {
        loadSubtaskNoteEntries(projectId, stId, `stViewNoteEntries_${stId}`);
    } else {
        const entriesContent = document.getElementById(`stViewNoteEntries_${stId}`);
        if (entriesContent) entriesContent.innerHTML = `<em>${t('detail.noNoteEntries')}</em>`;
    }

    // Auto-Save: nur bearbeitbare Felder
    ['stViewStatus', 'stViewName', 'stViewDeadline', 'stViewPriority', 'stViewAssigned'].forEach(prefix => {
        const input = document.getElementById(`${prefix}_${stId}`);
        if (input && !input.disabled) input.addEventListener('change', () => saveSubtaskView(row, true));
    });

    // Auto-Save: Notizen-Editor mit Debounce
    const stViewNotesContent = document.querySelector(`#stViewNotes_${stId} .wysiwyg-content`);
    if (stViewNotesContent) {
        stViewNotesContent.addEventListener('input', () => {
            autoSaveDebounced(`stViewNotes_${stId}`, () => saveSubtaskView(row, true));
        });
    }
}

async function saveSubtaskView(row, silent = false) {
    const stId = row._subtask_id || Math.abs(row.id);
    const projectId = row._project_id;
    const statusInput = document.getElementById(`stViewStatus_${stId}`);
    if (statusInput && !statusInput.reportValidity()) return;

    try {
        // 1. Prozentwert speichern und auch in der Haupttabelle aktualisieren.
        if (statusInput) {
            const statusPercent = statusInput.valueAsNumber;
            const body = { status_percent: statusPercent };
            if (currentUser?.is_admin) {
                body.name = document.getElementById(`stViewName_${stId}`).value;
                body.deadline = document.getElementById(`stViewDeadline_${stId}`).value;
                body.priority = document.getElementById(`stViewPriority_${stId}`).valueAsNumber || 50;
                body.assigned_to = Number(document.getElementById(`stViewAssigned_${stId}`).value) || 0;
            }
            const resp = await fetch(`/api/subtasks/${stId}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            if (!resp.ok) throw new Error(t('common.saveError'));
            if (currentUser?.is_admin) {
                row.name = body.name;
                row.priority = body.priority;
                row.deadline = body.deadline ? body.deadline.split('-').reverse().join('.') : '';
                row.assigned_to = body.assigned_to || null;
                const assignee = cachedUsers.find(u => u.id === row.assigned_to);
                row.assigned_to_name = assignee ? `${assignee.vorname} ${assignee.nachname}`.trim() : '';
            }
            row._status_percent = statusPercent;
            row.status = statusPercent >= 100 ? 'erledigt' : statusPercent > 0 ? 'in_arbeit' : 'offen';
            const statusIndex = aufgabenTable.config.columns.findIndex(col => col.field === 'status');
            const cells = document.querySelector(`[data-row-id="${row.id}"] .table-row`)?.querySelectorAll('.table-cell');
            if (cells?.[statusIndex]) cells[statusIndex].innerHTML = renderSubtaskProgress(statusPercent);
            if (currentUser?.is_admin && cells) {
                aufgabenTable.config.columns.forEach((col, index) => {
                    if (cells[index] && ['name', 'priority', 'deadline', 'assigned_to_name'].includes(col.field)) {
                        cells[index].innerHTML = aufgabenTable.renderCell(row[col.field], col, row);
                    }
                });
            }
        }

        // 2. Notizen speichern (nur wenn project_id bekannt)
        if (projectId) {
            const notesContainer = document.getElementById(`stViewNotes_${stId}`);
            const notesContent = notesContainer?.querySelector('.wysiwyg-content');
            if (notesContent) {
                await fetch(`/api/tasks/${projectId}/subtasks/${stId}/notes`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ content: sanitizeHtml(notesContent.innerHTML) }),
                });
            }
        }

        if (!silent) {
            showNotification(t('subtask.saved'), 'success');
            await aufgabenTable.loadData();
        }
    } catch (error) {
        console.error('Speichern fehlgeschlagen:', error);
        showNotification(t('common.saveError'), 'error');
    }
}

async function deleteTask(taskId) {
    if (!await msgbox('cancel/yes', 'confirm', t('tasks.deleteConfirm'))) return;

    try {
        const resp = await fetch(`/api/tasks/${taskId}`, { method: 'DELETE' });
        if (!resp.ok) throw new Error(t('common.deleteError'));

        showNotification(t('tasks.deleted'), 'success');
        await aufgabenTable.loadData();
    } catch (error) {
        console.error('Loeschen fehlgeschlagen:', error);
        showNotification(t('common.deleteError'), 'error');
    }
}

// ========================================
// SubTasks
// ========================================

async function loadSubTasks(taskId) {
    const container = document.getElementById(`subtaskContainer_${taskId}`);
    if (!container) return;

    // Expanded-State merken
    const expandedIds = [];
    container.querySelectorAll('tr.subtask-row.expanded').forEach(row => {
        expandedIds.push(parseInt(row.dataset.subtaskId));
    });

    try {
        const resp = await fetch(`/api/tasks/${taskId}/subtasks`);
        const data = await resp.json();
        renderSubTasks(taskId, data.items || []);

        // Projektknoten-Position speichern
        if (container) {
            container._netzplanProjectPos = {
                x: data.netzplan_project_x ?? null,
                y: data.netzplan_project_y ?? null,
            };
        }

        // Expanded-State wiederherstellen
        expandedIds.forEach(id => toggleSubTaskDetail(taskId, id));
    } catch (error) {
        console.error('SubTasks laden fehlgeschlagen:', error);
        container.innerHTML = `<div class="table-error">${t('common.loadError')}</div>`;
    }
}

function renderSubTasks(taskId, subtasks) {
    const container = document.getElementById(`subtaskContainer_${taskId}`);
    if (!container) return;

    // Subtask-Daten fuer spaetere Verwendung speichern
    container._subtasksData = subtasks;

    // Parent-Task Permissions bestimmen
    const parentRow = aufgabenTable.filteredData.find(r => String(r.id) === String(taskId));
    const parentPerm = parentRow ? getTaskPermissions(parentRow) : { isCreator: false, isLegacy: true, isAdmin: !!currentUser?.is_admin };

    const areasOptions = buildAreaOptions();
    const colCount = 12;

    let html = `<table class="subtask-table">
        <thead>
            <tr>
                <th class="pos-cell">${t('subtask.col.pos')}</th>
                <th class="subtask-id-cell">${t('subtask.col.id')}</th>
                <th>${t('subtask.col.name')}</th>
                <th>${t('subtask.col.predecessor')}</th>
                <th>${t('subtask.col.area')}</th>
                <th>${t('subtask.col.from')}</th>
                <th>${t('subtask.col.assignedTo')}</th>
                <th>${t('subtask.col.created')}</th>
                <th>${t('subtask.col.deadline')}</th>
                <th>${t('subtask.col.priority')}</th>
                <th>${t('subtask.col.status')}</th>
                <th></th>
            </tr>
        </thead>
        <tbody>`;

    subtasks.forEach(st => {
        const predIds = st.predecessor_ids || [];
        const predDisplay = st.predecessors_display || '';
        const statusPct = st.status_percent || 0;
        const stPerms = st.permissions || { can_read: true, can_edit: true, can_create: true };
        const canEdit = parentPerm.isAdmin || stPerms.can_edit;
        const isSubAssigned = !!st.assigned_to;
        const isSubCreator = parentPerm.isCreator || parentPerm.isLegacy;

        // Readonly-Logik pro Feld
        const stNameRO = (!canEdit || (!parentPerm.isAdmin && isSubCreator && isSubAssigned)) ? 'disabled' : '';
        const stNameROClass = stNameRO ? 'field-readonly' : '';
        const fieldRO = !canEdit ? 'disabled' : '';
        const fieldROClass = !canEdit ? 'field-readonly' : '';
        const stDeadlineRO = !canEdit ? 'disabled' : '';
        const stDeadlineROClass = stDeadlineRO ? 'field-readonly' : '';
        const stPrioRO = !canEdit ? 'disabled' : '';
        const stPrioROClass = stPrioRO ? 'field-readonly' : '';
        const stStatusRO = (!canEdit || (!parentPerm.isAdmin && isSubCreator && isSubAssigned)) ? 'disabled' : '';
        const dlISO = convertToISO(st.deadline);

        // Vorgaenger-Optionen und Chips (positionsunabhaengig, aber ohne Zyklen/Redundanz)
        const transitivePreds = getTransitivePredecessors(st.id, subtasks);
        const transitiveSuccessors = getTransitiveSuccessors(st.id, subtasks);
        let predOptions = '';
        // "Pos 0: Projekt" nur anbieten wenn keine Vorgaenger vorhanden
        if (predIds.length === 0) {
            const projName = parentRow ? parentRow.name : 'Projekt';
            predOptions += `<option value="0">Pos. 0: ${escapeHtml(projName)}</option>`;
        }
        predOptions += subtasks
            .filter(other =>
                other.id !== st.id &&
                !transitivePreds.has(other.id) &&
                !transitiveSuccessors.has(other.id)
            )
            .map(other => `<option value="${other.id}">Pos. ${other.position_number}: ${escapeHtml(other.name)}</option>`)
            .join('');

        const predChips = predIds.map(pid => {
            const pred = subtasks.find(s => s.id === pid);
            const posLabel = pred ? (pid === 0 ? '0' : pred.position_number) : (pid === 0 ? '0' : '?');
            return `<span class="predecessor-chip" data-pred-id="${pid}"><span>${posLabel}</span><button class="remove" onclick="event.stopPropagation(); removePredecessor(${taskId}, ${st.id}, ${pid})">&times;</button></span>`;
        }).join('');

        // Daten-Zeile mit Dual-Content (Text + verstecktes Input)
        html += `<tr class="subtask-row" data-subtask-id="${st.id}" onpointerdown="ExpandableTable.trackRowPointerDown(event)" onclick="if (ExpandableTable.shouldToggleRow(event)) toggleSubTaskDetail(${taskId}, ${st.id})">
            <td class="pos-cell"><span class="subtask-expand-icon">&#9654;</span><span class="pos-number">${st.position_number || ''}</span><span class="pos-arrows st-cell-edit"><button class="pos-arrow up" onclick="event.stopPropagation(); moveSubTask(${taskId}, ${st.id}, 'up')" title="Nach oben">&#9650;</button><button class="pos-arrow down" onclick="event.stopPropagation(); moveSubTask(${taskId}, ${st.id}, 'down')" title="Nach unten">&#9660;</button></span></td>
            <td class="subtask-id-cell">${st.id}</td>
            ${!stNameRO ? `<td>
                <span class="st-cell-text">${escapeHtml(st.name)}</span>
                <span class="st-cell-edit"><input type="text" id="stEditName_${st.id}" value="${escapeAttr(st.name)}" class="subtask-name-input" onclick="event.stopPropagation()"></span>
            </td>` : `<td>${escapeHtml(st.name)}</td>`}
            ${canEdit ? `<td class="predecessor-cell">
                <span class="st-cell-text">${predDisplay ? `<span class="predecessor-display">${escapeHtml(predDisplay)}</span>` : '-'}</span>
                <span class="st-cell-edit">
                    <select id="predSelect_${st.id}" onclick="event.stopPropagation()" onchange="addPredecessorDirect(${taskId}, ${st.id}, this)">
                        <option value="">${t('subtask.addPred')}</option>
                        ${predOptions}
                    </select>
                    <div class="st-pred-chips" id="predChips_${st.id}">${predChips}</div>
                </span>
            </td>` : `<td>${predDisplay ? `<span class="predecessor-display">${escapeHtml(predDisplay)}</span>` : '-'}</td>`}
            ${canEdit ? `<td>
                <span class="st-cell-text">${escapeHtml(st.area_name || '-')}</span>
                <span class="st-cell-edit">
                    <div class="area-select-wrapper">
                        <select id="stEditArea_${st.id}" onclick="event.stopPropagation()">
                            <option value="">-</option>
                            ${areasOptions.map(a => `<option value="${a.id}" ${a.id === st.area_id ? 'selected' : ''}>${escapeHtml(a.name)}</option>`).join('')}
                        </select>
                        <button class="area-add-btn" onclick="event.stopPropagation(); addNewAreaInDetail(this, ${taskId}, ${st.id})" title="Neuer Bereich">+</button>
                    </div>
                </span>
            </td>` : `<td>${escapeHtml(st.area_name || '-')}</td>`}
            <td>${escapeHtml(st.created_by_name || '-')}</td>
            ${(parentPerm.isAdmin || parentPerm.isCreator || parentPerm.isLegacy) ? `<td>
                <span class="st-cell-text">${escapeHtml(st.assigned_to_name || '-')}</span>
                <span class="st-cell-edit">
                    <select id="stEditAssigned_${st.id}" onclick="event.stopPropagation()">
                        ${buildUserOptions(st.assigned_to)}
                    </select>
                </span>
            </td>` : `<td>${escapeHtml(st.assigned_to_name || '-')}</td>`}
            <td>${escapeHtml(st.created_at)}</td>
            ${!stDeadlineRO ? `<td>
                <span class="st-cell-text">${escapeHtml(st.deadline || '-')}</span>
                <span class="st-cell-edit"><input type="date" id="stEditDeadline_${st.id}" value="${dlISO}" onclick="event.stopPropagation()"></span>
            </td>` : `<td>${escapeHtml(st.deadline || '-')}</td>`}
            ${!stPrioRO ? `<td>
                <span class="st-cell-text">${renderPriority(st.priority)}</span>
                <span class="st-cell-edit"><input type="number" id="stEditPriority_${st.id}" value="${st.priority}" min="1" max="100" style="width:60px" onclick="event.stopPropagation()"></span>
            </td>` : `<td>${renderPriority(st.priority)}</td>`}
            ${!stStatusRO ? `<td>
                <span class="st-cell-text">${renderSubtaskProgress(statusPct)}</span>
                <span class="st-cell-edit"><input type="number" id="stEditStatus_${st.id}" value="${escapeAttr(statusPct)}" min="0" max="100" step="1" required onclick="event.stopPropagation()"></span>
            </td>` : `<td>${renderSubtaskProgress(statusPct)}</td>`}`;

        // Delete-Button (Ersteller, Legacy oder Admin)
        html += `<td>`;
        if (parentPerm.isCreator || parentPerm.isLegacy || parentPerm.isAdmin) {
            html += `<div class="subtask-actions">
                    <button class="subtask-btn delete" onclick="event.stopPropagation(); deleteSubTask(${taskId}, ${st.id})">x</button>
                </div>`;
        }
        html += `</td></tr>`;

        // Detail-Zeile (versteckt): nur noch Beschreibung/WYSIWYG
        html += `<tr class="subtask-detail-row" data-subtask-detail-id="${st.id}">
            <td colspan="${colCount}">
                <div class="subtask-detail-content">`;

        // WYSIWYG oder Readonly-Beschreibung
        if (!parentPerm.isAdmin && isSubCreator && isSubAssigned) {
            html += `<div class="description-readonly markdown-body">${renderMarkdown(st.description, st.description_format) || `<em>${t('detail.noDescription')}</em>`}</div>`;
        } else if (canEdit) {
            html += `<div id="stWysiwyg_${st.id}"></div>`;
        } else {
            html += `<div class="description-readonly markdown-body">${renderMarkdown(st.description, st.description_format) || `<em>${t('detail.noDescription')}</em>`}</div>`;
        }

        html += `<div class="notes-section subtask-notes-list">
            <h5>${t('detail.notes')}</h5>
            <div class="subtask-notes-content" id="stAllNotesContent_${st.id}"><em>${t('common.loading')}</em></div>
        </div>`;

        html += `<div class="notes-section note-entries-list">
            <h5>${t('detail.noteHistory')}</h5>
            <div class="subtask-notes-content" id="stNoteEntriesContent_${st.id}"><em>${t('common.loading')}</em></div>
        </div>`;

        html += `</div>
            </td>
        </tr>`;
    });

    html += `</tbody></table>`;

    // "+ Teilaufgabe"-Button: Admin, Ersteller, Legacy oder Teammitglieder mit can_create
    const firstSt = subtasks[0];
    const canCreate = parentPerm.isAdmin || parentPerm.isCreator || parentPerm.isLegacy || (firstSt && firstSt.permissions && firstSt.permissions.can_create);
    if (canCreate || subtasks.length === 0) {
        html += `<div class="subtask-add-row">
            <button class="control-btn" onclick="addSubTask(${taskId})">${t('subtask.add')}</button>
        </div>`;
    }

    container.innerHTML = html;
}

function renderSubTaskNotesList(notes) {
    if (!notes || notes.length === 0) {
        return `<em>${t('detail.noNotes')}</em>`;
    }

    return notes.map(note => {
        const userName = note.user_name || `User ${note.user_id}`;
        const updatedAt = note.updated_at ? `<span>${escapeHtml(note.updated_at)}</span>` : '';
        return `<div class="subtask-note-item">
            <div class="subtask-note-meta">
                <strong>${escapeHtml(userName)}</strong>
                ${updatedAt}
            </div>
            <div class="subtask-note-body markdown-body">${renderMarkdown(note.content || '', note.content_format) || `<em>${t('detail.noNotes')}</em>`}</div>
        </div>`;
    }).join('');
}

function mountAdminNoteEditors(container, items, url, isHandoff = false) {
    if (!currentUser?.is_admin) return;
    const bodies = container.querySelectorAll('.subtask-note-body');
    items.forEach((item, index) => {
        const body = bodies[index];
        if (!body) return;
        const id = isHandoff ? item.id : item.user_id;
        const editorId = `${container.id}_editor_${id}`;
        body.classList.remove('markdown-body');
        body.innerHTML = `<div id="${editorId}"></div>`;
        createMarkdownField(editorId, item.content || '', {
            url: `${url}/${id}`, field: 'content', model: item,
            label: t(isHandoff ? 'detail.noteHistory' : 'detail.notes'),
            onSaved: source => {
                if (isHandoff) {
                    const preview = body.closest('.note-entry-item')?.querySelector('.note-entry-preview');
                    if (preview) preview.textContent = getNoteEntryPreview(renderMarkdown(source)) || t('detail.noNoteEntries');
                }
            },
        });
    });
}

async function loadAdminNotes(url, elementId, excludeUserId = null) {
    const container = document.getElementById(elementId);
    if (!container) return;
    try {
        const response = await fetch(url);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();
        const notes = (data.items || []).filter(note => note.user_id !== excludeUserId);
        container.innerHTML = renderSubTaskNotesList(notes);
        mountAdminNoteEditors(container, notes, url);
    } catch (error) {
        container.innerHTML = `<em>${t('detail.loadError')}</em>`;
    }
}

function getNoteEntryPreview(html) {
    const doc = new DOMParser().parseFromString(html, 'text/html');

    // Zeilen- und Blockgrenzen als Leerzeichen erhalten, damit Woerter aus
    // aufeinanderfolgenden Absaetzen in der Vorschau nicht zusammenlaufen.
    doc.body.querySelectorAll('br, p, div, pre, h1, h2, h3, h4, h5, h6, li, blockquote, td, th, hr').forEach(node => {
        node.after(doc.createTextNode(' '));
    });

    return (doc.body.textContent || '').replace(/\s+/g, ' ').trim();
}

function renderNoteEntriesList(entries) {
    if (!entries || entries.length === 0) {
        return `<em>${t('detail.noNoteEntries')}</em>`;
    }

    return entries.map(entry => {
        const userName = entry.user_name || `User ${entry.user_id}`;
        const createdAt = entry.created_at ? `<span>${escapeHtml(entry.created_at)}</span>` : '';
        const sanitizedContent = renderMarkdown(entry.content || '', entry.content_format);
        const emptyText = t('detail.noNoteEntries');
        const preview = getNoteEntryPreview(sanitizedContent) || emptyText;
        const body = sanitizedContent || `<em>${emptyText}</em>`;
        return `<details class="subtask-note-item note-entry-item">
            <summary class="note-entry-summary">
                <span class="subtask-note-meta">
                    <strong>${escapeHtml(userName)}</strong>
                    <span class="note-entry-meta-end">
                        ${createdAt}
                        <span class="note-entry-toggle-icon" aria-hidden="true">&#9660;</span>
                    </span>
                </span>
                <span class="note-entry-preview">${escapeHtml(preview)}</span>
            </summary>
            <div class="subtask-note-body markdown-body">${body}</div>
        </details>`;
    }).join('');
}

async function loadTaskNoteEntries(taskId) {
    const entriesContent = document.getElementById(`taskNoteEntries_${taskId}`);
    if (!entriesContent) return;
    try {
        const resp = await fetch(`/api/tasks/${taskId}/note-entries`);
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();
        entriesContent.innerHTML = renderNoteEntriesList(data.items || []);
        mountAdminNoteEditors(entriesContent, data.items || [], `/api/tasks/${taskId}/note-entries`, true);
    } catch (e) {
        entriesContent.innerHTML = `<em>${t('detail.loadError')}</em>`;
    }
}

async function loadSubtaskNoteEntries(taskId, subtaskId, elementId) {
    const entriesContent = document.getElementById(elementId);
    if (!entriesContent) return;
    try {
        const resp = await fetch(`/api/tasks/${taskId}/subtasks/${subtaskId}/note-entries`);
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();
        entriesContent.innerHTML = renderNoteEntriesList(data.items || []);
        mountAdminNoteEditors(entriesContent, data.items || [], `/api/tasks/${taskId}/subtasks/${subtaskId}/note-entries`, true);
    } catch (e) {
        entriesContent.innerHTML = `<em>${t('detail.loadError')}</em>`;
    }
}

async function toggleSubTaskDetail(taskId, subtaskId) {
    const dataRow = document.querySelector(`tr.subtask-row[data-subtask-id="${subtaskId}"]`);
    const detailRow = document.querySelector(`tr.subtask-detail-row[data-subtask-detail-id="${subtaskId}"]`);
    if (!dataRow || !detailRow) return;

    const isExpanding = !dataRow.classList.contains('expanded');
    const rowTop = dataRow.getBoundingClientRect().top;

    dataRow.classList.toggle('expanded');
    detailRow.classList.toggle('visible');

    // Beim Zuklappen: Scroll-Position korrigieren
    if (!isExpanding) {
        const newRowTop = dataRow.getBoundingClientRect().top;
        window.scrollBy(0, newRowTop - rowTop);
    }

    // Beim Aufklappen: Editoren initialisieren + Notizen laden
    if (isExpanding) {
        const editorContainer = document.getElementById(`stWysiwyg_${subtaskId}`);
        if (editorContainer && !editorContainer._editorInit) {
            const container = document.getElementById(`subtaskContainer_${taskId}`);
            const subtasks = container?._subtasksData || [];
            const st = subtasks.find(s => s.id === subtaskId);
            const stPerms = st?.permissions || { can_edit: true };
            if (currentUser?.is_admin || stPerms.can_edit) {
                createMarkdownField(`stWysiwyg_${subtaskId}`, st?.description || '', {
                    url: `/api/subtasks/${subtaskId}`, model: st,
                });
            }
            editorContainer._editorInit = true;
        }

        // Alle Teilaufgaben-Notizen lazy laden, inkl. MCP-/Agent-Notizen.
        const notesContent = document.getElementById(`stAllNotesContent_${subtaskId}`);
        if (notesContent && !notesContent._loaded) {
            notesContent._loaded = true;
            try {
                const notesResp = await fetch(`/api/tasks/${taskId}/subtasks/${subtaskId}/notes`);
                if (!notesResp.ok) throw new Error(`HTTP ${notesResp.status}`);
                const notesData = await notesResp.json();
                notesContent.innerHTML = renderSubTaskNotesList(notesData.items || []);
                mountAdminNoteEditors(notesContent, notesData.items || [], `/api/tasks/${taskId}/subtasks/${subtaskId}/notes`);
            } catch (e) {
                notesContent.innerHTML = `<em>${t('detail.loadError')}</em>`;
            }
        }

        const entriesContent = document.getElementById(`stNoteEntriesContent_${subtaskId}`);
        if (entriesContent && !entriesContent._loaded) {
            entriesContent._loaded = true;
            loadSubtaskNoteEntries(taskId, subtaskId, `stNoteEntriesContent_${subtaskId}`);
        }

        // Auto-Save: Inline-Felder (in der Daten-Zeile) bei Aenderung sofort speichern
        if (!dataRow._autoSaveInit) {
            dataRow._autoSaveInit = true;
            ['stEditName', 'stEditArea', 'stEditDeadline', 'stEditPriority', 'stEditStatus', 'stEditAssigned'].forEach(prefix => {
                const el = document.getElementById(`${prefix}_${subtaskId}`);
                if (el && !el.disabled) {
                    el.addEventListener('change', () => saveSubTask(taskId, subtaskId, true));
                }
            });

            // Auto-Save: WYSIWYG-Editor mit Debounce
            const stEditorContent = document.querySelector(`#stWysiwyg_${subtaskId} .wysiwyg-content`);
            if (stEditorContent) {
                stEditorContent.addEventListener('input', () => {
                    autoSaveDebounced(`subtask_${subtaskId}`, () => saveSubTask(taskId, subtaskId, true));
                });
            }
        }
    }
}

async function saveSubTask(taskId, subtaskId, silent = false) {
    const container = document.getElementById(`subtaskContainer_${taskId}`);
    const subtasks = container?._subtasksData || [];
    const st = subtasks.find(s => s.id === subtaskId);

    const body = {};

    const nameInput = document.getElementById(`stEditName_${subtaskId}`);
    if (nameInput && !nameInput.disabled) body.name = nameInput.value;

    body.area_id = parseInt(document.getElementById(`stEditArea_${subtaskId}`)?.value) || null;

    const dlInput = document.getElementById(`stEditDeadline_${subtaskId}`);
    if (dlInput && !dlInput.disabled) body.deadline = dlInput.value || '';

    const prioInput = document.getElementById(`stEditPriority_${subtaskId}`);
    if (prioInput && !prioInput.disabled) body.priority = parseInt(prioInput.value) || 50;

    const statusInput = document.getElementById(`stEditStatus_${subtaskId}`);
    if (statusInput && !statusInput.disabled) {
        if (!statusInput.reportValidity()) return;
        body.status_percent = statusInput.valueAsNumber;
    }

    // WYSIWYG-Content nur senden wenn Editor existiert (nicht bei readonly-Beschreibung)
    const editorContainer = document.getElementById(`stWysiwyg_${subtaskId}`);
    const editorContent = editorContainer?.querySelector('.wysiwyg-content');
    if (editorContent) body.description = sanitizeHtml(editorContent.innerHTML);

    // Aktuelle predecessor_ids sammeln
    const chipsContainer = document.getElementById(`predChips_${subtaskId}`);
    const currentChips = chipsContainer ? chipsContainer.querySelectorAll('.predecessor-chip') : [];
    body.predecessor_ids = Array.from(currentChips).map(c => parseInt(c.dataset.predId));

    // Zuweisung
    const assignSelect = document.getElementById(`stEditAssigned_${subtaskId}`);
    if (assignSelect) {
        const val = assignSelect.value;
        body.assigned_to = val ? parseInt(val) : 0;
    }

    try {
        const resp = await fetch(`/api/subtasks/${subtaskId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (!resp.ok) throw new Error(t('common.saveError'));

        if (!silent) {
            showNotification(t('subtask.saved'), 'success');
            await loadSubTasks(taskId);
        } else if (st) {
            // Lokale Daten aktualisieren damit Zuklappen korrekte Werte zeigt
            if (body.name !== undefined) st.name = body.name;
            if (body.area_id !== undefined) st.area_id = body.area_id;
            if (body.deadline !== undefined) st.deadline = body.deadline;
            if (body.priority !== undefined) st.priority = body.priority;
            if (body.status_percent !== undefined) st.status_percent = body.status_percent;
            if (body.assigned_to !== undefined) st.assigned_to = body.assigned_to;
            if (body.description !== undefined) st.description = body.description;

            // Text-Spans in der Tabellenzeile aktualisieren
            const dataRow = document.querySelector(`tr.subtask-row[data-subtask-id="${subtaskId}"]`);
            if (dataRow) {
                const cells = dataRow.querySelectorAll('td');
                cells.forEach(cell => {
                    const textSpan = cell.querySelector('.st-cell-text');
                    if (!textSpan) return;
                    const input = cell.querySelector('input, select');
                    if (!input) return;
                    const inputId = input.id || '';
                    if (inputId.startsWith('stEditName_')) {
                        textSpan.textContent = input.value;
                    } else if (inputId.startsWith('stEditStatus_')) {
                        textSpan.innerHTML = renderSubtaskProgress(st.status_percent);
                    } else if (inputId.startsWith('stEditPriority_')) {
                        textSpan.textContent = input.value;
                    } else if (inputId.startsWith('stEditDeadline_')) {
                        textSpan.textContent = input.value || '-';
                    } else if (inputId.startsWith('stEditArea_')) {
                        const opt = input.options[input.selectedIndex];
                        textSpan.textContent = opt && opt.value ? opt.textContent : '-';
                        st.area_name = opt && opt.value ? opt.textContent : '';
                    } else if (inputId.startsWith('stEditAssigned_')) {
                        const opt = input.options[input.selectedIndex];
                        textSpan.textContent = opt && opt.value ? opt.textContent : '-';
                        st.assigned_to_name = opt && opt.value ? opt.textContent : '';
                    }
                });
            }
        }
    } catch (error) {
        console.error('SubTask speichern fehlgeschlagen:', error);
        showNotification(t('common.saveError'), 'error');
    }
}

async function addNewAreaInDetail(btn, taskId, subtaskId) {
    const name = prompt(t('subtask.newAreaPrompt'));
    if (!name || !name.trim()) return;

    try {
        const resp = await fetch('/api/areas', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: name.trim() }),
        });
        if (!resp.ok) throw new Error(t('common.error'));

        const data = await resp.json();
        cachedAreas.push({ id: data.id, name: data.name });

        // Dropdown aktualisieren und neuen Bereich auswaehlen
        const select = document.getElementById(`stEditArea_${subtaskId}`);
        if (select) {
            const option = document.createElement('option');
            option.value = data.id;
            option.textContent = data.name;
            option.selected = true;
            select.appendChild(option);
        }

        showNotification(t('subtask.areaCreated', { name: data.name }), 'success');
    } catch (error) {
        showNotification(t('subtask.areaExistsError'), 'error');
    }
}

function buildAreaOptions() {
    return cachedAreas || [];
}

async function updateSubTask(subtaskId, field, value) {
    try {
        const body = {};
        body[field] = value;
        const resp = await fetch(`/api/subtasks/${subtaskId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (!resp.ok) throw new Error(t('common.error'));
    } catch (error) {
        console.error('SubTask-Update fehlgeschlagen:', error);
        showNotification(t('team.updateError'), 'error');
    }
}

async function addSubTask(taskId) {
    try {
        const resp = await fetch(`/api/tasks/${taskId}/subtasks`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: t('subtask.newName') }),
        });
        if (!resp.ok) throw new Error(t('common.error'));

        await loadSubTasks(taskId);
    } catch (error) {
        console.error('SubTask anlegen fehlgeschlagen:', error);
        showNotification(t('common.createError'), 'error');
    }
}

async function moveSubTask(taskId, subtaskId, direction) {
    const resp = await fetch(`/api/subtasks/${subtaskId}/move`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ direction }),
    });
    if (!resp.ok) {
        showNotification(t('subtask.moveFailed'), 'error');
        return;
    }
    const data = await resp.json();
    if (data.removed_dependencies && data.removed_dependencies.length > 0) {
        showNotification(t('subtask.moveRemovedDeps', { count: data.removed_dependencies.length }), 'info');
    }
    await loadSubTasks(taskId);
}

async function deleteSubTask(taskId, subtaskId) {
    if (!await msgbox('cancel/yes', 'confirm', t('subtask.deleteConfirm'))) return;

    try {
        const resp = await fetch(`/api/subtasks/${subtaskId}`, { method: 'DELETE' });
        if (!resp.ok) throw new Error(t('common.error'));

        await loadSubTasks(taskId);
    } catch (error) {
        console.error('SubTask loeschen fehlgeschlagen:', error);
        showNotification(t('common.deleteError'), 'error');
    }
}

// ========================================
// Vorgaenger (Predecessors)
// ========================================

function getTransitivePredecessors(subtaskId, subtasks) {
    const visited = new Set();
    const st = subtasks.find(s => s.id === subtaskId);
    if (!st) return visited;
    const queue = [...(st.predecessor_ids || [])];
    while (queue.length > 0) {
        const id = queue.shift();
        if (visited.has(id)) continue;
        visited.add(id);
        const pred = subtasks.find(s => s.id === id);
        if (pred && pred.predecessor_ids) {
            queue.push(...pred.predecessor_ids);
        }
    }
    return visited;
}

function getTransitiveSuccessors(subtaskId, subtasks) {
    const visited = new Set();
    const queue = [subtaskId];
    while (queue.length > 0) {
        const id = queue.shift();
        subtasks.forEach(st => {
            if ((st.predecessor_ids || []).includes(id) && !visited.has(st.id)) {
                visited.add(st.id);
                queue.push(st.id);
            }
        });
    }
    return visited;
}

async function addPredecessor(taskId, subtaskId) {
    const select = document.getElementById(`predSelect_${subtaskId}`);
    if (!select || !select.value) return;

    const predId = parseInt(select.value);

    // Aktuelle Chips lesen um Duplikate zu vermeiden
    const chipsContainer = document.getElementById(`predChips_${subtaskId}`);
    if (chipsContainer && chipsContainer.querySelector(`[data-pred-id="${predId}"]`)) {
        showNotification(t('subtask.predExists'), 'error');
        return;
    }

    // Aktuelle predecessor_ids sammeln
    const currentChips = chipsContainer ? chipsContainer.querySelectorAll('.predecessor-chip') : [];
    const predIds = Array.from(currentChips).map(c => parseInt(c.dataset.predId));
    predIds.push(predId);

    try {
        const resp = await fetch(`/api/subtasks/${subtaskId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ predecessor_ids: predIds }),
        });
        if (!resp.ok) throw new Error(t('common.error'));

        // Tabelle neu laden
        await loadSubTasks(taskId);
    } catch (error) {
        console.error('Vorgaenger hinzufuegen fehlgeschlagen:', error);
        showNotification(t('team.addError'), 'error');
    }
}

async function addPredecessorDirect(taskId, subtaskId, selectEl) {
    if (!selectEl || !selectEl.value) return;

    const predId = parseInt(selectEl.value);

    // Aktuelle Chips lesen um Duplikate zu vermeiden
    const chipsContainer = document.getElementById(`predChips_${subtaskId}`);
    if (chipsContainer && chipsContainer.querySelector(`[data-pred-id="${predId}"]`)) {
        showNotification(t('subtask.predExists'), 'error');
        selectEl.value = '';
        return;
    }

    // Aktuelle predecessor_ids sammeln
    const currentChips = chipsContainer ? chipsContainer.querySelectorAll('.predecessor-chip') : [];
    const predIds = Array.from(currentChips).map(c => parseInt(c.dataset.predId));
    predIds.push(predId);

    // Select zuruecksetzen
    selectEl.value = '';

    try {
        const resp = await fetch(`/api/subtasks/${subtaskId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ predecessor_ids: predIds }),
        });
        if (!resp.ok) throw new Error(t('common.error'));

        // Tabelle neu laden
        await loadSubTasks(taskId);
    } catch (error) {
        console.error('Vorgaenger hinzufuegen fehlgeschlagen:', error);
        showNotification(t('team.addError'), 'error');
    }
}

async function removePredecessor(taskId, subtaskId, predIdToRemove) {
    if (!await msgbox('cancel/yes', 'confirm', t('subtask.predRemoveConfirm'))) return;

    // Aktuelle predecessor_ids sammeln und die zu entfernende rausfiltern
    const chipsContainer = document.getElementById(`predChips_${subtaskId}`);
    const currentChips = chipsContainer ? chipsContainer.querySelectorAll('.predecessor-chip') : [];
    const predIds = Array.from(currentChips)
        .map(c => parseInt(c.dataset.predId))
        .filter(id => id !== predIdToRemove);

    try {
        const resp = await fetch(`/api/subtasks/${subtaskId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ predecessor_ids: predIds }),
        });
        if (!resp.ok) throw new Error(t('common.error'));

        // Tabelle neu laden
        await loadSubTasks(taskId);
    } catch (error) {
        console.error('Vorgaenger entfernen fehlgeschlagen:', error);
        showNotification(t('team.removeError'), 'error');
    }
}

async function addNewArea(btn, subtaskId) {
    const name = prompt(t('subtask.newAreaPrompt'));
    if (!name || !name.trim()) return;

    try {
        const resp = await fetch('/api/areas', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: name.trim() }),
        });
        if (!resp.ok) throw new Error(t('common.error'));

        const data = await resp.json();
        cachedAreas.push({ id: data.id, name: data.name });

        // Dropdown aktualisieren
        const select = btn.previousElementSibling;
        if (select) {
            const option = document.createElement('option');
            option.value = data.id;
            option.textContent = data.name;
            option.selected = true;
            select.appendChild(option);

            // SubTask mit neuem Bereich aktualisieren
            if (subtaskId) {
                await updateSubTask(subtaskId, 'area_id', data.id);
            }
        }

        showNotification(t('subtask.areaCreated', { name: data.name }), 'success');
    } catch (error) {
        showNotification(t('subtask.areaExistsError'), 'error');
    }
}

// ========================================
// Modal: Neue Aufgabe
// ========================================

function openNewTaskModal() {
    createModal({
        title: t('tasks.newTaskTitle'),
        body: `
            <div class="modal-field">
                <label>${t('tasks.name')}</label>
                <input type="text" id="newTaskName" placeholder="${t('tasks.namePlaceholder')}" autofocus>
            </div>
            <div class="modal-field">
                <label>${t('tasks.type')}</label>
                <select id="newTaskType">
                    <option value="aufgabe">${t('type.aufgabe')}</option>
                    <option value="projekt">${t('type.projekt')}</option>
                </select>
            </div>
            <div class="modal-field">
                <label>${t('tasks.deadline')}</label>
                <input type="date" id="newTaskDeadline">
            </div>
            <div class="modal-field">
                <label>${t('tasks.priorityRange')}</label>
                <input type="number" id="newTaskPriority" value="50" min="1" max="100">
            </div>`,
        footer: `<button class="action-btn" onclick="closeModal()">${t('common.cancel')}</button>` +
                `<button class="action-btn primary" onclick="createNewTask()">${t('common.create')}</button>`,
        onOpen: () => {
            // Enter im Name-Feld erstellt Aufgabe
            const nameInput = document.getElementById('newTaskName');
            if (nameInput) {
                nameInput.focus();
                nameInput.addEventListener('keydown', (e) => {
                    if (e.key === 'Enter') createNewTask();
                });
            }
        },
    });
}

async function createNewTask() {
    const name = document.getElementById('newTaskName')?.value?.trim();
    if (!name) {
        showNotification(t('tasks.nameRequired'), 'error');
        return;
    }

    const task_type = document.getElementById('newTaskType')?.value || 'aufgabe';
    const deadline = document.getElementById('newTaskDeadline')?.value || '';
    const priority = parseInt(document.getElementById('newTaskPriority')?.value) || 50;

    try {
        const resp = await fetch('/api/tasks', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, task_type, deadline, priority }),
        });

        if (!resp.ok) throw new Error(t('common.createError'));

        // Modal schliessen
        closeModal();

        showNotification(t('tasks.created'), 'success');
        await aufgabenTable.loadData();
    } catch (error) {
        console.error('Erstellen fehlgeschlagen:', error);
        showNotification(t('common.createError'), 'error');
    }
}

// ========================================
// Team-Dialog (Sprint C)
// ========================================

async function openTeamDialog(projectId) {
    createModal({
        title: t('team.title'),
        cssClass: 'modal-wide',
        body: `
            <div class="team-info-text">${t('team.creatorInfo')}</div>
            <div class="team-add-row">
                <select id="teamAddUserSelect">
                    <option value="">${t('team.selectUser')}</option>
                </select>
                <button class="control-btn primary" onclick="addTeamMember(${projectId})">${t('team.addMember')}</button>
            </div>
            <div id="teamMembersContainer">
                <div class="table-loading"><div class="spinner"></div></div>
            </div>`,
        footer: `<button class="action-btn" onclick="closeModal()">${t('common.close')}</button>`,
    });

    await loadTeamMembers(projectId);
}

async function loadTeamMembers(projectId) {
    const container = document.getElementById('teamMembersContainer');
    if (!container) return;

    try {
        const resp = await fetch(`/api/tasks/${projectId}/members`);
        if (!resp.ok) throw new Error(t('common.loadError'));
        const data = await resp.json();
        const members = data.items || [];

        // User-Dropdown aktualisieren (ohne Ersteller und bestehende Mitglieder)
        const parentRow = aufgabenTable.filteredData.find(r => String(r.id) === String(projectId));
        const creatorId = parentRow ? parentRow.created_by : null;
        const memberIds = members.map(m => m.user_id);

        const select = document.getElementById('teamAddUserSelect');
        if (select) {
            select.innerHTML = `<option value="">${t('team.selectUser')}</option>`;
            cachedUsers.forEach(u => {
                if (u.id === creatorId || memberIds.includes(u.id)) return;
                const name = `${u.vorname} ${u.nachname}`.trim() || `User ${u.id}`;
                select.innerHTML += `<option value="${u.id}">${escapeHtml(name)}</option>`;
            });
        }

        if (members.length === 0) {
            container.innerHTML = `<div class="team-empty">${t('team.noMembers')}</div>`;
            return;
        }

        let html = `<table class="team-table">
            <thead>
                <tr>
                    <th>${t('team.col.lastName')}</th>
                    <th>${t('team.col.firstName')}</th>
                    <th>${t('team.col.email')}</th>
                    <th style="text-align:center">${t('team.col.read')}</th>
                    <th style="text-align:center">${t('team.col.edit')}</th>
                    <th style="text-align:center">${t('team.col.create')}</th>
                    <th></th>
                </tr>
            </thead>
            <tbody>`;

        members.forEach(m => {
            html += `<tr data-member-uid="${m.user_id}">
                <td>${escapeHtml(m.nachname)}</td>
                <td>${escapeHtml(m.vorname)}</td>
                <td>${escapeHtml(m.email)}</td>
                <td style="text-align:center"><input type="checkbox" ${m.can_read ? 'checked' : ''} onchange="updateTeamMember(${projectId}, ${m.user_id}, this.closest('tr'))"></td>
                <td style="text-align:center"><input type="checkbox" ${m.can_edit ? 'checked' : ''} onchange="updateTeamMember(${projectId}, ${m.user_id}, this.closest('tr'))"></td>
                <td style="text-align:center"><input type="checkbox" ${m.can_create ? 'checked' : ''} onchange="updateTeamMember(${projectId}, ${m.user_id}, this.closest('tr'))"></td>
                <td><button class="team-remove-btn" onclick="removeTeamMember(${projectId}, ${m.user_id})">x</button></td>
            </tr>`;
        });

        html += '</tbody></table>';
        container.innerHTML = html;
    } catch (error) {
        console.error('Team laden fehlgeschlagen:', error);
        container.innerHTML = `<div class="table-error">${t('common.loadError')}</div>`;
    }
}

async function addTeamMember(projectId) {
    const select = document.getElementById('teamAddUserSelect');
    if (!select || !select.value) {
        showNotification(t('team.selectRequired'), 'error');
        return;
    }

    try {
        const resp = await fetch(`/api/tasks/${projectId}/members`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ user_id: parseInt(select.value), can_read: true }),
        });
        if (!resp.ok) {
            const err = await resp.json();
            throw new Error(err.detail || t('common.error'));
        }

        showNotification(t('team.memberAdded'), 'success');
        await loadTeamMembers(projectId);
    } catch (error) {
        showNotification(error.message || t('team.addError'), 'error');
    }
}

async function updateTeamMember(projectId, userId, row) {
    if (!row) return;
    const checkboxes = row.querySelectorAll('input[type="checkbox"]');
    const can_read = checkboxes[0]?.checked || false;
    const can_edit = checkboxes[1]?.checked || false;
    const can_create = checkboxes[2]?.checked || false;

    try {
        const resp = await fetch(`/api/tasks/${projectId}/members/${userId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ can_read, can_edit, can_create }),
        });
        if (!resp.ok) throw new Error(t('common.error'));
    } catch (error) {
        showNotification(t('team.updateError'), 'error');
    }
}

async function removeTeamMember(projectId, userId) {
    if (!await msgbox('cancel/yes', 'confirm', t('team.removeMemberConfirm'))) return;

    try {
        const resp = await fetch(`/api/tasks/${projectId}/members/${userId}`, { method: 'DELETE' });
        if (!resp.ok) throw new Error(t('common.error'));

        showNotification(t('team.memberRemoved'), 'success');
        await loadTeamMembers(projectId);
    } catch (error) {
        showNotification(t('team.removeError'), 'error');
    }
}

// ========================================
// Hilfsfunktionen
// ========================================

function convertToISO(dateStr) {
    if (!dateStr) return '';
    // DD.MM.YYYY -> YYYY-MM-DD
    const parts = dateStr.split('.');
    if (parts.length === 3) {
        return `${parts[2]}-${parts[1]}-${parts[0]}`;
    }
    return dateStr;
}

// ========================================
// Netzplan (Projekt-Aufgabengraph)
// ========================================

let netzplanNetwork = null;
let _netzplanLinkState = null;
let _netzplanRebuilding = false;

const NETZPLAN_NODE_METRICS = {
    taskMaxLineChars: 24,
    projectMaxLineChars: 28,
    taskCharWidth: 7.2,
    projectCharWidth: 7.8,
    taskLineHeight: 17,
    projectLineHeight: 18,
    marginX: 24,
    marginY: 16,
    minTaskWidth: 110,
    maxTaskWidth: 210,
    minProjectWidth: 120,
    maxProjectWidth: 240,
    levelGap: 90,
    compactLevelGap: 76,
    verticalGap: 34,
    compactVerticalGap: 22,
    horizontalGap: 44,
    topDownLevelGap: 96,
};

function getNetzplanColors() {
    const isDark = document.documentElement.getAttribute('data-theme') === 'dark';
    return {
        nodeOffen: { background: isDark ? '#4a4a5a' : '#bdc3c7', border: isDark ? '#6a6a7a' : '#95a5a6', font: isDark ? '#e8e8e8' : '#333333' },
        nodeArbeit: { background: isDark ? '#1a5276' : '#d4e6f1', border: isDark ? '#2980b9' : '#3498db', font: isDark ? '#e8e8e8' : '#333333' },
        nodeErledigt: { background: isDark ? '#1e5631' : '#d5f5e3', border: isDark ? '#27ae60' : '#28a745', font: isDark ? '#e8e8e8' : '#333333' },
        edge: isDark ? '#4a4a6a' : '#bbb',
        highlight: { background: isDark ? '#2980b9' : '#3498db', border: isDark ? '#3498db' : '#2471a3' },
        project: { background: isDark ? '#5b3a8c' : '#e8daef', border: isDark ? '#8e44ad' : '#9b59b6', font: isDark ? '#e8e8e8' : '#333333' },
    };
}

function getNodeColor(statusPercent, colors) {
    if (statusPercent >= 100) return colors.nodeErledigt;
    if (statusPercent > 0) return colors.nodeArbeit;
    return colors.nodeOffen;
}

function _splitNetzplanLongWord(word, maxLineChars) {
    const chunks = [];
    let remaining = String(word || '');
    while (remaining.length > maxLineChars) {
        chunks.push(remaining.slice(0, maxLineChars));
        remaining = remaining.slice(maxLineChars);
    }
    if (remaining) chunks.push(remaining);
    return chunks;
}

function _wrapNetzplanLabel(text, maxLineChars = NETZPLAN_NODE_METRICS.taskMaxLineChars) {
    const words = String(text || '')
        .replace(/\s+/g, ' ')
        .trim()
        .split(' ')
        .flatMap(word => word.length > maxLineChars ? _splitNetzplanLongWord(word, maxLineChars) : [word]);

    if (words.length === 0) return '';

    const lines = [];
    let current = '';
    words.forEach(word => {
        if (!current) {
            current = word;
            return;
        }
        if ((current.length + 1 + word.length) <= maxLineChars) {
            current += ` ${word}`;
        } else {
            lines.push(current);
            current = word;
        }
    });
    if (current) lines.push(current);
    return lines.join('\n');
}

function _measureNetzplanNode(label, {
    charWidth,
    lineHeight,
    minWidth,
    maxWidth,
} = {}) {
    const lines = String(label || '').split('\n');
    const maxChars = Math.max(1, ...lines.map(line => Array.from(line).length));
    const rawWidth = maxChars * charWidth + NETZPLAN_NODE_METRICS.marginX;
    return {
        width: Math.max(minWidth, Math.min(maxWidth, rawWidth)),
        height: Math.max(lineHeight + NETZPLAN_NODE_METRICS.marginY, lines.length * lineHeight + NETZPLAN_NODE_METRICS.marginY),
    };
}

function _getNetzplanTaskMeta(st) {
    const posLabel = st.position_number ? `${st.position_number}: ` : '';
    const rawLabel = `${posLabel}${st.name || ''}`;
    const label = _wrapNetzplanLabel(rawLabel, NETZPLAN_NODE_METRICS.taskMaxLineChars);
    return {
        rawLabel,
        label,
        ..._measureNetzplanNode(label, {
            charWidth: NETZPLAN_NODE_METRICS.taskCharWidth,
            lineHeight: NETZPLAN_NODE_METRICS.taskLineHeight,
            minWidth: NETZPLAN_NODE_METRICS.minTaskWidth,
            maxWidth: NETZPLAN_NODE_METRICS.maxTaskWidth,
        }),
    };
}

function _getNetzplanProjectMeta(projectName) {
    const rawLabel = projectName || 'Projekt';
    const label = _wrapNetzplanLabel(rawLabel, NETZPLAN_NODE_METRICS.projectMaxLineChars);
    return {
        rawLabel,
        label,
        ..._measureNetzplanNode(label, {
            charWidth: NETZPLAN_NODE_METRICS.projectCharWidth,
            lineHeight: NETZPLAN_NODE_METRICS.projectLineHeight,
            minWidth: NETZPLAN_NODE_METRICS.minProjectWidth,
            maxWidth: NETZPLAN_NODE_METRICS.maxProjectWidth,
        }),
    };
}

function _stackNetzplanLevel(group, positionMap, nodeMeta, x, verticalGap, centered) {
    const totalHeight = group.reduce((sum, st) => sum + nodeMeta[st.id].height, 0)
        + Math.max(0, group.length - 1) * verticalGap;
    let cursor = centered ? -totalHeight / 2 : 0;
    group.forEach(st => {
        const height = nodeMeta[st.id].height;
        positionMap[st.id] = {
            x,
            y: cursor + height / 2,
        };
        cursor += height + verticalGap;
    });
}

function _rowNetzplanLevel(group, positionMap, nodeMeta, y, horizontalGap, centered) {
    const totalWidth = group.reduce((sum, st) => sum + nodeMeta[st.id].width, 0)
        + Math.max(0, group.length - 1) * horizontalGap;
    let cursor = centered ? -totalWidth / 2 : 0;
    group.forEach(st => {
        const width = nodeMeta[st.id].width;
        positionMap[st.id] = {
            x: cursor + width / 2,
            y,
        };
        cursor += width + horizontalGap;
    });
}

function _getNetzplanEdgeDirection(fromId, toId, positionMap) {
    const from = positionMap[fromId];
    const to = positionMap[toId];
    if (!from || !to) return 'horizontal';
    return Math.abs(to.y - from.y) > Math.abs(to.x - from.x) ? 'vertical' : 'horizontal';
}

function _computeValidNetzplanTargets(sourceId, subtasks) {
    // Spezialfall: Projektknoten (Node 0) - nur Nodes ohne Vorgaenger sind gueltig
    if (sourceId === 0) {
        const valid = new Set();
        subtasks.forEach(target => {
            const predIds = target.predecessor_ids || [];
            if (predIds.length === 0) {
                valid.add(target.id);
            }
        });
        return valid;
    }

    const st = subtasks.find(s => s.id === sourceId);
    if (!st) return new Set();

    // Bereits direkte Nachfolger (sourceId ist Vorgaenger von diesen)
    const directSuccessors = new Set();
    subtasks.forEach(s => {
        if ((s.predecessor_ids || []).includes(sourceId)) directSuccessors.add(s.id);
    });

    // Transitive Nachfolger von sourceId (wuerde Zyklus erzeugen)
    const transitiveSuccessors = new Set();
    const queue = [sourceId];
    while (queue.length > 0) {
        const nid = queue.shift();
        subtasks.forEach(s => {
            if ((s.predecessor_ids || []).includes(nid) && !transitiveSuccessors.has(s.id)) {
                transitiveSuccessors.add(s.id);
                queue.push(s.id);
            }
        });
    }

    // Transitive Vorgaenger von potentiellem Ziel: Wenn sourceId bereits transitiver
    // Vorgaenger eines Ziels ist, waere die direkte Kante redundant
    const valid = new Set();
    subtasks.forEach(target => {
        if (target.id === sourceId) return; // sich selbst
        if (directSuccessors.has(target.id)) return; // bereits direkte Abhaengigkeit
        if (transitiveSuccessors.has(target.id)) return; // wuerde Zyklus erzeugen

        // Pruefen auf transitive Redundanz: ist sourceId bereits transitiver Vorgaenger von target?
        const transitivePreds = getTransitivePredecessors(target.id, subtasks);
        if (transitivePreds.has(sourceId)) return; // transitiv redundant

        valid.add(target.id);
    });

    return valid;
}

function _netzplanValue(value) {
    if (value === null || value === undefined || value === '') {
        return '<span class="netzplan-detail-empty">-</span>';
    }
    return escapeHtml(value);
}

function _netzplanDetailField(label, value) {
    return `
        <div class="netzplan-detail-field">
            <span class="netzplan-detail-label">${escapeHtml(label)}</span>
            <span class="netzplan-detail-value">${value}</span>
        </div>
    `;
}

function _netzplanStatusFromPercent(percent) {
    const pct = parseInt(percent, 10) || 0;
    if (pct >= 100) return { key: 'erledigt', label: t('status.erledigt') };
    if (pct > 0) return { key: 'in_arbeit', label: t('status.in_arbeit') };
    return { key: 'offen', label: t('status.offen') };
}

function _netzplanRenderPredecessors(st, subtasks, projectName) {
    const predIds = st.predecessor_ids || [];
    if (predIds.length === 0) return '<span class="netzplan-detail-empty">-</span>';
    return predIds.map(pid => {
        if (pid === 0) {
            return `<span class="netzplan-detail-chip">Pos. 0: ${escapeHtml(projectName || 'Projekt')}</span>`;
        }
        const pred = subtasks.find(s => s.id === pid);
        if (!pred) return `<span class="netzplan-detail-chip">#${escapeHtml(pid)}</span>`;
        return `<span class="netzplan-detail-chip">Pos. ${escapeHtml(pred.position_number || '?')}: ${escapeHtml(pred.name || '')}</span>`;
    }).join('');
}

function _openNetzplanNodeDetails(nodeId, parentRow, subtasks, projectName) {
    if (nodeId === 0) {
        const statusKey = parentRow?.status || '';
        const statusLabel = statusKey ? t(`status.${statusKey}`) : '-';
        const typeKey = parentRow?.task_type || 'projekt';
        const typeLabel = t(`type.${typeKey}`);
        const subtaskTotal = parentRow?.subtask_total ?? subtasks.length;
        const subtaskDone = parentRow?.subtask_done ?? subtasks.filter(st => (st.status_percent || 0) >= 100).length;
        const description = renderMarkdown(parentRow?.description || '', parentRow?.description_format) || `<em>${t('detail.noDescription')}</em>`;

        createModal({
            title: `${escapeHtml(typeLabel)} - Details`,
            cssClass: 'modal-wide netzplan-detail-dialog',
            maxWidth: '760px',
            body: `
                <div class="netzplan-detail-menu">
                    <div class="netzplan-detail-summary">
                        <span class="netzplan-detail-badge">Pos. 0</span>
                        <div>
                            <div class="netzplan-detail-name">${escapeHtml(projectName || parentRow?.name || 'Projekt')}</div>
                            <div class="netzplan-detail-subline">${escapeHtml(typeLabel)}</div>
                        </div>
                    </div>
                    <div class="netzplan-detail-grid">
                        ${_netzplanDetailField(t('tasks.col.status'), _netzplanValue(statusLabel))}
                        ${_netzplanDetailField(t('tasks.col.priority'), _netzplanValue(parentRow?.priority))}
                        ${_netzplanDetailField(t('tasks.col.deadline'), _netzplanValue(parentRow?.deadline))}
                        ${_netzplanDetailField(t('tasks.col.created'), _netzplanValue(parentRow?.created_at))}
                        ${_netzplanDetailField(t('tasks.col.from'), _netzplanValue(parentRow?.created_by_name))}
                        ${_netzplanDetailField(t('tasks.col.assignedTo'), _netzplanValue(parentRow?.assigned_to_name))}
                        ${_netzplanDetailField(t('subtask.title'), _netzplanValue(`${subtaskDone}/${subtaskTotal}`))}
                    </div>
                    <div class="netzplan-detail-section">
                        <div class="netzplan-detail-section-title">${t('detail.description')}</div>
                        <div class="description-readonly markdown-body">${description}</div>
                    </div>
                </div>
            `,
            footer: `<button class="action-btn" onclick="closeModal()">${t('common.close')}</button>`,
        });
        return;
    }

    const st = subtasks.find(s => s.id === nodeId);
    if (!st) return;

    const status = _netzplanStatusFromPercent(st.status_percent);
    const statusHtml = `<span class="netzplan-detail-status status-${status.key}">${escapeHtml(status.label)} (${escapeHtml(st.status_percent || 0)}%)</span>`;
    const predecessorHtml = _netzplanRenderPredecessors(st, subtasks, projectName);
    const description = renderMarkdown(st.description || '', st.description_format) || `<em>${t('detail.noDescription')}</em>`;

    createModal({
        title: `${t('subtask.title')} - Details`,
        cssClass: 'modal-wide netzplan-detail-dialog',
        maxWidth: '760px',
        body: `
            <div class="netzplan-detail-menu">
                <div class="netzplan-detail-summary">
                    <span class="netzplan-detail-badge">Pos. ${escapeHtml(st.position_number || '-')}</span>
                    <div>
                        <div class="netzplan-detail-name">${escapeHtml(st.name || '')}</div>
                        <div class="netzplan-detail-subline">${escapeHtml(projectName || '')}</div>
                    </div>
                </div>
                <div class="netzplan-detail-grid">
                    ${_netzplanDetailField(t('subtask.col.status'), statusHtml)}
                    ${_netzplanDetailField(t('subtask.col.area'), _netzplanValue(st.area_name))}
                    ${_netzplanDetailField(t('subtask.col.priority'), _netzplanValue(st.priority))}
                    ${_netzplanDetailField(t('subtask.col.deadline'), _netzplanValue(st.deadline))}
                    ${_netzplanDetailField(t('subtask.col.created'), _netzplanValue(st.created_at))}
                    ${_netzplanDetailField(t('subtask.col.from'), _netzplanValue(st.created_by_name))}
                    ${_netzplanDetailField(t('subtask.col.assignedTo'), _netzplanValue(st.assigned_to_name))}
                </div>
                <div class="netzplan-detail-section">
                    <div class="netzplan-detail-section-title">${t('subtask.col.predecessor')}</div>
                    <div class="netzplan-detail-chip-list">${predecessorHtml}</div>
                </div>
                <div class="netzplan-detail-section">
                    <div class="netzplan-detail-section-title">${t('detail.description')}</div>
                    <div class="description-readonly markdown-body">${description}</div>
                </div>
            </div>
        `,
        footer: `<button class="action-btn" onclick="closeModal()">${t('common.close')}</button>`,
    });
}

function _buildNetzplanGraphData(subtasks, colors, savedPositions, projectName, layoutType = 'barycenter') {
    const nodesArr = [];
    const edgesArr = [];

    // Level-Berechnung: Knoten ohne Vorgaenger = Level 0
    const levelMap = {};
    function computeLevel(st) {
        if (levelMap[st.id] !== undefined) return levelMap[st.id];
        const predIds = st.predecessor_ids || [];
        if (predIds.length === 0) {
            levelMap[st.id] = 0;
            return 0;
        }
        let maxPredLevel = 0;
        predIds.forEach(pid => {
            const pred = subtasks.find(s => s.id === pid);
            if (pred) {
                maxPredLevel = Math.max(maxPredLevel, computeLevel(pred));
            }
        });
        levelMap[st.id] = maxPredLevel + 1;
        return levelMap[st.id];
    }
    subtasks.forEach(st => computeLevel(st));

    // Positionen berechnen: X nach Level, Y kollisionsfrei nach Knotenhoehe
    const levelGroups = {};
    subtasks.forEach(st => {
        const lvl = levelMap[st.id];
        if (!levelGroups[lvl]) levelGroups[lvl] = [];
        levelGroups[lvl].push(st);
    });

    const nodeMeta = { 0: _getNetzplanProjectMeta(projectName) };
    subtasks.forEach(st => {
        nodeMeta[st.id] = _getNetzplanTaskMeta(st);
    });

    // Successor-Map aufbauen: nodeId -> [IDs der abhaengigen Knoten]
    const successorMap = {};
    subtasks.forEach(st => {
        (st.predecessor_ids || []).forEach(pid => {
            if (!successorMap[pid]) successorMap[pid] = [];
            successorMap[pid].push(st.id);
        });
    });

    // Barycenter-Heuristik (Sugiyama): Knoten innerhalb jedes Levels
    // nach dem Durchschnitt der Positionen ihrer Nachbarn sortieren
    const levels = Object.keys(levelGroups).map(Number).sort((a, b) => a - b);
    const orderIndex = {}; // nodeId -> index within its level

    // Initiale Reihenfolge setzen
    levels.forEach(lvl => {
        levelGroups[lvl].forEach((st, idx) => { orderIndex[st.id] = idx; });
    });

    // 4 Sweeps (forward + backward) zur Kreuzungsminimierung
    for (let sweep = 0; sweep < 4; sweep++) {
        // Forward sweep: Level 0 -> max
        for (let li = 1; li < levels.length; li++) {
            const lvl = levels[li];
            levelGroups[lvl].forEach(st => {
                const preds = (st.predecessor_ids || []).filter(pid => subtasks.some(s => s.id === pid));
                if (preds.length > 0) {
                    const sum = preds.reduce((acc, pid) => acc + (orderIndex[pid] || 0), 0);
                    orderIndex[st.id] = sum / preds.length;
                }
            });
            levelGroups[lvl].sort((a, b) => (orderIndex[a.id] || 0) - (orderIndex[b.id] || 0));
            levelGroups[lvl].forEach((st, idx) => { orderIndex[st.id] = idx; });
        }

        // Backward sweep: max -> Level 0
        for (let li = levels.length - 2; li >= 0; li--) {
            const lvl = levels[li];
            levelGroups[lvl].forEach(st => {
                const succs = (successorMap[st.id] || []).filter(sid => subtasks.some(s => s.id === sid));
                if (succs.length > 0) {
                    const sum = succs.reduce((acc, sid) => acc + (orderIndex[sid] || 0), 0);
                    orderIndex[st.id] = sum / succs.length;
                }
            });
            levelGroups[lvl].sort((a, b) => (orderIndex[a.id] || 0) - (orderIndex[b.id] || 0));
            levelGroups[lvl].forEach((st, idx) => { orderIndex[st.id] = idx; });
        }
    }

    // Positionen aus optimierter Reihenfolge berechnen
    const positionMap = {};
    const levelWidths = {};
    const levelHeights = {};
    levels.forEach(lvl => {
        const group = levelGroups[lvl];
        levelWidths[lvl] = Math.max(
            NETZPLAN_NODE_METRICS.minTaskWidth,
            ...group.map(st => nodeMeta[st.id].width),
        );
        levelHeights[lvl] = Math.max(
            NETZPLAN_NODE_METRICS.taskLineHeight + NETZPLAN_NODE_METRICS.marginY,
            ...group.map(st => nodeMeta[st.id].height),
        );
    });

    const levelGap = layoutType === 'compact'
        ? NETZPLAN_NODE_METRICS.compactLevelGap
        : NETZPLAN_NODE_METRICS.levelGap;
    const verticalGap = layoutType === 'compact'
        ? NETZPLAN_NODE_METRICS.compactVerticalGap
        : NETZPLAN_NODE_METRICS.verticalGap;
    const levelX = {};
    levels.forEach((lvl, idx) => {
        if (idx === 0) {
            levelX[lvl] = 0;
            return;
        }
        const prevLvl = levels[idx - 1];
        levelX[lvl] = levelX[prevLvl] + (levelWidths[prevLvl] + levelWidths[lvl]) / 2 + levelGap;
    });

    if (layoutType === 'topDown') {
        const levelY = {};
        levels.forEach((lvl, idx) => {
            if (idx === 0) {
                levelY[lvl] = 0;
                return;
            }
            const prevLvl = levels[idx - 1];
            levelY[lvl] = levelY[prevLvl]
                + (levelHeights[prevLvl] + levelHeights[lvl]) / 2
                + NETZPLAN_NODE_METRICS.topDownLevelGap;
        });

        levels.forEach(lvl => {
            const group = levelGroups[lvl];
            _rowNetzplanLevel(group, positionMap, nodeMeta, levelY[lvl], NETZPLAN_NODE_METRICS.horizontalGap, true);
        });
    } else if (layoutType === 'compact') {
        // Kompakt: Gleiche Barycenter-Reihenfolge, aber geringerer Abstand ohne Ueberlappung
        levels.forEach(lvl => {
            const group = levelGroups[lvl];
            _stackNetzplanLevel(group, positionMap, nodeMeta, levelX[lvl], verticalGap, true);
        });
    } else if (layoutType === 'topAligned') {
        // Oben ausgerichtet: Alle Knoten starten bei y=0 (oben)
        levels.forEach(lvl => {
            const group = levelGroups[lvl];
            _stackNetzplanLevel(group, positionMap, nodeMeta, levelX[lvl], verticalGap, false);
        });
    } else {
        // Barycenter (Standard): Zentriert und kollisionsfrei gestapelt
        levels.forEach(lvl => {
            const group = levelGroups[lvl];
            _stackNetzplanLevel(group, positionMap, nodeMeta, levelX[lvl], verticalGap, true);
        });
    }

    // Projektknoten-Position: eine Ebene links, Y-zentriert auf Nachfolger
    const projSuccs = (successorMap[0] || []).filter(sid => positionMap[sid]);
    const projY = projSuccs.length > 0
        ? projSuccs.reduce((sum, sid) => sum + positionMap[sid].y, 0) / projSuccs.length
        : 0;
    const firstLevel = levels[0] ?? 0;
    const firstLevelX = levelX[firstLevel] ?? 0;
    const firstLevelWidth = levelWidths[firstLevel] ?? NETZPLAN_NODE_METRICS.minTaskWidth;
    if (layoutType === 'topDown') {
        const projX = projSuccs.length > 0
            ? projSuccs.reduce((sum, sid) => sum + positionMap[sid].x, 0) / projSuccs.length
            : 0;
        const firstLevelY = positionMap[levelGroups[firstLevel]?.[0]?.id]?.y ?? 0;
        const firstLevelHeight = levelHeights[firstLevel] ?? nodeMeta[0].height;
        positionMap[0] = {
            x: projX,
            y: firstLevelY - (nodeMeta[0].height + firstLevelHeight) / 2 - NETZPLAN_NODE_METRICS.topDownLevelGap,
        };
    } else {
        positionMap[0] = {
            x: firstLevelX - (nodeMeta[0].width + firstLevelWidth) / 2 - levelGap,
            y: projY,
        };
    }

    // Gespeicherte Positionen ueberschreiben (falls vorhanden)
    if (savedPositions) {
        Object.keys(savedPositions).forEach(id => {
            const numId = parseInt(id);
            if (positionMap[numId] !== undefined && savedPositions[id].x != null && savedPositions[id].y != null) {
                positionMap[numId] = { x: savedPositions[id].x, y: savedPositions[id].y };
            }
        });
    }

    // Projektknoten (Node 0)
    const pc = colors.project;
    const projectPos = positionMap[0];
    nodesArr.push({
        id: 0,
        label: nodeMeta[0].label,
        x: projectPos.x,
        y: projectPos.y,
        color: {
            background: pc.background,
            border: pc.border,
            highlight: { background: colors.highlight.background, border: colors.highlight.border },
        },
        font: { color: pc.font, size: 14, bold: true, face: "'Segoe UI', Arial, sans-serif" },
        shape: 'diamond',
        size: 25,
        widthConstraint: { maximum: NETZPLAN_NODE_METRICS.maxProjectWidth },
        borderWidth: 2,
        borderWidthSelected: 3,
        title: projectName || 'Projekt',
    });

    subtasks.forEach(st => {
        const statusPct = st.status_percent || 0;
        const nodeColor = getNodeColor(statusPct, colors);
        const pos = positionMap[st.id];
        const meta = nodeMeta[st.id];

        nodesArr.push({
            id: st.id,
            label: meta.label,
            x: pos.x,
            y: pos.y,
            color: {
                background: nodeColor.background,
                border: nodeColor.border,
                highlight: { background: colors.highlight.background, border: colors.highlight.border },
            },
            font: { color: nodeColor.font, size: 13, face: "'Segoe UI', Arial, sans-serif" },
            shape: 'box',
            margin: { top: 8, right: 12, bottom: 8, left: 12 },
            widthConstraint: {
                minimum: NETZPLAN_NODE_METRICS.minTaskWidth,
                maximum: NETZPLAN_NODE_METRICS.maxTaskWidth,
            },
            borderWidth: 2,
            borderWidthSelected: 3,
            title: `${st.name}\n${t('tasks.status')}: ${statusPct}%${st.deadline ? '\n' + t('subtask.col.deadline') + ': ' + st.deadline : ''}${st.area_name ? '\n' + t('subtask.col.area') + ': ' + st.area_name : ''}`,
        });

        // Kanten fuer Vorgaenger
        const predIds = st.predecessor_ids || [];
        predIds.forEach(pid => {
            const edgeDirection = _getNetzplanEdgeDirection(pid, st.id, positionMap);
            edgesArr.push({
                from: pid,
                to: st.id,
                arrows: { to: { enabled: true, scaleFactor: 0.8 } },
                color: { color: colors.edge, highlight: colors.highlight.border },
                smooth: { type: 'cubicBezier', forceDirection: edgeDirection, roundness: 0.5 },
                width: 1.5,
            });
        });
    });

    return { nodesArr, edgesArr };
}

function openNetzplan(taskId) {
    const parentRow = aufgabenTable.filteredData.find(r => String(r.id) === String(taskId));
    const projectName = parentRow ? parentRow.name : `Projekt #${taskId}`;

    // SubTasks-Daten aus dem gespeicherten Container lesen
    const stContainer = document.getElementById(`subtaskContainer_${taskId}`);
    let subtasks = stContainer?._subtasksData || [];

    if (subtasks.length === 0) {
        showNotification(t('netzplan.noSubtasks'), 'error');
        return;
    }

    const colors = getNetzplanColors();
    const defaultTitle = `${escapeHtml(projectName)} - Netzplan`;

    // Gespeicherte Positionen aus subtasks extrahieren
    const savedPositions = {};
    subtasks.forEach(st => {
        if (st.netzplan_x != null && st.netzplan_y != null) {
            savedPositions[st.id] = { x: st.netzplan_x, y: st.netzplan_y };
        }
    });
    // Projektknoten-Position aus Container laden
    const projectPos = stContainer?._netzplanProjectPos;
    if (projectPos && projectPos.x != null && projectPos.y != null) {
        savedPositions[0] = { x: projectPos.x, y: projectPos.y };
    }

    // Undo/Redo-System
    const netzplanHistory = [];
    let netzplanHistoryPointer = -1;
    const NETZPLAN_HISTORY_MAX = 50;

    function _pushNetzplanHistory(entry) {
        // Redo-Teil abschneiden
        netzplanHistory.splice(netzplanHistoryPointer + 1);
        netzplanHistory.push(entry);
        // Trimmen auf max Groesse
        if (netzplanHistory.length > NETZPLAN_HISTORY_MAX) {
            netzplanHistory.shift();
        }
        netzplanHistoryPointer = netzplanHistory.length - 1;
        _updateUndoRedoButtons();
    }

    function _updateUndoRedoButtons() {
        const undoBtn = document.getElementById('netzplanUndoBtn');
        const redoBtn = document.getElementById('netzplanRedoBtn');
        if (undoBtn) undoBtn.disabled = netzplanHistoryPointer < 0;
        if (redoBtn) redoBtn.disabled = netzplanHistoryPointer >= netzplanHistory.length - 1;
    }

    async function _savePositionsToDb(positions) {
        const posArr = Object.keys(positions).map(id => ({
            id: parseInt(id),
            x: positions[id].x,
            y: positions[id].y,
        }));
        // Lokale Daten aktualisieren (damit Schliessen/Oeffnen korrekte Positionen zeigt)
        subtasks.forEach(st => {
            if (positions[st.id]) {
                st.netzplan_x = positions[st.id].x;
                st.netzplan_y = positions[st.id].y;
            }
        });
        const sc = document.getElementById(`subtaskContainer_${taskId}`);
        if (sc) {
            sc._subtasksData = subtasks;
            if (positions[0]) {
                sc._netzplanProjectPos = { x: positions[0].x, y: positions[0].y };
            }
        }
        try {
            await fetch(`/api/tasks/${taskId}/subtasks/save-netzplan-positions`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ positions: posArr }),
            });
        } catch (e) {
            console.error('Netzplan-Positionen speichern fehlgeschlagen:', e);
        }
    }

    function _getAllPositions() {
        if (!netzplanNetwork) return {};
        const pos = netzplanNetwork.getPositions();
        const result = {};
        Object.keys(pos).forEach(id => {
            result[id] = { x: pos[id].x, y: pos[id].y };
        });
        return result;
    }

    // Overlay erstellen
    const overlay = document.createElement('div');
    overlay.className = 'netzplan-overlay';
    overlay.innerHTML = `
        <div class="netzplan-header">
            <div class="netzplan-title" id="netzplanTitle">${defaultTitle}</div>
            <div class="netzplan-legend">
                <span class="netzplan-legend-item"><span class="netzplan-legend-dot status-offen"></span> ${t('netzplan.statusOpen')}</span>
                <span class="netzplan-legend-item"><span class="netzplan-legend-dot status-arbeit"></span> ${t('netzplan.statusWip')}</span>
                <span class="netzplan-legend-item"><span class="netzplan-legend-dot status-erledigt"></span> ${t('netzplan.statusDone')}</span>
            </div>
            <div class="netzplan-toolbar">
                <button class="netzplan-toolbar-btn" id="netzplanUndoBtn" disabled title="${t('netzplan.undo')}">&#x21B6;</button>
                <button class="netzplan-toolbar-btn" id="netzplanRedoBtn" disabled title="${t('netzplan.redo')}">&#x21B7;</button>
                <div class="netzplan-toolbar-dropdown" id="netzplanAutoLayoutDropdown">
                    <button class="netzplan-toolbar-btn" title="${t('netzplan.autoLayout')}">&#x2725; Auto &#x25BE;</button>
                    <div class="netzplan-toolbar-dropdown-menu">
                        <button data-layout="barycenter">Barycenter (Standard)</button>
                        <button data-layout="compact">Kompakt</button>
                        <button data-layout="topAligned">Oben ausgerichtet</button>
                        <button data-layout="topDown">Von oben nach unten</button>
                    </div>
                </div>
                <button class="netzplan-toolbar-btn" id="netzplanFitBtn">${t('netzplan.fit')}</button>
            </div>
            <button class="netzplan-close-btn" title="${t('common.close')}">&times;</button>
        </div>
        <div class="netzplan-body">
            <div class="netzplan-graph" id="netzplanGraph"></div>
        </div>
    `;

    document.body.appendChild(overlay);

    // Graph-Daten aufbereiten
    const hasSaved = Object.keys(savedPositions).length > 0;
    const { nodesArr, edgesArr } = _buildNetzplanGraphData(subtasks, colors, hasSaved ? savedPositions : null, projectName);

    // vis-network initialisieren
    const graphContainer = document.getElementById('netzplanGraph');
    const data = { nodes: new vis.DataSet(nodesArr), edges: new vis.DataSet(edgesArr) };

    const options = {
        layout: { randomSeed: 1 },
        physics: { enabled: false },
        interaction: {
            hover: true,
            tooltipDelay: 200,
            zoomView: true,
            dragView: true,
            dragNodes: true,
        },
        edges: {
            smooth: { type: 'cubicBezier', forceDirection: 'horizontal', roundness: 0.5 },
        },
    };

    netzplanNetwork = new vis.Network(graphContainer, data, options);

    // Fit nach Rendering
    netzplanNetwork.once('afterDrawing', () => {
        netzplanNetwork.fit({ animation: { duration: 300, easingFunction: 'easeInOutQuad' } });
    });

    // Fit-Button
    document.getElementById('netzplanFitBtn').addEventListener('click', () => {
        if (netzplanNetwork) {
            netzplanNetwork.fit({ animation: { duration: 300, easingFunction: 'easeInOutQuad' } });
        }
    });

    // Drag-Handler: Positionen speichern nach Node-Verschiebung
    let _dragOldPos = null;
    netzplanNetwork.on('dragStart', (params) => {
        if (params.nodes && params.nodes.length > 0) {
            const nodeId = params.nodes[0];
            const pos = netzplanNetwork.getPositions([nodeId]);
            _dragOldPos = { nodeId, x: pos[nodeId].x, y: pos[nodeId].y };
        }
    });
    netzplanNetwork.on('dragEnd', () => {
        if (!_dragOldPos) return;
        const nodeId = _dragOldPos.nodeId;
        const pos = netzplanNetwork.getPositions([nodeId]);
        if (!pos[nodeId]) { _dragOldPos = null; return; }
        const newX = pos[nodeId].x;
        const newY = pos[nodeId].y;
        if (_dragOldPos.x !== newX || _dragOldPos.y !== newY) {
            _pushNetzplanHistory({
                type: 'position',
                nodeId: nodeId,
                oldX: _dragOldPos.x,
                oldY: _dragOldPos.y,
                newX: newX,
                newY: newY,
            });
            _savePositionsToDb(_getAllPositions());
        }
        _dragOldPos = null;
    });

    // Auto-Layout-Dropdown
    const autoLayoutDropdown = document.getElementById('netzplanAutoLayoutDropdown');
    const autoLayoutToggle = autoLayoutDropdown.querySelector('.netzplan-toolbar-btn');
    autoLayoutToggle.addEventListener('click', (e) => {
        e.stopPropagation();
        autoLayoutDropdown.classList.toggle('open');
    });
    // Close on outside click
    document.addEventListener('click', () => {
        autoLayoutDropdown.classList.remove('open');
    });

    // Layout-Optionen
    autoLayoutDropdown.querySelectorAll('[data-layout]').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            autoLayoutDropdown.classList.remove('open');
            const layoutType = btn.dataset.layout;
            if (!netzplanNetwork) return;
            const beforePositions = _getAllPositions();
            const newColors = getNetzplanColors();
            const { nodesArr: newNodes, edgesArr: newEdges } = _buildNetzplanGraphData(subtasks, newColors, null, projectName, layoutType);
            data.nodes.clear();
            data.nodes.add(newNodes);
            data.edges.clear();
            data.edges.add(newEdges);
            netzplanNetwork.fit({ animation: { duration: 300, easingFunction: 'easeInOutQuad' } });
            const afterPositions = _getAllPositions();
            _pushNetzplanHistory({
                type: 'auto-layout',
                beforePositions: beforePositions,
                afterPositions: afterPositions,
            });
            _savePositionsToDb(afterPositions);
        });
    });

    // ---- Linking-Modus Funktionen (Closures) ----

    // Originale Knoten-Styles merken (fuer Reset)
    const _originalNodeStyles = {};

    function enterLinkingMode(nodeId) {
        const validTargets = _computeValidNetzplanTargets(nodeId, subtasks);
        const positions = netzplanNetwork.getPositions([nodeId]);
        const sourcePos = positions[nodeId];

        _netzplanLinkState = {
            sourceId: nodeId,
            sourcePos: sourcePos,
            mouseCanvas: { x: sourcePos.x, y: sourcePos.y },
            validTargets: validTargets,
        };

        // Quellknoten hervorheben
        subtasks.forEach(st => {
            const currentNode = data.nodes.get(st.id);
            if (currentNode) {
                _originalNodeStyles[st.id] = {
                    color: currentNode.color,
                    font: currentNode.font,
                    borderWidth: currentNode.borderWidth,
                    opacity: currentNode.opacity,
                };
            }

            if (st.id === nodeId) {
                data.nodes.update({
                    id: st.id,
                    borderWidth: 4,
                    color: { ...currentNode.color, border: '#e67e22' },
                });
            } else if (!validTargets.has(st.id)) {
                data.nodes.update({
                    id: st.id,
                    opacity: 0.3,
                });
            }
        });

        // Projektknoten: hervorheben wenn Source, sonst dimmen
        const projectNode = data.nodes.get(0);
        if (projectNode) {
            _originalNodeStyles[0] = {
                color: projectNode.color,
                font: projectNode.font,
                borderWidth: projectNode.borderWidth,
                opacity: projectNode.opacity,
            };
            if (nodeId === 0) {
                data.nodes.update({
                    id: 0,
                    borderWidth: 4,
                    color: { ...projectNode.color, border: '#e67e22' },
                });
            } else {
                data.nodes.update({ id: 0, opacity: 0.3 });
            }
        }

        // Header-Text aendern
        const titleEl = document.getElementById('netzplanTitle');
        if (titleEl) titleEl.textContent = t('netzplan.selectTarget');

        // Canvas-Cursor
        graphContainer.style.cursor = 'crosshair';
    }

    function exitLinkingMode() {
        _netzplanLinkState = null;

        // Alle Knoten-Styles zuruecksetzen (inkl. Projektknoten)
        [...subtasks, { id: 0 }].forEach(st => {
            const orig = _originalNodeStyles[st.id];
            if (orig) {
                data.nodes.update({
                    id: st.id,
                    color: orig.color,
                    font: orig.font,
                    borderWidth: orig.borderWidth,
                    opacity: 1,
                });
            }
        });

        // Header-Text zuruecksetzen
        const titleEl = document.getElementById('netzplanTitle');
        if (titleEl) titleEl.textContent = defaultTitle;

        // Cursor zuruecksetzen
        graphContainer.style.cursor = '';

        netzplanNetwork.redraw();
    }

    async function _rebuildNetzplan() {
        if (_netzplanRebuilding) return;
        _netzplanRebuilding = true;
        try {
            // Aktuelle Positionen vor Rebuild erfassen
            const currentPositions = _getAllPositions();

            // Viewport speichern
            const viewPos = netzplanNetwork.getViewPosition();
            const scale = netzplanNetwork.getScale();

            // Frische Daten laden
            const resp = await fetch(`/api/tasks/${taskId}/subtasks`);
            const freshData = await resp.json();
            const freshSubtasks = freshData.items || [];

            // Container-Daten aktualisieren
            const sc = document.getElementById(`subtaskContainer_${taskId}`);
            if (sc) sc._subtasksData = freshSubtasks;

            // Lokales subtasks-Array aktualisieren (Closure-Referenz erhalten)
            subtasks.length = 0;
            freshSubtasks.forEach(st => subtasks.push(st));

            // Neuen Graph berechnen - bestehende Positionen beibehalten
            const newColors = getNetzplanColors();
            const { nodesArr: newNodes, edgesArr: newEdges } = _buildNetzplanGraphData(subtasks, newColors, currentPositions, projectName);

            // DataSets aktualisieren
            data.nodes.clear();
            data.nodes.add(newNodes);
            data.edges.clear();
            data.edges.add(newEdges);

            // Viewport wiederherstellen
            netzplanNetwork.moveTo({ position: viewPos, scale: scale });

            // Positionen in DB speichern
            _savePositionsToDb(_getAllPositions());

            // Tabelle im Hintergrund aktualisieren
            loadSubTasks(taskId);
        } finally {
            _netzplanRebuilding = false;
        }
    }

    async function _rebuildNetzplanWithPositions(forcedPositions) {
        if (_netzplanRebuilding) return;
        _netzplanRebuilding = true;
        try {
            const viewPos = netzplanNetwork.getViewPosition();
            const scale = netzplanNetwork.getScale();

            const resp = await fetch(`/api/tasks/${taskId}/subtasks`);
            const freshData = await resp.json();
            const freshSubtasks = freshData.items || [];

            const sc = document.getElementById(`subtaskContainer_${taskId}`);
            if (sc) sc._subtasksData = freshSubtasks;

            subtasks.length = 0;
            freshSubtasks.forEach(st => subtasks.push(st));

            const newColors = getNetzplanColors();
            const { nodesArr: newNodes, edgesArr: newEdges } = _buildNetzplanGraphData(subtasks, newColors, forcedPositions, projectName);

            data.nodes.clear();
            data.nodes.add(newNodes);
            data.edges.clear();
            data.edges.add(newEdges);

            netzplanNetwork.moveTo({ position: viewPos, scale: scale });
            _savePositionsToDb(_getAllPositions());
            loadSubTasks(taskId);
        } finally {
            _netzplanRebuilding = false;
        }
    }

    // ---- Event-Handler ----

    // afterDrawing: gestrichelte Linking-Linie zeichnen
    netzplanNetwork.on('afterDrawing', (ctx) => {
        if (!_netzplanLinkState) return;
        const { sourcePos, mouseCanvas } = _netzplanLinkState;

        ctx.save();
        ctx.beginPath();
        ctx.setLineDash([8, 4]);
        ctx.strokeStyle = '#e67e22';
        ctx.lineWidth = 2;
        ctx.moveTo(sourcePos.x, sourcePos.y);
        ctx.lineTo(mouseCanvas.x, mouseCanvas.y);
        ctx.stroke();

        // Pfeilspitze am Mauszeiger
        const angle = Math.atan2(mouseCanvas.y - sourcePos.y, mouseCanvas.x - sourcePos.x);
        const arrowLen = 12;
        ctx.setLineDash([]);
        ctx.fillStyle = '#e67e22';
        ctx.beginPath();
        ctx.moveTo(mouseCanvas.x, mouseCanvas.y);
        ctx.lineTo(
            mouseCanvas.x - arrowLen * Math.cos(angle - Math.PI / 6),
            mouseCanvas.y - arrowLen * Math.sin(angle - Math.PI / 6)
        );
        ctx.lineTo(
            mouseCanvas.x - arrowLen * Math.cos(angle + Math.PI / 6),
            mouseCanvas.y - arrowLen * Math.sin(angle + Math.PI / 6)
        );
        ctx.closePath();
        ctx.fill();
        ctx.restore();
    });

    // mousemove auf Canvas: Linking-Linie aktualisieren
    graphContainer.addEventListener('mousemove', (e) => {
        if (!_netzplanLinkState || !netzplanNetwork) return;
        const rect = graphContainer.getBoundingClientRect();
        const domPos = { x: e.clientX - rect.left, y: e.clientY - rect.top };
        const canvasPos = netzplanNetwork.DOMtoCanvas(domPos);
        _netzplanLinkState.mouseCanvas = canvasPos;
        netzplanNetwork.redraw();
    });

    let _netzplanClickTimer = null;
    const NETZPLAN_CLICK_DELAY_MS = 220;

    async function _handleNetzplanClick(params) {
        if (_netzplanRebuilding) return;

        const clickedNodeId = params.nodes && params.nodes.length > 0 ? params.nodes[0] : null;
        const clickedEdgeId = params.edges && params.edges.length > 0 ? params.edges[0] : null;

        if (!_netzplanLinkState) {
            // Kein Linking-Modus
            if (clickedNodeId !== null) {
                // Klick auf Knoten -> Linking-Modus starten
                enterLinkingMode(clickedNodeId);
            } else if (clickedEdgeId !== null) {
                // Klick auf Kante -> Loeschen mit Bestaetigung
                const edgeData = data.edges.get(clickedEdgeId);
                if (!edgeData) return;

                const fromSt = subtasks.find(s => s.id === edgeData.from);
                const toSt = subtasks.find(s => s.id === edgeData.to);
                const fromLabel = edgeData.from === 0 ? 'Projekt' : (fromSt ? `${fromSt.position_number}: ${fromSt.name}` : `#${edgeData.from}`);
                const toLabel = toSt ? `${toSt.position_number}: ${toSt.name}` : `#${edgeData.to}`;

                const confirmed = await msgbox('cancel/yes', 'confirm', t('netzplan.removeDep', { from: fromLabel, to: toLabel }));
                if (!confirmed) return;

                try {
                    const positionsBefore = _getAllPositions();
                    const resp = await fetch(`/api/tasks/${taskId}/subtasks/remove-dependency`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ from_id: edgeData.from, to_id: edgeData.to }),
                    });
                    if (!resp.ok) {
                        const err = await resp.json();
                        showNotification(err.detail || t('team.removeError'), 'error');
                        return;
                    }
                    showNotification(t('netzplan.depRemoved'), 'success');
                    await _rebuildNetzplan();
                    _pushNetzplanHistory({
                        type: 'dependency-remove',
                        fromId: edgeData.from,
                        toId: edgeData.to,
                        positionsBefore: positionsBefore,
                        positionsAfter: _getAllPositions(),
                    });
                } catch (error) {
                    showNotification(t('team.removeError'), 'error');
                }
            }
        } else {
            // Linking-Modus aktiv
            if (clickedNodeId === null || clickedNodeId === _netzplanLinkState.sourceId) {
                // Klick auf leere Flaeche oder Quellknoten -> abbrechen
                exitLinkingMode();
            } else if (_netzplanLinkState.validTargets.has(clickedNodeId)) {
                // Gueltiger Zielknoten -> Abhaengigkeit erstellen
                const sourceId = _netzplanLinkState.sourceId;
                exitLinkingMode();

                try {
                    const positionsBefore = _getAllPositions();
                    const resp = await fetch(`/api/tasks/${taskId}/subtasks/add-dependency`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ from_id: sourceId, to_id: clickedNodeId }),
                    });
                    if (!resp.ok) {
                        const err = await resp.json();
                        showNotification(err.detail || t('team.addError'), 'error');
                        return;
                    }
                    showNotification(t('netzplan.depAdded'), 'success');
                    await _rebuildNetzplan();
                    _pushNetzplanHistory({
                        type: 'dependency-add',
                        fromId: sourceId,
                        toId: clickedNodeId,
                        positionsBefore: positionsBefore,
                        positionsAfter: _getAllPositions(),
                    });
                } catch (error) {
                    showNotification(t('team.addError'), 'error');
                }
            } else {
                // Ungueltiger Knoten
                showNotification(t('netzplan.invalidConnection'), 'error');
            }
        }
    }

    netzplanNetwork.on('click', (params) => {
        if (_netzplanClickTimer) clearTimeout(_netzplanClickTimer);
        _netzplanClickTimer = setTimeout(() => {
            _netzplanClickTimer = null;
            _handleNetzplanClick(params);
        }, NETZPLAN_CLICK_DELAY_MS);
    });

    netzplanNetwork.on('doubleClick', (params) => {
        if (_netzplanClickTimer) {
            clearTimeout(_netzplanClickTimer);
            _netzplanClickTimer = null;
        }
        if (_netzplanRebuilding) return;

        const clickedNodeId = params.nodes && params.nodes.length > 0 ? params.nodes[0] : null;
        if (clickedNodeId === null) return;

        if (_netzplanLinkState) exitLinkingMode();
        _openNetzplanNodeDetails(clickedNodeId, parentRow, subtasks, projectName);
    });

    // Theme-Wechsel: Graph-Farben aktualisieren
    const themeHandler = () => {
        if (!netzplanNetwork) return;
        const newColors = getNetzplanColors();
        // Projektknoten aktualisieren
        const pc = newColors.project;
        data.nodes.update({
            id: 0,
            color: { background: pc.background, border: pc.border, highlight: { background: newColors.highlight.background, border: newColors.highlight.border } },
            font: { color: pc.font, size: 14, bold: true, face: "'Segoe UI', Arial, sans-serif" },
        });
        subtasks.forEach(st => {
            const statusPct = st.status_percent || 0;
            const nc = getNodeColor(statusPct, newColors);
            data.nodes.update({
                id: st.id,
                color: { background: nc.background, border: nc.border, highlight: { background: newColors.highlight.background, border: newColors.highlight.border } },
                font: { color: nc.font, size: 13, face: "'Segoe UI', Arial, sans-serif" },
            });
        });
        data.edges.forEach(edge => {
            data.edges.update({ id: edge.id, color: { color: newColors.edge, highlight: newColors.highlight.border } });
        });
    };
    document.addEventListener('themeChanged', themeHandler);

    // ---- Undo/Redo Logik ----

    function _applyPositions(positions) {
        Object.keys(positions).forEach(id => {
            const numId = parseInt(id);
            data.nodes.update({ id: numId, x: positions[id].x, y: positions[id].y });
        });
    }

    async function _netzplanUndo() {
        if (netzplanHistoryPointer < 0) return;
        const entry = netzplanHistory[netzplanHistoryPointer];
        netzplanHistoryPointer--;
        _updateUndoRedoButtons();

        if (entry.type === 'position') {
            data.nodes.update({ id: entry.nodeId, x: entry.oldX, y: entry.oldY });
            _savePositionsToDb(_getAllPositions());
        } else if (entry.type === 'auto-layout') {
            _applyPositions(entry.beforePositions);
            _savePositionsToDb(entry.beforePositions);
        } else if (entry.type === 'dependency-add') {
            // Undo: Dependency wieder entfernen
            try {
                await fetch(`/api/tasks/${taskId}/subtasks/remove-dependency`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ from_id: entry.fromId, to_id: entry.toId }),
                });
                await _rebuildNetzplanWithPositions(entry.positionsBefore);
            } catch (e) {
                showNotification(t('netzplan.undoFailed'), 'error');
            }
        } else if (entry.type === 'dependency-remove') {
            // Undo: Dependency wieder hinzufuegen
            try {
                await fetch(`/api/tasks/${taskId}/subtasks/add-dependency`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ from_id: entry.fromId, to_id: entry.toId }),
                });
                await _rebuildNetzplanWithPositions(entry.positionsBefore);
            } catch (e) {
                showNotification(t('netzplan.undoFailed'), 'error');
            }
        }
    }

    async function _netzplanRedo() {
        if (netzplanHistoryPointer >= netzplanHistory.length - 1) return;
        netzplanHistoryPointer++;
        const entry = netzplanHistory[netzplanHistoryPointer];
        _updateUndoRedoButtons();

        if (entry.type === 'position') {
            data.nodes.update({ id: entry.nodeId, x: entry.newX, y: entry.newY });
            _savePositionsToDb(_getAllPositions());
        } else if (entry.type === 'auto-layout') {
            _applyPositions(entry.afterPositions);
            _savePositionsToDb(entry.afterPositions);
        } else if (entry.type === 'dependency-add') {
            // Redo: Dependency wieder hinzufuegen
            try {
                await fetch(`/api/tasks/${taskId}/subtasks/add-dependency`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ from_id: entry.fromId, to_id: entry.toId }),
                });
                await _rebuildNetzplanWithPositions(entry.positionsAfter);
            } catch (e) {
                showNotification(t('netzplan.redoFailed'), 'error');
            }
        } else if (entry.type === 'dependency-remove') {
            // Redo: Dependency wieder entfernen
            try {
                await fetch(`/api/tasks/${taskId}/subtasks/remove-dependency`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ from_id: entry.fromId, to_id: entry.toId }),
                });
                await _rebuildNetzplanWithPositions(entry.positionsAfter);
            } catch (e) {
                showNotification(t('netzplan.redoFailed'), 'error');
            }
        }
    }

    // Undo/Redo-Buttons
    document.getElementById('netzplanUndoBtn').addEventListener('click', _netzplanUndo);
    document.getElementById('netzplanRedoBtn').addEventListener('click', _netzplanRedo);

    // Schliessen-Logik (mit komplettem Cleanup)
    const closeOverlay = () => {
        _netzplanLinkState = null;
        if (_netzplanClickTimer) {
            clearTimeout(_netzplanClickTimer);
            _netzplanClickTimer = null;
        }
        document.removeEventListener('themeChanged', themeHandler);
        document.removeEventListener('keydown', keyHandler);
        if (netzplanNetwork) {
            netzplanNetwork.destroy();
            netzplanNetwork = null;
        }
        overlay.remove();
    };

    const keyHandler = (e) => {
        if (document.querySelector('.modal-overlay')) return;
        if (e.key === 'Escape') {
            if (_netzplanLinkState) {
                exitLinkingMode();
            } else {
                closeOverlay();
            }
        } else if (e.key === 'z' && (e.ctrlKey || e.metaKey) && !e.shiftKey) {
            e.preventDefault();
            _netzplanUndo();
        } else if ((e.key === 'y' && (e.ctrlKey || e.metaKey)) || (e.key === 'z' && (e.ctrlKey || e.metaKey) && e.shiftKey)) {
            e.preventDefault();
            _netzplanRedo();
        }
    };
    document.addEventListener('keydown', keyHandler);

    overlay.querySelector('.netzplan-close-btn').addEventListener('click', closeOverlay);
    overlay.addEventListener('click', (e) => { if (e.target === overlay) closeOverlay(); });
}

// ========================================
// Nextcloud-Integration
// ========================================

async function openNcDirDialog(taskId, currentPath) {
    if (currentPath) {
        const confirmed = await msgbox('confirm', 'warning', t('nc.changeConfirm'));
        if (!confirmed) return;
    }

    createModal({
        title: t('nc.fileStorage'),
        cssClass: 'modal-wide',
        body: `
            <div class="nc-browse-breadcrumb" id="ncBrowseBc"></div>
            <div id="ncDirList">
                <div class="table-loading"><div class="spinner"></div></div>
            </div>`,
        footer: (currentPath ? `<button class="action-btn danger" onclick="saveNcPath(${taskId}, '')">${t('nc.removeMapping')}</button>` : '') +
                `<button class="action-btn" onclick="closeModal()">${t('common.cancel')}</button>`,
        onOpen: () => {
            // Footer braucht ID fuer dynamische Buttons
            const footer = document.querySelector('.modal-overlay .modal-footer');
            if (footer) footer.id = 'ncDirFooter';
            // Browse starten bei Root
            ncBrowseDir(taskId, '', currentPath);
        },
    });
}

async function ncBrowseDir(taskId, browsePath, selectedPath) {
    const listContainer = document.getElementById('ncDirList');
    const bcContainer = document.getElementById('ncBrowseBc');
    if (!listContainer) return;

    listContainer.innerHTML = '<div class="table-loading"><div class="spinner"></div></div>';

    // Breadcrumb aktualisieren
    if (bcContainer) {
        const parts = browsePath ? browsePath.split('/').filter(Boolean) : [];
        let bcHtml = `<span class="nc-bc-item" onclick="ncBrowseDir(${taskId}, '', '${escapeAttr(selectedPath)}')">&#127968; ${t('nc.rootDir')}</span>`;
        let accumulated = '';
        parts.forEach(part => {
            accumulated += (accumulated ? '/' : '') + part;
            const p = accumulated;
            bcHtml += `<span class="nc-bc-sep">/</span>`;
            bcHtml += `<span class="nc-bc-item" onclick="ncBrowseDir(${taskId}, '${escapeAttr(p)}', '${escapeAttr(selectedPath)}')">${escapeHtml(part)}</span>`;
        });
        bcContainer.innerHTML = bcHtml;
    }

    // "Diesen Ordner waehlen"-Button anzeigen wenn wir nicht in Root sind
    const footer = document.getElementById('ncDirFooter');
    if (footer && browsePath) {
        // Bestehende "waehlen"-Buttons entfernen
        footer.querySelectorAll('.nc-select-btn').forEach(b => b.remove());
        const selectBtn = document.createElement('button');
        selectBtn.className = 'action-btn primary nc-select-btn';
        selectBtn.textContent = t('nc.selectDir', { name: browsePath.split('/').pop() });
        selectBtn.onclick = () => saveNcPath(taskId, browsePath);
        footer.insertBefore(selectBtn, footer.firstChild);
    } else if (footer) {
        footer.querySelectorAll('.nc-select-btn').forEach(b => b.remove());
    }

    try {
        const resp = await fetch(`/api/nextcloud/directories?path=${encodeURIComponent(browsePath)}`);
        if (!resp.ok) throw new Error(t('nc.loadDirError'));
        const data = await resp.json();
        const dirs = data.directories || [];

        if (dirs.length === 0) {
            listContainer.innerHTML = `<div class="nc-dir-empty">${t('nc.noSubdirs')}</div>`;
            return;
        }

        let html = '<div class="nc-dir-grid">';
        dirs.forEach(d => {
            const isSelected = d.path === selectedPath;
            html += `<div class="nc-dir-item${isSelected ? ' nc-dir-selected' : ''}">
                <span class="nc-dir-icon" onclick="event.stopPropagation(); ncBrowseDir(${taskId}, '${escapeAttr(d.path)}', '${escapeAttr(selectedPath)}')">&#128194;</span>
                <span class="nc-dir-name" onclick="event.stopPropagation(); ncBrowseDir(${taskId}, '${escapeAttr(d.path)}', '${escapeAttr(selectedPath)}')">${escapeHtml(d.name)}</span>
                <button class="nc-dir-select-btn" onclick="event.stopPropagation(); saveNcPath(${taskId}, '${escapeAttr(d.path)}')">${isSelected ? '&#10003; ' + t('nc.selected') : t('nc.select')}</button>
            </div>`;
        });
        html += '</div>';
        listContainer.innerHTML = html;
    } catch (error) {
        listContainer.innerHTML = `<p style="color:var(--color-bearish,#e74c3c)">${escapeHtml(error.message)}</p>`;
    }
}

async function saveNcPath(taskId, value) {
    try {
        const resp = await fetch(`/api/tasks/${taskId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ nextcloud_path: value }),
        });
        if (!resp.ok) throw new Error(t('common.error'));
        showNotification(value ? t('nc.pathAssigned', { path: value }) : t('nc.pathRemoved'), 'success');

        // Modal schliessen
        closeModal();

        // Tabelle neu laden damit das Detail mit/ohne File Browser aktualisiert wird
        await aufgabenTable.loadData();
    } catch (error) {
        showNotification(t('nc.saveFailed'), 'error');
    }
}
