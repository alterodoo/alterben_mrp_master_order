# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError

class WorkorderNovedadesWizard(models.TransientModel):
    _name = 'alterben.workorder.novedades.wizard'
    _description = 'Registrar Novedades de Calidad y Desechos (WO)'

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        m2o_fields = (
            'workorder_id',
            'production_id',
            'workcenter_id',
            'quality_team_id',
            'product_to_scrap_id',
            'location_id',
            'scrap_location_id',
        )
        for fname in m2o_fields:
            if fname not in res:
                continue
            val = res.get(fname)
            if isinstance(val, models.BaseModel):
                res[fname] = val.id or False
            elif val and not isinstance(val, int):
                res[fname] = False
        return res

    def _sanitize_m2o(self):
        for wizard in self:
            for fname in (
                'workorder_id',
                'production_id',
                'product_finished_id',
                'workcenter_id',
                'quality_team_id',
                'product_to_scrap_id',
                'uom_id',
                'location_id',
                'scrap_location_id',
            ):
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

    workorder_id = fields.Many2one('mrp.workorder', string='Orden de trabajo', required=True)
    production_id = fields.Many2one('mrp.production', string='Orden de fabricación', required=True)
    product_finished_id = fields.Many2one('product.product', string='Producto final', compute='_compute_product_finished', store=False)

    workcenter_id = fields.Many2one('mrp.workcenter', string='Centro de trabajo', required=True)

    # Alertas de calidad
    create_alert = fields.Boolean(string='Crear alerta de calidad', default=True)
    alert_name = fields.Char(string='Título de alerta', default='Novedad en operación')
    quality_team_id = fields.Many2one('quality.alert.team', string='Equipo de calidad', default=lambda self: self.env['quality.alert.team'].search([('name','=','Producción')], limit=1).id)
    
    quality_main_cause_id = fields.Many2one('quality.reason', string='Causa principal')
    quality_tag_ids = fields.Many2many('quality.tag', string='Etiquetas')
    description = fields.Text(string='Descripción / Observaciones')

    # Desechos
    producto_desecho_tipo = fields.Selection([
        ('mp', 'Materia prima (insumo de la MO)'),
        ('pf', 'Producto terminado'),
    ], string='Qué desea desechar', default='pf')
    create_scrap = fields.Boolean(string='Crear desecho (scrap)', default=False)

    product_to_scrap_id = fields.Many2one('product.product', string='Producto a desechar')
    allowed_product_ids = fields.Many2many('product.product', string='Productos permitidos', compute='_compute_allowed_products', store=False)
    
    @api.depends('production_id','producto_desecho_tipo','workorder_id')
    def _compute_allowed_products(self):
        for wizard in self:
            products = self.env['product.product'].browse()
            mo = wizard.production_id
            if isinstance(mo, models.BaseModel):
                mo = mo.exists()
            else:
                mo = False
            if not mo:
                wo = wizard.workorder_id
                if isinstance(wo, models.BaseModel):
                    wo = wo.exists()
                else:
                    wo = False
                mo = wo.production_id.exists() if wo else False
            if mo:
                if wizard.producto_desecho_tipo == 'pf':
                    if isinstance(mo.product_id, models.BaseModel):
                        products |= mo.product_id.exists()
                else:
                    products |= mo.move_raw_ids.mapped('product_id')
            wizard.allowed_product_ids = products
    qty_scrap = fields.Float(string='Cantidad a desechar', default=1.0)
    uom_id = fields.Many2one('uom.uom', string='UdM', compute='_compute_uom', store=False)
    location_id = fields.Many2one('stock.location', string='Ubicación de origen',
                                  default=lambda self: self.env['stock.location'].search([('complete_name','=','WH/PREPRODUCCION')], limit=1).id)
    scrap_location_id = fields.Many2one('stock.location', string='Ubicación de desecho',
                                        domain=[('scrap_location','=',True)],
                                        default=lambda self: self.env['stock.location'].search([('complete_name','=','Virtual Locations/Desecho Producción')], limit=1).id)

    @api.depends('production_id')
    def _compute_product_finished(self):
        for w in self:
            production = w.production_id
            if not isinstance(production, models.BaseModel):
                w.product_finished_id = False
                continue
            production = production.exists()
            if not production:
                w.product_finished_id = False
                continue
            product = production.product_id
            if not isinstance(product, models.BaseModel):
                w.product_finished_id = False
                continue
            product = product.exists()
            w.product_finished_id = product if product else False

    @api.onchange('producto_desecho_tipo', 'production_id')
    def _onchange_product_to_scrap_domain(self):
        self._sanitize_m2o()
        return {'domain': {'product_to_scrap_id': [('id', 'in', self.allowed_product_ids.ids)]}}

    @api.onchange('workorder_id', 'production_id', 'product_to_scrap_id')
    def _onchange_sanitize(self):
        self._sanitize_m2o()
    @api.onchange('quality_tag_ids')
    def _onchange_quality_tag_ids_autoscrap(self):
        # Si alguna etiqueta tiene x_studio_perdida_total, habilitar el desecho automáticamente
        has_flag = any(getattr(tag, 'x_studio_perdida_total', False) for tag in (self.quality_tag_ids or self.env['quality.tag']))
        if has_flag:
            self.create_scrap = True

    @api.depends('product_to_scrap_id', 'production_id', 'producto_desecho_tipo')
    def _compute_uom(self):
        for w in self:
            prod = w.product_to_scrap_id
            if isinstance(prod, models.BaseModel):
                prod = prod.exists()
            else:
                prod = False
            if not prod and w.producto_desecho_tipo == 'pf':
                production = w.production_id
                if isinstance(production, models.BaseModel):
                    production = production.exists()
                else:
                    production = False
                if production and isinstance(production.product_id, models.BaseModel):
                    prod = production.product_id.exists()
            if prod and isinstance(prod.uom_id, models.BaseModel):
                w.uom_id = prod.uom_id.exists()
            else:
                w.uom_id = False

    def _action_confirm_internal(self, force_create_scrap=None):
        # Validación estricta: no permitir alerta sin etiquetas
        if self.create_alert and (not self.quality_tag_ids or len(self.quality_tag_ids) == 0):
            raise UserError(_('Debe seleccionar al menos una etiqueta de calidad para crear la alerta.'))

        self.ensure_one()
        mo = self.production_id
        wo = self.workorder_id

        # Validaciones
        create_scrap_flag = self.create_scrap if force_create_scrap is None else bool(force_create_scrap)
        if create_scrap_flag:
            if not self.quality_tag_ids:
                raise UserError(_('Debe seleccionar al menos una etiqueta.'))
            if not self.product_to_scrap_id and not (self.producto_desecho_tipo == 'pf' and mo and mo.product_id):
                raise UserError(_('Seleccione el producto a desechar.'))
            if self.qty_scrap <= 0:
                raise UserError(_('La cantidad a desechar debe ser mayor que cero.'))
            if not self.location_id or not self.scrap_location_id:
                raise UserError(_('Debe indicar ubicaciones de origen y desecho.'))

        # Crear alerta
        if self.create_alert:
            vals = {
                'team_id': self.quality_team_id.id if self.quality_team_id else False,
                'description': self.description or '',
            }
            if 'product_id' in self.env['quality.alert']._fields and mo and mo.product_id:
                vals['product_id'] = mo.product_id.id
            if 'workorder_id' in self.env['quality.alert']._fields and wo:
                vals['workorder_id'] = wo.id
            if 'production_id' in self.env['quality.alert']._fields and mo:
                vals['production_id'] = mo.id
            if 'root_cause_id' in self.env['quality.alert']._fields and hasattr(self, 'quality_main_cause_id') and self.quality_main_cause_id:
                vals['root_cause_id'] = self.quality_main_cause_id.id
            alert = self.env['quality.alert'].with_context(from_wo_novedades=True, mo_id=(mo.id if mo else False), wo_id=(wo.id if wo else False)).create(vals)
            if self.quality_tag_ids and 'tag_ids' in self.env['quality.alert']._fields:
                alert.write({'tag_ids': [(6, 0, self.quality_tag_ids.ids)]})

        # Gate booleano x_studio_perdida_total
        flag_ok = True
        perdida_field = 'x_studio_perdida_total'
        if wo and perdida_field in wo._fields:
            flag_ok = bool(wo[perdida_field])
        elif mo and perdida_field in mo._fields:
            flag_ok = bool(mo[perdida_field])

        create_scrap_flag = self.create_scrap if force_create_scrap is None else bool(force_create_scrap)
        if create_scrap_flag:
            if not flag_ok:
                raise UserError(_('No está habilitado el desecho para esta orden (active la opción de pérdida total).'))
            product = self.product_to_scrap_id or (self.producto_desecho_tipo == 'pf' and mo and mo.product_id) or False
            scrap_vals = {
                'product_id': product.id,
                'scrap_qty': self.qty_scrap,
                'product_uom_id': product.uom_id.id,
                'origin': '%s - %s' % ((mo.name or '') if mo else '', (wo.name or '') if wo else ''),
                'location_id': self.location_id.id if self.location_id else self.env['stock.location'].search([('complete_name','=','WH/PREPRODUCCION')], limit=1).id,
                'scrap_location_id': (self.scrap_location_id.id if self.scrap_location_id else self.env['stock.location'].search([('complete_name','=','Virtual Locations/Desecho Producción')], limit=1).id),
            }
            for fname, val in [('production_id', mo.id if mo else False), ('workorder_id', wo.id if wo else False)]:
                if fname in self.env['stock.scrap']._fields and val:
                    scrap_vals[fname] = val

            # Campos Studio (opcionales)
            if 'x_studio_origen' in self.env['stock.scrap']._fields and mo:
                scrap_vals['x_studio_origen'] = mo.origin or ''
            if 'x_studio_pedido_original' in self.env['stock.scrap']._fields and mo and hasattr(mo, 'x_studio_pedido_original'):
                scrap_vals['x_studio_pedido_original'] = mo.x_studio_pedido_original


            scrap = self.env['stock.scrap'].create(scrap_vals)
            if getattr(scrap, 'state', '') == 'draft':
                scrap.action_validate()

        
        create_scrap_flag = self.create_scrap if force_create_scrap is None else bool(force_create_scrap)
        if create_scrap_flag:
            if not self.quality_tag_ids:
                raise UserError(_('Debe seleccionar al menos una etiqueta.'))
            if not self.product_to_scrap_id:
                raise UserError(_('Seleccione el producto a desechar.'))
            if self.qty_scrap <= 0:
                raise UserError(_('La cantidad a desechar debe ser mayor que cero.'))
        return {'type': 'ir.actions.act_window_close'}

    def action_confirm(self):
        self.ensure_one()
        # If a product is selected to scrap but the checkbox is not enabled, ask user.
        if self.product_to_scrap_id and not self.create_scrap:
            msg = _('¿Desea desechar el producto %s?\nUd ha seleccionado un producto a desechar pero no activó la opción de desechar.') % (self.product_to_scrap_id.display_name,)
            return {
                'type': 'ir.actions.act_window',
                'res_model': 'alterben.workorder.scrap.confirm.wizard',
                'view_mode': 'form',
                'target': 'new',
                'context': {
                    'default_message': msg,
                    'active_novedades_id': self.id,
                }
            }
        # Normal behavior
        return self._action_confirm_internal()
