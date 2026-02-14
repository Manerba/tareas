/**
 * Tareas - i18n (Internationalisierung)
 * Leichtgewichtiges Custom-i18n ohne externes Framework.
 *
 * Sprachen: DE (Standard), EN, ES, FR, RO, UK, RU
 * Persistenz: localStorage (Key: 'tareas-lang')
 * Sprachwechsel: Page Reload
 *
 * API:
 *   t(key)                         -> uebersetzter String
 *   t(key, { name: 'Foo' })        -> mit Interpolation {name}
 *   getLang()                       -> aktueller Sprachcode
 *   setLang(code)                   -> Sprache wechseln (Reload)
 *   getAvailableLangs()             -> [{code, label}]
 */

(function() {
    'use strict';

    const STORAGE_KEY = 'tareas-lang';
    const DEFAULT_LANG = 'de';
    const AVAILABLE_LANGS = [
        { code: 'de', label: 'Deutsch' },
        { code: 'en', label: 'English' },
        { code: 'es', label: 'Español' },
        { code: 'fr', label: 'Français' },
        { code: 'ro', label: 'Română' },
        { code: 'uk', label: 'Українська' },
        { code: 'ru', label: 'Русский' },
    ];

    // Aktuelle Sprache ermitteln
    const raw = localStorage.getItem(STORAGE_KEY);
    const currentLang = AVAILABLE_LANGS.some(l => l.code === raw) ? raw : DEFAULT_LANG;

    // Strings: DE inline (kein FOUC), andere per synchronem XHR
    let strings = null;

    function loadStrings() {
        if (currentLang === DEFAULT_LANG) {
            // DE-Strings synchron per XHR laden (kein async noetig, Datei ist klein)
            const xhr = new XMLHttpRequest();
            xhr.open('GET', '/static/i18n/de.json', false);
            xhr.send();
            if (xhr.status === 200) {
                strings = JSON.parse(xhr.responseText);
            } else {
                console.error('i18n: Konnte de.json nicht laden');
                strings = {};
            }
        } else {
            // Zielsprache laden
            const xhr = new XMLHttpRequest();
            xhr.open('GET', `/static/i18n/${currentLang}.json`, false);
            xhr.send();
            if (xhr.status === 200) {
                strings = JSON.parse(xhr.responseText);
            } else {
                console.warn(`i18n: Konnte ${currentLang}.json nicht laden, Fallback auf DE`);
                strings = {};
            }

            // DE als Fallback laden
            const xhrDe = new XMLHttpRequest();
            xhrDe.open('GET', '/static/i18n/de.json', false);
            xhrDe.send();
            if (xhrDe.status === 200) {
                const deStrings = JSON.parse(xhrDe.responseText);
                // Fehlende Keys aus DE ergaenzen
                for (const key in deStrings) {
                    if (!(key in strings)) {
                        strings[key] = deStrings[key];
                    }
                }
            }
        }
    }

    loadStrings();

    /**
     * Uebersetzt einen Key mit optionaler Interpolation.
     * @param {string} key - Translation Key (z.B. 'common.save')
     * @param {Object} [params] - Interpolationsparameter (z.B. { name: 'Test' })
     * @returns {string} Uebersetzter String
     */
    function t(key, params) {
        let str = strings[key];
        if (str === undefined) {
            if (typeof console !== 'undefined') {
                console.warn(`i18n: Missing key "${key}"`);
            }
            return key;
        }
        if (params) {
            for (const [k, v] of Object.entries(params)) {
                str = str.replace(new RegExp('\\{' + k + '\\}', 'g'), v);
            }
        }
        return str;
    }

    function getLang() {
        return currentLang;
    }

    function setLang(code) {
        if (!AVAILABLE_LANGS.some(l => l.code === code)) return;
        localStorage.setItem(STORAGE_KEY, code);
        location.reload();
    }

    function getAvailableLangs() {
        return AVAILABLE_LANGS;
    }

    /**
     * Auto-Patch: Ersetzt Textinhalt von Elementen mit data-i18n Attribut.
     * Wird nach DOMContentLoaded aufgerufen.
     */
    function applyDataI18n() {
        document.querySelectorAll('[data-i18n]').forEach(el => {
            const key = el.getAttribute('data-i18n');
            const translated = t(key);
            if (translated !== key) {
                el.textContent = translated;
            }
        });
        document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
            const key = el.getAttribute('data-i18n-placeholder');
            const translated = t(key);
            if (translated !== key) {
                el.placeholder = translated;
            }
        });
        document.querySelectorAll('[data-i18n-title]').forEach(el => {
            const key = el.getAttribute('data-i18n-title');
            const translated = t(key);
            if (translated !== key) {
                el.title = translated;
            }
        });
    }

    // Auto-Patch beim Laden
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', applyDataI18n);
    } else {
        applyDataI18n();
    }

    // html lang Attribut setzen
    document.documentElement.lang = currentLang;

    // Globale API
    window.t = t;
    window.getLang = getLang;
    window.setLang = setLang;
    window.getAvailableLangs = getAvailableLangs;
})();
