/**
 * Tareas Admin - Mail-Tab
 * SMTP-Konfiguration, Testmail, Mail-Vorlagen
 */

let mailConfig = null;
let mailTemplates = [];

// ========================================
// Tab Initialisierung
// ========================================

async function initMailTab() {
    await initTabGeneric({
        showFilterBar: false,
        loadFn: () => Promise.all([loadMailConfig(), loadMailTemplates()]),
        renderFn: renderMailTab,
    });
}

async function loadMailConfig() {
    mailConfig = await loadConfigFromAPI('/api/admin/mail/config');
}

async function loadMailTemplates() {
    try {
        const resp = await fetch('/api/admin/mail/templates');
        if (!resp.ok) throw new Error(t('common.loadError'));
        const data = await resp.json();
        mailTemplates = data.templates || [];
    } catch (e) {
        mailTemplates = [];
    }
}

// ========================================
// Rendering
// ========================================

function renderMailTab() {
    const container = document.getElementById('contentContainer');
    if (!container) return;

    const hasConfig = !!mailConfig;

    let html = '<div class="mail-container">';

    // Sektion 1: SMTP-Server Konfiguration
    html += renderMailConfigSection(hasConfig);

    // Sektion 2: Testmail (nur wenn Config vorhanden)
    if (hasConfig) {
        html += renderTestMailSection();
    }

    // Sektion 3: Mail-Vorlagen
    if (mailTemplates.length > 0) {
        html += renderTemplatesSection();
    }

    html += '</div>';
    container.innerHTML = html;
}

function renderMailConfigSection(hasConfig) {
    const c = mailConfig || {};
    const encOptions = ['none', 'starttls', 'ssl'];

    let encSelect = '<select id="mailEncryption">';
    for (const opt of encOptions) {
        const label = opt === 'none' ? t('mailAdmin.encNone') : opt === 'starttls' ? 'STARTTLS' : 'SSL/TLS';
        const sel = (c.encryption || 'starttls') === opt ? 'selected' : '';
        encSelect += `<option value="${opt}" ${sel}>${label}</option>`;
    }
    encSelect += '</select>';

    let html = `
        <div class="mail-section">
            <h3>${t('mailAdmin.smtpTitle')}</h3>
            <div class="mail-form">
                <div class="mail-form-row">
                    <div class="mail-form-field">
                        <label>${t('mailAdmin.smtpServer')}</label>
                        <input type="text" id="mailSmtpServer" value="${escapeAttr(c.smtp_server || '')}"
                               placeholder="smtp.example.com">
                    </div>
                    <div class="mail-form-field" style="max-width:120px">
                        <label>${t('mailAdmin.port')}</label>
                        <input type="number" id="mailSmtpPort" value="${c.smtp_port || 587}">
                    </div>
                    <div class="mail-form-field" style="max-width:140px">
                        <label>${t('mailAdmin.encryption')}</label>
                        ${encSelect}
                    </div>
                </div>
                <div class="mail-form-row">
                    <div class="mail-form-field" style="max-width:100px">
                        <label>${t('mailAdmin.auth')}</label>
                        <label class="mail-checkbox">
                            <input type="checkbox" id="mailAuthEnabled" ${c.auth_enabled !== 0 ? 'checked' : ''}>
                            ${t('mailAdmin.active')}
                        </label>
                    </div>
                    <div class="mail-form-field">
                        <label>${t('auth.username')}</label>
                        <input type="text" id="mailUsername" value="${escapeAttr(c.username || '')}"
                               placeholder="user@example.com">
                    </div>
                    <div class="mail-form-field">
                        <label>${t('auth.password')}</label>
                        <input type="password" id="mailPassword"
                               value="${hasConfig ? '********' : ''}"
                               placeholder="${t('auth.password')}..." autocomplete="new-password">
                    </div>
                </div>
                <div class="mail-form-row">
                    <div class="mail-form-field">
                        <label>${t('mailAdmin.fromAddress')}</label>
                        <input type="email" id="mailFromAddress" value="${escapeAttr(c.from_address || '')}"
                               placeholder="noreply@example.com">
                    </div>
                    <div class="mail-form-field">
                        <label>${t('mailAdmin.fromName')}</label>
                        <input type="text" id="mailFromName" value="${escapeAttr(c.from_name || 'Tareas')}"
                               placeholder="Tareas">
                    </div>
                </div>
                <div class="mail-form-actions">
                    <button class="action-btn primary" onclick="saveMailConfig()">${t('mailAdmin.testAndSave')}</button>
                    ${hasConfig ? `<button class="action-btn danger" onclick="deleteMailConfig()">${t('common.delete')}</button>` : ''}
                </div>
            </div>`;

    if (hasConfig) {
        const enc = c.encryption === 'ssl' ? 'SSL/TLS' : c.encryption === 'starttls' ? 'STARTTLS' : t('mailAdmin.encNone');
        html += `
            <div class="mail-status-info">
                <strong>${t('mailAdmin.activeConfig')}</strong>
                ${escapeHtml(c.smtp_server)}:${c.smtp_port} | ${enc} | ${t('mailAdmin.fromName')}: ${escapeHtml(c.from_name)} &lt;${escapeHtml(c.from_address)}&gt;
            </div>`;
    }

    html += '</div>';
    return html;
}

function renderTestMailSection() {
    return `
        <div class="mail-section">
            <h3>${t('mailAdmin.testTitle')}</h3>
            <div class="mail-form">
                <div class="mail-form-row">
                    <div class="mail-form-field">
                        <label>${t('mailAdmin.recipient')}</label>
                        <input type="email" id="mailTestRecipient" placeholder="empfaenger@example.com">
                    </div>
                    <div class="mail-form-field" style="max-width:200px;justify-content:flex-end">
                        <button class="action-btn primary" onclick="sendTestMail()">${t('mailAdmin.sendTest')}</button>
                    </div>
                </div>
            </div>
        </div>`;
}

function renderTemplatesSection() {
    const eventLabels = {
        'task_assigned': t('mailAdmin.event.task_assigned'),
        'status_change': t('mailAdmin.event.status_change'),
        'deadline_reached': t('mailAdmin.event.deadline_reached'),
        'deadline_warning': t('mailAdmin.event.deadline_warning'),
        'invite': t('mailAdmin.event.invite'),
        'test': t('mailAdmin.event.test'),
    };

    const placeholderHints = {
        'task_assigned': '{recipient_name}, {task_name}, {actor_name}, {deadline}, {priority}, {app_url}',
        'status_change': '{recipient_name}, {task_name}, {actor_name}, {new_status}, {app_url}',
        'deadline_reached': '{recipient_name}, {task_name}, {app_url}',
        'deadline_warning': '{recipient_name}, {task_name}, {days_before}, {deadline}, {app_url}',
        'invite': '{recipient_name}, {username}, {app_url}',
        'test': t('mailAdmin.noPlaceholders'),
    };

    let html = `<div class="mail-section"><h3>${t('mailAdmin.templates')}</h3>`;
    html += `<div class="mail-status-info" style="margin-bottom: 16px;">${t('mailAdmin.templateAppUrlHint')}</div>`;

    for (const tmpl of mailTemplates) {
        const label = eventLabels[tmpl.event_type] || tmpl.event_type;
        const hint = placeholderHints[tmpl.event_type] || '';

        html += `
            <div class="mail-template-section" data-event-type="${tmpl.event_type}">
                <div class="mail-template-header">
                    <strong>${escapeHtml(label)}</strong>
                    <span class="mail-placeholder-hint">${t('mailAdmin.placeholder')}: ${escapeHtml(hint)}</span>
                </div>
                <div class="mail-form">
                    <div class="mail-form-row">
                        <div class="mail-form-field">
                            <label>${t('mailAdmin.subject')}</label>
                            <input type="text" id="tmplSubject_${tmpl.event_type}" value="${escapeAttr(tmpl.subject)}">
                        </div>
                    </div>
                    <div class="mail-form-row">
                        <div class="mail-form-field">
                            <label>${t('mailAdmin.body')}</label>
                            <textarea id="tmplBody_${tmpl.event_type}" rows="5">${escapeHtml(tmpl.body_text)}</textarea>
                        </div>
                    </div>
                    <div class="mail-form-actions">
                        <button class="action-btn primary" onclick="saveMailTemplate('${tmpl.event_type}')">${t('common.save')}</button>
                    </div>
                </div>
            </div>`;
    }

    html += '</div>';
    return html;
}



// ========================================
// Mail Config CRUD
// ========================================

async function saveMailConfig() {
    const smtp_server = document.getElementById('mailSmtpServer')?.value?.trim();
    const smtp_port = parseInt(document.getElementById('mailSmtpPort')?.value) || 587;
    const encryption = document.getElementById('mailEncryption')?.value || 'starttls';
    const auth_enabled = document.getElementById('mailAuthEnabled')?.checked ?? true;
    const username = document.getElementById('mailUsername')?.value?.trim() || '';
    const password = document.getElementById('mailPassword')?.value || '';
    const from_address = document.getElementById('mailFromAddress')?.value?.trim();
    const from_name = document.getElementById('mailFromName')?.value?.trim() || 'Tareas';

    if (!smtp_server || !from_address) {
        showNotification(t('mailAdmin.fillRequired'), 'error');
        return;
    }

    await saveConfigToAPI({
        endpoint: '/api/admin/mail/config',
        data: { smtp_server, smtp_port, encryption, auth_enabled, username, password, from_address, from_name },
        successMessage: t('mailAdmin.saved'),
        onSuccess: async () => {
            await loadMailConfig();
            renderMailTab();
        },
    });
}

async function deleteMailConfig() {
    await deleteConfigFromAPI({
        endpoint: '/api/admin/mail/config',
        confirmMessage: t('mailAdmin.deleteConfirm'),
        successMessage: t('mailAdmin.deleted'),
        onSuccess: () => {
            mailConfig = null;
            renderMailTab();
        },
    });
}

// ========================================
// Testmail
// ========================================

async function sendTestMail() {
    const to_email = document.getElementById('mailTestRecipient')?.value?.trim();
    if (!to_email) {
        showNotification(t('mailAdmin.recipientRequired'), 'error');
        return;
    }

    try {
        const resp = await fetch('/api/admin/mail/test', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ to_email }),
        });

        if (!resp.ok) {
            const data = await resp.json().catch(() => ({}));
            throw new Error(data.detail || t('mailAdmin.sendError'));
        }

        showNotification(t('mailAdmin.testSent', { email: to_email }), 'success');
    } catch (error) {
        showNotification(error.message, 'error');
    }
}

// ========================================
// Templates
// ========================================

async function saveMailTemplate(eventType) {
    const subject = document.getElementById(`tmplSubject_${eventType}`)?.value || '';
    const body_text = document.getElementById(`tmplBody_${eventType}`)?.value || '';

    if (!subject.trim()) {
        showNotification(t('mailAdmin.subjectRequired'), 'error');
        return;
    }

    try {
        const resp = await fetch(`/api/admin/mail/templates/${eventType}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ subject, body_text }),
        });

        if (!resp.ok) {
            const data = await resp.json().catch(() => ({}));
            throw new Error(data.detail || t('common.saveError'));
        }

        showNotification(t('mailAdmin.templateSaved'), 'success');
    } catch (error) {
        showNotification(error.message, 'error');
    }
}
