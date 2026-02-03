from odoo import models, fields, api, _

class StockMove(models.Model):
    _inherit = "stock.move"

    ct_fully_labeled = fields.Boolean(string="Etiquetas asignadas", compute="_compute_ct_fully_labeled")
    ct_label_range = fields.Char(string="Rango de etiquetas", compute="_compute_ct_label_range")
    ct_pre_from = fields.Char(string="Pre-asignado desde", copy=False)
    ct_pre_to = fields.Char(string="Pre-asignado hasta", copy=False)

    @api.depends("move_line_ids.quantity", "move_line_ids.control_total_label_ids")
    def _compute_ct_fully_labeled(self):
        for move in self:
            qty = int(sum(ml.quantity or 0 for ml in move.move_line_ids))
            labels = sum(len(ml.control_total_label_ids) for ml in move.move_line_ids)
            move.ct_fully_labeled = bool(qty) and labels >= qty

    def action_open_control_total_move_wizard(self):
        self.ensure_one()
        return {
            "name": _("Asignar Control Total"),
            "type": "ir.actions.act_window",
            "res_model": "assign.control.total.wizard",
            "view_mode": "form",
            "view_id": self.env.ref("alterben_mrp_master_order.view_assign_control_total_wizard_form").id,
            "target": "new",
            "context": {
                "default_picking_id": self.picking_id.id,
                "default_move_id": self.id,
            },
        }

    @api.depends("picking_id", "product_id", "ct_pre_from", "ct_pre_to")
    def _compute_ct_label_range(self):
        Label = self.env['control.total.label']
        import re

        def split_code(code):
            m = re.match(r'^(?P<prefix>\D*?)(?P<num>\d+)$', code or '')
            if m:
                return (m.group('prefix') or ''), int(m.group('num'))
            return (code or ''), None

        for move in self:
            move.ct_label_range = False
            if not move.picking_id or not move.product_id:
                continue
            existing = Label.search([
                ('picking_id', '=', move.picking_id.id),
                ('product_id', '=', move.product_id.id),
                ('active', '=', True),
            ], order='name asc')
            if not existing:
                # fallback by product template if needed
                existing = Label.search([
                    ('picking_id', '=', move.picking_id.id),
                    ('product_id.product_tmpl_id', '=', move.product_id.product_tmpl_id.id),
                    ('active', '=', True),
                ], order='name asc')
            nums = []
            pf_common = None
            for rec in existing:
                pf, nn = split_code(rec.name)
                if nn is None:
                    continue
                if pf_common is None:
                    pf_common = pf
                if pf == pf_common:
                    nums.append(nn)
            if nums and pf_common is not None:
                move.ct_label_range = f"{pf_common}{min(nums)} -> {pf_common}{max(nums)}"
            else:
                # No etiquetas asignadas: mostrar pre-asignación si existe
                if move.ct_pre_from and move.ct_pre_to:
                    move.ct_label_range = f"{move.ct_pre_from} -> {move.ct_pre_to}"
