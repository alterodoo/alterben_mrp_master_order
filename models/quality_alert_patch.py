# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError

class QualityAlertFromWOPatch(models.Model):
    _inherit = 'quality.alert'
    
    scrap_id = fields.Many2one('stock.scrap', string='Desecho', copy=False)
    scrap_count = fields.Integer(compute='_compute_scrap_count', string='Scrap Count')
    validated_scrap_id = fields.Many2one('stock.scrap', string='Validated Scrap', related='scrap_id', readonly=True)
    scrap_display = fields.Char(string='Desecho generado', compute='_compute_scrap_display', store=False)

    @api.depends('scrap_id')
    def _compute_scrap_count(self):
        for alert in self:
            alert.scrap_count = 1 if alert.scrap_id else 0

    def _compute_scrap_display(self):
        for alert in self:
            alert.scrap_display = alert.scrap_id.display_name if alert.scrap_id else ''

    def action_create_scrap(self):
        self.ensure_one()
        if not self.product_id:
            raise UserError(_('No se puede crear un desecho sin un producto. Por favor, asigne un producto a la alerta de calidad primero.'))

        # Verificar si ya existe un desecho para esta alerta
        if self.scrap_id:
            return self.action_view_scraps()

        # Crear el desecho
        scrap_vals = {
            'product_id': self.product_id.id,
            'product_uom_id': self.product_id.uom_id.id,
            'scrap_qty': 1.0,  # Cantidad por defecto, puede ser ajustada
            'reason_code_id': self.env.ref('quality_control.scrap_reason_quality_issue', False).id,
            'origin': self.name,
            'quality_alert_id': self.id,
            'note': _('Creado desde la alerta de calidad: %s') % self.name,
        }
        # Poner el número de alerta en el campo studio si existe en desecho
        if 'x_studio_alerta_de_calidad' in self.env['stock.scrap']._fields:
            scrap_vals['x_studio_alerta_de_calidad'] = self.display_name or self.name

        # Intentar obtener ubicaciones por defecto
        if self.workorder_id and self.workorder_id.production_id:
            production = self.workorder_id.production_id
            scrap_vals.update({
                'location_id': production.location_src_id.id,
                'picking_id': production.move_raw_ids and production.move_raw_ids[0].picking_id.id or False,
                'production_id': production.id,
            })

        scrap = self.env['stock.scrap'].create(scrap_vals)
        self.scrap_id = scrap.id
        # Poner el número de desecho en el campo studio de la alerta, si existe
        self._sync_studio_links(scrap)

        return {
            'name': _('Desecho creado'),
            'view_mode': 'form',
            'res_model': 'stock.scrap',
            'res_id': scrap.id,
            'type': 'ir.actions.act_window',
            'target': 'current',
            'context': {'form_view_initial_mode': 'edit'}
        }

    def action_view_scraps(self):
        self.ensure_one()
        if not self.scrap_id:
            raise UserError(_('No hay un desecho asociado a esta alerta.'))
        
        action = self.env.ref('stock.stock_scrap_action').read()[0]
        action['views'] = [(self.env.ref('stock.stock_scrap_form_view').id, 'form')]
        action['res_id'] = self.scrap_id.id
        return action

    def _update_vals_from_wo_context(self, vals):
        if not self.env.context.get('from_wo_novedades'):
            return vals
        mo = self.env['mrp.production'].browse(self.env.context.get('mo_id') or False)
        wo = self.env['mrp.workorder'].browse(self.env.context.get('wo_id') or False)

        # no forzar 'name'
        vals.pop('name', None)

        # title = MO
        if 'title' in self._fields and mo and mo.name and not vals.get('title'):
            vals['title'] = mo.name

        # product template
        if 'product_tmpl_id' in self._fields and mo and mo.product_id and mo.product_id.product_tmpl_id and not vals.get('product_tmpl_id'):
            vals['product_tmpl_id'] = mo.product_id.product_tmpl_id.id

        # reason
        if 'reason_id' in self._fields and not vals.get('reason_id'):
            Reason = self.env['quality.reason']
            reason = Reason.search([('name','=','Producción')], limit=1) or Reason.search([], limit=1)
            if reason:
                vals['reason_id'] = reason.id

        # assigned date
        if 'date_assign' in self._fields and not vals.get('date_assign'):
            vals['date_assign'] = fields.Datetime.now()

        # studio fields
        if 'x_studio_operacion' in self._fields and wo and not vals.get('x_studio_operacion'):
            vals['x_studio_operacion'] = wo.name or ''
        if 'x_studio_origen' in self._fields and mo and not vals.get('x_studio_origen'):
            vals['x_studio_origen'] = mo.origin or ''
        if 'x_studio_pedido_original' in self._fields and mo and hasattr(mo, 'x_studio_pedido_original') and not vals.get('x_studio_pedido_original'):
            vals['x_studio_pedido_original'] = mo.x_studio_pedido_original

        # link wo if field exists
        if 'workorder_id' in self._fields and wo and not vals.get('workorder_id'):
            vals['workorder_id'] = wo.id

        return vals

    def create(self, vals_list):
        single = isinstance(vals_list, dict)
        if single:
            vals_list = [vals_list]
        vals_list = [self._update_vals_from_wo_context(dict(v)) for v in vals_list]
        recs = super().create(vals_list)
        if recs:
            recs._sync_studio_links()
        return recs[0] if single else recs

    def write(self, vals):
        res = super().write(vals)
        # Si cambian scrap_id o name, sincronizar campos studio
        if any(k in vals for k in ('scrap_id', 'name')):
            self._sync_studio_links()
        return res

    def _sync_studio_links(self, scrap=None):
        """Mantiene sincronizados los campos studio entre alerta y desecho."""
        for alert in self:
            scrap_rec = scrap or alert.scrap_id
            if scrap_rec and 'x_studio_desecho' in alert._fields:
                try:
                    alert.x_studio_desecho = scrap_rec.display_name or scrap_rec.name
                except Exception:
                    pass
            if scrap_rec and 'x_studio_alerta_de_calidad' in scrap_rec._fields:
                try:
                    scrap_rec.x_studio_alerta_de_calidad = alert.display_name or alert.name
                except Exception:
                    pass
