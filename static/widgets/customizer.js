/* ─────────────────────────────────────────────────────────────
 *  Hub customizer editor — runs only on /hub/customize.
 *
 *  Renders:
 *   – the catalog gallery (add buttons)
 *   – the active widget list (reorder / resize / config / remove)
 *   – a live mini-preview of the hub layout (via a second init of
 *     HubWidgets on a separate grid element)
 *
 *  Talks to HubWidgets via its public setLayout / addItem /
 *  removeItem / moveItem / setItemSize / setItemConfig API.
 * ───────────────────────────────────────────────────────────── */

(function () {
    function escapeHtml(s) {
        return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
            return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c];
        });
    }
    function escapeAttr(s) { return escapeHtml(s); }
    function i18n(key, fallback) {
        return (window.t && window.t(key)) || fallback;
    }

    function bootPreview() {
        const grid = document.getElementById("hubWidgetGrid");
        if (!grid || !window.HubWidgets) return;
        // Auth: same heuristic as on the hub itself.
        const auth = document.querySelector(".user-status") ? "loggedin" : "anonymous";
        // Use a separate widget engine instance via a fresh container —
        // the real /hub grid is NOT present on this page, so init() will
        // attach to this one (its container IS the live preview).
        window.HubWidgets.init(grid, { auth: auth });
    }

    async function renderCatalog() {
        if (!window.HubWidgets) return;
        const manifest = window.HubWidgets.getManifestSync();
        let list = manifest && Array.isArray(manifest.widgets) ? manifest.widgets : [];
        if (!list.length) {
            // Manifest hasn't loaded yet — fetch once to populate.
            list = ((await fetch("/static/widgets/manifest.json", { cache: "no-cache" }))
                .then(function (r) { return r.ok ? r.json().then(function (m) { return m.widgets || []; }) : []; })
                .then(function (ws) { renderCatalogItems(ws); })
                .catch(function () { renderCatalogItems([]); }));
            return;
        }
        renderCatalogItems(list);
    }

    function lucided(node) {
        try {
            if (window.lucide && window.lucide.createIcons) {
                window.lucide.createIcons({
                    attrs: { "stroke-width": 2.5 },
                    elements: Array.prototype.slice.call(node.querySelectorAll("[data-lucide]"))
                });
            }
        } catch (_) {}
    }

    function renderCatalogItems(widgets) {
        const root = document.getElementById("hubCatalog");
        if (!root) return;
        if (!widgets.length) {
            root.innerHTML =
                '<div class="empty-list">' + escapeHtml(i18n("hub.customize.catalog_empty", "Keine Widgets verfügbar.")) + "</div>";
            return;
        }
        root.innerHTML = widgets.map(function (w) {
            return (
                '<div class="catalog-card" data-type="' + escapeAttr(w.id) + '">' +
                    '<div class="cc-icon"><i data-lucide="' + escapeAttr(w.icon || "package") + '"></i></div>' +
                    '<div class="cc-name">' + escapeHtml(w.name || w.id) + "</div>" +
                    '<div class="cc-desc">' + escapeHtml(w.description || "") + "</div>" +
                    '<div class="cc-action">' +
                        '<button type="button" class="btn btn-primary add-btn" data-add="' + escapeAttr(w.id) + '">' +
                            '<i data-lucide="plus"></i>' +
                            '<span>' + escapeHtml(i18n("hub.customize.add", "Hinzufügen")) + "</span>" +
                        "</button>" +
                    "</div>" +
                "</div>"
            );
        }).join("");
        lucided(root);
        Array.prototype.forEach.call(root.querySelectorAll(".add-btn"), function (btn) {
            btn.addEventListener("click", function () {
                const type = btn.getAttribute("data-add");
                if (!type) return;
                window.HubWidgets.addItem(type);
                refreshActiveList();
            });
        });
    }

    function fieldHtml(meta) {
        if (!meta || !meta.config_schema) return "";
        return meta.config_schema.map(function (field) {
            const id = "f-" + Math.random().toString(36).slice(2, 8);
            const labelText = escapeHtml((field.label || field.key));
            if (field.type === "boolean") {
                return (
                    '<div class="ai-row">' +
                        '<label for="' + escapeAttr(id) + '">' + labelText + "</label>" +
                        '<span class="check">' +
                            '<input type="checkbox" data-config-field="' + escapeAttr(field.key) + '" id="' + escapeAttr(id) + '" />' +
                            "<span>" + escapeHtml(i18n("hub.customize.field.boolean", "aktiv")) + "</span>" +
                        "</span>" +
                    "</div>"
                );
            }
            if (field.type === "select") {
                const opts = (field.options || []).map(function (o) {
                    return "<option value=\"" + escapeAttr(o.value) + "\">" + escapeHtml(o.label || o.value) + "</option>";
                }).join("");
                return (
                    '<div class="ai-row">' +
                        '<label for="' + escapeAttr(id) + '">' + labelText + "</label>" +
                        '<select class="form-control" data-config-field="' + escapeAttr(field.key) + '" id="' + escapeAttr(id) + '">' + opts + "</select>" +
                    "</div>"
                );
            }
            if (field.type === "textarea") {
                return (
                    '<div class="ai-row">' +
                        '<label for="' + escapeAttr(id) + '">' + labelText + "</label>" +
                        '<textarea class="form-control" data-config-field="' + escapeAttr(field.key) + '" id="' + escapeAttr(id) + '" rows="3"></textarea>' +
                    "</div>"
                );
            }
            // text / url / icon — default to single-line input
            return (
                '<div class="ai-row">' +
                    '<label for="' + escapeAttr(id) + '">' + labelText + "</label>" +
                    '<input type="text" class="form-control" data-config-field="' + escapeAttr(field.key) + '" id="' + escapeAttr(id) + '" />' +
                "</div>"
            );
        }).join("");
    }

    function activeItemHtml(item, meta, schemaHtml) {
        return (
            '<div class="active-item" data-id="' + escapeAttr(item.id) + '">' +
                '<div class="ai-head">' +
                    '<div class="ai-title">' +
                        '<i data-lucide="' + escapeAttr(meta.icon || "package") + '"></i>' +
                        '<span>' + escapeHtml(meta.name || item.type) + "</span>" +
                    "</div>" +
                    '<div class="ai-toolbar">' +
                        '<button type="button" class="ai-btn" data-action="up"   aria-label="' + escapeAttr(i18n("hub.customize.up",   "Nach oben")) + '"><i data-lucide="arrow-up"></i></button>' +
                        '<button type="button" class="ai-btn" data-action="down" aria-label="' + escapeAttr(i18n("hub.customize.down", "Nach unten")) + '"><i data-lucide="arrow-down"></i></button>' +
                        '<button type="button" class="ai-btn ai-btn-remove" data-action="remove" aria-label="' + escapeAttr(i18n("hub.customize.remove", "Entfernen")) + '"><i data-lucide="trash-2"></i></button>' +
                    "</div>" +
                "</div>" +
                '<div class="ai-body">' +
                    '<div class="ai-row">' +
                        '<label data-i18n="hub.customize.size">' + escapeHtml(i18n("hub.customize.size_label", "Größe")) + "</label>" +
                        '<select class="form-control" data-config-field="__size__">' +
                            '<option value="small">' + escapeHtml(i18n("hub.customize.size.small", "Klein")) + "</option>" +
                            '<option value="medium">' + escapeHtml(i18n("hub.customize.size.medium", "Mittel")) + "</option>" +
                            '<option value="large">' + escapeHtml(i18n("hub.customize.size.large", "Groß")) + "</option>" +
                        "</select>" +
                    "</div>" +
                    schemaHtml +
                "</div>" +
            "</div>"
        );
    }

    function refreshActiveList() {
        const root = document.getElementById("hubActiveList");
        if (!root || !window.HubWidgets) return;
        const layout = window.HubWidgets.getLayout();
        const manifest = window.HubWidgets.getManifestSync();
        if (!Array.isArray(layout.items) || !layout.items.length) {
            root.innerHTML = '<div class="empty-list">' +
                escapeHtml(i18n("hub.customize.active_empty", "Noch keine Widgets. Füge oben eines aus dem Katalog hinzu.")) +
            "</div>";
            lucided(root);
            return;
        }

        // Order layout.items by `order` before rendering, so the user sees
        // them in their current sequence.
        const sorted = layout.items.slice().sort(function (a, b) { return a.order - b.order; });
        const widgetsByType = (manifest && Array.isArray(manifest.widgets)
            ? manifest.widgets
            : []
        ).reduce(function (acc, w) { acc[w.id] = w; return acc; }, {});

        root.innerHTML = sorted.map(function (item) {
            const meta = widgetsByType[item.type] || { id: item.type, name: item.type, icon: "package", sizes: ["small","medium","large"], default_size: "medium" };
            return activeItemHtml(item, meta, fieldHtml(meta));
        }).join("");

        // Pre-fill each widget item's form with its current config + size.
        Array.prototype.forEach.call(root.querySelectorAll(".active-item"), function (el, idx) {
            const id = el.getAttribute("data-id");
            const item = sorted[idx];
            if (!id || !item) return;
            const sizeSelect = el.querySelector('[data-config-field="__size__"]');
            if (sizeSelect) {
                sizeSelect.value = (item.size && ["small","medium","large"].includes(item.size)) ? item.size : "medium";
                sizeSelect.addEventListener("change", function () {
                    window.HubWidgets.setItemSize(id, sizeSelect.value);
                });
            }
            const config = (item && item.config) || {};
            Array.prototype.forEach.call(
                el.querySelectorAll("[data-config-field]:not([data-config-field='__size__'])"),
                function (field) {
                    const key = field.getAttribute("data-config-field");
                    if (!key) return;
                    const value = config[key];
                    if (field.type === "checkbox") {
                        field.checked = Boolean(value);
                        field.addEventListener("change", function () {
                            const partial = {}; partial[key] = field.checked;
                            window.HubWidgets.setItemConfig(id, partial);
                        });
                    } else if (field.tagName === "SELECT") {
                        if (value != null) field.value = String(value);
                        field.addEventListener("change", function () {
                            const partial = {}; partial[key] = field.value;
                            window.HubWidgets.setItemConfig(id, partial);
                        });
                    } else {
                        if (value != null) field.value = String(value);
                        // Use 'input' for live feedback; 'change' for blur.
                        field.addEventListener("input", function () {
                            const partial = {}; partial[key] = field.value;
                            window.HubWidgets.setItemConfig(id, partial);
                        });
                    }
                }
            );
        });

        // Wire the up / down / remove toolbar buttons. Up and down are
        // disabled at the list boundaries (first/last items) so the user
        // gets visual feedback before they click a no-op.
        const total = sorted.length;
        Array.prototype.forEach.call(root.querySelectorAll(".active-item"), function (el, idx) {
            const up = el.querySelector('.ai-btn[data-action="up"]');
            const down = el.querySelector('.ai-btn[data-action="down"]');
            if (up && idx === 0) { up.disabled = true; up.setAttribute("aria-disabled", "true"); }
            if (down && idx === total - 1) { down.disabled = true; down.setAttribute("aria-disabled", "true"); }
        });
        Array.prototype.forEach.call(root.querySelectorAll(".ai-btn[data-action]"), function (btn) {
            if (btn.disabled) return;
            btn.addEventListener("click", function () {
                const id = btn.closest(".active-item").getAttribute("data-id");
                const action = btn.getAttribute("data-action");
                if (action === "up") window.HubWidgets.moveItem(id, -1);
                else if (action === "down") window.HubWidgets.moveItem(id, +1);
                else if (action === "remove") {
                    if (!confirm(i18n("hub.customize.confirm_remove", "Dieses Widget wirklich entfernen?"))) return;
                    window.HubWidgets.removeItem(id);
                }
                refreshActiveList();
            });
        });

        lucided(root);
    }

    function boot() {
        bootPreview();
        renderCatalog();
        refreshActiveList();

        const reset = document.getElementById("hubResetLayout");
        if (reset) {
            reset.addEventListener("click", function () {
                if (!confirm(i18n("hub.customize.confirm_reset", "Alle Widget-Änderungen verwerfen?"))) return;
                window.HubWidgets.resetDefaults();
                refreshActiveList();
            });
        }

        document.addEventListener("hub-widgets:change", function () {
            refreshActiveList();
        });

        // Re-render labels etc. when locale changes.
        document.addEventListener("i18n:applied", function () {
            renderCatalog();
            refreshActiveList();
        });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", boot);
    } else {
        boot();
    }
})();
