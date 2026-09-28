/** Task/project settings. All permission edits stay local until Save succeeds. */
async function openTaskSettings(taskId) {
    const overlay = createModal({
        title: t('taskSettings.title'), cssClass: 'task-settings-dialog',
        closeOnBackdrop: false, closeOnEscape: false,
        body: `<div class="task-settings-layout">
            <nav class="task-settings-nav" aria-label="${t('taskSettings.title')}">
                <button type="button" class="active" aria-current="page">${t('permissions.title')}</button>
            </nav>
            <section class="task-settings-panel" aria-label="${t('permissions.title')}">
                <div id="taskSettingsContent">${t('common.loading')}</div>
                <p id="taskSettingsError" class="task-settings-error" role="alert" hidden></p>
            </section>
        </div>`,
        footer: `<button type="button" class="action-btn" id="taskSettingsCancel">${t('common.cancel')}</button>
            <button type="button" class="action-btn primary" id="taskSettingsSave" disabled>${t('common.save')}</button>`,
    });
    const dialog = overlay.querySelector('.modal-content');
    dialog.setAttribute('role', 'dialog');
    dialog.setAttribute('aria-modal', 'true');
    dialog.setAttribute('aria-label', t('taskSettings.title'));
    const content = overlay.querySelector('#taskSettingsContent');
    const error = overlay.querySelector('#taskSettingsError');
    const save = overlay.querySelector('#taskSettingsSave');
    const cancel = overlay.querySelector('#taskSettingsCancel');
    cancel.addEventListener('click', closeModal);
    let draft, config, original;
    const userName = user => `${user.vorname || ''} ${user.nachname || ''}`.trim() || user.username || `User ${user.id}`;
    const serialise = () => JSON.stringify([...draft.values()].sort((a, b) => a.user_id - b.user_id));
    const changed = () => { save.disabled = serialise() === original; };

    try {
        const response = await fetch(`/api/tasks/${taskId}/members`);
        if (!response.ok) throw new Error(t(response.status === 403 ? 'permissions.denied' : 'common.loadError'));
        config = await response.json();
        if (!overlay.isConnected) return;
        overlay.querySelector('.modal-header').textContent = `${t('taskSettings.title')} – ${config.task.name}`;
        const users = new Map(config.users.map(user => [user.id, user]));
        const project = config.task.task_type === 'projekt';
        draft = new Map(config.items.map(member => [member.user_id, {
            user_id: member.user_id, can_read: !!(member.can_read || member.can_edit || member.can_create),
            can_edit: !!member.can_edit, can_create: project && !!member.can_create,
        }]));
        original = serialise();
        const owner = users.get(config.task.created_by);
        content.innerHTML = `
            <h3>${t('permissions.title')}</h3>
            <p class="permissions-hint">${t('permissions.hint')}</p>
            ${project ? `<p class="permissions-hint">${t('permissions.inherited')}</p>` : ''}
            <div class="permissions-fixed">
                ${owner ? `<div><strong>${escapeHtml(userName(owner))}</strong> · ${t('permissions.owner')} · ${t('permissions.fullAccess')}</div>` : ''}
                <div>${t('permissions.admins')} · ${t('permissions.fullAccess')}</div>
            </div>
            <fieldset id="permissionFields">
                <div class="permissions-add">
                    <input type="search" id="permissionUserSearch" placeholder="${t('permissions.search')}" aria-label="${t('permissions.search')}">
                    <select id="permissionUserSelect" aria-label="${t('team.selectUser')}"></select>
                    <button type="button" class="control-btn" id="permissionAdd">${t('permissions.add')}</button>
                </div>
                <div class="permissions-table-scroll">
                    <table class="permissions-table">
                        <thead><tr><th>${t('permissions.user')}</th><th>${t('permissions.access')}</th>
                            ${project ? `<th>${t('permissions.create')}</th>` : ''}<th></th></tr></thead>
                        <tbody id="permissionRows"></tbody>
                    </table>
                    <p id="permissionEmpty">${t('permissions.empty')}</p>
                </div>
            </fieldset>`;
        const search = content.querySelector('#permissionUserSearch');
        const select = content.querySelector('#permissionUserSelect');
        const add = content.querySelector('#permissionAdd');
        const rows = content.querySelector('#permissionRows');
        const editable = uid => uid !== config.task.created_by && !users.get(uid)?.is_admin;

        function updateCandidates() {
            const query = search.value.trim().toLocaleLowerCase();
            const selected = select.value;
            const candidates = config.users.filter(user => user.is_active && editable(user.id) && !draft.has(user.id)
                && `${userName(user)} ${user.username || ''} ${user.email || ''}`.toLocaleLowerCase().includes(query));
            select.innerHTML = `<option value="">${t('team.selectUser')}</option>` + candidates.map(user =>
                `<option value="${user.id}">${escapeHtml(userName(user))}${user.email ? ` (${escapeHtml(user.email)})` : ''}</option>`).join('');
            if (candidates.some(user => String(user.id) === selected)) select.value = selected;
            add.disabled = !select.value;
        }

        function renderMembers() {
            rows.innerHTML = '';
            for (const member of draft.values()) {
                // Existing redundant admin memberships remain untouched when saving.
                if (!editable(member.user_id)) continue;
                const user = users.get(member.user_id) || {id: member.user_id};
                const row = document.createElement('tr');
                row.dataset.userId = member.user_id;
                row.innerHTML = `<td><strong>${escapeHtml(userName(user))}</strong>
                    ${config.task.assigned_to === member.user_id ? `<small>${t('permissions.assigned')}</small>` : ''}
                    ${user.email ? `<small>${escapeHtml(user.email)}</small>` : ''}</td>
                    <td><select data-permission="access" aria-label="${escapeAttr(t('permissions.access') + ': ' + userName(user))}">
                        ${!member.can_read ? `<option value="none">${t('permissions.noAccess')}</option>` : ''}
                        <option value="read">${t('team.col.read')}</option>
                        <option value="edit">${t('team.col.edit')}</option>
                    </select></td>
                    ${project ? `<td><label class="permission-create-label"><input type="checkbox" data-permission="create" aria-label="${escapeAttr(t('permissions.create') + ': ' + userName(user))}" ${member.can_create ? 'checked' : ''}><span>${t('permissions.create')}</span></label></td>` : ''}
                    <td><button type="button" class="control-btn" data-remove>${t('permissions.remove')}</button></td>`;
                const access = row.querySelector('[data-permission="access"]');
                access.value = member.can_edit ? 'edit' : member.can_read ? 'read' : 'none';
                access.addEventListener('change', () => {
                    member.can_edit = access.value === 'edit';
                    member.can_read = access.value !== 'none' || member.can_create;
                    changed();
                });
                row.querySelector('[data-permission="create"]')?.addEventListener('change', event => {
                    member.can_create = event.target.checked;
                    if (member.can_create && !member.can_read) {
                        member.can_read = true;
                        access.value = 'read';
                    }
                    changed();
                });
                row.querySelector('[data-remove]').addEventListener('click', () => {
                    draft.delete(member.user_id);
                    renderMembers();
                    changed();
                });
                rows.appendChild(row);
            }
            content.querySelector('#permissionEmpty').hidden = !!rows.children.length;
            updateCandidates();
        }
        search.addEventListener('input', updateCandidates);
        select.addEventListener('change', () => { add.disabled = !select.value; });
        add.addEventListener('click', () => {
            if (!select.value) return;
            const uid = Number(select.value);
            draft.set(uid, {user_id: uid, can_read: true, can_edit: false, can_create: false});
            renderMembers();
            changed();
        });
        renderMembers();
        search.focus();
    } catch (failure) {
        error.textContent = failure.message;
        error.hidden = false;
        content.textContent = '';
        return;
    }

    save.addEventListener('click', async () => {
        save.disabled = true;
        cancel.disabled = true;
        content.querySelector('fieldset').disabled = true;
        error.hidden = true;
        try {
            const response = await fetch(`/api/tasks/${taskId}/members`, {
                method: 'PUT', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({members: [...draft.values()], revision: config.revision}),
            });
            if (!response.ok) throw new Error(t(response.status === 409 ? 'permissions.conflict'
                : response.status === 403 ? 'permissions.denied' : 'common.saveError'));
            closeModal();
            showNotification(t('permissions.saved'), 'success');
            if (aufgabenTable) await aufgabenTable.loadData();
        } catch (failure) {
            error.textContent = failure.message;
            error.hidden = false;
            changed();
        } finally {
            cancel.disabled = false;
            content.querySelector('fieldset').disabled = false;
        }
    });
}
