/** Standalone editor startup, loaded externally so strict CSP can execute it. */
// Farbschema aus localStorage uebernehmen
const schemes = { light: 'light', dark: 'dark', graphit: 'light', slate: 'light', ink: 'dark' };
let savedScheme = localStorage.getItem('dashboard-color-scheme');
if (!schemes[savedScheme]) {
    savedScheme = localStorage.getItem('dashboard-theme') === 'dark' || localStorage.getItem('theme') === 'dark'
        ? 'dark'
        : 'light';
}
document.documentElement.setAttribute('data-color-scheme', savedScheme);
document.documentElement.setAttribute('data-theme', schemes[savedScheme]);

function escapeHtml(str) {
    if (!str) return '';
    return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

async function initEditor() {
    const params = new URLSearchParams(window.location.search);
    const taskId = params.get('taskId');
    const filePath = params.get('path');

    if (!taskId || !filePath) {
        document.getElementById('ooEditorBody').innerHTML =
            `<div class="oo-loading" style="color:var(--color-bearish)">${escapeHtml(t('editor.missingParams'))}</div>`;
        return;
    }

    const fileName = filePath.includes('/') ? filePath.split('/').pop() : filePath;
    document.getElementById('editorTitle').textContent = fileName;
    document.title = `${fileName} - Tareas`;

    try {
        const resp = await fetch(`/api/tasks/${taskId}/files/edit?path=${encodeURIComponent(filePath)}`);
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            throw new Error(err.detail || t('editor.configError'));
        }

        const data = await resp.json();

        // Badge
        const badgeContainer = document.getElementById('oo-badge-container');
        if (badgeContainer) {
            badgeContainer.innerHTML = data.can_edit
                ? `<span class="oo-badge oo-badge-edit">${t('editor.edit')}</span>`
                : `<span class="oo-badge oo-badge-readonly">${t('editor.readonly')}</span>`;
        }

        // ONLYOFFICE JS API laden
        await new Promise((resolve, reject) => {
            const script = document.createElement('script');
            script.src = `${data.editor_url}/web-apps/apps/api/documents/api.js`;
            script.onload = resolve;
            script.onerror = () => reject(new Error(t('editor.apiError')));
            document.head.appendChild(script);
        });

        // Editor-Container
        const body = document.getElementById('ooEditorBody');
        body.innerHTML = '<div id="ooEditorContainer" style="width:100%;height:100%;"></div>';

        // Config
        const config = data.editor_config;
        config.token = data.token;
        config.width = '100%';
        config.height = '100%';
        config.events = {
            onError: (event) => {
                console.error('ONLYOFFICE Fehler:', event?.data?.errorCode);
            },
        };

        new DocsAPI.DocEditor('ooEditorContainer', config);

    } catch (error) {
        document.getElementById('ooEditorBody').innerHTML =
            `<div class="oo-loading" style="color:var(--color-bearish)">${escapeHtml(error.message)}</div>`;
    }
}

// i18n aktualisiert data-i18n zuerst, danach Dateiname und Editor setzen.
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initEditor);
} else {
    initEditor();
}
