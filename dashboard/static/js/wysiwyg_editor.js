/**
 * Leichtgewichtiger WYSIWYG-Editor
 * Basiert auf contentEditable + document.execCommand()
 *
 * Verwendung:
 *   const editor = new WysiwygEditor(containerId, initialHTML);
 *   const html = editor.getContent();
 */

class WysiwygEditor {
    constructor(containerId, initialHTML = '') {
        this.containerId = containerId;
        this.container = document.getElementById(containerId);
        if (!this.container) return;

        this.render(initialHTML);
    }

    render(initialHTML) {
        this.container.innerHTML = '';
        this.container.className = 'wysiwyg-container';

        // Toolbar
        const toolbar = document.createElement('div');
        toolbar.className = 'wysiwyg-toolbar';
        toolbar.innerHTML = this.buildToolbar();
        this.container.appendChild(toolbar);

        // Content Area
        this.content = document.createElement('div');
        this.content.className = 'wysiwyg-content';
        this.content.contentEditable = 'true';
        this.content.innerHTML = sanitizeHtml(initialHTML) || '<p><br></p>';
        this.container.appendChild(this.content);

        // Event-Listener fuer Toolbar-Buttons
        toolbar.addEventListener('click', (e) => {
            const btn = e.target.closest('[data-cmd]');
            if (btn) {
                e.preventDefault();
                this.execCommand(btn.dataset.cmd, btn.dataset.value || null);
                this.content.focus();
            }
        });

        // Select-Change fuer formatBlock und fontSize
        toolbar.addEventListener('change', (e) => {
            if (e.target.dataset.cmd) {
                e.preventDefault();
                this.execCommand(e.target.dataset.cmd, e.target.value);
                e.target.value = e.target.options[0].value;
                this.content.focus();
            }
        });
    }

    buildToolbar() {
        return `
            <select data-cmd="formatBlock" title="${t('wysiwyg.formatBlock')}">
                <option value="">${t('wysiwyg.format')}</option>
                <option value="p">${t('wysiwyg.normal')}</option>
                <option value="pre">${t('wysiwyg.code')}</option>
                <option value="h1">${t('wysiwyg.heading1')}</option>
                <option value="h2">${t('wysiwyg.heading2')}</option>
                <option value="h3">${t('wysiwyg.heading3')}</option>
            </select>
            <span class="toolbar-sep"></span>
            <button data-cmd="bold" title="${t('wysiwyg.bold')}"><b>F</b></button>
            <button data-cmd="underline" title="${t('wysiwyg.underline')}"><u>U</u></button>
            <button data-cmd="italic" title="${t('wysiwyg.italic')}"><i>K</i></button>
            <span class="toolbar-sep"></span>
            <button data-cmd="insertOrderedList" title="${t('wysiwyg.orderedList')}">1.</button>
            <button data-cmd="insertUnorderedList" title="${t('wysiwyg.unorderedList')}">&bull;</button>
            <span class="toolbar-sep"></span>
            <select data-cmd="fontSize" title="${t('wysiwyg.fontSize')}">
                <option value="">${t('wysiwyg.size')}</option>
                <option value="1">${t('wysiwyg.small')}</option>
                <option value="3">${t('wysiwyg.normalSize')}</option>
                <option value="5">${t('wysiwyg.large')}</option>
                <option value="7">${t('wysiwyg.xlarge')}</option>
            </select>
        `;
    }

    execCommand(cmd, value) {
        if (cmd === 'formatBlock' && value) {
            document.execCommand('formatBlock', false, '<' + value + '>');
        } else if (cmd === 'fontSize' && value) {
            document.execCommand('fontSize', false, value);
        } else {
            document.execCommand(cmd, false, value);
        }
    }

    getContent() {
        if (!this.content) return '';
        return this.content.innerHTML;
    }

    setContent(html) {
        if (this.content) {
            this.content.innerHTML = sanitizeHtml(html) || '<p><br></p>';
        }
    }
}
