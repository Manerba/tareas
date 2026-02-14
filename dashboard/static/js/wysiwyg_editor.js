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
            <select data-cmd="formatBlock" title="Formatvorlage">
                <option value="">Format</option>
                <option value="p">Standard</option>
                <option value="pre">Code</option>
                <option value="h1">Ueberschrift 1</option>
                <option value="h2">Ueberschrift 2</option>
                <option value="h3">Ueberschrift 3</option>
            </select>
            <span class="toolbar-sep"></span>
            <button data-cmd="bold" title="Fett"><b>F</b></button>
            <button data-cmd="underline" title="Unterstrichen"><u>U</u></button>
            <button data-cmd="italic" title="Kursiv"><i>K</i></button>
            <span class="toolbar-sep"></span>
            <button data-cmd="insertOrderedList" title="Nummerierte Liste">1.</button>
            <button data-cmd="insertUnorderedList" title="Aufzaehlung">&bull;</button>
            <span class="toolbar-sep"></span>
            <select data-cmd="fontSize" title="Schriftgroesse">
                <option value="">Groesse</option>
                <option value="1">Klein</option>
                <option value="3">Normal</option>
                <option value="5">Gross</option>
                <option value="7">Sehr Gross</option>
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
