from odoo import api, fields, models


class ReportPvbMedidasFigura(models.AbstractModel):
    _name = 'report.alterben_mrp_master_order.report_pvb_medidas_figura'
    _description = 'Reporte PVB+ Medidas de figura (backend)'

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
        lines = getattr(master, 'line_ids_corte', self.env['mrp.master.order.line'].browse())
        return lines.filtered(lambda l: l.product_id)

    def _build_rows(self, lines):
        Receta = self.env['receta.pvb']
        rows = []
        for line in lines:
            rec = Receta.get_by_product(line.product_id)
            rows.append({
                'line': line,
                'alto': rec.alto if rec else False,
                'ancho': rec.ancho if rec else False,
                'v1': rec.v1 if rec else False,
                'v2': rec.v2 if rec else False,
                'c1': rec.c1 if rec else False,
                'c2': rec.c2 if rec else False,
                'sr': rec.ficha if rec else False,
            })
        return rows

    @api.model
    def _get_report_values(self, docids, data=None):
        data = data or {}
        master = self._resolve_master(docids, data)
        lines = self._get_lines(master)
        rows = self._build_rows(lines)
        now_utc = fields.Datetime.now()
        now_tz = fields.Datetime.context_timestamp(self, now_utc)
        now_str = now_tz.strftime('%d/%m/%Y') if now_tz else ''
        return {
            'doc_ids': [master.id] if master else [],
            'doc_model': 'mrp.master.order',
            'docs': [master] if master else [],
            'master': master,
            'rows': rows,
            'line_count': len(rows),
            'now_str': now_str,
        }
