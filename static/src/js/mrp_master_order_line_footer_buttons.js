(function () {
    'use strict';

    var TABLE_CLASS = 'ab-mrp-line-grid';
    var BAR_CLASS = 'ab-mrp-line-top-bar';
    var BTN_CLASS = 'btn btn-secondary ab-mrp-line-top-btn';

    function tryParseInt(v) {
        var n = parseInt(v, 10);
        return isNaN(n) ? null : n;
    }

    function findRowIdFromRow(tr) {
        if (!tr) return null;
        var keys = ['data-res-id', 'data-id', 'data-oe-id'];
        for (var i = 0; i < keys.length; i++) {
            var v = tr.getAttribute(keys[i]);
            var n0 = tryParseInt(v);
            if (n0 !== null) return n0;
        }
        if (tr.dataset) {
            var n1 = tryParseInt(tr.dataset.resId || tr.dataset.id || tr.dataset.oeId);
            if (n1 !== null) return n1;
        }
        var cb = tr.querySelector('input[type="checkbox"][name="ids"], input.o_list_record_selector');
        if (cb && cb.value) {
            var n2 = tryParseInt(cb.value);
            if (n2 !== null) return n2;
        }
        return null;
    }

    function getLineIds(root) {
        if (!root) return [];
        var ids = [];
        var selected = root.querySelectorAll('input.o_list_record_selector:checked, input[type="checkbox"][name="ids"]:checked');
        if (selected && selected.length) {
            selected.forEach(function (cb) {
                var n = tryParseInt(cb.value);
                if (n !== null) ids.push(n);
            });
        }
        if (ids.length) return ids;
        var row = root.querySelector('tr.o_data_row');
        var id = findRowIdFromRow(row);
        return id ? [id] : [];
    }

    function rpcCall(method, ids) {
        var payload = {
            jsonrpc: '2.0',
            method: 'call',
            params: {
                model: 'mrp.master.order.line',
                method: method,
                args: [ids],
                kwargs: {},
            },
            id: Date.now(),
        };
        return fetch('/web/dataset/call_kw', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'same-origin',
            body: JSON.stringify(payload),
        }).then(function (resp) { return resp.json(); });
    }

    function openAction(action) {
        try {
            if (window.odoo && window.odoo.__DEBUG__ && window.odoo.__DEBUG__.services && window.odoo.__DEBUG__.services.action) {
                window.odoo.__DEBUG__.services.action.doAction(action);
                return true;
            }
        } catch (e) {}
        if (!action || action.type !== 'ir.actions.act_window') return false;
        var model = action.res_model;
        var viewMode = (action.view_mode || 'form').split(',')[0];
        var hash = '#model=' + encodeURIComponent(model || '') + '&view_type=' + encodeURIComponent(viewMode || 'form');
        if (action.res_id) {
            hash += '&id=' + encodeURIComponent(String(action.res_id));
        }
        if (action.domain) {
            hash += '&domain=' + encodeURIComponent(JSON.stringify(action.domain));
        }
        if (action.context) {
            hash += '&context=' + encodeURIComponent(JSON.stringify(action.context));
        }
        if (action.target === 'new') {
            window.open('/web' + hash, '_blank');
        } else {
            window.location.hash = hash;
        }
        return true;
    }

    function handleClick(root, method, btn) {
        var ids = getLineIds(root);
        if (!ids.length) {
            window.alert('No hay líneas en la tabla.');
            return;
        }
        if (btn) btn.setAttribute('disabled', 'disabled');
        rpcCall(method, ids).then(function (data) {
            if (btn) btn.removeAttribute('disabled');
            if (data && data.error) {
                window.alert((data.error.data && data.error.data.message) || data.error.message || 'Error al ejecutar la acción.');
                return;
            }
            if (data && data.result) {
                if (!openAction(data.result)) {
                    window.location.reload();
                }
            }
        }).catch(function () {
            if (btn) btn.removeAttribute('disabled');
            window.alert('No se pudo ejecutar la acción.');
        });
    }

    function isMrpLineList(listView) {
        if (!listView) return false;
        var model = listView.getAttribute('data-res-model');
        if (!model && listView.dataset) {
            model = listView.dataset.resModel || listView.dataset.model;
        }
        if (model) {
            return model === 'mrp.master.order.line';
        }
        var table = listView.querySelector('table.o_list_table');
        if (!table) return false;
        var hasProduct = table.querySelector('th[data-name="product_id"]');
        var hasCode = table.querySelector('th[data-name="product_code"]');
        return !!(hasProduct && hasCode);
    }

    function parseContextValue(raw) {
        if (!raw) return null;
        try {
            return JSON.parse(raw);
        } catch (e) {}
        try {
            return JSON.parse(raw.replace(/'/g, '"'));
        } catch (e2) {}
        return null;
    }

    function findContextFromElement(el) {
        var cur = el;
        while (cur) {
            var ctxAttr = cur.getAttribute && (cur.getAttribute('data-context') || (cur.dataset ? cur.dataset.context : null));
            var ctx = parseContextValue(ctxAttr);
            if (ctx) return ctx;
            cur = cur.parentElement;
        }
        return null;
    }

    function findContextInChildren(root) {
        if (!root || !root.querySelectorAll) return null;
        var nodes = root.querySelectorAll('[data-context]');
        for (var i = 0; i < nodes.length; i++) {
            var raw = nodes[i].getAttribute('data-context') || (nodes[i].dataset ? nodes[i].dataset.context : null);
            if (!raw) continue;
            if (raw.indexOf && raw.indexOf('mrp_tab') === -1) continue;
            var ctx = parseContextValue(raw);
            if (ctx) return ctx;
        }
        return null;
    }

    function getContextFromHash() {
        try {
            var hash = window.location.hash || '';
            var m = hash.match(/context=([^&]+)/);
            if (!m || !m[1]) return null;
            return parseContextValue(decodeURIComponent(m[1]));
        } catch (e) {
            return null;
        }
    }

    function detectTab(listView) {
        if (!listView) return 'otros';
        var ctx = findContextFromElement(listView) || findContextInChildren(listView) || getContextFromHash();
        if (ctx && ctx.mrp_tab) return ctx.mrp_tab;
        if (listView.querySelector('th[data-name="qty_to_prevaciar"], td[data-name="qty_to_prevaciar"]')) {
            return 'prevaciado';
        }
        if (listView.querySelector('th[data-name="qty_to_liberar"], th[data-name="reciclo_qty"], td[data-name="qty_to_liberar"], td[data-name="reciclo_qty"]')) {
            return 'inspeccion_final';
        }
        if (listView.querySelector('th[data-name="cantidad_ensamblada"], td[data-name="cantidad_ensamblada"]')) {
            return 'ensamblado';
        }
        if (listView.querySelector('th[data-name="ancho_pvb"], td[data-name="ancho_pvb"]')) {
            return 'corte';
        }
        return 'otros';
    }

    function buildButtons(bar, renderer, tab, forceDeliver) {
        var buttons = [
            { label: 'Agregar MOs abiertas', method: 'action_open_add_open_mo_wizard_line' },
            { label: 'Generar MOs', method: 'action_generate_pending_tab_line' },
            { label: 'Ver MOs', method: 'action_view_mos_tab_line' },
            { label: 'Ver WOs', method: 'action_view_wos_tab_line' },
            { label: 'Recalcular OPT', method: 'action_recalculate_opt_line', tabs: ['ensamblado'] },
            { label: 'Confirmar corte PVB', method: 'action_confirm_corte_pvb_line', tabs: ['corte'] },
            { label: 'Recalcular PVB', method: 'action_recalcular_corte_line', tabs: ['corte'] },
            { label: 'Marcar como hecho', method: 'action_mark_tab_done_prompt_line', tabs: ['inspeccion_final'] },
            { label: 'Entregar a bodega', method: 'action_generate_warehouse_delivery_line' },
            { label: 'Ver entregas', method: 'action_view_deliveries_line' },
        ];

        buttons.forEach(function (spec) {
            if (spec.tabs && spec.tabs.indexOf(tab) === -1) return;
            var btn = document.createElement('button');
            btn.type = 'button';
            btn.className = BTN_CLASS;
            btn.textContent = spec.label;
            btn.addEventListener('click', function () {
                handleClick(renderer, spec.method, btn);
            });
            bar.appendChild(btn);
        });
    }

    function installBar(root) {
        if (!root || !root.querySelectorAll) return;
        var listViews = root.querySelectorAll('.o_list_view');
        if (!listViews || !listViews.length) return;
        listViews.forEach(function (listView) {
            if (!isMrpLineList(listView)) return;
            var table = listView.querySelector('table.o_list_table') || listView.querySelector('table');
            if (!table) return;
            var renderer = table.closest('.o_list_renderer') || listView;
            if (!renderer) return;
            var existingBars = renderer.querySelectorAll('.ab-mrp-line-top-bar, .ab-mrp-line-bottom-bar');
            if (existingBars && existingBars.length) {
                existingBars.forEach(function (b) { b.remove(); });
            }
            var bar = document.createElement('div');
            bar.className = BAR_CLASS;
            var tab = detectTab(listView);
            var forceDeliver = !!listView.querySelector('th[data-name="qty_to_deliver"], th[data-name="reciclo_qty"], td[data-name="qty_to_deliver"], td[data-name="reciclo_qty"], button[name="action_open_control_total_wizard"]');
            buildButtons(bar, renderer, tab, forceDeliver);
            if (!bar.children.length) return;
            if (table.parentNode) {
                table.parentNode.insertBefore(bar, table);
            } else {
                renderer.insertBefore(bar, renderer.firstChild);
            }
        });
    }

    var obs = new MutationObserver(function (mutations) {
        mutations.forEach(function (m) {
            if (m.addedNodes) {
                m.addedNodes.forEach(function (n) {
                    if (n && n.querySelectorAll) {
                        installBar(n);
                    }
                });
            }
        });
    });

    function ready(fn) {
        if (document.readyState !== 'loading') {
            fn();
        } else {
            document.addEventListener('DOMContentLoaded', fn);
        }
    }

    ready(function () {
        try {
            installBar(document.body);
            obs.observe(document.body, { childList: true, subtree: true });
        } catch (e) {}
    });
})();
