from odoo import api, models, fields

class ReportInspeccionFinal(models.AbstractModel):
    _name = 'report.alterben_mrp_master_order.report_inspeccion_final'
    _description = 'Reporte Inspección Final (backend)'

    def _resolve_master(self, docids, data):
        env = self.env
        if data and 'master_id' in data:
            master = env['mrp.master.order'].browse(data['master_id'])
            if master.exists():
                return master
        if docids:
            return env['mrp.master.order'].browse(docids)
        return env['mrp.master.order']

    def _get_lines(self, master):
        if not master:
            return self.env['mrp.master.order.line'].browse()
        lines = getattr(master, 'line_ids_inspeccion_final', None)
        return lines if lines is not None else master.line_ids

    @api.model
    def _get_report_values(self, docids=None, data=None):
        data = data or {}
        master = self._resolve_master(docids, data)
        lines = self._get_lines(master)
        total_reciclo = sum(getattr(l, 'reciclo_qty', 0) or 0 for l in lines) if lines else 0
        total_almacen = sum(getattr(l, 'almacen_qty', 0) or 0 for l in lines) if lines else 0
        total_segunda = sum(getattr(l, 'segunda_qty', 0) or 0 for l in lines) if lines else 0
        total_destruidos = sum(getattr(l, 'destruidos_qty', 0) or 0 for l in lines) if lines else 0
        total_qty = sum(lines.mapped('product_qty')) if lines else 0.0
        total_vitrificacion = sum(1 for l in lines if getattr(l, 'vitrificacion_ok', False)) if lines else 0
        now_utc = fields.Datetime.now()
        now_tz = fields.Datetime.context_timestamp(self, now_utc)
        now_str = now_tz.strftime('%Y-%m-%d %H:%M') if now_tz else ''
        return {
            'doc_ids': master.ids,
            'doc_model': 'mrp.master.order',
            'docs': master,
            'master': master,
            'lines': lines,
            'notes': data.get('notes', ''),
            'username': data.get('username', self.env.user.name or ''),
            'total_qty': total_qty,
            'total_reciclo': total_reciclo,
            'total_almacen': total_almacen,
            'total_segunda': total_segunda,
            'total_destruidos': total_destruidos,
            'total_vitrificacion': total_vitrificacion,
            'now_str': now_str,
        }
