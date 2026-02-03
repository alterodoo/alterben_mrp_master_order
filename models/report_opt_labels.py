# -*- coding: utf-8 -*-
from odoo import api, fields, models


class ReportOptLabels(models.AbstractModel):
    _name = 'report.alterben_mrp_master_order.report_opt_labels'
    _description = 'Reporte Etiquetas OPT (backend)'

    def _resolve_master(self, docids, data):
        env = self.env
        mid = (data or {}).get('master_id') if data else None
        if mid:
            return env['mrp.master.order'].browse(mid)
        if docids:
            return env['mrp.master.order'].browse(docids[0])
        ctx = env.context or {}
        active_id = ctx.get('active_id') or (ctx.get('active_ids') or [None])[0]
        if active_id:
            return env['mrp.master.order'].browse(active_id)
        return env['mrp.master.order'].browse()

    def _get_lines(self, master):
        if not master:
            return self.env['mrp.master.order.line'].browse()
        # Etiquetas deben salir desde Corte PVB.
        lines = getattr(master, 'line_ids_corte', None)
        if lines is None:
            lines = getattr(master, 'line_ids_ensamblado', None)
        if lines is None:
            lines = getattr(master, 'line_ids_ensamblaje', None)
        return lines if lines is not None else master.line_ids

    def _get_label_qty(self, line):
        if hasattr(line, 'pvb_cortado_qty') and line.pvb_cortado_qty:
            qty = line.pvb_cortado_qty
        elif hasattr(line, 'cantidad_piezas') and line.cantidad_piezas:
            qty = line.cantidad_piezas
        elif line.cantidad_ensamblada is not None:
            qty = line.cantidad_ensamblada
        else:
            qty = line.product_qty
        qty = qty or 0.0
        return int(qty)

    def _get_label_color(self, reference):
        ref = (reference or "").upper()
        if "CLA-AFA" in ref or "VLA-AFA" in ref:
            return "CELESTE"
        if "VLA-FRJ" in ref:
            return "BLANCO"
        if "VLA-CLA" in ref:
            return "GRIS"
        if "VLA-COL" in ref:
            return "VERDE"
        return "OTRO"

    @api.model
    def _get_report_values(self, docids, data=None):
        data = data or {}
        master = self._resolve_master(docids, data)
        lines = self._get_lines(master)
        excluded_products = set()
        if master and getattr(master, "type_id", False) and master.type_id.opt_labels_exclude_product_ids:
            excluded_products = set(master.type_id.opt_labels_exclude_product_ids.ids)
        available_map = (data or {}).get("available_map") or {}
        labels = []
        for line in lines:
            if not line.product_id:
                continue
            if line.product_id.id in excluded_products:
                continue
            qty = self._get_label_qty(line)
            if qty <= 0:
                continue
            prod = line.product_id
            barcode = prod.barcode or prod.default_code or ''
            color = self._get_label_color(prod.default_code or '')
            label = {
                'barcode': barcode,
                'barcode_text': barcode,
                'name': prod.display_name or prod.name or '',
                'reference': prod.default_code or '',
                'color': color,
            }
            labels.extend([label] * qty)

        per_page = 30
        color_order = ["VERDE", "GRIS", "CELESTE", "BLANCO", "OTRO"]
        color_map = {c: [] for c in color_order}
        for label in labels:
            color_map.setdefault(label.get("color") or "OTRO", []).append(label)
        pages = []
        for color in color_order:
            chunk = color_map.get(color) or []
            # Insertar espacios vacíos al inicio de la primera hoja según disponibilidad.
            available = int(available_map.get(color, 30) or 30)
            if available < 1:
                available = 1
            if available > 30:
                available = 30
            empty_slots = max(0, 30 - available)
            if empty_slots and chunk:
                chunk = ([None] * empty_slots) + chunk
            for i in range(0, len(chunk), per_page):
                pages.append({"color": color, "labels": chunk[i:i + per_page]})
        if not pages:
            pages = [{"color": "", "labels": []}]
        now_utc = fields.Datetime.now()
        now_tz = fields.Datetime.context_timestamp(self, now_utc)
        now_str = now_tz.strftime('%Y-%m-%d %H:%M') if now_tz else ''
        return {
            'doc_ids': [master.id] if master else [],
            'doc_model': 'mrp.master.order',
            'docs': [master] if master else [],
            'master': master,
            'pages': pages,
            'now_str': now_str,
        }
