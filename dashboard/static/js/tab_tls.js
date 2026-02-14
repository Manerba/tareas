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
    const statusText = isActive ? t('tls.active') : t('tls.inactive');

    let html = `
        <div class="ldap-section">
            <h3>${t('tls.statusTitle')}</h3>
            <div class="ldap-status-info ${statusClass}">
                <strong>${t('tls.status')}</strong> ${statusText}<br>
                <strong>${t('tls.protocol')}</strong> ${protocol}<br>`;

    if (hasConfig) {
        html += `
                <strong>${t('tls.cert')}</strong> ${escapeHtml(tlsConfig.cert_path)}<br>
                <strong>${t('tls.key')}</strong> ${escapeHtml(tlsConfig.key_path)}<br>
                <strong>${t('tls.configuredAt')}</strong> ${escapeHtml(tlsConfig.created_at || '')}`;
    } else {
        html += `<strong>${t('tls.noTlsHint')}</strong>`;
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
            <h3>${t('tls.verifyTitle')}</h3>
            <div class="ldap-status-info" style="opacity: 0.9; margin-bottom: 12px;">
                ${t('tls.verifyHint')}
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
                    <button class="action-btn primary" onclick="saveTlsVerifyConfig()">${t('common.save')}</button>
                </div>
            </div>
        </div>`;
}

function renderTlsConfigSection(hasConfig, isActive) {
    const c = tlsConfig || {};

    let html = `
        <div class="ldap-section">
            <h3>${t('tls.configTitle')}</h3>
            <div class="ldap-form">
                <div class="ldap-form-row">
                    <div class="ldap-form-field">
                        <label>${t('tls.certPath')}</label>
                        <input type="text" id="tlsCertPath" value="${escapeAttr(c.cert_path || '')}"
                               placeholder="/etc/ssl/certs/tareas.pem">
                    </div>
                </div>
                <div class="ldap-form-row">
                    <div class="ldap-form-field">
                        <label>${t('tls.keyPath')}</label>
                        <input type="text" id="tlsKeyPath" value="${escapeAttr(c.key_path || '')}"
                               placeholder="/etc/ssl/private/tareas-key.pem">
                    </div>
                </div>
                <div class="ldap-form-row">
                    <div class="ldap-form-field">
                        <label style="display: flex; align-items: center; gap: 8px; cursor: pointer;">
                            <input type="checkbox" id="tlsEnabled" ${isActive ? 'checked' : ''}>
                            ${t('tls.enableCheckbox')}
                        </label>
                    </div>
                </div>
                <div class="ldap-form-actions">
                    <button class="action-btn" onclick="validateTlsFiles()">${t('tls.validateFiles')}</button>
                    <button class="action-btn primary" onclick="saveTlsConfig()">${t('tls.saveAndRestart')}</button>
                    ${hasConfig ? `<button class="action-btn danger" onclick="deleteTlsConfig()">${t('common.delete')}</button>` : ''}
                </div>
            </div>
            <div id="tlsValidationResult"></div>
        </div>`;

    return html;
}

function renderTlsGuideSection() {
    return `
        <div class="ldap-section">
            <h3>${t('tls.guide')}</h3>
            <div class="ldap-status-info" style="opacity: 0.9;">
                <strong>${t('tls.selfSigned')}</strong>
                <pre style="margin: 8px 0; padding: 8px; background: var(--bg-secondary); border-radius: 4px; overflow-x: auto; font-size: 12px;">openssl req -x509 -newkey rsa:4096 -nodes \\
  -keyout /etc/ssl/private/tareas-key.pem \\
  -out /etc/ssl/certs/tareas.pem \\
  -days 365 -subj "/CN=example.com"</pre>
            </div>
            <div class="ldap-status-info" style="margin-top: 8px; opacity: 0.9;">
                <strong>${t('tls.letsEncrypt')}</strong>
                <pre style="margin: 8px 0; padding: 8px; background: var(--bg-secondary); border-radius: 4px; overflow-x: auto; font-size: 12px;">apt install certbot
certbot certonly --standalone -d meine-domain.de
# Zertifikat: /etc/letsencrypt/live/meine-domain.de/fullchain.pem
# Key: /etc/letsencrypt/live/meine-domain.de/privkey.pem</pre>
            </div>
            <div class="ldap-status-info" style="margin-top: 8px; opacity: 0.9;">
                <strong>${t('tls.troubleshooting')}</strong><br>
                &bull; ${t('tls.trouble1')}<br>
                &bull; ${t('tls.trouble2')}<br>
                &bull; ${t('tls.trouble3')}<br>
                &bull; ${t('tls.trouble4')}
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
        showNotification(t('tls.pathsRequired'), 'error');
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
                resultDiv.innerHTML = `<div class="ldap-status-info ldap-status-connected" style="margin-top: 12px;">${t('tls.valid')}</div>`;
            }
            showNotification(t('tls.valid'), 'success');
        } else {
            if (resultDiv) {
                resultDiv.innerHTML = `<div class="ldap-status-info" style="margin-top: 12px; color: var(--danger);">${escapeHtml(data.error)}</div>`;
            }
            showNotification(data.error, 'error');
        }
    } catch (error) {
        showNotification(t('tls.validateError'), 'error');
    }
}

async function saveTlsConfig() {
    const cert_path = document.getElementById('tlsCertPath')?.value?.trim();
    const key_path = document.getElementById('tlsKeyPath')?.value?.trim();
    const enabled = document.getElementById('tlsEnabled')?.checked ? 1 : 0;

    if (!cert_path || !key_path) {
        showNotification(t('tls.pathsRequired'), 'error');
        return;
    }

    const action = enabled ? t('tls.enableConfirm') : t('tls.saveConfirm');
    if (!await msgbox('cancel/yes', 'warning', action)) return;

    await saveConfigToAPI({
        endpoint: '/api/admin/tls/config',
        data: { cert_path, key_path, enabled },
        successMessage: t('tls.saved'),
        onSuccess: async () => {
            await fetch('/api/dashboard/restart', { method: 'POST' });
        },
    });
}

async function deleteTlsConfig() {
    await deleteConfigFromAPI({
        endpoint: '/api/admin/tls/config',
        confirmMessage: t('tls.deleteConfirm'),
        successMessage: t('tls.deleted'),
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
        successMessage: t('tls.verifySaved'),
        onSuccess: () => {
            tlsVerifyConfig = { verify_nextcloud: !!verify_nextcloud, verify_onlyoffice: !!verify_onlyoffice,
                                verify_smtp: !!verify_smtp, verify_ldap: !!verify_ldap };
        },
    });
}
