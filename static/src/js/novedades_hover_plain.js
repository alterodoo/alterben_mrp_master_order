(function () {
    'use strict';
    function tryParseInt(v){ var n=parseInt(v); return isNaN(n)?null:n; }
    function findRowId(btn) {
        // 0) data-context rendered in button (rare in list, but try)
        try {
            var ctxAttr = btn && (btn.getAttribute('data-context') || (btn.dataset ? btn.dataset.context : null));
            if (ctxAttr) {
                try {
                    var ctx = JSON.parse(ctxAttr);
                    if (ctx && ctx._ab_wo_id) return {id: tryParseInt(ctx._ab_wo_id), via:'data-context'};
                } catch(_) {
                    try {
                        var ctx2 = JSON.parse(ctxAttr.replace(/'/g,'"'));
                        if (ctx2 && ctx2._ab_wo_id) return {id: tryParseInt(ctx2._ab_wo_id), via:'data-context*'};
                    } catch(__){}
                }
            }
        } catch(_){ }
        // Anchor row
        var tr = btn && btn.closest ? btn.closest('tr') : null;
        if (!tr) return {id:null, via:'no-tr'};
        // 1) Our visible debug cell
        var tdDbg = tr.querySelector('td.ab-wo-id');
        if (tdDbg) {
            var idText = (tdDbg.innerText || tdDbg.textContent || '').trim();
            var n0 = tryParseInt(idText);
            if (n0!==null) return {id:n0, via:'td.ab-wo-id'};
        }
        // 2) Common row attributes
        var keys = ['data-res-id', 'data-id', 'data-oe-id'];
        for (var i=0;i<keys.length;i++){
            var v = tr.getAttribute(keys[i]);
            if (!v && tr.dataset) {
                var dk = keys[i].replace('data-','').replace(/-([a-z])/g, function(_,c){return c.toUpperCase();});
                v = tr.dataset[dk];
            }
            var n1 = tryParseInt(v);
            if (n1!==null) return {id:n1, via:keys[i]};
        }
        // 3) Checkbox fallback
        var cb = tr.querySelector('input[type="checkbox"][name="ids"], input.o_list_record_selector');
        if (cb && cb.value) {
            var n2 = tryParseInt(cb.value);
            if (n2!==null) return {id:n2, via:'checkbox'};
        }
        // 4) Link fallback
        var a = tr.querySelector('a[href*="#id="]');
        if (a) {
            try {
                var m2 = a.getAttribute('href').match(/[#&]id=(\d+)/);
                if (m2) {
                    var n3 = tryParseInt(m2[1]);
                    if (n3!==null) return {id:n3, via:'link#id'};
                }
            } catch(e){}
        }
        return {id:null, via:'none'};
    }
    async function fetchCount(id) {
        var payload = { jsonrpc: "2.0", method: "call",
            params: { model: "mrp.workorder", method: "get_novedades_count", args: [[id]], kwargs: {} }, id: Date.now() };
        const resp = await fetch("/web/dataset/call_kw", {
            method: "POST", headers: {"Content-Type": "application/json"}, credentials: "same-origin",
            body: JSON.stringify(payload),
        });
        const data = await resp.json();
        if (data && typeof data.result !== "undefined") return data.result;
        throw new Error("RPC sin resultado");
    }
    function installCounts(root) {
        if (!root) return;
        var buttons = root.querySelectorAll("button.ab-nov-count");
        if (!buttons || !buttons.length) return;
        buttons.forEach(function(btn) {
            if (!btn || btn.getAttribute("data-loaded")) return;
            var found = findRowId(btn);
            btn.setAttribute("data-loaded", "1");
            if (!found.id) {
                btn.textContent = "?";
                btn.title = "No se pudo detectar el ID de la fila";
                return;
            }
            btn.textContent = "...";
            btn.title = "Cargando novedades...";
            fetchCount(found.id).then(function(count) {
                btn.textContent = String(count || 0);
                btn.title = "Ver novedades";
            }).catch(function() {
                btn.textContent = "?";
                btn.title = "No se pudo cargar el conteo";
            });
        });
    }
    var obs = new MutationObserver(function(mutations) {
        mutations.forEach(function(m) {
            if (m.addedNodes) {
                m.addedNodes.forEach(function(n) {
                    if (n && n.querySelectorAll) {
                        var lists = n.querySelectorAll("div.o_list_view, table.o_list_table");
                        if (lists && lists.length) installCounts(n);
                    }
                });
            }
        });
    });
    function ready(fn){ if (document.readyState !== 'loading'){ fn(); } else { document.addEventListener('DOMContentLoaded', fn); } }
    ready(function(){
        try {
            installCounts(document.body);
            obs.observe(document.body, { childList: true, subtree: true });
        } catch(e) {}
    });
})();
