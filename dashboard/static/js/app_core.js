/**
 * Tareas - Core Module
 * Initialisierung, Theme, Settings, Tabs, URL-Routing, Notifications
 */

// ========================================
// Globaler 401-Interceptor
// ========================================

const _origFetch = window.fetch;
window.fetch = async function(url, options = {}) {
    // CSRF-Header fuer alle mutierenden API-Requests
    if (String(url).includes('/api/') && options.method && options.method !== 'GET') {
        options.headers = { 'X-Requested-With': 'XMLHttpRequest', ...options.headers };
    }
    const resp = await _origFetch.call(this, url, options);
    if (resp.status === 401 && String(url).includes('/api/')) {
        window.location.href = '/login';
    }
    return resp;
};

// ========================================
// Globale Variablen
// ========================================

let currentTab = 'aufgaben';
let currentUser = null;

// Settings Dropdown State
let settingsDropdownOpen = false;

// Farbschema-Konfiguration
const COLOR_SCHEMES = [
    {
        code: 'light',
        theme: 'light',
        i18nKey: 'settings.scheme.light',
        fallbackLabel: 'Hell',
        swatches: ['#ffffff', '#172033', '#1d4ed8'],
    },
    {
        code: 'dark',
        theme: 'dark',
        i18nKey: 'settings.scheme.dark',
        fallbackLabel: 'Dunkel',
        swatches: ['#0b1220', '#020617', '#22d3ee'],
    },
    {
        code: 'graphit',
        theme: 'light',
        i18nKey: 'settings.scheme.graphit',
        fallbackLabel: 'Graphit / Cyan',
        swatches: ['#111827', '#0e7490', '#92400e'],
    },
    {
        code: 'slate',
        theme: 'light',
        i18nKey: 'settings.scheme.slate',
        fallbackLabel: 'Slate / Emerald',
        swatches: ['#1f2933', '#047857', '#be123c'],
    },
    {
        code: 'ink',
        theme: 'dark',
        i18nKey: 'settings.scheme.ink',
        fallbackLabel: 'Ink / Indigo',
        swatches: ['#111014', '#c4b5fd', '#bef264'],
    },
];

// DOM-Cache fuer Tab-Inhalte (DocumentFragment-basiert)
const tabDOMCache = {};

// ========================================
// Initialisierung
// ========================================

document.addEventListener('DOMContentLoaded', function() {
    initTheme();
    initEventListeners();

    // Admin-Mode Guard: Tabs nur in der Haupt-App initialisieren
    if (!document.body.dataset.adminMode) {
        loadCurrentUser();
        initTabs();
    }
});

function initEventListeners() {
    // Settings-Button
    const settingsBtn = document.getElementById('settingsBtn');
    if (settingsBtn) {
        settingsBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            toggleSettingsDropdown();
        });
    }

    // Restart-Button
    const restartBtn = document.getElementById('restartBtn');
    if (restartBtn) {
        restartBtn.addEventListener('click', restartDashboard);
    }

    // Logout-Button
    const logoutBtn = document.getElementById('logoutBtn');
    if (logoutBtn) {
        logoutBtn.addEventListener('click', logout);
    }

    // Passwort-Aendern-Button
    const changePasswordBtn = document.getElementById('changePasswordBtn');
    if (changePasswordBtn) {
        changePasswordBtn.addEventListener('click', openPasswordChangeModal);
    }

    // Mail-Benachrichtigungen-Button
    const mailPrefsBtn = document.getElementById('mailPrefsBtn');
    if (mailPrefsBtn) {
        mailPrefsBtn.addEventListener('click', openMailPreferencesModal);
    }

    // Language Submenu
    const langToggle = document.getElementById('langToggle');
    if (langToggle) {
        langToggle.addEventListener('click', toggleLangMenu);
        buildLangMenu();
    }

    // Farbschema Submenu
    const schemeToggle = document.getElementById('schemeToggle');
    if (schemeToggle) {
        schemeToggle.addEventListener('click', toggleColorSchemeMenu);
        buildColorSchemeMenu();
    }

    // Tabs
    document.querySelectorAll('.tab').forEach(tab => {
        tab.addEventListener('click', function(e) {
            e.preventDefault();
            navigateTo(this.dataset.tab);
        });
    });

    // Klick ausserhalb Settings-Dropdown schliesst es
    document.addEventListener('click', function(e) {
        const settingsWrapper = document.querySelector('.settings-dropdown-wrapper');
        if (settingsWrapper && settingsDropdownOpen && !settingsWrapper.contains(e.target)) {
            toggleSettingsDropdown(false);
        }
    });

    // ESC schliesst Overlays
    document.addEventListener('keydown', function(e) {
        if (e.key === 'Escape') {
            if (settingsDropdownOpen) {
                toggleSettingsDropdown(false);
            }
        }
    });
}

// ========================================
// User / Auth
// ========================================

async function loadCurrentUser() {
    try {
        const resp = await _origFetch('/api/auth/me');
        if (!resp.ok) {
            window.location.href = '/login';
            return;
        }
        const data = await resp.json();
        currentUser = data.user;

        const headerUsername = document.getElementById('headerUsername');
        if (headerUsername && currentUser) {
            const displayName = currentUser.vorname
                ? `${currentUser.vorname} ${currentUser.nachname}`.trim()
                : currentUser.username;
            headerUsername.textContent = displayName;
        }
    } catch (e) {
        // Netzwerkfehler - nichts tun
    }
}

async function logout() {
    try {
        await fetch('/api/auth/logout', { method: 'POST' });
    } catch (e) {
        // Ignorieren
    }
    document.cookie = 'tareas_session=; path=/; expires=Thu, 01 Jan 1970 00:00:00 GMT';
    window.location.href = '/login';
}

function openPasswordChangeModal() {
    toggleSettingsDropdown(false);

    createModal({
        title: t('password.change'),
        body: `
            <div class="modal-field">
                <label>${t('password.old')}</label>
                <input type="password" id="pwdOld" autocomplete="current-password">
            </div>
            <div class="modal-field">
                <label>${t('password.new')}</label>
                <input type="password" id="pwdNew" autocomplete="new-password">
            </div>
            <div class="modal-field">
                <label>${t('password.confirm')}</label>
                <input type="password" id="pwdConfirm" autocomplete="new-password">
            </div>`,
        footer: `<button class="action-btn" onclick="closeModal()">${t('common.cancel')}</button>` +
                `<button class="action-btn primary" id="pwdSaveBtn">${t('common.change')}</button>`,
        onOpen: () => {
            document.getElementById('pwdOld').focus();

            document.getElementById('pwdSaveBtn').addEventListener('click', async () => {
                const oldPwd = document.getElementById('pwdOld').value;
                const newPwd = document.getElementById('pwdNew').value;
                const confirmPwd = document.getElementById('pwdConfirm').value;

                if (!oldPwd || !newPwd) {
                    showNotification(t('password.fillAll'), 'error');
                    return;
                }
                if (newPwd !== confirmPwd) {
                    showNotification(t('password.mismatch'), 'error');
                    return;
                }
                if (newPwd.length < 4) {
                    showNotification(t('password.minLength'), 'error');
                    return;
                }

                try {
                    const resp = await fetch('/api/auth/password', {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ old_password: oldPwd, new_password: newPwd }),
                    });

                    if (!resp.ok) {
                        const data = await resp.json().catch(() => ({}));
                        throw new Error(data.detail || t('common.error'));
                    }

                    showNotification(t('password.changed'), 'success');
                    closeModal();
                } catch (error) {
                    showNotification(error.message, 'error');
                }
            });
        },
    });
}

// ========================================
// Theme / Color Scheme Handling
// ========================================

function initTheme() {
    let savedScheme = normalizeColorScheme(localStorage.getItem('dashboard-color-scheme'));
    if (!localStorage.getItem('dashboard-color-scheme')) {
        // Migration vom alten separaten Dark-Mode-Schalter.
        savedScheme = localStorage.getItem('dashboard-theme') === 'dark' ? 'dark' : 'light';
    }
    applyColorScheme(savedScheme);
    updateColorSchemeButton(savedScheme);
    initLangStatus();
}

function normalizeColorScheme(code) {
    return COLOR_SCHEMES.some(s => s.code === code) ? code : 'light';
}

function getColorScheme() {
    return normalizeColorScheme(document.documentElement.getAttribute('data-color-scheme'));
}

function getColorSchemeConfig(code = getColorScheme()) {
    const normalized = normalizeColorScheme(code);
    return COLOR_SCHEMES.find(s => s.code === normalized) || COLOR_SCHEMES[0];
}

function getColorSchemeLabel(scheme) {
    const label = t(scheme.i18nKey);
    return label === scheme.i18nKey ? scheme.fallbackLabel : label;
}

function applyColorScheme(code) {
    const cfg = getColorSchemeConfig(code);
    document.documentElement.setAttribute('data-color-scheme', cfg.code);
    document.documentElement.setAttribute('data-theme', cfg.theme);
}

function toggleColorSchemeMenu() {
    const sub = document.getElementById('schemeSubMenu');
    const icon = document.getElementById('schemeExpandIcon');
    if (!sub) return;

    const langSub = document.getElementById('langSubMenu');
    const langIcon = document.getElementById('langExpandIcon');
    if (langSub) langSub.style.display = 'none';
    if (langIcon) langIcon.classList.remove('open');

    const isOpen = sub.style.display !== 'none';
    sub.style.display = isOpen ? 'none' : 'block';
    if (icon) icon.classList.toggle('open', !isOpen);
}

function buildColorSchemeMenu() {
    const sub = document.getElementById('schemeSubMenu');
    if (!sub) return;
    const current = getColorScheme();
    sub.innerHTML = COLOR_SCHEMES.map(s => {
        const active = s.code === current ? ' active' : '';
        const check = s.code === current ? '\u2713' : '';
        const label = getColorSchemeLabel(s);
        const swatches = s.swatches
            .map(color => `<span style="background:${escapeAttr(color)}"></span>`)
            .join('');
        return `<div class="settings-sub-item${active}" data-scheme="${escapeAttr(s.code)}">
            <span class="lang-check">${check}</span>
            <span class="scheme-swatch">${swatches}</span>
            <span>${escapeHtml(label)}</span>
        </div>`;
    }).join('');

    if (!sub.dataset.bound) {
        sub.addEventListener('click', (e) => {
            const item = e.target.closest('[data-scheme]');
            if (item) setColorScheme(item.dataset.scheme);
        });
        sub.dataset.bound = '1';
    }
}

function setColorScheme(code) {
    const scheme = normalizeColorScheme(code);
    const cfg = getColorSchemeConfig(scheme);
    applyColorScheme(scheme);
    localStorage.setItem('dashboard-color-scheme', scheme);
    updateColorSchemeButton(scheme);
    buildColorSchemeMenu();

    document.dispatchEvent(new CustomEvent('colorSchemeChanged', {
        detail: { colorScheme: scheme, theme: cfg.theme },
    }));
    document.dispatchEvent(new CustomEvent('themeChanged', {
        detail: { theme: cfg.theme, colorScheme: scheme },
    }));
}

function updateColorSchemeButton(code) {
    const cfg = getColorSchemeConfig(code);
    const schemeStatus = document.getElementById('schemeStatus');
    if (schemeStatus) {
        schemeStatus.textContent = getColorSchemeLabel(cfg);
    }
}

// ========================================
// Language Handling
// ========================================

function toggleLangMenu() {
    const sub = document.getElementById('langSubMenu');
    const icon = document.getElementById('langExpandIcon');
    if (!sub) return;

    const schemeSub = document.getElementById('schemeSubMenu');
    const schemeIcon = document.getElementById('schemeExpandIcon');
    if (schemeSub) schemeSub.style.display = 'none';
    if (schemeIcon) schemeIcon.classList.remove('open');

    const isOpen = sub.style.display !== 'none';
    sub.style.display = isOpen ? 'none' : 'block';
    if (icon) icon.classList.toggle('open', !isOpen);
}

function buildLangMenu() {
    const sub = document.getElementById('langSubMenu');
    if (!sub) return;
    const langs = getAvailableLangs();
    const current = getLang();
    sub.innerHTML = langs.map(l => {
        const active = l.code === current ? ' active' : '';
        const check = l.code === current ? '\u2713' : '';
        return `<div class="settings-sub-item${active}" data-lang="${l.code}">
            <span class="lang-check">${check}</span>
            <span>${l.label}</span>
        </div>`;
    }).join('');
    sub.addEventListener('click', (e) => {
        const item = e.target.closest('[data-lang]');
        if (item) setLang(item.dataset.lang);
    });
}

function initLangStatus() {
    const langStatus = document.getElementById('langStatus');
    if (langStatus) {
        langStatus.textContent = getLang().toUpperCase();
    }
}

// ========================================
// Settings Dropdown
// ========================================

function toggleSettingsDropdown(forceState = null) {
    const dropdown = document.getElementById('settingsDropdown');
    const btn = document.getElementById('settingsBtn');

    if (forceState !== null) {
        settingsDropdownOpen = forceState;
    } else {
        settingsDropdownOpen = !settingsDropdownOpen;
    }

    if (dropdown) {
        dropdown.style.display = settingsDropdownOpen ? 'block' : 'none';
    }
    if (btn) {
        btn.style.transform = settingsDropdownOpen ? 'rotate(90deg)' : '';
    }
}

async function restartDashboard() {
    if (!confirm(t('restart.confirm'))) {
        return;
    }

    const restartBtn = document.getElementById('restartBtn');
    if (restartBtn) {
        restartBtn.innerHTML = `<span class="settings-icon">\u231B</span><span class="settings-label">${t('restart.restarting')}</span>`;
        restartBtn.style.pointerEvents = 'none';
    }

    try {
        await fetch('/api/dashboard/restart', { method: 'POST' });
    } catch (e) {
        // Erwarteter Fehler - Verbindung bricht ab
    }

    setTimeout(() => {
        location.reload();
    }, 3000);
}

// ========================================
// URL-Routing
// ========================================

function parseRoute() {
    const path = window.location.pathname.replace(/\/+$/, '') || '/';
    const segments = path.split('/').filter(Boolean);

    if (segments.length === 0) return { tab: 'aufgaben' };

    const first = segments[0];

    const tabMap = {
        'aufgaben': 'aufgaben',
    };

    return { tab: tabMap[first] || 'aufgaben' };
}

function buildPath(tab) {
    const pathMap = {
        'aufgaben': '/aufgaben',
    };
    return pathMap[tab] || '/aufgaben';
}

function navigateTo(tab, replaceState) {
    const path = buildPath(tab);
    const state = { tab };

    if (replaceState) {
        history.replaceState(state, '', path);
    } else {
        history.pushState(state, '', path);
    }

    switchTab(tab);
}

// Browser-Zurueck/Vorwaerts
window.addEventListener('popstate', function(e) {
    const route = parseRoute();
    switchTab(route.tab);
});

// ========================================
// Tab-Wechsel
// ========================================

function initTabs() {
    const route = parseRoute();
    switchTab(route.tab);
    const path = buildPath(route.tab);
    history.replaceState({ tab: route.tab }, '', path);
}

function switchTab(tab) {
    const previousTab = currentTab;
    const isSameTab = (previousTab === tab);

    // Gleicher Tab nochmal geklickt = Cache verwerfen (Refresh)
    if (isSameTab) {
        invalidateTabCache(tab);
    } else {
        saveTabDOM(previousTab);
    }

    currentTab = tab;

    // Tab-Buttons aktualisieren
    document.querySelectorAll('.main-tabs .tab').forEach(t => {
        t.classList.remove('active');
        if (t.dataset.tab === tab) {
            t.classList.add('active');
        }
    });

    const filterBar = document.getElementById('filterBar');

    // Cache vorhanden? DOM wiederherstellen
    if (!isSameTab && restoreTabDOM(tab)) {
        return;
    }

    // Kein Cache oder gleicher Tab: Init-Funktion aufrufen
    if (tab === 'aufgaben') {
        initAufgabenTab();
        if (filterBar) filterBar.style.display = 'flex';
    }
}

// ========================================
// DOM-Caching fuer Tabs
// ========================================

function saveTabDOM(tabName) {
    const contentContainer = document.getElementById('contentContainer');
    const filterBar = document.getElementById('filterBar');
    if (!contentContainer) return;

    if (contentContainer.children.length === 0) return;

    const contentFragment = document.createDocumentFragment();
    while (contentContainer.firstChild) {
        contentFragment.appendChild(contentContainer.firstChild);
    }

    const filterFragment = document.createDocumentFragment();
    let filterBarDisplay = 'none';
    if (filterBar) {
        filterBarDisplay = filterBar.style.display || window.getComputedStyle(filterBar).display;
        while (filterBar.firstChild) {
            filterFragment.appendChild(filterBar.firstChild);
        }
    }

    tabDOMCache[tabName] = {
        content: contentFragment,
        filter: filterFragment,
        filterBarDisplay: filterBarDisplay,
        scrollY: window.scrollY,
    };
}

function restoreTabDOM(tabName) {
    const cached = tabDOMCache[tabName];
    if (!cached) return false;

    const contentContainer = document.getElementById('contentContainer');
    const filterBar = document.getElementById('filterBar');

    if (contentContainer) {
        contentContainer.innerHTML = '';
        contentContainer.appendChild(cached.content);
    }

    if (filterBar) {
        filterBar.innerHTML = '';
        filterBar.appendChild(cached.filter);
        filterBar.style.display = cached.filterBarDisplay;
    }

    const savedScrollY = cached.scrollY;
    delete tabDOMCache[tabName];

    requestAnimationFrame(() => {
        window.scrollTo(0, savedScrollY);
    });

    return true;
}

function invalidateTabCache(tabName) {
    delete tabDOMCache[tabName];
}

// ========================================
// Notifications
// ========================================

function showNotification(message, type = 'info') {
    const existing = document.querySelector('.notification');
    if (existing) existing.remove();

    const notification = document.createElement('div');
    notification.className = `notification notification-${type}`;
    notification.textContent = message;

    document.body.appendChild(notification);

    setTimeout(() => {
        notification.classList.add('fade-out');
        setTimeout(() => notification.remove(), 300);
    }, 3000);
}

// ========================================
// Hilfsfunktionen
// ========================================

function escapeHtml(text) {
    if (text === null || text === undefined) return '';
    const div = document.createElement('div');
    div.textContent = String(text);
    return div.innerHTML;
}

function escapeAttr(text) {
    if (text === null || text === undefined) return '';
    return String(text)
        .replace(/&/g, '&amp;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');
}

/**
 * HTML-Sanitizer: Nur Tags/Attribute aus WYSIWYG-Editor-Allowlist durchlassen.
 * Unerlaubte Tags werden unwrapped (Textinhalt bleibt), Attribute entfernt.
 */
function sanitizeHtml(html) {
    if (!html) return '';
    const ALLOWED_TAGS = new Set([
        'p', 'br', 'div', 'pre', 'h1', 'h2', 'h3',
        'b', 'strong', 'i', 'em', 'u',
        'ol', 'ul', 'li',
        'font',
    ]);
    const ALLOWED_ATTRS = {'font': new Set(['size'])};

    const doc = new DOMParser().parseFromString(html, 'text/html');

    function clean(parent) {
        for (const node of Array.from(parent.childNodes)) {
            if (node.nodeType === Node.TEXT_NODE) continue;
            if (node.nodeType !== Node.ELEMENT_NODE) { node.remove(); continue; }
            clean(node);
            const tag = node.tagName.toLowerCase();
            if (!ALLOWED_TAGS.has(tag)) {
                while (node.firstChild) parent.insertBefore(node.firstChild, node);
                node.remove();
            } else {
                const allowed = ALLOWED_ATTRS[tag];
                for (const a of Array.from(node.attributes)) {
                    if (!allowed || !allowed.has(a.name)) node.removeAttribute(a.name);
                }
            }
        }
    }

    clean(doc.body);
    return doc.body.innerHTML;
}

function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func.apply(this, args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

function formatPercent(value) {
    if (value === null || value === undefined) return '-';
    return Math.round(value) + '%';
}

// ========================================
// Mail-Benachrichtigungen Modal
// ========================================

async function openMailPreferencesModal() {
    toggleSettingsDropdown(false);

    const overlay = createModal({
        title: t('mail.prefs.title'),
        maxWidth: '500px',
        body: '<div class="table-loading"><div class="spinner"></div></div>',
        footer: `<button class="action-btn" onclick="closeModal()">${t('common.cancel')}</button>` +
                `<button class="action-btn primary" id="mailPrefsSaveBtn" disabled>${t('common.save')}</button>`,
    });

    // Prefs laden
    try {
        const resp = await fetch('/api/user/mail/preferences');
        if (!resp.ok) throw new Error(t('common.loadError'));
        const data = await resp.json();
        const prefs = data.preferences || [];

        const eventLabels = {
            'task_assigned': t('mail.event.task_assigned'),
            'status_change': t('mail.event.status_change'),
            'deadline_reached': t('mail.event.deadline_reached'),
            'deadline_warning': t('mail.event.deadline_warning'),
        };

        let html = '<div class="mail-prefs-list">';
        for (const pref of prefs) {
            const label = eventLabels[pref.event_type] || pref.event_type;
            const checked = pref.enabled ? 'checked' : '';

            html += `<div class="mail-pref-item">
                <label>
                    <input type="checkbox" data-event="${pref.event_type}" ${checked}>
                    ${escapeHtml(label)}
                </label>`;

            if (pref.event_type === 'deadline_warning') {
                html += `<div class="mail-pref-days">
                    <input type="number" id="mailPrefDays_${pref.event_type}" value="${pref.days_before}" min="1" max="30">
                    <span>${t('mail.prefs.daysBefore')}</span>
                </div>`;
            }

            html += '</div>';
        }
        html += '</div>';

        const modalBody = overlay.querySelector('.modal-body');
        modalBody.innerHTML = html;

        const saveBtn = document.getElementById('mailPrefsSaveBtn');
        saveBtn.disabled = false;

        saveBtn.addEventListener('click', async () => {
            const preferences = [];
            overlay.querySelectorAll('.mail-pref-item input[type="checkbox"]').forEach(cb => {
                const eventType = cb.dataset.event;
                const enabled = cb.checked;
                const daysInput = document.getElementById(`mailPrefDays_${eventType}`);
                const days_before = daysInput ? parseInt(daysInput.value) || 2 : 2;
                preferences.push({ event_type: eventType, enabled, days_before });
            });

            try {
                const resp = await fetch('/api/user/mail/preferences', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ preferences }),
                });

                if (!resp.ok) {
                    const data = await resp.json().catch(() => ({}));
                    throw new Error(data.detail || t('common.saveError'));
                }

                showNotification(t('mail.prefs.saved'), 'success');
                closeModal();
            } catch (error) {
                showNotification(error.message, 'error');
            }
        });
    } catch (error) {
        const modalBody = overlay.querySelector('.modal-body');
        modalBody.innerHTML = `<p style="color:var(--color-bearish)">${t('mail.prefs.loadError')}</p>`;
    }
}

// ============================================================================
// Modal-Hilfsfunktionen
// ============================================================================

/**
 * Erstellt ein Modal-Dialog mit Overlay, ESC-Handler und Click-Outside-Close.
 * @param {Object} config
 * @param {string} config.title - Modal-Titel
 * @param {string} config.body - HTML fuer den Modal-Body
 * @param {string} [config.footer] - HTML fuer den Footer (Buttons). Standard: Abbrechen-Button
 * @param {string} [config.maxWidth] - Max-Breite (z.B. '500px', '700px')
 * @param {string} [config.cssClass] - Zusaetzliche CSS-Klasse fuer modal-content (z.B. 'modal-wide')
 * @param {Function} [config.onOpen] - Callback nach dem Oeffnen (erhaelt overlay-Element)
 * @param {Function} [config.onClose] - Callback beim Schliessen
 * @returns {HTMLElement} Das Overlay-Element
 */
function createModal(config) {
    // Bestehendes Modal entfernen
    const existing = document.querySelector('.modal-overlay');
    if (existing) existing.remove();

    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay';

    const maxWidthStyle = config.maxWidth ? ` style="max-width:${config.maxWidth}"` : '';
    const cssClass = config.cssClass ? ` ${config.cssClass}` : '';
    const footer = config.footer !== undefined ? config.footer :
        `<button class="action-btn" onclick="closeModal()">${t('common.cancel')}</button>`;

    overlay.innerHTML = `
        <div class="modal-content${cssClass}"${maxWidthStyle}>
            <div class="modal-header">${config.title}</div>
            <div class="modal-body">${config.body}</div>
            ${footer ? `<div class="modal-footer">${footer}</div>` : ''}
        </div>
    `;

    // Click-Outside schliessen
    overlay.addEventListener('click', (e) => {
        if (e.target === overlay) closeModal();
    });

    document.body.appendChild(overlay);

    // ESC-Handler
    const escHandler = (e) => {
        if (e.key === 'Escape') closeModal();
    };
    document.addEventListener('keydown', escHandler);
    overlay._escHandler = escHandler;

    if (config.onClose) overlay._onClose = config.onClose;
    if (config.onOpen) config.onOpen(overlay);

    return overlay;
}

/**
 * Schliesst das aktuell offene Modal.
 */
function closeModal() {
    const overlay = document.querySelector('.modal-overlay');
    if (!overlay) return;
    if (overlay._escHandler) {
        document.removeEventListener('keydown', overlay._escHandler);
    }
    if (overlay._onClose) overlay._onClose();
    overlay.remove();
}

// ============================================================================
// Tab-Initialisierung Hilfsfunktion
// ============================================================================

/**
 * Generische Tab-Initialisierung: Spinner anzeigen, FilterBar steuern, laden + rendern.
 * @param {Object} config
 * @param {boolean} [config.showFilterBar=false] - FilterBar anzeigen?
 * @param {Function} [config.loadFn] - Async-Funktion zum Laden der Daten
 * @param {Function} config.renderFn - Funktion zum Rendern des Tabs
 */
async function initTabGeneric(config) {
    const container = document.getElementById('contentContainer');
    if (!container) return;

    container.innerHTML = '<div class="table-loading"><div class="spinner"></div></div>';

    const filterBar = document.getElementById('filterBar');
    if (filterBar) filterBar.style.display = config.showFilterBar ? 'flex' : 'none';

    if (config.loadFn) await config.loadFn();
    if (config.renderFn) config.renderFn();
}

// ============================================================================
// Config-CRUD Hilfsfunktionen
// ============================================================================

/**
 * Laedt eine Konfiguration vom Server.
 * @param {string} endpoint - API-Endpunkt (z.B. '/api/admin/ldap/config')
 * @returns {Object|null} Config-Objekt oder null bei Fehler
 */
async function loadConfigFromAPI(endpoint) {
    try {
        const resp = await fetch(endpoint);
        if (!resp.ok) throw new Error(t('common.loadError'));
        const data = await resp.json();
        return data.config !== undefined ? data.config : data;
    } catch (e) {
        return null;
    }
}

/**
 * Speichert eine Konfiguration auf dem Server (POST).
 * @param {Object} config
 * @param {string} config.endpoint - API-Endpunkt
 * @param {Object} config.data - Zu sendende Daten
 * @param {string} config.successMessage - Erfolgsmeldung
 * @param {Function} [config.onSuccess] - Callback nach Erfolg (z.B. reload + render)
 * @returns {boolean} true bei Erfolg, false bei Fehler
 */
async function saveConfigToAPI(config) {
    try {
        const resp = await fetch(config.endpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(config.data),
        });

        if (!resp.ok) {
            const errData = await resp.json().catch(() => ({}));
            throw new Error(errData.detail || t('common.saveError'));
        }

        showNotification(config.successMessage, 'success');
        if (config.onSuccess) await config.onSuccess();
        return true;
    } catch (error) {
        showNotification(error.message, 'error');
        return false;
    }
}

/**
 * Loescht eine Konfiguration auf dem Server (DELETE) mit Bestaetigungsdialog.
 * @param {Object} config
 * @param {string} config.endpoint - API-Endpunkt
 * @param {string} config.confirmMessage - Bestaetigungsfrage
 * @param {string} config.successMessage - Erfolgsmeldung
 * @param {Function} [config.onSuccess] - Callback nach Erfolg
 * @returns {boolean} true bei Erfolg, false bei Abbruch/Fehler
 */
async function deleteConfigFromAPI(config) {
    if (!await msgbox('cancel/yes', 'warning', config.confirmMessage)) return false;

    try {
        const resp = await fetch(config.endpoint, { method: 'DELETE' });
        if (!resp.ok) {
            const errData = await resp.json().catch(() => ({}));
            throw new Error(errData.detail || t('common.deleteError'));
        }

        showNotification(config.successMessage, 'success');
        if (config.onSuccess) await config.onSuccess();
        return true;
    } catch (error) {
        showNotification(error.message, 'error');
        return false;
    }
}

// ========================================
// MsgBox - Globaler Bestaetigungsdialog
// ========================================

/**
 * Zeigt einen modalen Dialog an.
 * @param {"ok"|"cancel/yes"} buttons - Button-Layout
 * @param {"alert"|"confirm"|"warning"} type - Styling-Variante
 * @param {string} message - Nachrichtentext
 * @returns {Promise<boolean>} true bei Ok/Ja, false bei Abbrechen
 */
async function msgbox(buttons, type, message) {
    return new Promise(resolve => {
        const overlay = document.createElement('div');
        overlay.className = 'modal-overlay';

        let buttonsHtml = '';
        if (buttons === 'ok') {
            buttonsHtml = `<button class="action-btn msgbox-btn-primary" data-action="confirm">${t('common.ok')}</button>`;
        } else {
            buttonsHtml = `
                <button class="action-btn" data-action="cancel">${t('common.cancel')}</button>
                <button class="action-btn msgbox-btn-primary" data-action="confirm">${t('common.yes')}</button>`;
        }

        overlay.innerHTML = `
            <div class="modal-content msgbox-dialog type-${type}">
                <div class="modal-body">
                    <p style="margin:0;font-size:14px">${escapeHtml(message)}</p>
                </div>
                <div class="modal-footer">
                    ${buttonsHtml}
                </div>
            </div>
        `;

        const close = (result) => {
            overlay.remove();
            document.removeEventListener('keydown', keyHandler);
            resolve(result);
        };

        const cancelBtn = overlay.querySelector('[data-action="cancel"]');
        const confirmBtn = overlay.querySelector('[data-action="confirm"]');

        if (cancelBtn) cancelBtn.onclick = () => close(false);
        confirmBtn.onclick = () => close(true);
        overlay.onclick = (e) => { if (e.target === overlay) close(false); };

        const keyHandler = (e) => {
            if (e.key === 'Escape') close(false);
            if (e.key === 'Enter') close(true);
        };
        document.addEventListener('keydown', keyHandler);

        document.body.appendChild(overlay);
        confirmBtn.focus();
    });
}
