from odoo import api, fields, models

class ControlTotalLabel(models.Model):
    _name = "control.total.label"
    _description = "Etiqueta de Control Total (unidad)"
    _order = "create_date desc"

    name = fields.Char("Código de etiqueta", required=True, index=True)
    active = fields.Boolean(default=True)
    inactive_reason = fields.Selection([
        ("roto_despacho", "Roto en despacho"),
        ("roto_instalacion", "Roto en instalación"),
        ("roto_transporte", "Roto en transporte"),
        ("producto_falla", "Producto con falla"),
        ("seguro_aplicado", "Seguro aplicado"),
        ("inconsistencias_seguro", "Inconsistencias en la presentación del seguro"),
    ], string="Razón de inactivación")

    partner_id = fields.Many2one("res.partner", string="Cliente", index=True)
    picking_id = fields.Many2one("stock.picking", string="Despacho", index=True)
    master_order_id = fields.Many2one("mrp.master.order", string="Orden Maestra", index=True)
    master_order_line_id = fields.Many2one("mrp.master.order.line", string="Línea de Orden Maestra", index=True)
    move_line_id = fields.Many2one("stock.move.line", string="Línea de movimiento", index=True)
    product_id = fields.Many2one("product.product", string="Producto", index=True)
    date_dispatch = fields.Datetime("Fecha de instalación", default=fields.Datetime.now)
    note = fields.Char("Nota")

    document_reference = fields.Char(
        string='Documento',
        compute='_compute_document_reference',
        store=True
    )

    @api.depends('picking_id', 'master_order_id', 'master_order_line_id', 
                 'picking_id.name', 'master_order_id.name', 'master_order_line_id.display_name')
    def _compute_document_reference(self):
        for record in self:
            if record.picking_id:
                record.document_reference = f"Despacho: {record.picking_id.name}"
            elif record.master_order_line_id and record.master_order_line_id.production_id:
                # Mostrar el número de orden de fabricación si está disponible
                record.document_reference = f"Orden de fabricación: {record.master_order_line_id.production_id.name}"
            elif record.master_order_line_id:
                # Si no hay orden de fabricación, mostrar el ID de la línea
                record.document_reference = f"Línea: {record.master_order_line_id.display_name}"
            elif record.master_order_id:
                record.document_reference = f"Orden: {record.master_order_id.name}"
            else:
                record.document_reference = ""

    _sql_constraints = [
        ("uniq_name", "unique(name)", "El código de etiqueta debe ser único."),
    ]

    def write(self, vals):
        res = super().write(vals)
        if 'active' in vals:
            # Actualizar órdenes maestras relacionadas
            master_orders = self.env['mrp.master.order']
            for label in self:
                if label.master_order_id:
                    master_orders |= label.master_order_id
            if master_orders:
                master_orders._compute_ct_complete_master()
        return res