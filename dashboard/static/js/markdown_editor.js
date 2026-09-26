/** Markdown is stored as source text. HTML is produced only for display. */
const markdownParser = new marked.Marked({
    gfm: true,
    breaks: true,
    renderer: {
        // HTML examples remain text; never enable HTML from Markdown source.
        html({ text }) { return escapeHtml(text); },
        checkbox({ checked }) { return checked ? '☑ ' : '☐ '; },
    },
});

const legacyMarkdownConverter = new TurndownService({
    headingStyle: 'atx', codeBlockStyle: 'fenced', bulletListMarker: '-',
});
// Old WYSIWYG paragraphs sometimes already contain Markdown syntax.
legacyMarkdownConverter.escape = text => text;

function markdownSource(value, format = 'markdown') {
    const source = String(value || '');
    if (format !== 'legacy') return source;
    let hasLegacyHtml = false;
    marked.walkTokens(marked.lexer(source), token => {
        if (token.type === 'html' && /<\/?(?:p|div|br|h[1-6]|b|strong|i|em|u|ul|ol|li|font|pre|span)(?:\s|\/?\>)/i.test(token.text)) {
            hasLegacyHtml = true;
        }
    });
    return hasLegacyHtml ? legacyMarkdownConverter.turndown(sanitizeHtml(source)) : source;
}

function renderMarkdown(value, format = 'markdown') {
    return sanitizeHtml(markdownParser.parse(markdownSource(value, format)));
}

class MarkdownEditor {
    // Keep unfinished edits when expandable rows are rebuilt or tabs change.
    static drafts = new Map();

    constructor(containerId, value = '', options = {}) {
        this.containerId = containerId;
        this.container = document.getElementById(containerId);
        if (!this.container) return;
        this.options = options;
        this.source = markdownSource(value, options.format);
        this.editing = !options.readOnly && MarkdownEditor.drafts.has(containerId);
        this.saving = false;
        this.render();
    }

    render() {
        this.container.classList.add('markdown-editor');
        this.container.innerHTML = `
            <div class="markdown-toolbar">
                <span class="markdown-label">${escapeHtml(this.options.label || t('detail.description'))}</span>
                ${this.options.readOnly ? '' : `
                    <button type="button" class="control-btn" data-md-action="edit">${t('markdown.edit')}</button>
                    <button type="button" class="control-btn primary" data-md-action="save" hidden>${t('common.save')}</button>
                    <button type="button" class="control-btn" data-md-action="cancel" hidden>${t('common.cancel')}</button>
                `}
            </div>
            <div class="markdown-body" data-md-preview></div>
            <textarea class="markdown-source" aria-label="${escapeAttr(t('markdown.source'))}" spellcheck="false" hidden></textarea>
            <p class="markdown-hint" hidden>${t('markdown.hint')}</p>
            <p class="markdown-error" role="alert" hidden></p>`;
        this.preview = this.container.querySelector('[data-md-preview]');
        this.input = this.container.querySelector('.markdown-source');
        this.error = this.container.querySelector('.markdown-error');
        this.input.value = MarkdownEditor.drafts.get(this.containerId) ?? this.source;
        this.input.addEventListener('input', () => {
            MarkdownEditor.drafts.set(this.containerId, this.input.value);
        });
        this.container.querySelector('[data-md-action="edit"]')?.addEventListener('click', () => {
            this.editing = true;
            this.updateView();
            this.input.focus();
        });
        this.container.querySelector('[data-md-action="cancel"]')?.addEventListener('click', () => {
            MarkdownEditor.drafts.delete(this.containerId);
            this.input.value = this.source;
            this.editing = false;
            this.error.hidden = true;
            this.updateView();
        });
        this.container.querySelector('[data-md-action="save"]')?.addEventListener('click', () => this.save());
        this.updateView();
    }

    updateView() {
        this.preview.innerHTML = renderMarkdown(this.source) || `<em>${t('markdown.empty')}</em>`;
        this.preview.hidden = this.editing;
        this.input.hidden = !this.editing;
        this.container.querySelector('.markdown-hint').hidden = !this.editing;
        for (const button of this.container.querySelectorAll('[data-md-action]')) {
            button.hidden = button.dataset.mdAction === 'edit' ? this.editing : !this.editing;
            button.disabled = this.saving;
        }
        this.input.disabled = this.saving;
    }

    async save() {
        if (this.saving || this.options.readOnly) return;
        const source = this.input.value;
        this.saving = true;
        this.error.hidden = true;
        this.updateView();
        try {
            await this.options.onSave(source);
            this.source = source;
            if (MarkdownEditor.drafts.get(this.containerId) === source) {
                MarkdownEditor.drafts.delete(this.containerId);
            }
            this.editing = false;
        } catch (error) {
            MarkdownEditor.drafts.set(this.containerId, source);
            this.error.textContent = t('common.saveError');
            this.error.hidden = false;
        } finally {
            this.saving = false;
            this.updateView();
            if (!this.editing) this.container.querySelector('[data-md-action="edit"]')?.focus();
        }
    }
}
