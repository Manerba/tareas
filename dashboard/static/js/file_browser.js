/**
 * Tareas - Dateiablage (File Browser)
 * Baumansicht und Icon-Grid fuer Nextcloud-Dateien via WebDAV-Proxy.
 */

class FileBrowser {
    constructor(containerId, taskId, options = {}) {
        this.containerId = containerId;
        this.taskId = taskId;
        this.currentPath = '';
        this.viewMode = options.viewMode || 'tree'; // 'tree' oder 'grid'
        this.items = [];
        this.expandedDirs = new Set();
        this.treeCache = {}; // Pfad -> Items (Lazy-Load Cache)
        this._contextMenu = null;

        this.render();
        this.loadDirectory('');
    }

    getContainer() {
        return document.getElementById(this.containerId);
    }

    // ========================================
    // Rendering
    // ========================================

    render() {
        const container = this.getContainer();
        if (!container) return;

        container.innerHTML = `
            <div class="fb-wrapper">
                <div class="fb-header">
                    <div class="fb-breadcrumb" id="fb-breadcrumb-${this.taskId}"></div>
                    <div class="fb-actions">
                        <button class="fb-view-toggle" id="fb-toggle-${this.taskId}" title="${t('files.toggleView')}">
                            ${this.viewMode === 'tree' ? this._iconGrid() : this._iconTree()}
                        </button>
                        <button class="fb-action-btn" id="fb-upload-${this.taskId}" title="${t('files.upload')}">
                            ${this._iconUpload()} ${t('files.upload')}
                        </button>
                        <button class="fb-action-btn" id="fb-mkdir-${this.taskId}" title="${t('files.folder')}">
                            ${this._iconNewFolder()} ${t('files.folder')}
                        </button>
                    </div>
                </div>
                <div class="fb-content fb-drop-zone" id="fb-content-${this.taskId}">
                    <div class="fb-loading"><div class="spinner"></div></div>
                </div>
                <div class="fb-upload-progress" id="fb-progress-${this.taskId}" style="display:none">
                    <div class="fb-progress-bar"><div class="fb-progress-fill" id="fb-progress-fill-${this.taskId}"></div></div>
                    <span class="fb-progress-text" id="fb-progress-text-${this.taskId}"></span>
                </div>
                <input type="file" id="fb-file-input-${this.taskId}" multiple style="display:none">
            </div>
        `;

        // Event-Listener
        const toggle = document.getElementById(`fb-toggle-${this.taskId}`);
        toggle?.addEventListener('click', () => this.toggleView());

        const uploadBtn = document.getElementById(`fb-upload-${this.taskId}`);
        uploadBtn?.addEventListener('click', () => this.onUploadClick());

        const mkdirBtn = document.getElementById(`fb-mkdir-${this.taskId}`);
        mkdirBtn?.addEventListener('click', () => this.onMkdirClick());

        const fileInput = document.getElementById(`fb-file-input-${this.taskId}`);
        fileInput?.addEventListener('change', (e) => this.handleFileSelect(e));

        // Event-Delegation: Breadcrumb
        const bcContainer = document.getElementById(`fb-breadcrumb-${this.taskId}`);
        bcContainer?.addEventListener('click', (e) => {
            const link = e.target.closest('[data-path]');
            if (link) this.navigateTo(link.dataset.path);
        });

        // Event-Delegation: Content-Bereich (Grid + Tree)
        const content = document.getElementById(`fb-content-${this.taskId}`);

        // Grid: Doppelklick
        content?.addEventListener('dblclick', (e) => {
            const gridItem = e.target.closest('.fb-grid-item[data-name]');
            if (gridItem) {
                const name = gridItem.dataset.name;
                const type = gridItem.dataset.type;
                if (type === 'directory') {
                    this.navigateTo(this.currentPath ? this.currentPath + '/' + name : name);
                } else {
                    this.onItemDblClick(name, type);
                }
                return;
            }
            const treeRow = e.target.closest('.fb-tree-row[data-name]');
            if (treeRow) {
                this.onItemDblClick(treeRow.dataset.name, treeRow.dataset.type, treeRow.dataset.parent);
                return;
            }
        });

        // Grid + Tree: Kontextmenu
        content?.addEventListener('contextmenu', (e) => {
            const gridItem = e.target.closest('.fb-grid-item[data-name]');
            if (gridItem) {
                e.preventDefault();
                this.onContextMenu(e, gridItem.dataset.name, gridItem.dataset.type, gridItem.dataset.nclink || '');
                return;
            }
            const treeRow = e.target.closest('.fb-tree-row[data-name]');
            if (treeRow) {
                e.preventDefault();
                this.onContextMenu(e, treeRow.dataset.itempath, treeRow.dataset.type, treeRow.dataset.nclink || '');
                return;
            }
        });

        // Tree: Expand-Toggle (Click)
        content?.addEventListener('click', (e) => {
            const expandBtn = e.target.closest('.fb-tree-expand[data-toggle-path]');
            if (expandBtn) {
                e.stopPropagation();
                this.toggleTreeDir(expandBtn.dataset.togglePath);
                return;
            }
            // Grid: Edit-Button (OnlyOffice)
            const editBtn = e.target.closest('.fb-grid-edit[data-edit-path]');
            if (editBtn) {
                e.stopPropagation();
                if (typeof openOnlyOfficeEditor === 'function') {
                    openOnlyOfficeEditor(this.taskId, editBtn.dataset.editPath);
                }
                return;
            }
        });

        // Drag & Drop
        if (content) {
            content.addEventListener('dragover', (e) => {
                e.preventDefault();
                e.stopPropagation();
                content.classList.add('fb-drag-over');
            });
            content.addEventListener('dragleave', (e) => {
                e.preventDefault();
                e.stopPropagation();
                content.classList.remove('fb-drag-over');
            });
            content.addEventListener('drop', (e) => {
                e.preventDefault();
                e.stopPropagation();
                content.classList.remove('fb-drag-over');
                if (e.dataTransfer.files.length > 0) {
                    this.uploadFiles(e.dataTransfer.files);
                }
            });
        }

        // Context-Menu global schliessen
        document.addEventListener('click', () => this._closeContextMenu());
    }

    // ========================================
    // Daten laden
    // ========================================

    async loadDirectory(path) {
        const content = document.getElementById(`fb-content-${this.taskId}`);
        if (!content) return;

        if (this.viewMode === 'grid') {
            content.innerHTML = '<div class="fb-loading"><div class="spinner"></div></div>';
        }

        try {
            const resp = await fetch(`/api/tasks/${this.taskId}/files?path=${encodeURIComponent(path)}`);
            if (!resp.ok) {
                const err = await resp.json().catch(() => ({}));
                throw new Error(err.detail || t('common.loadError'));
            }
            const data = await resp.json();
            this.items = data.items || [];
            this.currentPath = path;

            // Cache fuer Baumansicht
            this.treeCache[path] = this.items;

            this.renderBreadcrumb();

            if (this.viewMode === 'tree') {
                this.renderTree();
            } else {
                this.renderGrid();
            }
        } catch (error) {
            content.innerHTML = `<div class="fb-error">${escapeHtml(error.message)}</div>`;
        }
    }

    // ========================================
    // Breadcrumb
    // ========================================

    renderBreadcrumb() {
        const bc = document.getElementById(`fb-breadcrumb-${this.taskId}`);
        if (!bc) return;

        const parts = this.currentPath ? this.currentPath.split('/').filter(Boolean) : [];
        let html = `<span class="fb-bc-item fb-bc-root" data-path="">${this._iconFolder()} ${t('files.title')}</span>`;

        let accumulated = '';
        parts.forEach((part, i) => {
            accumulated += (accumulated ? '/' : '') + part;
            const p = accumulated;
            html += `<span class="fb-bc-sep">/</span>`;
            html += `<span class="fb-bc-item" data-path="${escapeAttr(p)}">${escapeHtml(part)}</span>`;
        });

        bc.innerHTML = html;
    }

    navigateTo(path) {
        this.loadDirectory(path);
    }

    // ========================================
    // Grid-Ansicht
    // ========================================

    renderGrid() {
        const content = document.getElementById(`fb-content-${this.taskId}`);
        if (!content) return;

        if (this.items.length === 0) {
            content.innerHTML = `<div class="fb-empty">${t('files.empty')}</div>`;
            return;
        }

        let html = '<div class="fb-grid">';
        this.items.forEach(item => {
            const icon = this._getFileIcon(item);
            const sizeStr = item.type === 'directory' ? '' : this._formatSize(item.size);
            const isEditable = item.type === 'file' && typeof isOnlyOfficeEditable === 'function' && isOnlyOfficeEditable(item.name);
            const editIcon = isEditable
                ? `<div class="fb-grid-edit" data-edit-path="${escapeAttr(this.currentPath ? this.currentPath + '/' + item.name : item.name)}" title="${t('files.openInEditor')}">${this._iconEdit()}</div>`
                : '';
            html += `
                <div class="fb-grid-item ${item.type === 'directory' ? 'fb-grid-dir' : 'fb-grid-file'}"
                     data-name="${escapeAttr(item.name)}"
                     data-type="${escapeAttr(item.type)}"
                     data-nclink="${escapeAttr(item.nextcloud_link || '')}"
                     title="${escapeAttr(item.name)}${sizeStr ? ' (' + sizeStr + ')' : ''}">
                    ${editIcon}
                    <div class="fb-grid-icon">${icon}</div>
                    <div class="fb-grid-name">${escapeHtml(item.name)}</div>
                    ${sizeStr ? `<div class="fb-grid-size">${sizeStr}</div>` : ''}
                </div>`;
        });
        html += '</div>';
        content.innerHTML = html;
    }

    // ========================================
    // Baumansicht
    // ========================================

    renderTree() {
        const content = document.getElementById(`fb-content-${this.taskId}`);
        if (!content) return;

        const items = this.treeCache[this.currentPath] || this.items;
        if (items.length === 0) {
            content.innerHTML = `<div class="fb-empty">${t('files.empty')}</div>`;
            return;
        }

        let html = '<div class="fb-tree">';
        html += this._renderTreeLevel(items, this.currentPath, 0);
        html += '</div>';
        content.innerHTML = html;
    }

    _renderTreeLevel(items, parentPath, depth) {
        let html = '';
        items.forEach(item => {
            const itemPath = parentPath ? `${parentPath}/${item.name}` : item.name;
            const isExpanded = this.expandedDirs.has(itemPath);
            const indent = depth * 20;
            const sizeStr = item.type === 'directory' ? '' : this._formatSize(item.size);
            const icon = this._getFileIcon(item);
            const expandIcon = item.type === 'directory'
                ? `<span class="fb-tree-expand ${isExpanded ? 'expanded' : ''}" data-toggle-path="${escapeAttr(itemPath)}">${isExpanded ? '&#9660;' : '&#9654;'}</span>`
                : '<span class="fb-tree-expand-spacer"></span>';

            html += `
                <div class="fb-tree-row" style="padding-left:${indent}px"
                     data-name="${escapeAttr(item.name)}"
                     data-type="${escapeAttr(item.type)}"
                     data-parent="${escapeAttr(parentPath)}"
                     data-itempath="${escapeAttr(itemPath)}"
                     data-nclink="${escapeAttr(item.nextcloud_link || '')}">
                    ${expandIcon}
                    <span class="fb-tree-icon">${icon}</span>
                    <span class="fb-tree-name">${escapeHtml(item.name)}</span>
                    ${sizeStr ? `<span class="fb-tree-size">${sizeStr}</span>` : ''}
                    <span class="fb-tree-date">${item.last_modified ? this._formatDate(item.last_modified) : ''}</span>
                </div>`;

            // Unterverzeichnis-Inhalt (wenn aufgeklappt)
            if (item.type === 'directory' && isExpanded && this.treeCache[itemPath]) {
                html += this._renderTreeLevel(this.treeCache[itemPath], itemPath, depth + 1);
            }
        });
        return html;
    }

    async toggleTreeDir(path) {
        if (this.expandedDirs.has(path)) {
            this.expandedDirs.delete(path);
        } else {
            this.expandedDirs.add(path);
            // Lazy-Load
            if (!this.treeCache[path]) {
                try {
                    const resp = await fetch(`/api/tasks/${this.taskId}/files?path=${encodeURIComponent(path)}`);
                    if (resp.ok) {
                        const data = await resp.json();
                        this.treeCache[path] = data.items || [];
                    }
                } catch (e) { /* ignore */ }
            }
        }
        this.renderTree();
    }

    // ========================================
    // Ansicht wechseln
    // ========================================

    toggleView() {
        this.viewMode = this.viewMode === 'tree' ? 'grid' : 'tree';
        const toggle = document.getElementById(`fb-toggle-${this.taskId}`);
        if (toggle) {
            toggle.innerHTML = this.viewMode === 'tree' ? this._iconGrid() : this._iconTree();
            toggle.title = this.viewMode === 'tree' ? t('files.gridView') : t('files.treeView');
        }
        if (this.viewMode === 'tree') {
            this.treeCache[this.currentPath] = this.items;
            this.renderTree();
        } else {
            this.renderGrid();
        }
    }

    // ========================================
    // Interaktionen
    // ========================================

    onItemDblClick(name, type, parentPath) {
        if (type === 'directory') {
            const path = this.viewMode === 'tree' && parentPath !== undefined
                ? (parentPath ? `${parentPath}/${name}` : name)
                : (this.currentPath ? `${this.currentPath}/${name}` : name);
            this.loadDirectory(path);
        } else {
            // ONLYOFFICE Editor oder Download
            const filePath = this.viewMode === 'tree' && parentPath !== undefined
                ? (parentPath ? `${parentPath}/${name}` : name)
                : (this.currentPath ? `${this.currentPath}/${name}` : name);

            if (typeof isOnlyOfficeEditable === 'function' && isOnlyOfficeEditable(name)) {
                openOnlyOfficeEditor(this.taskId, filePath);
            } else if (this._isImageFile(name)) {
                this.openFileInline(filePath);
            } else {
                this.downloadFile(filePath);
            }
        }
    }

    onContextMenu(event, nameOrPath, type, nextcloudLink) {
        event.preventDefault();
        event.stopPropagation();
        this._closeContextMenu();

        // In der Grid-Ansicht: nameOrPath ist nur der Name, Pfad muss zusammengebaut werden
        const fullPath = this.viewMode === 'grid'
            ? (this.currentPath ? `${this.currentPath}/${nameOrPath}` : nameOrPath)
            : nameOrPath;
        const name = nameOrPath.includes('/') ? nameOrPath.split('/').pop() : nameOrPath;

        let html = '<div class="fb-ctx-menu">';
        if (type === 'file') {
            if (typeof isOnlyOfficeEditable === 'function' && isOnlyOfficeEditable(name)) {
                html += `<div class="fb-ctx-item" data-action="edit" data-path="${escapeAttr(fullPath)}">${t('files.openInEditor')}</div>`;
            }
            if (this._isImageFile(name)) {
                html += `<div class="fb-ctx-item" data-action="view" data-path="${escapeAttr(fullPath)}">${t('files.view')}</div>`;
            }
            html += `<div class="fb-ctx-item" data-action="download" data-path="${escapeAttr(fullPath)}">${t('files.download')}</div>`;
            if (nextcloudLink) {
                html += `<div class="fb-ctx-item" data-action="nextcloud" data-url="${escapeAttr(nextcloudLink)}">${t('files.openInNc')}</div>`;
            }
        } else {
            html += `<div class="fb-ctx-item" data-action="open" data-path="${escapeAttr(fullPath)}">${t('files.open')}</div>`;
        }
        html += `<div class="fb-ctx-sep"></div>`;
        html += `<div class="fb-ctx-item" data-action="rename" data-path="${escapeAttr(fullPath)}" data-name="${escapeAttr(name)}">${t('files.rename')}</div>`;
        html += `<div class="fb-ctx-item fb-ctx-danger" data-action="delete" data-path="${escapeAttr(fullPath)}" data-type="${escapeAttr(type)}">${t('files.delete')}</div>`;
        html += '</div>';

        const menu = document.createElement('div');
        menu.className = 'fb-ctx-overlay';
        menu.innerHTML = html;
        menu.style.position = 'fixed';
        menu.style.left = event.clientX + 'px';
        menu.style.top = event.clientY + 'px';
        menu.style.zIndex = '9999';

        // Event-Delegation fuer Context-Menu-Items
        menu.addEventListener('click', (e) => {
            const item = e.target.closest('[data-action]');
            if (!item) return;
            const action = item.dataset.action;
            const path = item.dataset.path;
            switch (action) {
                case 'edit':
                    if (typeof openOnlyOfficeEditor === 'function') openOnlyOfficeEditor(this.taskId, path);
                    break;
                case 'view':
                    this.openFileInline(path);
                    break;
                case 'download':
                    this.downloadFile(path);
                    break;
                case 'nextcloud':
                    window.open(item.dataset.url, '_blank');
                    break;
                case 'open':
                    this.loadDirectory(path);
                    break;
                case 'rename':
                    this.renameItem(path, item.dataset.name);
                    break;
                case 'delete':
                    this.deleteItem(path, item.dataset.type);
                    break;
            }
        });

        document.body.appendChild(menu);
        this._contextMenu = menu;

        // Sicherstellen dass das Menu im Viewport bleibt
        const rect = menu.querySelector('.fb-ctx-menu').getBoundingClientRect();
        if (rect.right > window.innerWidth) {
            menu.style.left = (event.clientX - rect.width) + 'px';
        }
        if (rect.bottom > window.innerHeight) {
            menu.style.top = (event.clientY - rect.height) + 'px';
        }
    }

    _closeContextMenu() {
        if (this._contextMenu) {
            this._contextMenu.remove();
            this._contextMenu = null;
        }
    }

    // ========================================
    // Dateioperationen
    // ========================================

    _isImageFile(fileName) {
        if (!fileName) return false;
        const ext = fileName.includes('.') ? fileName.split('.').pop().toLowerCase() : '';
        return ['jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp', 'svg', 'ico'].includes(ext);
    }

    openFileInline(path) {
        const url = `/api/tasks/${this.taskId}/files/download?path=${encodeURIComponent(path)}&inline=true`;
        window.open(url, '_blank');
    }

    downloadFile(path) {
        const url = `/api/tasks/${this.taskId}/files/download?path=${encodeURIComponent(path)}`;
        const a = document.createElement('a');
        a.href = url;
        a.download = '';
        document.body.appendChild(a);
        a.click();
        a.remove();
    }

    onUploadClick() {
        const input = document.getElementById(`fb-file-input-${this.taskId}`);
        if (input) input.click();
    }

    handleFileSelect(e) {
        const files = e.target.files;
        if (files.length > 0) {
            this.uploadFiles(files);
        }
        // Reset input
        e.target.value = '';
    }

    async uploadFiles(files) {
        const progressBar = document.getElementById(`fb-progress-${this.taskId}`);
        const progressFill = document.getElementById(`fb-progress-fill-${this.taskId}`);
        const progressText = document.getElementById(`fb-progress-text-${this.taskId}`);
        if (progressBar) progressBar.style.display = 'flex';

        const total = files.length;
        let done = 0;

        for (const file of files) {
            if (progressText) progressText.textContent = `${done + 1}/${total}: ${file.name}`;
            if (progressFill) progressFill.style.width = `${(done / total) * 100}%`;

            const formData = new FormData();
            formData.append('file', file);

            try {
                const resp = await fetch(
                    `/api/tasks/${this.taskId}/files/upload?path=${encodeURIComponent(this.currentPath)}`,
                    { method: 'POST', body: formData }
                );
                if (!resp.ok) {
                    const err = await resp.json().catch(() => ({}));
                    throw new Error(err.detail || t('files.uploadFailed'));
                }
                done++;
                if (progressFill) progressFill.style.width = `${(done / total) * 100}%`;
            } catch (error) {
                showNotification(t('files.uploadError', { name: file.name, error: error.message }), 'error');
            }
        }

        if (progressBar) {
            setTimeout(() => { progressBar.style.display = 'none'; }, 1000);
        }

        if (done > 0) {
            showNotification(t('files.uploaded', { count: done }), 'success');
            this.loadDirectory(this.currentPath);
        }
    }

    async onMkdirClick() {
        const name = prompt(t('files.newFolderPrompt'));
        if (!name || !name.trim()) return;

        try {
            const resp = await fetch(
                `/api/tasks/${this.taskId}/files/mkdir?path=${encodeURIComponent(this.currentPath)}`,
                {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name: name.trim() }),
                }
            );
            if (!resp.ok) {
                const err = await resp.json().catch(() => ({}));
                throw new Error(err.detail || t('common.error'));
            }
            showNotification(t('files.folderCreated', { name: name.trim() }), 'success');
            this.loadDirectory(this.currentPath);
        } catch (error) {
            showNotification(error.message, 'error');
        }
    }

    async deleteItem(path, type) {
        this._closeContextMenu();
        const typeLabel = type === 'directory' ? t('files.folderWithContent') : t('files.file');
        const name = path.split('/').pop();
        if (!await msgbox('cancel/yes', 'warning', t('files.deleteConfirm', { type: typeLabel, name: name }))) return;

        try {
            const resp = await fetch(
                `/api/tasks/${this.taskId}/files?path=${encodeURIComponent(path)}`,
                { method: 'DELETE' }
            );
            if (!resp.ok) {
                const err = await resp.json().catch(() => ({}));
                throw new Error(err.detail || t('common.error'));
            }
            showNotification(t('files.deleted'), 'success');
            this.loadDirectory(this.currentPath);
        } catch (error) {
            showNotification(error.message, 'error');
        }
    }

    async renameItem(path, oldName) {
        this._closeContextMenu();
        const newName = prompt(t('files.renamePrompt'), oldName);
        if (!newName || !newName.trim() || newName.trim() === oldName) return;

        // Ziel-Pfad berechnen: gleicher Ordner, neuer Name
        const parentPath = path.includes('/') ? path.substring(0, path.lastIndexOf('/')) : '';
        const dest = parentPath ? `${parentPath}/${newName.trim()}` : newName.trim();

        try {
            const resp = await fetch(`/api/tasks/${this.taskId}/files/move`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ source: path, destination: dest }),
            });
            if (!resp.ok) {
                const err = await resp.json().catch(() => ({}));
                throw new Error(err.detail || t('common.error'));
            }
            showNotification(t('files.renamed'), 'success');
            this.loadDirectory(this.currentPath);
        } catch (error) {
            showNotification(error.message, 'error');
        }
    }

    // ========================================
    // Datei-Icons (SVG-basiert)
    // ========================================

    _getFileIcon(item) {
        if (item.type === 'directory') return this._iconFolder();

        const name = item.name.toLowerCase();
        const ext = name.includes('.') ? name.split('.').pop() : '';
        const mime = (item.mime_type || '').toLowerCase();

        // Bilder
        if (['jpg', 'jpeg', 'png', 'gif', 'svg', 'webp', 'bmp', 'ico'].includes(ext) || mime.startsWith('image/')) {
            return this._iconImage();
        }
        // PDF
        if (ext === 'pdf' || mime === 'application/pdf') {
            return this._iconPdf();
        }
        // Office: Word
        if (['doc', 'docx', 'odt', 'rtf'].includes(ext)) {
            return this._iconWord();
        }
        // Office: Excel
        if (['xls', 'xlsx', 'ods', 'csv'].includes(ext)) {
            return this._iconExcel();
        }
        // Office: PowerPoint
        if (['ppt', 'pptx', 'odp'].includes(ext)) {
            return this._iconPresentation();
        }
        // Text/Code
        if (['txt', 'md', 'py', 'js', 'ts', 'html', 'css', 'json', 'xml', 'yaml', 'yml', 'sh', 'bat', 'log', 'ini', 'cfg', 'conf'].includes(ext) || mime.startsWith('text/')) {
            return this._iconText();
        }
        // Archive
        if (['zip', 'tar', 'gz', 'bz2', 'xz', '7z', 'rar'].includes(ext)) {
            return this._iconArchive();
        }
        // Video
        if (['mp4', 'avi', 'mkv', 'mov', 'wmv', 'flv', 'webm'].includes(ext) || mime.startsWith('video/')) {
            return this._iconVideo();
        }
        // Audio
        if (['mp3', 'wav', 'flac', 'ogg', 'aac', 'wma', 'm4a'].includes(ext) || mime.startsWith('audio/')) {
            return this._iconAudio();
        }

        return this._iconFile();
    }

    // SVG Icons (kompakte Inline-SVGs)
    _iconFolder() {
        return '<svg viewBox="0 0 24 24" class="fb-icon fb-icon-folder"><path d="M10 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2h-8l-2-2z" fill="currentColor"/></svg>';
    }
    _iconFile() {
        return '<svg viewBox="0 0 24 24" class="fb-icon fb-icon-file"><path d="M14 2H6c-1.1 0-2 .9-2 2v16c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V8l-6-6zm-1 7V3.5L18.5 9H13z" fill="currentColor"/></svg>';
    }
    _iconImage() {
        return '<svg viewBox="0 0 24 24" class="fb-icon fb-icon-image"><path d="M21 19V5c0-1.1-.9-2-2-2H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2zM8.5 13.5l2.5 3.01L14.5 12l4.5 6H5l3.5-4.5z" fill="currentColor"/></svg>';
    }
    _iconPdf() {
        return '<svg viewBox="0 0 24 24" class="fb-icon fb-icon-pdf"><path d="M14 2H6c-1.1 0-2 .9-2 2v16c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V8l-6-6zm-1 7V3.5L18.5 9H13z" fill="currentColor"/><text x="12" y="17" text-anchor="middle" font-size="6" font-weight="bold" fill="white">PDF</text></svg>';
    }
    _iconWord() {
        return '<svg viewBox="0 0 24 24" class="fb-icon fb-icon-word"><path d="M14 2H6c-1.1 0-2 .9-2 2v16c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V8l-6-6zm-1 7V3.5L18.5 9H13z" fill="currentColor"/><text x="12" y="17" text-anchor="middle" font-size="5" font-weight="bold" fill="white">DOC</text></svg>';
    }
    _iconExcel() {
        return '<svg viewBox="0 0 24 24" class="fb-icon fb-icon-excel"><path d="M14 2H6c-1.1 0-2 .9-2 2v16c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V8l-6-6zm-1 7V3.5L18.5 9H13z" fill="currentColor"/><text x="12" y="17" text-anchor="middle" font-size="5" font-weight="bold" fill="white">XLS</text></svg>';
    }
    _iconPresentation() {
        return '<svg viewBox="0 0 24 24" class="fb-icon fb-icon-ppt"><path d="M14 2H6c-1.1 0-2 .9-2 2v16c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V8l-6-6zm-1 7V3.5L18.5 9H13z" fill="currentColor"/><text x="12" y="17" text-anchor="middle" font-size="5" font-weight="bold" fill="white">PPT</text></svg>';
    }
    _iconText() {
        return '<svg viewBox="0 0 24 24" class="fb-icon fb-icon-text"><path d="M14 2H6c-1.1 0-2 .9-2 2v16c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V8l-6-6zm-1 7V3.5L18.5 9H13z" fill="currentColor"/><path d="M7 13h10v1H7zm0 3h7v1H7z" fill="white"/></svg>';
    }
    _iconArchive() {
        return '<svg viewBox="0 0 24 24" class="fb-icon fb-icon-archive"><path d="M14 2H6c-1.1 0-2 .9-2 2v16c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V8l-6-6zm-1 7V3.5L18.5 9H13z" fill="currentColor"/><text x="12" y="17" text-anchor="middle" font-size="5" font-weight="bold" fill="white">ZIP</text></svg>';
    }
    _iconVideo() {
        return '<svg viewBox="0 0 24 24" class="fb-icon fb-icon-video"><path d="M18 4l2 4h-3l-2-4h-2l2 4h-3l-2-4H8l2 4H7L5 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V4h-4z" fill="currentColor"/></svg>';
    }
    _iconAudio() {
        return '<svg viewBox="0 0 24 24" class="fb-icon fb-icon-audio"><path d="M12 3v10.55c-.59-.34-1.27-.55-2-.55-2.21 0-4 1.79-4 4s1.79 4 4 4 4-1.79 4-4V7h4V3h-6z" fill="currentColor"/></svg>';
    }
    _iconUpload() {
        return '<svg viewBox="0 0 24 24" class="fb-icon-btn"><path d="M9 16h6v-6h4l-7-7-7 7h4v6zm-4 2h14v2H5v-2z" fill="currentColor"/></svg>';
    }
    _iconNewFolder() {
        return '<svg viewBox="0 0 24 24" class="fb-icon-btn"><path d="M20 6h-8l-2-2H4c-1.11 0-1.99.89-1.99 2L2 18c0 1.11.89 2 2 2h16c1.11 0 2-.89 2-2V8c0-1.11-.89-2-2-2zm-1 8h-3v3h-2v-3h-3v-2h3V9h2v3h3v2z" fill="currentColor"/></svg>';
    }
    _iconTree() {
        return '<svg viewBox="0 0 24 24" class="fb-icon-btn"><path d="M3 3h6v2H3zm8 0h10v2H11zM3 7h6v2H3zm8 0h10v2H11zM3 11h6v2H3zm8 0h10v2H11zM3 15h6v2H3zm8 0h10v2H11zM3 19h6v2H3zm8 0h10v2H11z" fill="currentColor"/></svg>';
    }
    _iconGrid() {
        return '<svg viewBox="0 0 24 24" class="fb-icon-btn"><path d="M3 3h8v8H3zm10 0h8v8h-8zM3 13h8v8H3zm10 0h8v8h-8z" fill="currentColor"/></svg>';
    }
    _iconEdit() {
        return '<svg viewBox="0 0 24 24" class="fb-icon-edit"><path d="M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04c.39-.39.39-1.02 0-1.41l-2.34-2.34c-.39-.39-1.02-.39-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z" fill="currentColor"/></svg>';
    }

    // ========================================
    // Hilfsfunktionen
    // ========================================

    _formatSize(bytes) {
        if (!bytes || bytes === 0) return '';
        const units = ['B', 'KB', 'MB', 'GB'];
        let i = 0;
        let size = bytes;
        while (size >= 1024 && i < units.length - 1) {
            size /= 1024;
            i++;
        }
        return `${size.toFixed(i > 0 ? 1 : 0)} ${units[i]}`;
    }

    _formatDate(dateStr) {
        if (!dateStr) return '';
        try {
            const d = new Date(dateStr);
            return d.toLocaleDateString('de-DE', { day: '2-digit', month: '2-digit', year: 'numeric' });
        } catch {
            return dateStr;
        }
    }

    destroy() {
        this._closeContextMenu();
    }
}
