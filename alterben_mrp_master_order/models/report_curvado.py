from odoo import api, models, fields

class ReportCurvado(models.AbstractModel):
    _name = 'report.alterben_mrp_master_order.report_curvado'
    _description = 'Reporte Curvado (backend)'

    def _resolve_master(self, docids, data):
        env = self.env
        # Try explicit data master_id
        mid = (data or {}).get('master_id') if data else None
        if mid:
            return env['mrp.master.order'].browse(mid)
        # Try docids
        if docids:
            return env['mrp.master.order'].browse(docids[0])
        # Try context actives
        ctx = env.context or {}
        active_id = ctx.get('active_id') or (ctx.get('active_ids') or [None])[0]
        if active_id:
            return env['mrp.master.order'].browse(active_id)
        # Fallback to empty recordset
        return env['mrp.master.order'].browse()

    def _get_lines(self, master, tab):
        if not master:
            return self.env['mrp.master.order.line'].browse()
        if tab == 'hp_t1':
            return master.line_ids_hp_t1
        if tab == 'hp_t2':
            return master.line_ids_hp_t2
        if tab == 'hg_t1':
            return master.line_ids_hg_t1
        if tab == 'hg_t2':
            return master.line_ids_hg_t2
        # Fallback: union of all tabs
        return (master.line_ids_hp_t1 | master.line_ids_hp_t2) | (master.line_ids_hg_t1 | master.line_ids_hg_t2)

    @api.model
    def _get_report_values(self, docids, data=None):
        data = data or {}
        master = self._resolve_master(docids, data)
        tab = data.get('tab') or self.env.context.get('curvado_tab') or ''
        username = data.get('username') or self.env.user.name or ''
        notes = data.get('notes') or ''
        lines = self._get_lines(master, tab)
        # Precompute aggregates to keep QWeb simple and robust
        line_count = len(lines)
        total_program = sum(lines.mapped('product_qty')) if lines else 0.0
        total_arrastre = sum(lines.mapped('arrastre_qty')) if lines else 0.0
        total_qty = sum(lines.mapped('qty_total')) if lines else 0.0
        total_scrap = sum(lines.mapped('scrap_qty')) if lines else 0.0
        total_produced = sum(lines.mapped('cantidad_real')) if lines else 0.0
        # Current timestamp in user's timezone
        now_utc = fields.Datetime.now()
        now_tz = fields.Datetime.context_timestamp(self, now_utc)
        now_str = now_tz.strftime('%Y-%m-%d %H:%M') if now_tz else ''
        return {
            'doc_ids': [master.id] if master else [],
            'doc_model': 'mrp.master.order',
            'docs': [master] if master else [],
            'master': master,
            'tab': tab,
            'username': username,
            'notes': notes,
            'lines': lines,
            'line_count': line_count,
            'total_program': total_program,
            'total_arrastre': total_arrastre,
            'total_qty': total_qty,
            'total_scrap': total_scrap,
            'total_produced': total_produced,
            'now_str': now_str,
        }
