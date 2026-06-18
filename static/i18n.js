/* ────────────────────────────────────────────────────────────────
 *  serverjonas i18n
 *  Loads <page>_<lang>.json + base_<lang>.json and swaps DOM text.
 *  Public API: window.t(key, vars), window.i18n.setLanguage(lang)
 * ──────────────────────────────────────────────────────────────── */
(function () {
    const STORAGE_KEY = "serverjonas_lang";
    const DEFAULT_LANG = "deu";
    const SUPPORTED_LANGS = ["deu", "eng"];
    const LANG_MAP = { deu: "de", eng: "en" };

    let currentLang = DEFAULT_LANG;
    let translations = {};
    let fallback = {}; // German strings, used when a key is missing in target lang
    let inflightToken = 0; // guards against concurrent setLanguage calls resolving out of order

    /* ─── Language detection ─── */
    function detectLanguage() {
        try {
            const url = new URL(window.location.href);
            const param = url.searchParams.get("lang");
            if (param && SUPPORTED_LANGS.includes(param)) {
                localStorage.setItem(STORAGE_KEY, param);
                return param;
            }
        } catch (_) {}
        const stored = localStorage.getItem(STORAGE_KEY);
        if (stored && SUPPORTED_LANGS.includes(stored)) return stored;
        return DEFAULT_LANG;
    }

    /* ─── File loading ─── */
    async function fetchJSON(name) {
        try {
            const res = await fetch("/static/" + name, { cache: "no-store" });
            if (!res.ok) return {};
            return await res.json();
        } catch (e) {
            console.warn("[i18n] could not load", name, e);
            return {};
        }
    }

    async function loadTranslations(lang) {
        const page = (document.body && document.body.dataset.page) || "base";
        const needPage = page && page !== "base";
        const [baseDef, pageDef, baseLang, pageLang] = await Promise.all([
            fetchJSON("base_deu.json"),
            needPage ? fetchJSON(page + "_deu.json") : Promise.resolve({}),
            fetchJSON("base_" + lang + ".json"),
            needPage ? fetchJSON(page + "_" + lang + ".json") : Promise.resolve({}),
        ]);
        fallback = Object.assign({}, baseDef, pageDef);
        // Target language wins; German fills in missing keys.
        return Object.assign({}, fallback, baseLang, pageLang);
    }

    /* ─── Variable interpolation ─── */
    function interpolate(template, vars) {
        if (!vars) return template;
        return String(template).replace(/\{(\w+)\}/g, function (m, k) {
            return vars[k] !== undefined ? String(vars[k]) : m;
        });
    }

    /* ─── DOM walking ─── */
    const ATTRS = [
        ["data-i18n", "text"],
        ["data-i18n-html", "html"],
        ["data-i18n-placeholder", "attr:placeholder"],
        ["data-i18n-title", "attr:title"],
        ["data-i18n-aria", "attr:aria-label"],
        ["data-i18n-value", "attr:value"],
    ];

    function applyOne(el, attr, kind, key, t) {
        const value = t[key];
        if (value === undefined || value === null) return;
        if (kind === "text") el.textContent = value;
        else if (kind === "html") el.innerHTML = value;
        else el.setAttribute(attr.split(":")[1], value);
        el.setAttribute("data-i18n-applied", "1");
    }

    function applyTranslations(t) {
        document.documentElement.setAttribute(
            "lang",
            LANG_MAP[currentLang] || LANG_MAP[DEFAULT_LANG]
        );
        ATTRS.forEach(function ([attr, kind]) {
            document.querySelectorAll("[" + attr + "]").forEach(function (el) {
                applyOne(el, attr, kind, el.getAttribute(attr), t);
            });
        });
        // Language switcher active state
        document.querySelectorAll("[data-lang]").forEach(function (el) {
            el.classList.toggle("i18n-active", el.getAttribute("data-lang") === currentLang);
        });
        document.body.setAttribute("data-i18n-ready", "1");
        document.dispatchEvent(
            new CustomEvent("i18n:applied", { detail: { lang: currentLang } })
        );
    }

    /* ─── Public API ─── */
    async function setLanguage(lang, opts) {
        opts = opts || {};
        if (!SUPPORTED_LANGS.includes(lang)) return;
        const token = ++inflightToken;
        if (opts.persist !== false) localStorage.setItem(STORAGE_KEY, lang);
        const newTranslations = await loadTranslations(lang);
        // Discard late responses so the most recent click wins.
        if (token !== inflightToken) return;
        currentLang = lang;
        translations = newTranslations;
        applyTranslations(translations);
        window.__currentLang = lang;
        return translations;
    }

    function t(key, vars) {
        const value = translations[key];
        const source = value !== undefined ? value : fallback[key];
        // Missing key: surface as a clearly-formatted placeholder so typos are obvious in production.
        return interpolate(source !== undefined ? source : "[i18n: " + key + "]", vars);
    }

    window.t = t;
    window.i18n = {
        setLanguage: function (lang) { return setLanguage(lang); },
        getLanguage: function () { return currentLang; },
        supported: SUPPORTED_LANGS,
        reapply: function () { applyTranslations(translations); },
    };

    /* ─── Boot ─── */
    function boot() {
        const lang = detectLanguage();
        setLanguage(lang, { persist: false });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", boot);
    } else {
        boot();
    }
})();
