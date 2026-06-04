/**
 * Tareas Admin - MCP-Tab
 * Token-Verwaltung, Audit-Log-Viewer, Kill-Switch.
 */

let mcpConfig = null;
let mcpTokens = [];
let mcpAudit = { items: [], total: 0, limit: 100, offset: 0 };
let mcpAuditFilters = {
    actor_type: '',
    entity_type: '',
    action: '',
};

// ========================================
// Initialisierung
// ========================================

async function initMcpTab() {
    await initTabGeneric({
        showFilterBar: false,
        loadFn: loadAllMcpData,
        renderFn: renderMcpTab,
    });
}

async function loadAllMcpData() {
    await Promise.all([
        loadMcpConfig(),
        loadMcpTokens(),
        loadMcpAudit(),
    ]);
}

async function loadMcpConfig() {
    try {
        const resp = await fetch('/api/admin/mcp/config');
        mcpConfig = resp.ok ? await resp.json() : null;
    } catch (e) {
        mcpConfig = null;
    }
}

async function loadMcpTokens() {
    try {
        const resp = await fetch('/api/admin/mcp/tokens');
        const data = resp.ok ? await resp.json() : { items: [] };
        mcpTokens = data.items || [];
    } catch (e) {
        mcpTokens = [];
    }
}

async function loadMcpAudit() {
    const params = new URLSearchParams();
    params.set('limit', String(mcpAudit.limit));
    params.set('offset', String(mcpAudit.offset));
    if (mcpAuditFilters.actor_type) params.set('actor_type', mcpAuditFilters.actor_type);
    if (mcpAuditFilters.entity_type) params.set('entity_type', mcpAuditFilters.entity_type);
    if (mcpAuditFilters.action) params.set('action', mcpAuditFilters.action);
    try {
        const resp = await fetch('/api/admin/mcp/audit?' + params.toString());
        const data = resp.ok ? await resp.json() : { items: [], total: 0 };
        mcpAudit = { ...mcpAudit, items: data.items || [], total: data.total || 0 };
    } catch (e) {
        mcpAudit = { ...mcpAudit, items: [], total: 0 };
    }
}

// ========================================
// Rendering
// ========================================

function renderMcpTab() {
    const container = document.getElementById('contentContainer');
    if (!container) return;

    let html = '<div class="ldap-container">';
    html += renderMcpStatusSection();
    html += renderMcpTokensSection();
    html += renderMcpAuditSection();
    html += '</div>';
    container.innerHTML = html;

    // Event-Handler nachtraeglich verkabeln
    wireMcpHandlers();
}

function renderMcpStatusSection() {
    const cfg = mcpConfig || { enabled: true, server_url: '', available_tools: [], active_token_count: 0 };
    const enabled = !!cfg.enabled;
    const statusClass = enabled ? 'ldap-status-connected' : '';
    const statusLabel = enabled ? 'aktiv' : 'deaktiviert';
    const toolsHtml = (cfg.available_tools || [])
        .map(t => `<code style="margin-right:6px;display:inline-block">${escapeHtml(t)}</code>`)
        .join(' ');

    return `
        <div class="ldap-section">
            <h3>MCP-Server</h3>
            <div class="ldap-status-info ${statusClass}">
                <strong>Status:</strong> ${statusLabel}<br>
                <strong>Endpoint:</strong> <code>${escapeHtml(cfg.server_url || '(unbekannt)')}</code><br>
                <strong>Aktive Tokens:</strong> ${cfg.active_token_count}<br>
                <strong>Tools (${(cfg.available_tools || []).length}):</strong><br>
                <div style="margin-top:6px;line-height:1.8">${toolsHtml}</div>
            </div>
            <div style="margin-top:12px">
                <button id="mcpKillSwitchBtn" class="action-btn ${enabled ? 'danger' : 'primary'}">
                    ${enabled ? 'MCP-Server deaktivieren' : 'MCP-Server aktivieren'}
                </button>
            </div>
        </div>`;
}

function renderMcpTokensSection() {
    let rows = '';
    if (mcpTokens.length === 0) {
        rows = `<tr><td colspan="5" style="text-align:center;opacity:0.6;padding:20px">Keine Tokens vorhanden</td></tr>`;
    } else {
        for (const t of mcpTokens) {
            const statusBadge = t.active
                ? '<span style="color:var(--color-success, #2e7d32)">aktiv</span>'
                : `<span style="opacity:0.6">widerrufen ${escapeHtml(t.revoked_at || '')}</span>`;
            const lastUsed = t.last_used_at ? escapeHtml(t.last_used_at) : '<span style="opacity:0.5">nie</span>';
            const action = t.active
                ? `<button class="action-btn danger" data-revoke="${t.id}">Widerrufen</button>`
                : '';
            rows += `
                <tr>
                    <td>${escapeHtml(t.display_name)}</td>
                    <td>${escapeHtml(t.created_at || '')}</td>
                    <td>${lastUsed}</td>
                    <td>${statusBadge}</td>
                    <td>${action}</td>
                </tr>`;
        }
    }
    return `
        <div class="ldap-section">
            <h3>API-Tokens</h3>
            <div style="margin-bottom:12px">
                <button id="mcpNewTokenBtn" class="action-btn primary">Neuen Token erstellen</button>
            </div>
            <table class="admin-table" style="width:100%;border-collapse:collapse">
                <thead>
                    <tr>
                        <th style="text-align:left;padding:8px;border-bottom:1px solid var(--border-color)">Name</th>
                        <th style="text-align:left;padding:8px;border-bottom:1px solid var(--border-color)">Erstellt</th>
                        <th style="text-align:left;padding:8px;border-bottom:1px solid var(--border-color)">Zuletzt genutzt</th>
                        <th style="text-align:left;padding:8px;border-bottom:1px solid var(--border-color)">Status</th>
                        <th style="padding:8px;border-bottom:1px solid var(--border-color)"></th>
                    </tr>
                </thead>
                <tbody>${rows}</tbody>
            </table>
        </div>`;
}

function renderMcpAuditSection() {
    const filterOptions = {
        actor_type: ['', 'mcp', 'user', 'system'],
        entity_type: ['', 'task', 'sub_task', 'task_note', 'sub_task_note', 'dependency', 'mcp_token', 'mcp_config'],
        action: ['', 'create', 'update', 'delete', 'status_change', 'revoke'],
    };
    const mkOpt = (k, val) => {
        const sel = mcpAuditFilters[k] === val ? 'selected' : '';
        return `<option value="${escapeHtml(val)}" ${sel}>${val ? escapeHtml(val) : 'alle'}</option>`;
    };

    let rows = '';
    if (mcpAudit.items.length === 0) {
        rows = `<tr><td colspan="5" style="text-align:center;opacity:0.6;padding:20px">Keine Eintraege</td></tr>`;
    } else {
        for (const a of mcpAudit.items) {
            const actorBadge = a.actor_type === 'mcp'
                ? '<span style="background:var(--color-primary,#1976d2);color:#fff;border-radius:3px;padding:1px 6px;font-size:11px;margin-right:6px">MCP</span>'
                : (a.actor_type === 'system'
                    ? '<span style="background:#888;color:#fff;border-radius:3px;padding:1px 6px;font-size:11px;margin-right:6px">SYS</span>'
                    : '');
            const actorLabel = (a.actor_display || a.actor_username || '(unbekannt)');
            let changesPreview = '';
            if (a.changes_json) {
                try {
                    const parsed = JSON.parse(a.changes_json);
                    changesPreview = JSON.stringify(parsed).slice(0, 80);
                } catch {
                    changesPreview = String(a.changes_json).slice(0, 80);
                }
            }
            rows += `
                <tr>
                    <td style="white-space:nowrap;font-family:monospace;font-size:12px">${escapeHtml(a.timestamp)}</td>
                    <td>${actorBadge}${escapeHtml(actorLabel)}</td>
                    <td>${escapeHtml(a.entity_type)}${a.entity_id ? ' #' + a.entity_id : ''}</td>
                    <td>${escapeHtml(a.action)}</td>
                    <td style="font-family:monospace;font-size:11px;opacity:0.85">${escapeHtml(changesPreview)}</td>
                </tr>`;
        }
    }

    const pageInfo = `${mcpAudit.offset + 1}-${Math.min(mcpAudit.offset + mcpAudit.limit, mcpAudit.total)} / ${mcpAudit.total}`;
    const prevDisabled = mcpAudit.offset === 0 ? 'disabled' : '';
    const nextDisabled = (mcpAudit.offset + mcpAudit.limit) >= mcpAudit.total ? 'disabled' : '';

    return `
        <div class="ldap-section">
            <h3>Audit-Log</h3>
            <div style="display:flex;gap:12px;margin-bottom:12px;align-items:flex-end;flex-wrap:wrap">
                <div>
                    <label style="display:block;font-size:12px;opacity:0.7">Actor-Typ</label>
                    <select id="auditFilterActor">${filterOptions.actor_type.map(v => mkOpt('actor_type', v)).join('')}</select>
                </div>
                <div>
                    <label style="display:block;font-size:12px;opacity:0.7">Entity</label>
                    <select id="auditFilterEntity">${filterOptions.entity_type.map(v => mkOpt('entity_type', v)).join('')}</select>
                </div>
                <div>
                    <label style="display:block;font-size:12px;opacity:0.7">Aktion</label>
                    <select id="auditFilterAction">${filterOptions.action.map(v => mkOpt('action', v)).join('')}</select>
                </div>
                <button id="auditApplyFiltersBtn" class="action-btn">Filtern</button>
            </div>
            <table class="admin-table" style="width:100%;border-collapse:collapse">
                <thead>
                    <tr>
                        <th style="text-align:left;padding:8px;border-bottom:1px solid var(--border-color)">Zeit</th>
                        <th style="text-align:left;padding:8px;border-bottom:1px solid var(--border-color)">Actor</th>
                        <th style="text-align:left;padding:8px;border-bottom:1px solid var(--border-color)">Entity</th>
                        <th style="text-align:left;padding:8px;border-bottom:1px solid var(--border-color)">Action</th>
                        <th style="text-align:left;padding:8px;border-bottom:1px solid var(--border-color)">Aenderungen</th>
                    </tr>
                </thead>
                <tbody>${rows}</tbody>
            </table>
            <div style="margin-top:12px;display:flex;gap:8px;align-items:center">
                <button id="auditPrevBtn" class="action-btn" ${prevDisabled}>&lt; Vorherige</button>
                <span style="opacity:0.7;font-size:13px">${pageInfo}</span>
                <button id="auditNextBtn" class="action-btn" ${nextDisabled}>Naechste &gt;</button>
            </div>
        </div>`;
}

// ========================================
// Event-Handling
// ========================================

function wireMcpHandlers() {
    const killBtn = document.getElementById('mcpKillSwitchBtn');
    if (killBtn) killBtn.addEventListener('click', toggleMcpEnabled);

    const newTokenBtn = document.getElementById('mcpNewTokenBtn');
    if (newTokenBtn) newTokenBtn.addEventListener('click', openNewTokenModal);

    document.querySelectorAll('[data-revoke]').forEach(btn => {
        btn.addEventListener('click', () => revokeToken(parseInt(btn.dataset.revoke, 10)));
    });

    const applyBtn = document.getElementById('auditApplyFiltersBtn');
    if (applyBtn) applyBtn.addEventListener('click', applyAuditFilters);

    const prevBtn = document.getElementById('auditPrevBtn');
    if (prevBtn) prevBtn.addEventListener('click', () => paginateAudit(-1));
    const nextBtn = document.getElementById('auditNextBtn');
    if (nextBtn) nextBtn.addEventListener('click', () => paginateAudit(1));
}

async function toggleMcpEnabled() {
    if (!mcpConfig) return;
    const next = !mcpConfig.enabled;
    const confirmMsg = next
        ? 'MCP-Server aktivieren?'
        : 'MCP-Server deaktivieren? Alle Token-Aufrufe werden ab sofort abgelehnt.';
    if (!await msgbox('cancel/yes', 'warning', confirmMsg)) return;
    try {
        const resp = await fetch('/api/admin/mcp/config', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ enabled: next }),
        });
        if (!resp.ok) throw new Error('Fehler beim Speichern');
        showNotification(next ? 'MCP aktiviert' : 'MCP deaktiviert', 'success');
        await loadMcpConfig();
        renderMcpTab();
    } catch (e) {
        showNotification(e.message || 'Fehler', 'error');
    }
}

function openNewTokenModal() {
    createModal({
        title: 'Neuen MCP-Token erstellen',
        body: `
            <div class="modal-field" style="margin-bottom:12px">
                <label for="mcpNewTokenName" style="display:block;margin-bottom:4px">
                    Name (z. B. "Claude @ Projekt Foo")
                </label>
                <input type="text" id="mcpNewTokenName" style="width:100%" autocomplete="off">
            </div>
            <p style="opacity:0.75;font-size:12px;margin:0">
                Beim Klick auf "Erstellen" wird der Token EINMAL angezeigt.
                Danach ist er nicht mehr abrufbar - du kannst aber jederzeit einen neuen erzeugen.
            </p>`,
        footer: `
            <button class="action-btn" onclick="closeModal()">Abbrechen</button>
            <button class="action-btn primary" id="mcpCreateTokenBtn">Erstellen</button>`,
        onOpen: () => {
            const input = document.getElementById('mcpNewTokenName');
            if (input) input.focus();
            const btn = document.getElementById('mcpCreateTokenBtn');
            if (btn) btn.addEventListener('click', createTokenSubmit);
        },
    });
}

async function createTokenSubmit() {
    const nameInput = document.getElementById('mcpNewTokenName');
    const name = (nameInput && nameInput.value || '').trim();
    if (!name) {
        showNotification('Name darf nicht leer sein', 'error');
        return;
    }
    try {
        const resp = await fetch('/api/admin/mcp/tokens', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ display_name: name }),
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) throw new Error(data.detail || 'Fehler beim Erstellen');
        closeModal();
        showTokenOnce(data);
        await loadMcpTokens();
        await loadMcpConfig();
        renderMcpTab();
    } catch (e) {
        showNotification(e.message || 'Fehler', 'error');
    }
}

function showTokenOnce(data) {
    const tokenStr = data.token || '';
    createModal({
        title: 'Token erstellt - jetzt sichern!',
        body: `
            <p style="margin:0 0 12px">
                Dieser Token wird nur EINMAL angezeigt. Bitte sicher kopieren und speichern:
            </p>
            <textarea id="mcpTokenView" readonly
                style="width:100%;min-height:80px;font-family:monospace;font-size:13px"
                onclick="this.select()">${escapeHtml(tokenStr)}</textarea>
            <p style="margin:12px 0 0;font-size:12px;opacity:0.75">
                Verwendung in Claude Code:
            </p>
            <pre style="background:var(--bg-secondary,#f5f5f5);padding:8px;border-radius:4px;font-size:11px;overflow:auto">{
  "mcpServers": {
    "tareas": {
      "type": "http",
      "url": "${escapeHtml((mcpConfig && mcpConfig.server_url) || 'http://localhost:8504/mcp/')}",
      "headers": { "Authorization": "Bearer ${escapeHtml(tokenStr)}" }
    }
  }
}</pre>`,
        footer: `
            <button class="action-btn" onclick="copyTokenToClipboard()">Token kopieren</button>
            <button class="action-btn primary" onclick="closeModal()">Schliessen</button>`,
        cssClass: 'modal-wide',
    });
}

function copyTokenToClipboard() {
    const ta = document.getElementById('mcpTokenView');
    if (!ta) return;
    ta.select();
    try {
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(ta.value);
        } else {
            document.execCommand('copy');
        }
        showNotification('Token kopiert', 'success');
    } catch (e) {
        showNotification('Kopieren fehlgeschlagen', 'error');
    }
}

async function revokeToken(tokenId) {
    if (!await msgbox('cancel/yes', 'warning', 'Token wirklich widerrufen? Bestehende Verbindungen werden abgelehnt.')) return;
    try {
        const resp = await fetch('/api/admin/mcp/tokens/' + tokenId, { method: 'DELETE' });
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            throw new Error(err.detail || 'Fehler beim Widerrufen');
        }
        showNotification('Token widerrufen', 'success');
        await loadMcpTokens();
        await loadMcpConfig();
        renderMcpTab();
    } catch (e) {
        showNotification(e.message || 'Fehler', 'error');
    }
}

async function applyAuditFilters() {
    mcpAuditFilters.actor_type = document.getElementById('auditFilterActor').value;
    mcpAuditFilters.entity_type = document.getElementById('auditFilterEntity').value;
    mcpAuditFilters.action = document.getElementById('auditFilterAction').value;
    mcpAudit.offset = 0;
    await loadMcpAudit();
    renderMcpTab();
}

async function paginateAudit(direction) {
    const newOffset = mcpAudit.offset + direction * mcpAudit.limit;
    if (newOffset < 0) return;
    if (newOffset >= mcpAudit.total) return;
    mcpAudit.offset = newOffset;
    await loadMcpAudit();
    renderMcpTab();
}
