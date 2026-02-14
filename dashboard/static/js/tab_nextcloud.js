/**
 * Tareas Admin - Nextcloud-Tab
 * Nextcloud-Konfiguration (WebDAV-Verbindung)
 */

let ncConfig = null;

// ========================================
// Tab Initialisierung
// ========================================

async function initNextcloudTab() {
    await initTabGeneric({
        showFilterBar: false,
        loadFn: loadNcConfig,
        renderFn: renderNextcloudTab,
    });
}

async function loadNcConfig() {
    ncConfig = await loadConfigFromAPI('/api/admin/nextcloud/config');
}

// ========================================
// Rendering
// ========================================

function renderNextcloudTab() {
    const container = document.getElementById('contentContainer');
    if (!container) return;

    const hasConfig = !!ncConfig;

    let html = '<div class="ldap-container">';

    // Nextcloud-Server Konfiguration
    html += renderNcConfigSection(hasConfig);

    html += '</div>';
    container.innerHTML = html;
}

function renderNcConfigSection(hasConfig) {
    const c = ncConfig || {};
    let html = `
        <div class="ldap-section">
            <h3>${t('ncAdmin.title')}</h3>
            <div class="ldap-form">
                <div class="ldap-form-row">
                    <div class="ldap-form-field">
                        <label>${t('ncAdmin.url')}</label>
                        <input type="text" id="ncServerUrl" value="${escapeAttr(c.server_url || '')}"
                               placeholder="https://cloud.firma.de">
                    </div>
                </div>
                <div class="ldap-form-row">
                    <div class="ldap-form-field">
                        <label>${t('auth.username')}</label>
                        <input type="text" id="ncUsername" value="${escapeAttr(c.username || '')}"
                               placeholder="service-account">
                    </div>
                    <div class="ldap-form-field">
                        <label>${t('ncAdmin.password')}</label>
                        <input type="password" id="ncPassword"
                               value="${hasConfig ? '********' : ''}"
                               placeholder="${t('auth.password')}..." autocomplete="new-password">
                    </div>
                </div>
                <div class="ldap-form-row">
                    <div class="ldap-form-field">
                        <label>${t('ncAdmin.basePath')}</label>
                        <input type="text" id="ncBasePath" value="${escapeAttr(c.base_path || '')}"
                               placeholder="/Projekte">
                    </div>
                </div>
                <div class="ldap-form-actions">
                    <button class="action-btn primary" onclick="saveNcConfig()">${t('ncAdmin.testAndSave')}</button>
                    ${hasConfig ? `<button class="action-btn danger" onclick="deleteNcConfig()">${t('common.delete')}</button>` : ''}
                </div>
            </div>`;

    // Status-Anzeige
    if (hasConfig) {
        html += `
            <div class="ldap-status-info">
                <strong>${t('ncAdmin.activeConfig')}</strong>
                ${escapeHtml(c.server_url)} | ${t('auth.username')}: ${escapeHtml(c.username)} | ${t('ncAdmin.basePath')}: ${escapeHtml(c.base_path)}
            </div>`;
    }

    html += '</div>';
    return html;
}

// ========================================
// Config CRUD
// ========================================

async function saveNcConfig() {
    const server_url = document.getElementById('ncServerUrl')?.value?.trim();
    const username = document.getElementById('ncUsername')?.value?.trim();
    const password = document.getElementById('ncPassword')?.value;
    const base_path = document.getElementById('ncBasePath')?.value?.trim();

    if (!server_url || !username || !password || !base_path) {
        showNotification(t('ncAdmin.fillRequired'), 'error');
        return;
    }

    await saveConfigToAPI({
        endpoint: '/api/admin/nextcloud/config',
        data: { server_url, username, password, base_path },
        successMessage: t('ncAdmin.saved'),
        onSuccess: async () => {
            await loadNcConfig();
            renderNextcloudTab();
        },
    });
}

async function deleteNcConfig() {
    await deleteConfigFromAPI({
        endpoint: '/api/admin/nextcloud/config',
        confirmMessage: t('ncAdmin.deleteConfirm'),
        successMessage: t('ncAdmin.deleted'),
        onSuccess: () => {
            ncConfig = null;
            renderNextcloudTab();
        },
    });
}
