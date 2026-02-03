# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError

class StockScrap(models.Model):
    _inherit = 'stock.scrap'

    quality_alert_id = fields.Many2one('quality.alert', string='Alerta de Calidad', copy=False)
    quality_alert_display = fields.Char(string='Alerta generada', compute='_compute_quality_alert_display', store=False)
    auto_validate_failed = fields.Boolean(string='Auto validacion fallida', copy=False)
    auto_validate_message = fields.Text(string='Motivo auto validacion', copy=False)
    import_stock_status = fields.Char(string='Estado stock importacion', copy=False)

    @api.depends('quality_alert_id')
    def _compute_quality_alert_display(self):
        for scrap in self:
            scrap.quality_alert_display = scrap.quality_alert_id.display_name if scrap.quality_alert_id else ''

    @api.model
    def create(self, vals):
        if not vals.get('x_studio_operacion'):
            wo_id = vals.get('workorder_id') or self.env.context.get('default_workorder_id')
            if wo_id:
                wo = self.env['mrp.workorder'].browse(wo_id)
                if wo and wo.operation_id:
                    vals['x_studio_operacion'] = wo.operation_id.display_name or wo.operation_id.name
        
        # Set default scrap location if not set
        if not vals.get('scrap_location_id'):
            scrap_location = self.env['stock.location'].search([('scrap_location', '=', True)], limit=1)
            if scrap_location:
                vals['scrap_location_id'] = scrap_location.id
        
        scrap = super().create(vals)
        
        # If created from a quality alert, link both ways
        if self.env.context.get('from_quality_alert') and self.env.context.get('quality_alert_id'):
            alert = self.env['quality.alert'].browse(self.env.context['quality_alert_id'])
            if alert:
                alert.scrap_id = scrap.id
                scrap.quality_alert_id = alert.id
                scrap._sync_studio_links(alert)

        # Si viene vinculado por vals (quality_alert_id), también enlazar campos studio
        elif vals.get('quality_alert_id'):
            alert = self.env['quality.alert'].browse(vals['quality_alert_id'])
            if alert:
                if not scrap.quality_alert_id:
                    scrap.quality_alert_id = alert.id
                if 'scrap_id' in alert._fields and not alert.scrap_id:
                    alert.scrap_id = scrap.id
                scrap._sync_studio_links(alert)
        scrap._maybe_auto_validate()
        scrap._recompute_master_lines_real_qty()
        return scrap

    def write(self, vals):
        res = super().write(vals)
        # Refrescar enlaces studio si cambian nombre o alerta
        if any(k in vals for k in ('name', 'quality_alert_id')):
            self._sync_studio_links()
        if any(k in vals for k in ('scrap_qty', 'quantity', 'workorder_id', 'production_id', 'product_id', 'state')):
            self._recompute_master_lines_real_qty()
        return res

    def unlink(self):
        productions = (self.mapped('production_id') | self.mapped('workorder_id.production_id')).filtered(lambda p: p)
        res = super().unlink()
        if productions:
            self.env['mrp.master.order.line']._recompute_cantidad_real_for_productions(productions)
        return res

    def _sync_studio_links(self, alert=None):
        """Mantiene sincronizados los campos Studio entre desecho y alerta."""
        for scrap in self:
            alert_rec = alert or scrap.quality_alert_id
            if not alert_rec:
                continue
            if 'x_studio_alerta_de_calidad' in scrap._fields:
                try:
                    scrap.x_studio_alerta_de_calidad = alert_rec.display_name or alert_rec.name
                except Exception:
                    pass
            if 'x_studio_desecho' in alert_rec._fields:
                try:
                    alert_rec.x_studio_desecho = scrap.display_name or scrap.name
                except Exception:
                    pass

    def _recompute_master_lines_real_qty(self):
        productions = (self.mapped('production_id') | self.mapped('workorder_id.production_id')).filtered(lambda p: p)
        if productions:
            self.env['mrp.master.order.line']._recompute_cantidad_real_for_productions(productions)
            self.env['mrp.master.order.line']._recompute_station_qty_for_productions(productions)

    def action_view_quality_alert(self):
        self.ensure_one()
        if not self.quality_alert_id:
            raise UserError(_('No hay una alerta de calidad asociada a este desecho.'))
        return {
            'name': _('Alerta de Calidad'),
            'view_mode': 'form',
            'res_model': 'quality.alert',
            'res_id': self.quality_alert_id.id,
            'type': 'ir.actions.act_window',
            'target': 'current',
            'context': {'form_view_initial_mode': 'edit'}
        }

    def _maybe_auto_validate(self):
        for scrap in self:
            production = scrap.production_id or scrap.workorder_id.production_id
            master = getattr(production, 'master_order_id', False) if production else False
            mtype = getattr(master, 'type_id', False) if master else False
            if mtype and getattr(mtype, 'auto_validate_scrap', False) and hasattr(scrap, 'action_validate') and scrap.state == 'draft':
                try:
                    scrap.sudo().action_validate()
                except Exception:
                    # No impedir la creación si la validación automática falla
                    pass
