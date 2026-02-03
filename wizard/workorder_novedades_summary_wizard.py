# -*- coding: utf-8 -*-
from odoo import models, fields, api

class WorkorderNovedadesSummaryWizard(models.TransientModel):
    _name = 'workorder.novedades.summary.wizard'
    _description = 'Resumen de novedades (alertas & desechos) por Workorder'

    workorder_id = fields.Many2one('mrp.workorder', string='WO', required=True, readonly=True)
    mo_id = fields.Many2one('mrp.production', string='Orden de fabricación', related='workorder_id.production_id', store=False, readonly=True)
    content = fields.Html(string='Resumen', sanitize=True)

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        wo = self.env['mrp.workorder'].browse(self._context.get('active_id'))
        if wo:
            res['workorder_id'] = wo.id
            res['mo_id'] = wo.production_id.id if wo.production_id else False
            text = False
            # Método del módulo original que arma el resumen
            if hasattr(wo, 'get_novedades_summary'):
                text = wo.get_novedades_summary()
            # Formateo HTML: lista legible en vez de <pre>
            html_lines = [l.strip('- ').replace('->', '→') for l in (text or '').splitlines() if l.strip()]
            items = ''.join(f"<li style='margin:2px 0'>{l}</li>" for l in html_lines)
            html = (                "<div style='font-size:14px;line-height:1.5'>"
                "<ul style='padding-left:18px;margin:0'>" + items + "</ul>"
                "</div>"
            )
            res['content'] = html
        return res

    def action_close(self):
        return {'type': 'ir.actions.act_window_close'}
