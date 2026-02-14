/**
 * Tareas Admin - Allgemein-Tab
 * Server-Adresse und allgemeine App-Konfiguration
 */

let appConfig = null;

async function initAllgemeinTab() {
    await initTabGeneric({
        showFilterBar: false,
        loadFn: loadAppConfig,
        renderFn: renderAllgemeinTab,
    });
}

async function loadAppConfig() {
    appConfig = await loadConfigFromAPI('/api/admin/app/config');
}

function renderAllgemeinTab() {
    const container = document.getElementById('contentContainer');
    if (!container) return;

    const c = appConfig || {};

    container.innerHTML = `
        <div class="mail-container">
            <div class="mail-section">
                <h3>Server-Adresse</h3>
                <div class="mail-form">
                    <div class="mail-form-row">
                        <div class="mail-form-field">
                            <label>Server-Adresse</label>
                            <input type="text" id="appServerAddress"
                                   value="${escapeAttr(c.server_address || '')}"
                                   placeholder="z.B. tareas.domain.tld oder 192.168.1.100:8504">
                            <small style="color: var(--text-muted); margin-top: 4px; display: block;">
                                Protokoll (http/https) wird automatisch aus den TLS-Einstellungen abgeleitet.
                                Diese Adresse wird in E-Mails als Link zur App verwendet.
                            </small>
                        </div>
                    </div>
                    <div class="mail-form-actions">
                        <button class="action-btn primary" onclick="saveAppConfig()">Speichern</button>
                    </div>
                </div>
            </div>
        </div>`;
}

async function saveAppConfig() {
    const server_address = document.getElementById('appServerAddress')?.value?.trim();

    if (!server_address) {
        showNotification('Bitte Server-Adresse eingeben', 'error');
        return;
    }

    await saveConfigToAPI({
        endpoint: '/api/admin/app/config',
        data: { server_address },
        successMessage: 'App-Konfiguration gespeichert',
        onSuccess: async () => {
            await loadAppConfig();
            renderAllgemeinTab();
        },
    });
}
