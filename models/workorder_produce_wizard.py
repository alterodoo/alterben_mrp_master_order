# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class WorkorderProduceWizard(models.TransientModel):
    _name = 'mrp.workorder.produce.wizard'
    _description = 'Registrar producción parcial por OT'

    def _sanitize_m2o(self):
        for wizard in self:
            for fname in ('workorder_id', 'production_id', 'product_id', 'lot_id'):
                val = wizard[fname]
                if isinstance(val, models.BaseModel):
                    if not val.exists():
                        wizard[fname] = False
                    continue
                if val is not False and val is not None:
                    wizard[fname] = False

    def read(self, fields=None, load='_classic_read'):
        self._sanitize_m2o()
        return super().read(fields=fields, load=load)

    @api.model
    def default_get(self, fields_list):
        # Primero llamar a super para obtener defaults básicos del contexto
        final_res = super().default_get(fields_list)
        defaults = {
            'workorder_id': False,
            'production_id': False,
            'product_id': False,
            'lot_id': False,
            'qty': 0.0,
            'tracking': 'none',
        }
        for fname, val in defaults.items():
            if fname in fields_list and fname not in final_res:
                final_res[fname] = val
        for fname in ('workorder_id', 'production_id', 'product_id', 'lot_id'):
            if fname not in final_res:
                continue
            val = final_res.get(fname)
            if isinstance(val, models.BaseModel):
                final_res[fname] = val.id or False
            elif val and not isinstance(val, int):
                final_res[fname] = False

        wo_id = final_res.get('workorder_id') or self.env.context.get('default_workorder_id')
        if isinstance(wo_id, models.BaseModel):
            wo_id = wo_id.id
        if not wo_id:
            # No hay OT en el contexto, devolver defaults estándar
            return final_res

        # Validar la cadena de registros completa (OT -> OF -> Producto)
        wo = self.env['mrp.workorder'].browse(wo_id).exists()
        if not wo:
            raise UserError(_("La Orden de Trabajo (ID: %s) indicada en el contexto ya no existe.") % wo_id)
        
        prod = wo.production_id.exists()
        if not prod:
            raise UserError(_("La Orden de Trabajo %s está ligada a una Orden de Fabricación que ya no existe.") % wo.name)
        
        product = prod.product_id.exists()
        if not product:
            raise UserError(_("La Orden de Fabricación %s no tiene un producto final válido (puede haber sido eliminado).") % prod.name)
            
        # Preparar los valores de reemplazo
        res = {}
        res['workorder_id'] = wo.id
        res['production_id'] = prod.id
        
        # Calcular cantidad sugerida, combinando lógicas previas
        remaining = max((prod.product_qty or 0.0) - (prod.qty_produced or 0.0), 0.0)
        qty_suggested = getattr(wo, 'qty_remaining', False) or remaining
        if remaining and qty_suggested and qty_suggested > remaining:
            qty_suggested = remaining
        res['qty'] = qty_suggested or 0.0
        
        # Actualizar el diccionario de resultado y devolver
        final_res.update(res)
        return final_res

    workorder_id = fields.Many2one('mrp.workorder', string='Orden de trabajo', required=True)
    production_id = fields.Many2one('mrp.production', string='Orden de fabricación', required=True)
    product_id = fields.Many2one('product.product', string='Producto', compute='_compute_product_and_tracking', store=False, readonly=True)
    qty = fields.Float(string='Cantidad a producir', digits='Product Unit of Measure', required=True)
    lot_id = fields.Many2one('stock.production.lot', string='Lote/Serie')
    tracking = fields.Selection(
        selection=[('serial', 'By Unique Serial Number'), ('lot', 'By Lots'), ('none', 'No Tracking')],
        string="Seguimiento",
        compute='_compute_product_and_tracking',
        store=False,
        readonly=True
    )

    @api.depends('production_id')
    def _compute_product_and_tracking(self):
        for wizard in self:
            production = wizard.production_id
            if not isinstance(production, models.BaseModel):
                wizard.product_id = False
                wizard.tracking = 'none'
                continue
            production = production.exists()
            if not production:
                wizard.product_id = False
                wizard.tracking = 'none'
                continue
            product = production.product_id
            if not isinstance(product, models.BaseModel):
                wizard.product_id = False
                wizard.tracking = 'none'
                continue
            product = product.exists()
            if product:
                wizard.product_id = product
                wizard.tracking = product.tracking
            else:
                wizard.product_id = False
                wizard.tracking = 'none'

    @api.onchange('workorder_id')
    def _onchange_workorder(self):
        self._sanitize_m2o()
        if not self.workorder_id:
            self.production_id = False
            self.qty = 0.0
            self.lot_id = False
            return

        if not isinstance(self.workorder_id, models.BaseModel):
            self.workorder_id = False
            self.production_id = False
            self.qty = 0.0
            self.lot_id = False
            return

        wo = self.workorder_id.exists()
        if not wo:
            self.production_id = False
            self.qty = 0.0
            self.lot_id = False
            return
        
        production = wo.production_id
        if not isinstance(production, models.BaseModel):
            self.production_id = False
            self.qty = 0.0
            self.lot_id = False
            return
        production = production.exists()
        if not production:
            self.production_id = False
            self.qty = 0.0
            self.lot_id = False
            return

        product = production.product_id
        if not isinstance(product, models.BaseModel):
            self.production_id = False
            self.qty = 0.0
            self.lot_id = False
            return
        product = product.exists()
        if not product:
            self.production_id = False
            self.qty = 0.0
            self.lot_id = False
            return

        self.production_id = production
        self.lot_id = False
        remaining = max((production.product_qty or 0.0) - (production.qty_produced or 0.0), 0.0)
        qty_suggested = getattr(wo, 'qty_remaining', False) or remaining
        if remaining and qty_suggested and qty_suggested > remaining:
            qty_suggested = remaining
        self.qty = qty_suggested or 0.0

    @api.onchange('production_id', 'product_id', 'lot_id')
    def _onchange_sanitize(self):
        self._sanitize_m2o()

    def _check_qty(self):
        if self.qty <= 0:
            raise UserError(_("Ingrese una cantidad mayor a cero."))
        remaining = max((self.production_id.product_qty or 0.0) - (self.production_id.qty_produced or 0.0), 0.0)
        if remaining and self.qty > remaining + 1e-6:
            raise UserError(_("La cantidad a producir supera la cantidad restante de la OP (%.2f).") % remaining)

    def _check_lot(self):
        if self.tracking in ('lot', 'serial') and not self.lot_id:
            raise UserError(_("El producto requiere lote/serie. Seleccione un lote/serie."))

    def action_confirm(self):
        self.ensure_one()
        self._check_qty()
        self._check_lot()
        wo = self.workorder_id.exists()
        if not wo:
            raise UserError(_("No se encontró la orden de trabajo (puede haber sido eliminada)."))
        production = wo.production_id.exists()
        if not production:
            raise UserError(_("La OT está ligada a una Orden de Fabricación que ya no existe. Corrija la OT/OP antes de registrar."))
        product = production.product_id.exists()
        if not product:
            raise UserError(_("El producto de la Orden de Fabricación fue eliminado. Corrija la OP antes de registrar."))

        # Preparar contexto para trazabilidad si aplica
        ctx = dict(self.env.context or {})
        if self.lot_id:
            ctx['final_lot_id'] = self.lot_id.id
            ctx['default_final_lot_id'] = self.lot_id.id

        # Ajustar qty_producing de la OP para que record_production use la cantidad correcta
        try:
            production.qty_producing = self.qty
        except Exception:
            pass

        # Ejecutar el flujo estándar de la OT; usa qty_producing
        try:
            wo.with_context(ctx).record_production(qty_produced=self.qty)
        except TypeError:
            # Compatibilidad: si la firma no acepta qty_produced, llamar sin argumento
            wo.with_context(ctx).record_production()

        return {'type': 'ir.actions.act_window_close'}
