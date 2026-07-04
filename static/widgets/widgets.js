/* ─────────────────────────────────────────────────────────────
 *  Widget engine
 *
 *  Responsibilities:
 *   1. Fetch the manifest from `/static/widgets/manifest.json`.
 *   2. Render the active layout (read from localStorage or the
 *      default layout baked in here) into a container element.
 *   3. For each widget slot: fetch the HTML fragment from
 *      `/static/widgets/<type>.html`, inject it, then call into
 *      the in-file behavior registry keyed by `type`.
 *   4. Persist layout changes to `localStorage` under the key
 *      `serverjonas_hub_layout` (versioned, JSON).
 *   5. Run destructive re-renders by tearing down previous
 *      instances (so a clock's setInterval doesn't leak).
 *
 *  Public API:
 *    HubWidgets.init(containerEl, options)
 *    HubWidgets.getLayout()
 *    HubWidgets.setLayout(layout)
 *    HubWidgets.addItem(type)
 *    HubWidgets.removeItem(id)
 *    HubWidgets.moveItem(id, delta)  // +1 = down, -1 = up
 *    HubWidgets.setItemSize(id, size)
 *    HubWidgets.setItemConfig(id, partial)
 *    HubWidgets.resetDefaults()
 *
 *  Conventions:
 *    – Each widget fragment uses data-wgt-role attributes for
 *      parts the behavior / editor will read/write.
 *    – Each behavior exports an init(container, config) function.
 *      Optional: destroy(container) for cleanup (intervals etc.).
 * ───────────────────────────────────────────────────────────── */

(function () {
    const STORAGE_KEY = "serverjonas_hub_layout";
    const MANIFEST_URL = "/static/widgets/manifest.json";
    const FRAGMENT_BASE = "/static/widgets/";

    // Layout schema — bump on breaking changes.
    const SCHEMA_VERSION = 1;
    const SIZES = ["small", "medium", "large"];
    const ALLOWED_DEFAULT_AUTHS = ["anonymous", "loggedin", "either"];

    const instancesBySlot = new WeakMap();   // slotEl → { destroy? }
    let manifestCache = null;

    /* ─── Manifest ─── */
    async function loadManifest() {
        if (manifestCache) return manifestCache;
        try {
            const res = await fetch(MANIFEST_URL, { cache: "no-cache" });
            if (!res.ok) throw new Error("manifest " + res.status);
            manifestCache = await res.json();
            return manifestCache;
        } catch (err) {
            console.warn("[hub-widgets] manifest fetch failed; falling back to empty", err);
            manifestCache = { version: SCHEMA_VERSION, widgets: [] };
            return manifestCache;
        }
    }

    /* ─── Defaults ─── */
    // Anon & loggedin have slightly different starter layouts. Keeping them
    // INSIDE the JS (instead of in the manifest) means the customize page
    // can always tell users "Reset to defaults" without us needing to ship a
    // second JSON file.
    function defaultItems(auth) {
        if (auth === "loggedin") {
            return [
                { id: rid(), type: "user-badge",  size: "medium", order: 0, config: {} },
                { id: rid(), type: "clock",       size: "medium", order: 1, config: {} },
                { id: rid(), type: "welcome",     size: "large",  order: 2, config: {
                    title: "Willkommen zurück!",
                    subtitle: "Schön, dass du wieder da bist."
                } },
                { id: rid(), type: "route-button", size: "small", order: 3, config: {
                    url: "/memes", label: "Memes", icon: "image", tint: "memes",
                    description: "Kuratierte Internet-Kultur."
                } },
                { id: rid(), type: "route-button", size: "small", order: 4, config: {
                    url: "/films", label: "Media", icon: "clapperboard", tint: "media",
                    description: "Filme und Serien streamen."
                } },
                { id: rid(), type: "route-button", size: "small", order: 5, config: {
                    url: "/friends", label: "Freunde", icon: "users", tint: "friends",
                    description: "Bleib mit der Community in Kontakt."
                } },
                { id: rid(), type: "route-button", size: "small", order: 6, config: {
                    url: "/chat", label: "Chat", icon: "message-circle", tint: "chat",
                    description: "Direktnachrichten in Echtzeit."
                } },
            ];
        }
        return [
            { id: rid(), type: "clock",       size: "medium", order: 0, config: {} },
            { id: rid(), type: "welcome",     size: "large",  order: 1, config: {
                title: "Willkommen bei serverjonas!",
                subtitle: "Dein Ort für Memes, Filme, Serien und echte Freundschaften."
            } },
            { id: rid(), type: "route-button", size: "small", order: 2, config: {
                url: "/login", label: "Einloggen", icon: "log-in", tint: "default",
                description: "Weiter mit deinem Konto."
            } },
            { id: rid(), type: "route-button", size: "small", order: 3, config: {
                url: "/register", label: "Registrieren", icon: "user-plus", tint: "default",
                description: "Neu dabei? Erstell dir einen Account."
            } },
        ];
    }

    function rid() {
        return "w" + Math.random().toString(36).slice(2, 10);
    }

    /* ─── Storage ─── */
    function readLayout() {
        try {
            const raw = localStorage.getItem(STORAGE_KEY);
            if (!raw) return null;
            const parsed = JSON.parse(raw);
            if (!parsed || parsed.version !== SCHEMA_VERSION) return null;
            if (!Array.isArray(parsed.items)) return null;
            return parsed;
        } catch (_) {
            return null;
        }
    }
    function writeLayout(layout) {
        try {
            localStorage.setItem(STORAGE_KEY, JSON.stringify(layout));
        } catch (_) {
            // Storage full / disabled. We still keep an in-memory mirror.
        }
        cache.layout = layout;
        document.dispatchEvent(new CustomEvent("hub-widgets:change", { detail: layout }));
    }
    const cache = { layout: null, auth: "anonymous" };

    /* ─── Fragment loader ─── */
    async function fetchFragment(type) {
        try {
            const res = await fetch(FRAGMENT_BASE + type + ".html", { cache: "no-cache" });
            if (!res.ok) throw new Error("fragment " + type + " " + res.status);
            return await res.text();
        } catch (err) {
            console.warn("[hub-widgets] fragment load failed", type, err);
            return (
                '<div class="wgt-card wgt-warn" style="padding:14px 16px;text-align:left;">' +
                    '<strong>' + escapeHtml(type) + '</strong> konnte nicht geladen werden.' +
                '</div>'
            );
        }
    }

    /* ─── Behavior registry ─── */
    const behaviors = {};
    function register(type, behavior) {
        behaviors[type] = behavior;
    }

    function escapeHtml(s) {
        return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
            return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c];
        });
    }
    function escapeAttr(s) {
        return escapeHtml(s);
    }
    function setText(el, value) {
        if (el) el.textContent = (value == null || value === "") ? "" : String(value);
    }
    function findRole(root, role) {
        return root.querySelector("[data-wgt-role=\"" + role + "\"]");
    }
    function applyRoleMap(container, config, roleMap) {
        Object.keys(roleMap).forEach(function (key) {
            const roleEls = Array.prototype.slice.call(
                container.querySelectorAll("[data-wgt-role=\"" + roleMap[key] + "\"]")
            );
            if (!roleEls.length) return;
            const value = config[key];
            const fn = behaviors.__readers && behaviors.__readers[roleMap[key]];
            if (typeof fn === "function") fn(roleEls, value, config);
            else roleEls.forEach(function (el) { setText(el, value); });
        });
    }

    /* ─── Behavior: clock ─── */
    register("clock", {
        init(container, config) {
            const opts = Object.assign({
                show_seconds: true, show_date: true, show_greeting: true, format: "24h"
            }, config || {});

            const greetingEl = findRole(container, "greeting");
            const timeEl     = findRole(container, "time");
            const dateEl     = findRole(container, "date");

            // Pick the greeting string via i18n if available, else fallback
            function greetingFor(d) {
                const h = d.getHours();
                let key = "hub.widget.greeting.day";
                if (h < 5)  key = "hub.widget.greeting.night";
                else if (h < 12) key = "hub.widget.greeting.morning";
                else if (h < 18) key = "hub.widget.greeting.day";
                else key = "hub.widget.greeting.evening";
                const fallback = {
                    "hub.widget.greeting.morning": "Guten Morgen",
                    "hub.widget.greeting.day":     "Hallo",
                    "hub.widget.greeting.evening": "Guten Abend",
                    "hub.widget.greeting.night":   "Gute Nacht"
                };
                return (window.t && window.t(key)) || fallback[key];
            }

            function tick() {
                const d = new Date();
                let h = d.getHours();
                let m = d.getMinutes();
                let s = d.getSeconds();

                let time;
                if (opts.format === "12h") {
                    const ampm = h >= 12 ? "PM" : "AM";
                    h = h % 12 || 12;
                    const hh = String(h).padStart(2, "0");
                    const mm = String(m).padStart(2, "0");
                    const ss = String(s).padStart(2, "0");
                    time = hh + ":" + mm + (opts.show_seconds ? ":" + ss : "") + " " + ampm;
                } else {
                    const hh = String(h).padStart(2, "0");
                    const mm = String(m).padStart(2, "0");
                    const ss = String(s).padStart(2, "0");
                    time = hh + ":" + mm + (opts.show_seconds ? ":" + ss : "");
                }
                setText(timeEl, time);
                if (opts.show_date) {
                    setText(dateEl, d.toLocaleDateString(undefined, {
                        weekday: "long", day: "numeric", month: "long"
                    }));
                } else if (dateEl) {
                    dateEl.textContent = "";
                }
                if (opts.show_greeting) {
                    setText(greetingEl, greetingFor(d) + ",");
                } else if (greetingEl) {
                    greetingEl.textContent = "";
                }
            }
            tick();
            const interval = setInterval(tick, 1000);
            return {
                destroy() { clearInterval(interval); }
            };
        }
    });

    /* ─── Behavior: route-button ─── */
    register("route-button", {
        init(container, config) {
            const opts = Object.assign({
                url: "/", label: "Button", description: "", icon: "link", tint: "default"
            }, config || {});

            const link    = findRole(container, "link");
            const icon    = findRole(container, "icon");
            const labelEl = findRole(container, "label");
            const descEl  = findRole(container, "desc");

            if (link) link.setAttribute("href", sanitizeUrl(opts.url));
            if (link) link.setAttribute("data-tint", opts.tint);
            setText(labelEl, opts.label);
            setText(descEl, opts.description);
            if (icon) icon.setAttribute("data-lucide", opts.icon);
        }
    });

    // Reject anything that isn't a same-origin path or an absolute http(s)
    // URL so a customizer config can't smuggle in `javascript:` schemes.
    // Returns the safe URL, falling back to "/" if the input is bad.
    function sanitizeUrl(value) {
        if (typeof value !== "string") return "/";
        const v = value.trim();
        if (!v) return "/";
        if (v.charAt(0) === "/") return v;
        if (/^https?:\/\//i.test(v)) return v;
        return "/";
    }

    /* ─── Behavior: welcome ─── */
    register("welcome", {
        init(container, config) {
            const opts = Object.assign({
                title: "Willkommen!", subtitle: ""
            }, config || {});
            setText(findRole(container, "title"), opts.title);
            setText(findRole(container, "subtitle"), opts.subtitle);
        }
    });

    /* ─── Behavior: postit ─── */
    register("postit", {
        init(container, config) {
            const opts = Object.assign({ title: "Notiz", body: "" }, config || {});
            setText(findRole(container, "title"), opts.title);
            setText(findRole(container, "body"), opts.body);
        }
    });

    /* ─── Behavior: user-badge ─── */
    register("user-badge", {
        init(container, config) {
            const opts = Object.assign({ show_settings_link: true }, config || {});

            // Template sets data-user on the grid wrapper; fall back to
            // body.dataset.user for safety on pages where the grid isn't
            // yet mounted (e.g. the customize page live preview during
            // initial load).
            const grid = document.getElementById("hubWidgetGrid");
            const userName = (
                (grid && grid.dataset.user) ||
                document.body.dataset.user ||
                ""
            ).trim();
            const settingsLink = findRole(container, "settings-link");

            setText(findRole(container, "user-name"), userName || "Gast");
            const greeting = (function () {
                const h = new Date().getHours();
                const key = h < 12 ? "hub.widget.greeting.morning"
                            : h < 18 ? "hub.widget.greeting.day"
                            : h < 22 ? "hub.widget.greeting.evening"
                                     : "hub.widget.greeting.night";
                const fallback = {
                    "hub.widget.greeting.morning": "Guten Morgen",
                    "hub.widget.greeting.day":     "Hallo",
                    "hub.widget.greeting.evening": "Guten Abend",
                    "hub.widget.greeting.night":   "Gute Nacht"
                };
                return (window.t && window.t(key)) || fallback[key];
            })();
            setText(findRole(container, "user-greeting"), greeting + "!");

            if (settingsLink) {
                settingsLink.style.display = opts.show_settings_link ? "" : "none";
            }
        }
    });

    /* ─── Item helpers ─── */
    function findWidgetMeta(manifest, type) {
        return manifest.widgets.find(function (w) { return w.id === type; });
    }

    function mergeDefaults(item, meta) {
        const out = {
            id: item.id || rid(),
            type: item.type,
            size: SIZES.includes(item.size) ? item.size : (meta.default_size || "medium"),
            order: typeof item.order === "number" ? item.order : 0,
            config: Object.assign({}, meta.default_config || {}, item.config || {})
        };
        return out;
    }

    function reindexOrder(items) {
        items.sort(function (a, b) { return a.order - b.order; });
        items.forEach(function (it, i) { it.order = i; });
    }

    /* ─── Render ─── */
    let currentContainer = null;

    function clearContainer(container) {
        // Tear down existing instances so intervals etc. don't leak.
        Array.prototype.forEach.call(container.querySelectorAll(".wgt-slot"), function (slot) {
            const inst = instancesBySlot.get(slot);
            if (inst && typeof inst.destroy === "function") {
                try { inst.destroy(); } catch (_) {}
            }
            instancesBySlot.delete(slot);
        });
        container.innerHTML = "";
    }

    async function renderOne(slotEl, item, manifest, ctx) {
        const meta = findWidgetMeta(manifest, item.type);
        if (!meta) {
            slotEl.innerHTML =
                '<div class="wgt-warn" style="padding:14px 16px;">' +
                    'Unbekanntes Widget: ' + escapeHtml(item.type) +
                '</div>';
            return;
        }

        const html = await fetchFragment(item.type);
        slotEl.innerHTML = html;

        const behavior = behaviors[item.type];
        let inst = null;
        if (behavior && typeof behavior.init === "function") {
            try {
                inst = behavior.init(slotEl, mergeDefaults(item, meta).config) || null;
            } catch (err) {
                console.warn("[hub-widgets] init failed", item.type, err);
            }
        }
        if (inst) instancesBySlot.set(slotEl, inst);

        // Re-render Lucide icons for the new subtree (locally, without
        // touching the rest of the document).
        try {
            if (window.lucide && typeof window.lucide.createIcons === "function") {
                window.lucide.createIcons({
                    attrs: { "stroke-width": 2.5 },
                    nameAttr: "data-lucide",
                    elements: Array.prototype.slice.call(slotEl.querySelectorAll("[data-lucide]"))
                });
            }
        } catch (_) {}
    }

    function ensureLayout(manifest) {
        // Always read from localStorage FIRST so an externally-edited
        // payload (or a write that happened while window.HubWidgets was
        // unloaded) takes precedence over the cached in-memory copy.
        const stored = readLayout();
        if (stored && stored.items && stored.items.length) {
            cache.layout = stored;
            return stored;
        }
        const layout = { version: SCHEMA_VERSION, items: defaultItems(cache.auth) };
        reindexOrder(layout.items);
        writeLayout(layout);
        return layout;
    }

    /* ─── Render all ─── */
    async function render(container) {
        if (!container) return;
        clearContainer(container);
        const manifest = await loadManifest();
        const layout = ensureLayout(manifest);

        // Apply each item in order. Items with an unknown type get
        // skipped silently rather than throwing.
        const sortedItems = layout.items.slice().sort(function (a, b) {
            return a.order - b.order;
        });

        // Empty-state placeholder
        if (!sortedItems.length) {
            const empty = document.createElement("div");
            empty.className = "hub-widget-empty";
            empty.innerHTML = (
                '<div class="em-icon"><i data-lucide="layout-grid"></i></div>' +
                '<h3 data-i18n="hub.customize.empty_title">Dein Hub ist leer</h3>' +
                '<p data-i18n="hub.customize.empty_body">Füge dein erstes Widget hinzu, um zu beginnen.</p>' +
                '<a href="/hub/customize" class="btn btn-primary">' +
                    '<i data-lucide="plus"></i>' +
                    '<span data-i18n="hub.customize.empty_cta">Widget hinzufügen</span>' +
                '</a>'
            );
            container.appendChild(empty);
            try {
                if (window.lucide && window.lucide.createIcons) {
                    window.lucide.createIcons({
                        attrs: { "stroke-width": 2.5 },
                        elements: Array.prototype.slice.call(empty.querySelectorAll("[data-lucide]"))
                    });
                }
            } catch (_) {}
            return;
        }

        // Render fragments sequentially so a stray fetch error doesn't
        // race ahead; widgets are cheap so the latency is invisible.
        for (let i = 0; i < sortedItems.length; i++) {
            const item = sortedItems[i];
            const meta = findWidgetMeta(manifest, item.type);
            if (!meta) continue;
            const slot = document.createElement("div");
            slot.className = "wgt-slot";
            const size = SIZES.includes(item.size) ? item.size : (meta.default_size || "medium");
            slot.setAttribute("data-size", size);
            slot.setAttribute("data-type", item.type);
            slot.setAttribute("data-id", item.id);
            container.appendChild(slot);
            await renderOne(slot, item, manifest, {});
        }
    }

    /* ─── Public API ─── */
    function init(container, options) {
        options = options || {};
        cache.auth = options.auth || "anonymous";
        currentContainer = container;
        // Hydrate from localStorage BEFORE the first render so a layout
        // the user saved on another tab is picked up without a roundtrip.
        cache.layout = readLayout();
        render(container);

        // Re-render when i18n applies (locale change ticks date formats etc.)
        document.addEventListener("i18n:applied", function onI18n() {
            if (currentContainer) render(currentContainer);
        });

        // Re-render when the theme changes (Lucide icons need to redraw to
        // inherit the new stroke colours). The data-theme swap itself is
        // handled by CSS variables; the observer just makes sure icons
        // pick up the right stroke colour via lucide.
        const themeObserver = new MutationObserver(function () {
            if (!currentContainer) return;
            try {
                if (window.lucide && window.lucide.createIcons) {
                    window.lucide.createIcons({
                        attrs: { "stroke-width": 2.5 },
                        elements: Array.prototype.slice.call(
                            currentContainer.querySelectorAll("[data-lucide]")
                        )
                    });
                }
            } catch (_) {}
        });
        themeObserver.observe(document.documentElement, {
            attributes: true, attributeFilter: ["data-theme"]
        });
    }

    let layoutHandlers = {};

    function getLayout() {
        return cache.layout || ensureLayout({ widgets: [] });
    }

    function setLayout(nextLayout) {
        if (!nextLayout || !Array.isArray(nextLayout.items)) return;
        const safe = {
            version: SCHEMA_VERSION,
            items: nextLayout.items.map(function (it) {
                return {
                    id: it.id || rid(),
                    type: String(it.type || ""),
                    size: SIZES.includes(it.size) ? it.size : "medium",
                    order: typeof it.order === "number" ? it.order : 0,
                    config: it.config && typeof it.config === "object" ? it.config : {}
                };
            }),
        };
        reindexOrder(safe.items);
        writeLayout(safe);
        if (currentContainer) render(currentContainer);
    }

    function addItem(type) {
        const layout = getLayout();
        const maxOrder = layout.items.reduce(function (m, it) {
            return Math.max(m, it.order);
        }, -1);
        layout.items.push({
            id: rid(), type: type, size: "medium", order: maxOrder + 1, config: {}
        });
        setLayout(layout);
    }

    function removeItem(id) {
        const layout = getLayout();
        layout.items = layout.items.filter(function (it) { return it.id !== id; });
        reindexOrder(layout.items);
        setLayout(layout);
    }

    function moveItem(id, delta) {
        const layout = getLayout();
        reindexOrder(layout.items);
        const idx = layout.items.findIndex(function (it) { return it.id === id; });
        if (idx === -1) return;
        const target = idx + delta;
        if (target < 0 || target >= layout.items.length) return;
        const swapped = layout.items[idx];
        layout.items[idx] = layout.items[target];
        layout.items[target] = swapped;
        reindexOrder(layout.items);
        setLayout(layout);
    }

    function setItemSize(id, size) {
        const layout = getLayout();
        const it = layout.items.find(function (x) { return x.id === id; });
        if (!it) return;
        if (!SIZES.includes(size)) return;
        it.size = size;
        setLayout(layout);
    }

    function setItemConfig(id, partial) {
        const layout = getLayout();
        const it = layout.items.find(function (x) { return x.id === id; });
        if (!it) return;
        it.config = Object.assign({}, it.config || {}, partial || {});
        setLayout(layout);
    }

    function resetDefaults() {
        try { localStorage.removeItem(STORAGE_KEY); } catch (_) {}
        cache.layout = null;
        const layout = { version: SCHEMA_VERSION, items: defaultItems(cache.auth) };
        reindexOrder(layout.items);
        writeLayout(layout);
        if (currentContainer) render(currentContainer);
    }

    function getManifestSync() {
        return manifestCache;
    }

    /* ─── Bootstrap when included directly by a page ─── */
    function autoBoot() {
        const grid = document.getElementById("hubWidgetGrid");
        if (!grid) return;
        // Auth state: .user-status is only rendered by the hub template
        // when a session is active. That keeps the JS engine decoupled
        // from a backend roundtrip and from the body context.
        const auth = document.querySelector(".user-status") ? "loggedin" : "anonymous";
        init(grid, { auth: auth === "loggedin" ? "loggedin" : "anonymous" });
    }

    window.HubWidgets = {
        init: init,
        render: render,
        getLayout: getLayout,
        setLayout: setLayout,
        addItem: addItem,
        removeItem: removeItem,
        moveItem: moveItem,
        setItemSize: setItemSize,
        setItemConfig: setItemConfig,
        resetDefaults: resetDefaults,
        getManifestSync: getManifestSync,
        _registry: behaviors,
        _autoBoot: autoBoot,
    };

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", autoBoot);
    } else {
        autoBoot();
    }
})();
