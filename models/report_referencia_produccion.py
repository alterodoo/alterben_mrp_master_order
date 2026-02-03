from odoo import api, fields, models


class ReportReferenciaProduccion(models.AbstractModel):
    _name = 'report.alterben_mrp_master_order.report_referencia_produccion'
    _description = 'Reporte Referencia de Produccion (backend)'

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
        # Este reporte debe tomar siempre Corte PVB.
        lines = getattr(master, 'line_ids_corte', None)
        if lines is None:
            # Fallbacks defensivos si el campo no existe
            lines = getattr(master, 'line_ids_ensamblado', None) or getattr(master, 'line_ids_ensamblaje', None)
            if lines is None:
                lines = master.line_ids
        return lines.filtered(lambda l: l.product_id)

    @api.model
    def _get_report_values(self, docids, data=None):
        data = data or {}
        master = self._resolve_master(docids, data)
        lines = self._get_lines(master)
        main_total_qty = sum((line.product_qty or 0.0) for line in lines)
        report_date = data.get('report_date')
        if report_date:
            if isinstance(report_date, str):
                report_date = fields.Date.from_string(report_date)
        if not report_date:
            report_date = fields.Date.context_today(self)
        report_date_str = report_date.strftime('%d/%m/%Y') if report_date else ''
        process_employee_name = data.get('process_employee_name') or ''
        main_rows = list(lines)
        min_rows = 25
        if len(main_rows) < min_rows:
            main_rows += [None] * (min_rows - len(main_rows))
        recycle_rows = [None] * 6
        roto_rows = [None] * 4
        return {
            'doc_ids': [master.id] if master else [],
            'doc_model': 'mrp.master.order',
            'docs': [master] if master else [],
            'master': master,
            'lines': lines,
            'main_rows': main_rows,
            'main_total_qty': main_total_qty,
            'report_date_str': report_date_str,
            'process_employee_name': process_employee_name,
            'recycle_rows': recycle_rows,
            'roto_rows': roto_rows,
        }
