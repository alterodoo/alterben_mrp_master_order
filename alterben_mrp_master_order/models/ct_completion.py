
from odoo import api, fields, models, _

class StockMove(models.Model):
    _inherit = 'stock.move'

    ct_complete_move = fields.Boolean(string='Etiquetas asignadas', compute='_compute_ct_complete_move', store=False)

    @api.depends('product_id', 'product_uom_qty', 'picking_id')
    def _compute_ct_complete_move(self):
        Label = self.env['control.total.label']
        for move in self:
            count = 0
            if move.picking_id and move.product_id:
                count = Label.search_count([('picking_id','=', move.picking_id.id),
                                            ('product_id','=', move.product_id.id),
                                            ('active','=', True)])
            need = int(move.product_uom_qty or 0)
            move.ct_complete_move = bool(need > 0 and count >= need)

class StockPicking(models.Model):
    _inherit = 'stock.picking'

    ct_complete_picking = fields.Boolean(string='Etiquetas asignadas', compute='_compute_ct_complete_picking', store=False)

    @api.depends('move_ids_without_package.product_id', 'move_ids_without_package.product_uom_qty')
    def _compute_ct_complete_picking(self):
        Label = self.env['control.total.label']
        for picking in self:
            complete = True
            for mv in picking.move_ids_without_package:
                if not mv.product_id:
                    continue
                need = int(mv.product_uom_qty or 0)
                if need <= 0:
                    continue
                cnt = Label.search_count([('picking_id','=', picking.id),
                                          ('product_id','=', mv.product_id.id),
                                          ('active','=', True)])
                if cnt < need:
                    complete = False
                    break
            picking.ct_complete_picking = complete


class StockPickingBanner(models.Model):
    _inherit = 'stock.picking'

    ct_banner_html = fields.Html(string='CT Banner', compute='_compute_ct_banner', store=False)

    def _compute_ct_banner(self):
        for p in self:
            if p.ct_complete_picking:
                p.ct_banner_html = "<div class='alert alert-success' role='alert' style='margin-bottom:8px;'>\u2705 Etiquetas asignadas</div>"
            else:
                p.ct_banner_html = "<div class='alert alert-danger' role='alert' style='margin-bottom:8px;'>\u26A0\ufe0f Pendiente de etiquetas de Seguro Control Total</div>"
