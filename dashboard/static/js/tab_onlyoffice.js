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
            <h3>ONLYOFFICE Document Server</h3>
            <div class="ldap-form">
                <div class="ldap-form-row">
                    <div class="ldap-form-field">
                        <label>Server-URL</label>
                        <input type="text" id="ooServerUrl" value="${escapeAttr(c.server_url || '')}"
                               placeholder="http://localhost:8090">
                    </div>
                </div>
                <div class="ldap-form-row">
                    <div class="ldap-form-field">
                        <label>JWT Secret</label>
                        <input type="password" id="ooJwtSecret"
                               value=""
                               placeholder="${hasConfig ? 'Unveraendert (leer lassen)' : 'JWT Secret vom Docker-Setup'}" autocomplete="new-password">
                    </div>
                </div>
                <div class="ldap-form-actions">
                    <button class="action-btn primary" onclick="saveOoConfig()">Speichern</button>
                    <button class="action-btn" onclick="testOoConnection()">Verbindung testen</button>
                    ${hasConfig ? '<button class="action-btn danger" onclick="deleteOoConfig()">Loeschen</button>' : ''}
                </div>
            </div>`;

    // Status-Anzeige
    if (hasConfig) {
        html += `
            <div class="ldap-status-info">
                <strong>Aktive Konfiguration:</strong>
                ${escapeHtml(c.server_url)} | JWT: konfiguriert
            </div>`;
    }

    // Setup-Hinweis
    html += `
        <div class="ldap-status-info" style="margin-top: 12px; opacity: 0.8;">
            <strong>Setup:</strong>
            Docker-Container starten mit <code>scripts/onlyoffice-setup.sh</code>.
            Das Script gibt die URL und das JWT Secret aus.
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
        showNotification('Bitte Server-URL angeben', 'error');
        return;
    }

    // Neukonfiguration: Secret ist Pflicht. Aenderung: leer = unveraendert
    if (!ooConfig && !jwt_secret_input) {
        showNotification('Bitte JWT Secret angeben', 'error');
        return;
    }

    const jwt_secret = jwt_secret_input || '********';

    await saveConfigToAPI({
        endpoint: '/api/admin/onlyoffice/config',
        data: { server_url, jwt_secret },
        successMessage: 'ONLYOFFICE-Konfiguration gespeichert',
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
            throw new Error(data.detail || 'Verbindungstest fehlgeschlagen');
        }

        showNotification('ONLYOFFICE Document Server ist erreichbar', 'success');
    } catch (error) {
        showNotification(error.message, 'error');
    }
}

async function deleteOoConfig() {
    await deleteConfigFromAPI({
        endpoint: '/api/admin/onlyoffice/config',
        confirmMessage: 'ONLYOFFICE-Konfiguration wirklich loeschen?',
        successMessage: 'ONLYOFFICE-Konfiguration geloescht',
        onSuccess: () => {
            ooConfig = null;
            renderOnlyOfficeTab();
        },
    });
}
