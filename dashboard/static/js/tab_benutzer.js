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
                <button class="control-btn primary" onclick="openNewUserModal()">+ Neuer Benutzer</button>
                <span class="spacer"></span>
                <input type="text" class="control-search" id="userSearchInput"
                       placeholder="Suche..." oninput="applyUserSearch(this.value)">
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
            { label: 'Nachname', field: 'nachname', width: 0, sortable: true },
            { label: 'Vorname', field: 'vorname', width: 130, sortable: true },
            { label: 'Benutzername', field: 'username', width: 130, sortable: true },
            { label: 'E-Mail', field: 'email', width: 180, sortable: true },
            { label: 'Quelle', field: 'auth_source', width: 70, sortable: true, align: 'center', renderer: 'sourceBadge' },
            { label: 'Status', field: 'is_active', width: 80, sortable: true, align: 'center', renderer: 'statusBadge' },
            { label: 'Admin', field: 'is_admin', width: 70, sortable: true, align: 'center', renderer: 'adminBadge' },
            { label: 'Erstellt', field: 'created_at', width: 100, sortable: true },
        ],
        detailFields: [],
        defaultSort: { field: 'nachname', direction: 'asc' },
        filters: [
            {
                id: 'userSearch',
                label: 'Suche',
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
        return '<span class="badge badge-admin">Admin</span>';
    }
    return '';
}

function renderSourceBadge(value) {
    if (value === 'ldap') {
        return '<span class="badge badge-ldap">LDAP</span>';
    }
    return '<span class="badge badge-local">Lokal</span>';
}

function renderStatusBadge(value) {
    if (value === 0) {
        return '<span class="badge badge-inactive">Deaktiviert</span>';
    }
    return '<span class="badge badge-active">Aktiv</span>';
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
                <label>Nachname</label>
                <input type="text" id="editNachname_${row.id}" value="${escapeAttr(row.nachname)}" ${readonly} style="${readonlyStyle}">
            </div>
            <div class="detail-edit-field">
                <label>Vorname</label>
                <input type="text" id="editVorname_${row.id}" value="${escapeAttr(row.vorname)}" ${readonly} style="${readonlyStyle}">
            </div>
            <div class="detail-edit-field">
                <label>E-Mail</label>
                <input type="email" id="editEmail_${row.id}" value="${escapeAttr(row.email)}" ${readonly} style="${readonlyStyle}">
            </div>
            <div class="detail-edit-field">
                <label>Admin</label>
                <label style="display:flex;align-items:center;gap:6px;cursor:pointer;font-size:13px;">
                    <input type="checkbox" id="editAdmin_${row.id}" ${row.is_admin ? 'checked' : ''}>
                    Administrator
                </label>
            </div>
        </div>`;

    // Passwort-Feld nur fuer lokale Benutzer
    if (!isLdap) {
        html += `
        <div class="detail-edit-header" style="margin-top:12px">
            <div class="detail-edit-field">
                <label>Neues Passwort (leer = nicht aendern)</label>
                <input type="password" id="editPassword_${row.id}" placeholder="Neues Passwort..." autocomplete="new-password">
            </div>
        </div>`;
    }

    // Buttons
    html += '<div class="detail-edit-actions">';
    html += `<button class="action-btn primary" onclick="saveUser(${row.id})">Speichern</button>`;

    // Einladungsmail-Button (nur wenn User E-Mail hat)
    if (row.email) {
        html += ` <button class="action-btn" onclick="sendInviteMail(${row.id}, '${escapeAttr(row.username)}')">Einladungsmail senden</button>`;
    }

    // Loeschen-Button: LDAP-User nur wenn deaktiviert, lokale immer
    if (!isLdap || !row.is_active) {
        html += ` <button class="action-btn danger" onclick="deleteUser(${row.id}, '${escapeAttr(row.username)}')">Loeschen</button>`;
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
            throw new Error(data.detail || 'Fehler beim Speichern');
        }

        showNotification('Benutzer gespeichert', 'success');
        await benutzerTable.loadData();
    } catch (error) {
        showNotification(error.message, 'error');
    }
}

async function deleteUser(userId, username) {
    if (!await msgbox('cancel/yes', 'warning', `Benutzer "${username}" wirklich loeschen?`)) return;

    try {
        const resp = await fetch(`/api/admin/users/${userId}`, { method: 'DELETE' });
        if (!resp.ok) {
            const data = await resp.json().catch(() => ({}));
            throw new Error(data.detail || 'Fehler beim Loeschen');
        }

        showNotification('Benutzer geloescht', 'success');
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
        title: 'Neuer Benutzer',
        body: `
            <div class="modal-field">
                <label>Benutzername *</label>
                <input type="text" id="newUsername" placeholder="Benutzername..." autofocus autocomplete="off">
            </div>
            <div class="modal-field">
                <label>Vorname</label>
                <input type="text" id="newVorname" placeholder="Vorname...">
            </div>
            <div class="modal-field">
                <label>Nachname</label>
                <input type="text" id="newNachname" placeholder="Nachname...">
            </div>
            <div class="modal-field">
                <label>E-Mail</label>
                <input type="email" id="newEmail" placeholder="E-Mail...">
            </div>
            <div class="modal-field">
                <label>Passwort *</label>
                <input type="password" id="newPassword" placeholder="Passwort..." autocomplete="new-password">
            </div>
            <div class="modal-field">
                <label style="display:flex;align-items:center;gap:6px;cursor:pointer;">
                    <input type="checkbox" id="newIsAdmin">
                    Administrator
                </label>
            </div>`,
        footer: '<button class="action-btn" onclick="closeModal()">Abbrechen</button>' +
                '<button class="action-btn primary" id="createUserBtn">Erstellen</button>',
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
                    showNotification('Benutzername ist Pflichtfeld', 'error');
                    return;
                }
                if (!password || password.length < 4) {
                    showNotification('Passwort muss mindestens 4 Zeichen haben', 'error');
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
                        throw new Error(data.detail || 'Fehler beim Erstellen');
                    }

                    closeModal();
                    showNotification('Benutzer erstellt', 'success');
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
    if (!await msgbox('cancel/yes', 'confirm', `Einladungsmail an "${username}" senden?`)) return;

    try {
        const resp = await fetch(`/api/admin/mail/invite/${userId}`, { method: 'POST' });

        if (!resp.ok) {
            const data = await resp.json().catch(() => ({}));
            throw new Error(data.detail || 'Fehler beim Senden');
        }

        showNotification('Einladungsmail gesendet', 'success');
    } catch (error) {
        showNotification(error.message, 'error');
    }
}
