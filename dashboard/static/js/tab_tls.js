/**
 * Tareas Admin - TLS-Tab
 * TLS/HTTPS Konfiguration
 */

let tlsConfig = null;
let tlsVerifyConfig = null;

// ========================================
// Tab Initialisierung
// ========================================

async function initTlsTab() {
    await initTabGeneric({
        showFilterBar: false,
        loadFn: () => Promise.all([loadTlsConfig(), loadTlsVerifyConfig()]),
        renderFn: renderTlsTab,
    });
}

async function loadTlsConfig() {
    tlsConfig = await loadConfigFromAPI('/api/admin/tls/config');
}

async function loadTlsVerifyConfig() {
    tlsVerifyConfig = await loadConfigFromAPI('/api/admin/tls/verify');
}

// ========================================
// Rendering
// ========================================

function renderTlsTab() {
    const container = document.getElementById('contentContainer');
    if (!container) return;

    const hasConfig = !!tlsConfig;
    const isActive = hasConfig && tlsConfig.enabled;

    let html = '<div class="ldap-container">';

    // Status-Sektion
    html += renderTlsStatusSection(hasConfig, isActive);

    // Zertifikatsvalidierung-Sektion
    html += renderTlsVerifySection();

    // Konfigurations-Sektion
    html += renderTlsConfigSection(hasConfig, isActive);

    // Anleitung-Sektion
    html += renderTlsGuideSection();

    html += '</div>';
    container.innerHTML = html;
}

function renderTlsStatusSection(hasConfig, isActive) {
    const protocol = isActive ? 'HTTPS' : 'HTTP';
    const statusClass = isActive ? 'ldap-status-connected' : '';
    const statusText = isActive ? 'Aktiv' : 'Inaktiv';

    let html = `
        <div class="ldap-section">
            <h3>TLS-Status</h3>
            <div class="ldap-status-info ${statusClass}">
                <strong>Status:</strong> ${statusText}<br>
                <strong>Protokoll:</strong> ${protocol}<br>`;

    if (hasConfig) {
        html += `
                <strong>Zertifikat:</strong> ${escapeHtml(tlsConfig.cert_path)}<br>
                <strong>Key:</strong> ${escapeHtml(tlsConfig.key_path)}<br>
                <strong>Konfiguriert am:</strong> ${escapeHtml(tlsConfig.created_at || '')}`;
    } else {
        html += `<strong>Hinweis:</strong> Kein TLS konfiguriert - Verbindungen sind unverschluesselt`;
    }

    html += `
            </div>
        </div>`;
    return html;
}

function renderTlsVerifySection() {
    const v = tlsVerifyConfig || {};

    return `
        <div class="ldap-section">
            <h3>Zertifikatsvalidierung</h3>
            <div class="ldap-status-info" style="opacity: 0.9; margin-bottom: 12px;">
                Aktiviert die Pruefung des TLS-Zertifikats fuer ausgehende Verbindungen.
                Das Zertifikat des Zielservers muss im System-Trust-Store vorhanden sein
                (<code>/usr/local/share/ca-certificates/</code> + <code>update-ca-certificates</code>).
            </div>
            <div class="ldap-form">
                <div class="ldap-form-row">
                    <div class="ldap-form-field">
                        <label style="display: flex; align-items: center; gap: 8px; cursor: pointer;">
                            <input type="checkbox" id="verifyNextcloud" ${v.verify_nextcloud ? 'checked' : ''}>
                            Nextcloud / WebDAV
                        </label>
                    </div>
                    <div class="ldap-form-field">
                        <label style="display: flex; align-items: center; gap: 8px; cursor: pointer;">
                            <input type="checkbox" id="verifyOnlyoffice" ${v.verify_onlyoffice ? 'checked' : ''}>
                            ONLYOFFICE
                        </label>
                    </div>
                </div>
                <div class="ldap-form-row">
                    <div class="ldap-form-field">
                        <label style="display: flex; align-items: center; gap: 8px; cursor: pointer;">
                            <input type="checkbox" id="verifySmtp" ${v.verify_smtp ? 'checked' : ''}>
                            SMTP (Mail)
                        </label>
                    </div>
                    <div class="ldap-form-field">
                        <label style="display: flex; align-items: center; gap: 8px; cursor: pointer;">
                            <input type="checkbox" id="verifyLdap" ${v.verify_ldap ? 'checked' : ''}>
                            LDAP
                        </label>
                    </div>
                </div>
                <div class="ldap-form-actions">
                    <button class="action-btn primary" onclick="saveTlsVerifyConfig()">Speichern</button>
                </div>
            </div>
        </div>`;
}

function renderTlsConfigSection(hasConfig, isActive) {
    const c = tlsConfig || {};

    let html = `
        <div class="ldap-section">
            <h3>TLS-Konfiguration</h3>
            <div class="ldap-form">
                <div class="ldap-form-row">
                    <div class="ldap-form-field">
                        <label>Zertifikat-Pfad (.pem/.crt)</label>
                        <input type="text" id="tlsCertPath" value="${escapeAttr(c.cert_path || '')}"
                               placeholder="/etc/ssl/certs/tareas.pem">
                    </div>
                </div>
                <div class="ldap-form-row">
                    <div class="ldap-form-field">
                        <label>Key-Pfad (.pem/.key)</label>
                        <input type="text" id="tlsKeyPath" value="${escapeAttr(c.key_path || '')}"
                               placeholder="/etc/ssl/private/tareas-key.pem">
                    </div>
                </div>
                <div class="ldap-form-row">
                    <div class="ldap-form-field">
                        <label style="display: flex; align-items: center; gap: 8px; cursor: pointer;">
                            <input type="checkbox" id="tlsEnabled" ${isActive ? 'checked' : ''}>
                            TLS aktivieren (Services muessen neu gestartet werden)
                        </label>
                    </div>
                </div>
                <div class="ldap-form-actions">
                    <button class="action-btn" onclick="validateTlsFiles()">Dateien pruefen</button>
                    <button class="action-btn primary" onclick="saveTlsConfig()">Speichern &amp; Neustart</button>
                    ${hasConfig ? '<button class="action-btn danger" onclick="deleteTlsConfig()">Loeschen</button>' : ''}
                </div>
            </div>
            <div id="tlsValidationResult"></div>
        </div>`;

    return html;
}

function renderTlsGuideSection() {
    return `
        <div class="ldap-section">
            <h3>Anleitung</h3>
            <div class="ldap-status-info" style="opacity: 0.9;">
                <strong>Selbst-signiertes Zertifikat erstellen:</strong>
                <pre style="margin: 8px 0; padding: 8px; background: var(--bg-secondary); border-radius: 4px; overflow-x: auto; font-size: 12px;">openssl req -x509 -newkey rsa:4096 -nodes \\
  -keyout /etc/ssl/private/tareas-key.pem \\
  -out /etc/ssl/certs/tareas.pem \\
  -days 365 -subj "/CN=example.com"</pre>
            </div>
            <div class="ldap-status-info" style="margin-top: 8px; opacity: 0.9;">
                <strong>Let's Encrypt (oeffentliche Domain):</strong>
                <pre style="margin: 8px 0; padding: 8px; background: var(--bg-secondary); border-radius: 4px; overflow-x: auto; font-size: 12px;">apt install certbot
certbot certonly --standalone -d meine-domain.de
# Zertifikat: /etc/letsencrypt/live/meine-domain.de/fullchain.pem
# Key: /etc/letsencrypt/live/meine-domain.de/privkey.pem</pre>
            </div>
            <div class="ldap-status-info" style="margin-top: 8px; opacity: 0.9;">
                <strong>Troubleshooting:</strong><br>
                &bull; Browser-Warnung bei selbst-signierten Zertifikaten ist normal<br>
                &bull; Dateien muessen fuer den Service-User lesbar sein<br>
                &bull; Nach Aenderungen: Services neu starten<br>
                &bull; Bei Problemen: TLS deaktivieren und Services neu starten
            </div>
        </div>`;
}

// ========================================
// Aktionen
// ========================================

async function validateTlsFiles() {
    const cert_path = document.getElementById('tlsCertPath')?.value?.trim();
    const key_path = document.getElementById('tlsKeyPath')?.value?.trim();
    const resultDiv = document.getElementById('tlsValidationResult');

    if (!cert_path || !key_path) {
        showNotification('Bitte beide Pfade angeben', 'error');
        return;
    }

    try {
        const resp = await fetch('/api/admin/tls/validate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ cert_path, key_path }),
        });

        const data = await resp.json();

        if (data.valid) {
            if (resultDiv) {
                resultDiv.innerHTML = `<div class="ldap-status-info ldap-status-connected" style="margin-top: 12px;">Zertifikat und Key sind gueltig</div>`;
            }
            showNotification('Zertifikat und Key sind gueltig', 'success');
        } else {
            if (resultDiv) {
                resultDiv.innerHTML = `<div class="ldap-status-info" style="margin-top: 12px; color: var(--danger);">${escapeHtml(data.error)}</div>`;
            }
            showNotification(data.error, 'error');
        }
    } catch (error) {
        showNotification('Fehler bei der Validierung', 'error');
    }
}

async function saveTlsConfig() {
    const cert_path = document.getElementById('tlsCertPath')?.value?.trim();
    const key_path = document.getElementById('tlsKeyPath')?.value?.trim();
    const enabled = document.getElementById('tlsEnabled')?.checked ? 1 : 0;

    if (!cert_path || !key_path) {
        showNotification('Bitte beide Pfade angeben', 'error');
        return;
    }

    const action = enabled ? 'TLS aktivieren und Services neu starten?' : 'TLS-Konfiguration speichern und Services neu starten?';
    if (!await msgbox('cancel/yes', 'warning', action)) return;

    await saveConfigToAPI({
        endpoint: '/api/admin/tls/config',
        data: { cert_path, key_path, enabled },
        successMessage: 'TLS-Konfiguration gespeichert, Services werden neu gestartet...',
        onSuccess: async () => {
            await fetch('/api/dashboard/restart', { method: 'POST' });
        },
    });
}

async function deleteTlsConfig() {
    await deleteConfigFromAPI({
        endpoint: '/api/admin/tls/config',
        confirmMessage: 'TLS-Konfiguration wirklich loeschen? Services werden auf HTTP zurueckgesetzt.',
        successMessage: 'TLS-Konfiguration geloescht, Services werden neu gestartet...',
        onSuccess: async () => {
            tlsConfig = null;
            renderTlsTab();
            await fetch('/api/dashboard/restart', { method: 'POST' });
        },
    });
}

async function saveTlsVerifyConfig() {
    const verify_nextcloud = document.getElementById('verifyNextcloud')?.checked ? 1 : 0;
    const verify_onlyoffice = document.getElementById('verifyOnlyoffice')?.checked ? 1 : 0;
    const verify_smtp = document.getElementById('verifySmtp')?.checked ? 1 : 0;
    const verify_ldap = document.getElementById('verifyLdap')?.checked ? 1 : 0;

    await saveConfigToAPI({
        endpoint: '/api/admin/tls/verify',
        data: { verify_nextcloud, verify_onlyoffice, verify_smtp, verify_ldap },
        successMessage: 'Zertifikatsvalidierung gespeichert',
        onSuccess: () => {
            tlsVerifyConfig = { verify_nextcloud: !!verify_nextcloud, verify_onlyoffice: !!verify_onlyoffice,
                                verify_smtp: !!verify_smtp, verify_ldap: !!verify_ldap };
        },
    });
}
