/**
 * Tareas Admin - LDAP-Tab
 * LDAP-Konfiguration und AD-Gruppen-Verwaltung
 */

let ldapConfig = null;

// ========================================
// Tab Initialisierung
// ========================================

async function initLdapTab() {
    await initTabGeneric({
        showFilterBar: false,
        loadFn: loadLdapConfig,
        renderFn: renderLdapTab,
    });
}

async function loadLdapConfig() {
    ldapConfig = await loadConfigFromAPI('/api/admin/ldap/config');
}

// ========================================
// Rendering
// ========================================

function renderLdapTab() {
    const container = document.getElementById('contentContainer');
    if (!container) return;

    const hasConfig = !!ldapConfig;
    const hasGroup = hasConfig && !!ldapConfig.group_dn;

    let html = '<div class="ldap-container">';

    // Sektion 1: LDAP-Server Konfiguration
    html += renderConfigSection(hasConfig);

    // Sektion 2: AD-Gruppe (nur wenn Config vorhanden)
    if (hasConfig) {
        html += renderGroupSection(hasGroup);
    }

    html += '</div>';
    container.innerHTML = html;

    // SSL-Checkbox Event
    const sslCheckbox = document.getElementById('ldapUseSSL');
    if (sslCheckbox) {
        sslCheckbox.addEventListener('change', function() {
            const portInput = document.getElementById('ldapPort');
            if (portInput) {
                portInput.value = this.checked ? 636 : 389;
            }
        });
    }
}

function renderConfigSection(hasConfig) {
    const c = ldapConfig || {};
    let html = `
        <div class="ldap-section">
            <h3>LDAP-Server Konfiguration</h3>
            <div class="ldap-form">
                <div class="ldap-form-row">
                    <div class="ldap-form-field">
                        <label>Server</label>
                        <input type="text" id="ldapServer" value="${escapeAttr(c.server || '')}"
                               placeholder="10.0.12.1 oder dc.firma.local">
                    </div>
                    <div class="ldap-form-field" style="max-width:120px">
                        <label>Port</label>
                        <input type="number" id="ldapPort" value="${c.port || 389}">
                    </div>
                    <div class="ldap-form-field" style="max-width:80px">
                        <label>SSL</label>
                        <label class="ldap-checkbox">
                            <input type="checkbox" id="ldapUseSSL" ${c.use_ssl ? 'checked' : ''}>
                            SSL
                        </label>
                    </div>
                </div>
                <div class="ldap-form-row">
                    <div class="ldap-form-field">
                        <label>Bind-DN</label>
                        <input type="text" id="ldapBindDN" value="${escapeAttr(c.bind_dn || '')}"
                               placeholder="CN=ldapreader,OU=Service,DC=firma,DC=local">
                    </div>
                </div>
                <div class="ldap-form-row">
                    <div class="ldap-form-field">
                        <label>Bind-Passwort</label>
                        <input type="password" id="ldapBindPassword"
                               value="${hasConfig ? '********' : ''}"
                               placeholder="Passwort..." autocomplete="new-password">
                    </div>
                </div>
                <div class="ldap-form-row">
                    <div class="ldap-form-field">
                        <label>Suchpfad (Search Base)</label>
                        <input type="text" id="ldapSearchBase" value="${escapeAttr(c.search_base || '')}"
                               placeholder="OU=Benutzer,DC=firma,DC=local">
                    </div>
                    <div class="ldap-form-field" style="max-width:180px">
                        <label>Sync-Intervall (Min.)</label>
                        <input type="number" id="ldapSyncInterval" value="${c.sync_interval_minutes || 60}" min="5">
                    </div>
                </div>
                <div class="ldap-form-actions">
                    <button class="action-btn primary" onclick="saveLdapConfig()">Verbindung testen &amp; Speichern</button>
                    ${hasConfig ? '<button class="action-btn danger" onclick="deleteLdapConfig()">Loeschen</button>' : ''}
                </div>
            </div>`;

    // Status-Anzeige
    if (hasConfig) {
        const ssl = c.use_ssl ? 'Ja' : 'Nein';
        const lastSync = c.last_sync_at || 'Noch nie';
        html += `
            <div class="ldap-status-info">
                <strong>Aktive Konfiguration:</strong>
                ${escapeHtml(c.server)}:${c.port} | SSL: ${ssl} | Suchpfad: ${escapeHtml(c.search_base)} | Letzter Sync: ${lastSync}
            </div>`;
    }

    html += '</div>';
    return html;
}

function renderGroupSection(hasGroup) {
    let html = '<div class="ldap-section">';
    html += '<h3>AD-Gruppe</h3>';

    if (hasGroup) {
        // Gruppe ist gewaehlt
        html += `
            <div class="ldap-group-selected">
                <div class="ldap-group-info">
                    <span class="badge badge-ldap">Gewaehlt</span>
                    <strong>${escapeHtml(ldapConfig.group_name)}</strong>
                    <span class="ldap-group-dn">${escapeHtml(ldapConfig.group_dn)}</span>
                </div>
                <button class="action-btn danger" onclick="deselectGroup()">Auswahl aufheben</button>
            </div>
            <div class="ldap-sync-bar">
                <button class="action-btn primary" onclick="manualSync()">Jetzt synchronisieren</button>
                <span class="ldap-last-sync">Letzter Sync: ${ldapConfig.last_sync_at || 'Noch nie'}</span>
            </div>
            <div class="ldap-hint">LDAP-Benutzer werden in der Benutzerverwaltung angezeigt.</div>`;
    } else {
        // Gruppen-Liste laden
        html += `
            <div id="ldapGroupsList">
                <button class="action-btn" onclick="loadGroups()">Gruppen laden</button>
            </div>`;
    }

    html += '</div>';
    return html;
}

// ========================================
// LDAP Config CRUD
// ========================================

async function saveLdapConfig() {
    const server = document.getElementById('ldapServer')?.value?.trim();
    const port = parseInt(document.getElementById('ldapPort')?.value) || 389;
    const use_ssl = document.getElementById('ldapUseSSL')?.checked ? 1 : 0;
    const bind_dn = document.getElementById('ldapBindDN')?.value?.trim();
    const bind_password = document.getElementById('ldapBindPassword')?.value;
    const search_base = document.getElementById('ldapSearchBase')?.value?.trim();
    const sync_interval_minutes = parseInt(document.getElementById('ldapSyncInterval')?.value) || 60;

    if (!server || !bind_dn || !bind_password || !search_base) {
        showNotification('Bitte alle Pflichtfelder ausfuellen', 'error');
        return;
    }

    await saveConfigToAPI({
        endpoint: '/api/admin/ldap/config',
        data: { server, port, use_ssl, bind_dn, bind_password, search_base, sync_interval_minutes },
        successMessage: 'LDAP-Konfiguration gespeichert (Verbindung OK)',
        onSuccess: async () => {
            await loadLdapConfig();
            renderLdapTab();
        },
    });
}

async function deleteLdapConfig() {
    await deleteConfigFromAPI({
        endpoint: '/api/admin/ldap/config',
        confirmMessage: 'LDAP-Konfiguration wirklich loeschen? Alle LDAP-Benutzer werden deaktiviert.',
        successMessage: 'LDAP-Konfiguration geloescht',
        onSuccess: () => {
            ldapConfig = null;
            renderLdapTab();
        },
    });
}

// ========================================
// Gruppen
// ========================================

async function loadGroups() {
    const container = document.getElementById('ldapGroupsList');
    if (!container) return;

    container.innerHTML = '<div class="table-loading"><div class="spinner"></div></div>';

    try {
        const resp = await fetch('/api/admin/ldap/groups');
        if (!resp.ok) {
            const data = await resp.json().catch(() => ({}));
            throw new Error(data.detail || 'Fehler beim Laden');
        }

        const data = await resp.json();
        const groups = data.groups || [];

        if (groups.length === 0) {
            container.innerHTML = '<p class="ldap-hint">Keine Gruppen gefunden im Suchpfad.</p>';
            return;
        }

        let html = `
            <table class="ldap-groups-table">
                <thead>
                    <tr><th>Name</th><th>DN</th><th style="width:60px"></th></tr>
                </thead>
                <tbody>`;

        for (const group of groups) {
            html += `
                <tr>
                    <td>${escapeHtml(group.name)}</td>
                    <td class="ldap-group-dn">${escapeHtml(group.dn)}</td>
                    <td><button class="action-btn primary" style="padding:2px 10px"
                            onclick="selectGroup('${escapeAttr(group.dn)}', '${escapeAttr(group.name)}')">+</button></td>
                </tr>`;
        }

        html += '</tbody></table>';
        container.innerHTML = html;
    } catch (error) {
        container.innerHTML = '';
        showNotification(error.message, 'error');
    }
}

async function selectGroup(dn, name) {
    try {
        const resp = await fetch('/api/admin/ldap/group', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ group_dn: dn, group_name: name }),
        });

        if (!resp.ok) {
            const data = await resp.json().catch(() => ({}));
            throw new Error(data.detail || 'Fehler beim Auswaehlen');
        }

        const data = await resp.json();
        const sync = data.sync || {};
        showNotification(
            `Gruppe "${name}" ausgewaehlt. Erstellt: ${sync.created || 0}, Aktualisiert: ${sync.updated || 0}`,
            'success'
        );

        await loadLdapConfig();
        renderLdapTab();
    } catch (error) {
        showNotification(error.message, 'error');
    }
}

async function deselectGroup() {
    if (!await msgbox('cancel/yes', 'warning', 'Gruppenauswahl aufheben? Alle LDAP-Benutzer werden deaktiviert.')) return;

    try {
        const resp = await fetch('/api/admin/ldap/group', { method: 'DELETE' });
        if (!resp.ok) throw new Error('Fehler');

        showNotification('Gruppenauswahl aufgehoben', 'success');
        await loadLdapConfig();
        renderLdapTab();
    } catch (error) {
        showNotification(error.message, 'error');
    }
}

// ========================================
// Sync
// ========================================

async function manualSync() {
    try {
        const resp = await fetch('/api/admin/ldap/sync', { method: 'POST' });
        if (!resp.ok) {
            const data = await resp.json().catch(() => ({}));
            throw new Error(data.detail || 'Sync fehlgeschlagen');
        }

        const data = await resp.json();
        const sync = data.sync || {};
        showNotification(
            `Sync abgeschlossen. Erstellt: ${sync.created || 0}, Aktualisiert: ${sync.updated || 0}, Deaktiviert: ${sync.deactivated || 0}`,
            'success'
        );

        await loadLdapConfig();
        renderLdapTab();
    } catch (error) {
        showNotification(error.message, 'error');
    }
}
