from odoo import api, models, fields

class ReportPrevaciado(models.AbstractModel):
    _name = 'report.alterben_mrp_master_order.report_prevaciado'
    _description = 'Reporte Prevaciado (backend)'

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
        lines = getattr(master, 'line_ids_prevaciado', None)
        return lines if lines is not None else master.line_ids

    @api.model
    def _get_report_values(self, docids, data=None):
        data = data or {}
        master = self._resolve_master(docids, data)
        username = data.get('username') or self.env.user.name or ''
        notes = data.get('notes') or ''
        lines = self._get_lines(master)
        line_count = len(lines)
        total_program = sum(lines.mapped('product_qty')) if lines else 0.0
        total_arrastre = sum(lines.mapped('arrastre_qty')) if lines else 0.0
        total_qty = sum(lines.mapped('qty_total')) if lines else 0.0
        total_scrap = sum(lines.mapped('scrap_qty')) if lines else 0.0
        total_produced = sum(lines.mapped('cantidad_real')) if lines else 0.0
        now_utc = fields.Datetime.now()
        now_tz = fields.Datetime.context_timestamp(self, now_utc)
        now_str = now_tz.strftime('%Y-%m-%d %H:%M') if now_tz else ''
        return {
            'doc_ids': [master.id] if master else [],
            'doc_model': 'mrp.master.order',
            'docs': [master] if master else [],
            'master': master,
            'username': username,
            'notes': notes,
            'lines': lines,
            'line_count': line_count,
            'total_program': total_program,
            'total_arrastre': total_arrastre,
            'total_qty': total_qty,
            'total_produced': total_produced,
            'total_scrap': total_scrap,
            'now_str': now_str,
        }
