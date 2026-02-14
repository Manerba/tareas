/**
 * Tareas Admin - Benutzer-Tab
 * Benutzerverwaltung mit ExpandableTable
 */

let benutzerTable = null;
let benutzerData = [];

// ========================================
// Tab Initialisierung
// ========================================

async function initBenutzerTab() {
    const container = document.getElementById('contentContainer');
    if (!container) return;

    container.innerHTML = '<div class="table-loading"><div class="spinner"></div></div>';

    // Control-Bar aufbauen
    const filterBar = document.getElementById('filterBar');
    if (filterBar) {
        filterBar.style.display = 'flex';
        filterBar.innerHTML = `
            <div class="control-bar">
                <button class="control-btn primary" onclick="openNewUserModal()">${t('users.newBtn')}</button>
                <span class="spacer"></span>
                <input type="text" class="control-search" id="userSearchInput"
                       placeholder="${t('common.search')}" oninput="applyUserSearch(this.value)">
            </div>
        `;
    }

    // Config client-seitig
    const config = {
        id: 'benutzer',
        apiEndpoint: '/api/admin/users',
        expandable: true,
        showHeader: true,
        gridTemplate: '30px 1fr 130px 130px 180px 70px 80px 70px 100px',
        columns: [
            { label: t('users.lastName'), field: 'nachname', width: 0, sortable: true },
            { label: t('users.firstName'), field: 'vorname', width: 130, sortable: true },
            { label: t('users.username'), field: 'username', width: 130, sortable: true },
            { label: t('users.email'), field: 'email', width: 180, sortable: true },
            { label: t('users.source'), field: 'auth_source', width: 70, sortable: true, align: 'center', renderer: 'sourceBadge' },
            { label: t('users.status'), field: 'is_active', width: 80, sortable: true, align: 'center', renderer: 'statusBadge' },
            { label: t('users.admin'), field: 'is_admin', width: 70, sortable: true, align: 'center', renderer: 'adminBadge' },
            { label: t('users.created'), field: 'created_at', width: 100, sortable: true },
        ],
        detailFields: [],
        defaultSort: { field: 'nachname', direction: 'asc' },
        filters: [
            {
                id: 'userSearch',
                label: t('common.search'),
                type: 'input',
                field: 'username',
                wildcard: true,
                wildcardFields: ['vorname', 'nachname', 'email'],
            },
        ],
    };

    container.innerHTML = '<div id="benutzerTableContainer"></div>';

    benutzerTable = new ExpandableTable(config, 'benutzerTableContainer', {
        onRowExpanded: onUserRowExpanded,
    });

    benutzerTable.renderers['adminBadge'] = renderAdminBadge;
    benutzerTable.renderers['sourceBadge'] = renderSourceBadge;
    benutzerTable.renderers['statusBadge'] = renderStatusBadge;

    if (typeof window.tables === 'undefined') window.tables = {};
    window.tables['benutzer'] = benutzerTable;

    await benutzerTable.loadData();
}

function applyUserSearch(value) {
    if (!benutzerTable) return;
    benutzerTable.setFilter('userSearch', value);
}

// ========================================
// Renderer
// ========================================

function renderAdminBadge(value) {
    if (value) {
        return `<span class="badge badge-admin">${t('users.admin')}</span>`;
    }
    return '';
}

function renderSourceBadge(value) {
    if (value === 'ldap') {
        return `<span class="badge badge-ldap">${t('users.ldapBadge')}</span>`;
    }
    return `<span class="badge badge-local">${t('users.local')}</span>`;
}

function renderStatusBadge(value) {
    if (value === 0) {
        return `<span class="badge badge-inactive">${t('users.inactive')}</span>`;
    }
    return `<span class="badge badge-active">${t('users.active')}</span>`;
}

// ========================================
// Detail-Bereich
// ========================================

function onUserRowExpanded(rowId, detailElement) {
    if (!detailElement || !benutzerTable) return;

    const row = benutzerTable.filteredData.find(r => String(r.id) === String(rowId));
    if (!row) return;

    const isLdap = row.auth_source === 'ldap';
    const readonly = isLdap ? 'readonly' : '';
    const readonlyStyle = isLdap ? 'opacity:0.6;' : '';

    let html = `<div class="detail-edit" data-user-id="${row.id}">
        <div class="detail-edit-header">
            <div class="detail-edit-field">
                <label>${t('users.lastName')}</label>
                <input type="text" id="editNachname_${row.id}" value="${escapeAttr(row.nachname)}" ${readonly} style="${readonlyStyle}">
            </div>
            <div class="detail-edit-field">
                <label>${t('users.firstName')}</label>
                <input type="text" id="editVorname_${row.id}" value="${escapeAttr(row.vorname)}" ${readonly} style="${readonlyStyle}">
            </div>
            <div class="detail-edit-field">
                <label>${t('users.email')}</label>
                <input type="email" id="editEmail_${row.id}" value="${escapeAttr(row.email)}" ${readonly} style="${readonlyStyle}">
            </div>
            <div class="detail-edit-field">
                <label>${t('users.admin')}</label>
                <label style="display:flex;align-items:center;gap:6px;cursor:pointer;font-size:13px;">
                    <input type="checkbox" id="editAdmin_${row.id}" ${row.is_admin ? 'checked' : ''}>
                    ${t('users.administrator')}
                </label>
            </div>
        </div>`;

    // Passwort-Feld nur fuer lokale Benutzer
    if (!isLdap) {
        html += `
        <div class="detail-edit-header" style="margin-top:12px">
            <div class="detail-edit-field">
                <label>${t('users.newPasswordHint')}</label>
                <input type="password" id="editPassword_${row.id}" placeholder="${t('users.newPasswordPlaceholder')}" autocomplete="new-password">
            </div>
        </div>`;
    }

    // Buttons
    html += '<div class="detail-edit-actions">';
    html += `<button class="action-btn primary" onclick="saveUser(${row.id})">${t('common.save')}</button>`;

    // Einladungsmail-Button (nur wenn User E-Mail hat)
    if (row.email) {
        html += ` <button class="action-btn" onclick="sendInviteMail(${row.id}, '${escapeAttr(row.username)}')">${t('users.sendInvite')}</button>`;
    }

    // Loeschen-Button: LDAP-User nur wenn deaktiviert, lokale immer
    if (!isLdap || !row.is_active) {
        html += ` <button class="action-btn danger" onclick="deleteUser(${row.id}, '${escapeAttr(row.username)}')">${t('common.delete')}</button>`;
    }

    html += '</div></div>';

    detailElement.innerHTML = html;
}

// ========================================
// User CRUD
// ========================================

async function saveUser(userId) {
    // Pruefen ob LDAP-User (dann nur is_admin senden)
    const row = benutzerTable?.filteredData?.find(r => String(r.id) === String(userId));
    const isLdap = row?.auth_source === 'ldap';

    const is_admin = document.getElementById(`editAdmin_${userId}`)?.checked ? 1 : 0;

    const body = { is_admin };

    if (!isLdap) {
        body.vorname = document.getElementById(`editVorname_${userId}`)?.value || '';
        body.nachname = document.getElementById(`editNachname_${userId}`)?.value || '';
        body.email = document.getElementById(`editEmail_${userId}`)?.value || '';
        const password = document.getElementById(`editPassword_${userId}`)?.value || '';
        if (password) body.password = password;
    }

    try {
        const resp = await fetch(`/api/admin/users/${userId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });

        if (!resp.ok) {
            const data = await resp.json().catch(() => ({}));
            throw new Error(data.detail || t('common.saveError'));
        }

        showNotification(t('users.saved'), 'success');
        await benutzerTable.loadData();
    } catch (error) {
        showNotification(error.message, 'error');
    }
}

async function deleteUser(userId, username) {
    if (!await msgbox('cancel/yes', 'warning', t('users.deleteConfirm', { username }))) return;

    try {
        const resp = await fetch(`/api/admin/users/${userId}`, { method: 'DELETE' });
        if (!resp.ok) {
            const data = await resp.json().catch(() => ({}));
            throw new Error(data.detail || t('common.deleteError'));
        }

        showNotification(t('users.deleted'), 'success');
        await benutzerTable.loadData();
    } catch (error) {
        showNotification(error.message, 'error');
    }
}

// ========================================
// Modal: Neuer Benutzer
// ========================================

function openNewUserModal() {
    createModal({
        title: t('users.new'),
        body: `
            <div class="modal-field">
                <label>${t('users.usernameRequired')}</label>
                <input type="text" id="newUsername" placeholder="${t('users.usernamePlaceholder')}" autofocus autocomplete="off">
            </div>
            <div class="modal-field">
                <label>${t('users.firstName')}</label>
                <input type="text" id="newVorname" placeholder="${t('users.firstnamePlaceholder')}">
            </div>
            <div class="modal-field">
                <label>${t('users.lastName')}</label>
                <input type="text" id="newNachname" placeholder="${t('users.lastnamePlaceholder')}">
            </div>
            <div class="modal-field">
                <label>${t('users.email')}</label>
                <input type="email" id="newEmail" placeholder="${t('users.emailPlaceholder')}">
            </div>
            <div class="modal-field">
                <label>${t('users.passwordRequired')}</label>
                <input type="password" id="newPassword" placeholder="${t('users.passwordPlaceholder')}" autocomplete="new-password">
            </div>
            <div class="modal-field">
                <label style="display:flex;align-items:center;gap:6px;cursor:pointer;">
                    <input type="checkbox" id="newIsAdmin">
                    ${t('users.administrator')}
                </label>
            </div>`,
        footer: `<button class="action-btn" onclick="closeModal()">${t('common.cancel')}</button>` +
                `<button class="action-btn primary" id="createUserBtn">${t('common.create')}</button>`,
        onOpen: () => {
            document.getElementById('newUsername').focus();

            document.getElementById('createUserBtn').addEventListener('click', async () => {
                const username = document.getElementById('newUsername').value.trim();
                const password = document.getElementById('newPassword').value;
                const vorname = document.getElementById('newVorname').value.trim();
                const nachname = document.getElementById('newNachname').value.trim();
                const email = document.getElementById('newEmail').value.trim();
                const is_admin = document.getElementById('newIsAdmin').checked ? 1 : 0;

                if (!username) {
                    showNotification(t('users.usernameNeeded'), 'error');
                    return;
                }
                if (!password || password.length < 4) {
                    showNotification(t('users.passwordMin'), 'error');
                    return;
                }

                try {
                    const resp = await fetch('/api/admin/users', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ username, password, vorname, nachname, email, is_admin }),
                    });

                    if (!resp.ok) {
                        const data = await resp.json().catch(() => ({}));
                        throw new Error(data.detail || t('common.createError'));
                    }

                    closeModal();
                    showNotification(t('users.createdMsg'), 'success');
                    await benutzerTable.loadData();
                } catch (error) {
                    showNotification(error.message, 'error');
                }
            });
        },
    });
}

// ========================================
// Einladungsmail
// ========================================

async function sendInviteMail(userId, username) {
    if (!await msgbox('cancel/yes', 'confirm', t('users.inviteConfirm', { username }))) return;

    try {
        const resp = await fetch(`/api/admin/mail/invite/${userId}`, { method: 'POST' });

        if (!resp.ok) {
            const data = await resp.json().catch(() => ({}));
            throw new Error(data.detail || t('common.saveError'));
        }

        showNotification(t('users.inviteSent'), 'success');
    } catch (error) {
        showNotification(error.message, 'error');
    }
}
