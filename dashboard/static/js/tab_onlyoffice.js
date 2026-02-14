/**
 * Tareas Admin - ONLYOFFICE-Tab
 * ONLYOFFICE Document Server Konfiguration
 */

let ooConfig = null;

// ========================================
// Tab Initialisierung
// ========================================

async function initOnlyOfficeTab() {
    await initTabGeneric({
        showFilterBar: false,
        loadFn: loadOoConfig,
        renderFn: renderOnlyOfficeTab,
    });
}

async function loadOoConfig() {
    ooConfig = await loadConfigFromAPI('/api/admin/onlyoffice/config');
}

// ========================================
// Rendering
// ========================================

function renderOnlyOfficeTab() {
    const container = document.getElementById('contentContainer');
    if (!container) return;

    const hasConfig = !!ooConfig;

    let html = '<div class="ldap-container">';

    // ONLYOFFICE-Server Konfiguration
    html += renderOoConfigSection(hasConfig);

    html += '</div>';
    container.innerHTML = html;
}

function renderOoConfigSection(hasConfig) {
    const c = ooConfig || {};
    let html = `
        <div class="ldap-section">
            <h3>${t('ooAdmin.title')}</h3>
            <div class="ldap-form">
                <div class="ldap-form-row">
                    <div class="ldap-form-field">
                        <label>${t('ooAdmin.serverUrl')}</label>
                        <input type="text" id="ooServerUrl" value="${escapeAttr(c.server_url || '')}"
                               placeholder="http://localhost:8090">
                    </div>
                </div>
                <div class="ldap-form-row">
                    <div class="ldap-form-field">
                        <label>${t('ooAdmin.jwtSecret')}</label>
                        <input type="password" id="ooJwtSecret"
                               value=""
                               placeholder="${hasConfig ? t('ooAdmin.jwtPlaceholderExisting') : t('ooAdmin.jwtPlaceholderNew')}" autocomplete="new-password">
                    </div>
                </div>
                <div class="ldap-form-actions">
                    <button class="action-btn primary" onclick="saveOoConfig()">${t('common.save')}</button>
                    <button class="action-btn" onclick="testOoConnection()">${t('ooAdmin.testConnection')}</button>
                    ${hasConfig ? `<button class="action-btn danger" onclick="deleteOoConfig()">${t('common.delete')}</button>` : ''}
                </div>
            </div>`;

    // Status-Anzeige
    if (hasConfig) {
        html += `
            <div class="ldap-status-info">
                <strong>${t('ooAdmin.activeConfig')}</strong>
                ${escapeHtml(c.server_url)} | ${t('ooAdmin.jwtConfigured')}
            </div>`;
    }

    // Setup-Hinweis
    html += `
        <div class="ldap-status-info" style="margin-top: 12px; opacity: 0.8;">
            <strong>Setup:</strong>
            ${t('ooAdmin.setupHint')}
        </div>`;

    html += '</div>';
    return html;
}

// ========================================
// Config CRUD
// ========================================

async function saveOoConfig() {
    const server_url = document.getElementById('ooServerUrl')?.value?.trim();
    const jwt_secret_input = document.getElementById('ooJwtSecret')?.value?.trim();

    if (!server_url) {
        showNotification(t('ooAdmin.urlRequired'), 'error');
        return;
    }

    // Neukonfiguration: Secret ist Pflicht. Aenderung: leer = unveraendert
    if (!ooConfig && !jwt_secret_input) {
        showNotification(t('ooAdmin.jwtRequired'), 'error');
        return;
    }

    const jwt_secret = jwt_secret_input || '********';

    await saveConfigToAPI({
        endpoint: '/api/admin/onlyoffice/config',
        data: { server_url, jwt_secret },
        successMessage: t('ooAdmin.saved'),
        onSuccess: async () => {
            await loadOoConfig();
            renderOnlyOfficeTab();
        },
    });
}

async function testOoConnection() {
    try {
        const resp = await fetch('/api/admin/onlyoffice/test');
        if (!resp.ok) {
            const data = await resp.json().catch(() => ({}));
            throw new Error(data.detail || t('ooAdmin.testFailed'));
        }

        showNotification(t('ooAdmin.testSuccess'), 'success');
    } catch (error) {
        showNotification(error.message, 'error');
    }
}

async function deleteOoConfig() {
    await deleteConfigFromAPI({
        endpoint: '/api/admin/onlyoffice/config',
        confirmMessage: t('ooAdmin.deleteConfirm'),
        successMessage: t('ooAdmin.deleted'),
        onSuccess: () => {
            ooConfig = null;
            renderOnlyOfficeTab();
        },
    });
}
