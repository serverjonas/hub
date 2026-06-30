/* ────────────────────────────────────────────────────────────────
 *  serverjonas i18n
 *  Loads <page>_<lang>.json + base_<lang>.json and swaps DOM text.
 *  Public API: window.t(key, vars), window.i18n.setLanguage(lang)
 *
 *  Translations are cached in localStorage and verified against the
 *  server-side version manifest (``/i18n/manifest.json``). A file is
 *  only refetched when its hash differs from the cached one, so the
 *  vast majority of page loads use cached data after the first one.
 * ──────────────────────────────────────────────────────────────── */
(function () {
    const STORAGE_KEY = "serverjonas_lang";
    const DEFAULT_LANG = "deu";

    // localStorage keys take the shape `i18n/<name>/version` and
    // `i18n/<name>/data` — matching the layout in the spec.
    const CACHE_PREFIX = "i18n";
    const MANIFEST_URL = "/i18n/manifest.json";

    // Code (ISO 639-2) → {htmlLang, label, nativeLabel, flag}.
    // flag is shown in the dropdown, label is used for screen-reader i18n.
    const SUPPORTED = [
        { code: "deu", htmlLang: "de", label: "German",       native: "Deutsch",       flag: "🇩🇪" },
        { code: "eng", htmlLang: "en", label: "English",      native: "English",       flag: "🇬🇧" },
        { code: "spa", htmlLang: "es", label: "Spanish",      native: "Español",       flag: "🇪🇸" },
        { code: "fra", htmlLang: "fr", label: "French",       native: "Français",      flag: "🇫🇷" },
        { code: "ita", htmlLang: "it", label: "Italian",      native: "Italiano",      flag: "🇮🇹" },
        { code: "nld", htmlLang: "nl", label: "Dutch",        native: "Nederlands",    flag: "🇳🇱" },
        { code: "por", htmlLang: "pt", label: "Portuguese",   native: "Português",     flag: "🇵🇹" },
        { code: "pol", htmlLang: "pl", label: "Polish",       native: "Polski",        flag: "🇵🇱" },
        { code: "rus", htmlLang: "ru", label: "Russian",      native: "Русский",       flag: "🇷🇺" },
    ];
    const SUPPORTED_CODES = SUPPORTED.map((l) => l.code);
    const LANG_MAP = Object.fromEntries(SUPPORTED.map((l) => [l.code, l.htmlLang]));

    let currentLang = DEFAULT_LANG;
    let translations = {};
    let fallback = {}; // German/English strings, used when a key is missing in target lang
    let inflightToken = 0; // guards against concurrent setLanguage calls resolving out of order

    // Manifest is fetched once per page load and shared across files.
    let manifestCache = null;
    let manifestPromise = null;

    /* ─── Public helpers (exported early) ─── */
    window.i18nLanguages = SUPPORTED;
    window.i18nSupported = SUPPORTED_CODES;
    window.i18nNative = (code) => {
        const entry = SUPPORTED.find((l) => l.code === code);
        return entry ? entry.native : code;
    };

    /* ─── Language detection ─── */
    function detectLanguage() {
        try {
            const url = new URL(window.location.href);
            const param = url.searchParams.get("lang");
            if (param && SUPPORTED_CODES.includes(param)) {
                localStorage.setItem(STORAGE_KEY, param);
                return param;
            }
        } catch (_) {}
        const stored = localStorage.getItem(STORAGE_KEY);
        if (stored && SUPPORTED_CODES.includes(stored)) return stored;
        return DEFAULT_LANG;
    }

    /* ─── Manifest loading ───
     * Returns a `{filename: version}` object. Failures fall back to {}
     * so the cache check below degrades gracefully to "always fetch".
     * The promise is memoised so concurrent loaders share the request. */
    async function loadManifest() {
        if (manifestCache) return manifestCache;
        if (manifestPromise) return manifestPromise;
        manifestPromise = (async () => {
            try {
                const res = await fetch(MANIFEST_URL, { cache: "no-cache" });
                if (!res.ok) return {};
                const data = await res.json();
                manifestCache = (data && typeof data === "object") ? data : {};
                return manifestCache;
            } catch (e) {
                console.warn("[i18n] manifest fetch failed", e);
                return {};
            } finally {
                manifestPromise = null;
            }
        })();
        return manifestPromise;
    }

    /* ─── localStorage cache helpers ─── */
    function cacheKeys(name) {
        return [
            CACHE_PREFIX + "/" + name + "/version",
            CACHE_PREFIX + "/" + name + "/data",
        ];
    }

    function getCachedVersion(name) {
        try {
            return localStorage.getItem(cacheKeys(name)[0]);
        } catch (_) {
            return null;
        }
    }

    function getCachedData(name) {
        try {
            const raw = localStorage.getItem(cacheKeys(name)[1]);
            if (raw === null) return null;
            return JSON.parse(raw);
        } catch (_) {
            return null;
        }
    }

    function saveToCache(name, version, data) {
        try {
            // Store data first; if storage is full and the data write fails
            // the version write is skipped so we never end up with a version
            // pointing at a missing/incomplete payload.
            localStorage.setItem(cacheKeys(name)[1], JSON.stringify(data));
            localStorage.setItem(cacheKeys(name)[0], version);
        } catch (_) {
            // localStorage full / disabled / quota exceeded — silently
            // degrade to network-only; the next load will retry.
        }
    }

    /* ─── File loading ─── */
    async function fetchJSON(name) {
        try {
            // We intentionally drop the legacy `cache: "no-store"` (a
            // leftover from the pre-manifest era). The client-side
            // version check in fetchWithCache() decides whether we refetch
            // at all; when we DO fetch, default browser caching is fine
            // and would let Flask serve a 304 if ETags are enabled.
            const res = await fetch("/static/" + name);
            if (!res.ok) return {};
            return await res.json();
        } catch (e) {
            console.warn("[i18n] could not load", name, e);
            return {};
        }
    }

    // Cache-aware variant: returns the parsed JSON for ``name``, using the
    // cached payload when the version reported by the manifest matches what
    // we previously stored. Falls back to a network fetch otherwise.
    async function fetchWithCache(name) {
        const manifest = await loadManifest();
        const version = manifest && Object.prototype.hasOwnProperty.call(manifest, name)
            ? manifest[name]
            : null;
        const cachedVersion = getCachedVersion(name);

        if (version && cachedVersion === version) {
            const cached = getCachedData(name);
            if (cached && typeof cached === "object") return cached;
        }

        const fresh = await fetchJSON(name);
        if (version && fresh && typeof fresh === "object") {
            saveToCache(name, version, fresh);
        }
        return fresh || {};
    }

    // Fallback chain (last wins): base_deu, page_deu, base_eng, page_<lang>, base_<lang>
    // → German never breaks anything; English fills missing keys; target language overrides last.
    async function loadTranslations(lang) {
        const page = (document.body && document.body.dataset.page) || "base";
        const needPage = page && page !== "base";
        const requests = [
            fetchWithCache("base_deu.json"),
            needPage ? fetchWithCache(page + "_deu.json") : Promise.resolve({}),
            fetchWithCache("base_eng.json"),
            needPage ? fetchWithCache(page + "_eng.json") : Promise.resolve({}),
        ];
        // Always try base_<lang>; only request page_<lang> when it likely exists.
        requests.push(fetchWithCache("base_" + lang + ".json"));
        if (needPage) requests.push(fetchWithCache(page + "_" + lang + ".json"));

        // querySelectorAll on the way: need to know which page we're on
        const results = await Promise.all(requests);

        const [_baseDef, pageDef, _baseEng, pageEng, baseLang, pageLang] = results;
        const fallbackChain = Object.assign({}, _baseDef, pageDef, _baseEng, pageEng);
        return Object.assign({}, fallbackChain, baseLang || {}, pageLang || {});
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

    function parseVars(el) {
        const raw = el.getAttribute("data-i18n-vars");
        if (!raw) return null;
        try {
            const parsed = JSON.parse(raw);
            if (parsed && typeof parsed === "object") return parsed;
        } catch (_) { /* don't break the page on malformed JSON */ }
        return null;
    }

    function applyOne(el, attr, kind, key, t) {
        const raw = t[key];
        if (raw === undefined || raw === null) return;
        const value = interpolate(raw, parseVars(el));
        if (kind === "text") el.textContent = value;
        else if (kind === "html") el.innerHTML = value;
        else el.setAttribute(attr.split(":")[1], value);
        el.setAttribute("data-i18n-applied", "1");
    }

    function applyTranslations(t) {
        const htmlLang = LANG_MAP[currentLang] || LANG_MAP[DEFAULT_LANG];
        document.documentElement.setAttribute("lang", htmlLang);
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
        document.body.setAttribute("data-lang-code", currentLang);
        document.dispatchEvent(
            new CustomEvent("i18n:applied", { detail: { lang: currentLang } })
        );
    }

    /* ─── Public API ─── */
    async function setLanguage(lang, opts) {
        opts = opts || {};
        if (!SUPPORTED_CODES.includes(lang)) return;
        const token = ++inflightToken;
        if (opts.persist !== false) {
            try { localStorage.setItem(STORAGE_KEY, lang); } catch (_) {}
        }
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
        supported: SUPPORTED_CODES,
        languages: SUPPORTED,
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
