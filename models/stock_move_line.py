from odoo import api, fields, models

class StockMoveLine(models.Model):
    _inherit = "stock.move.line"

    x_control_total_range = fields.Char(string="Control Total (rango)", copy=False, readonly=True)
    control_total_label_ids = fields.One2many("control.total.label", "move_line_id", string="Etiquetas")

    def _recompute_control_total_range(self):
        for line in self:
            codes = sorted([l.name for l in line.control_total_label_ids if l.active and l.name])
            if not codes:
                line.x_control_total_range = False
            elif len(codes) == 1:
                line.x_control_total_range = codes[0]
            else:
                line.x_control_total_range = f"{codes[0]} -> {codes[-1]}"

    def action_open_control_total_line_wizard(self):
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
                "default_move_line_id": self.id,
                "dialog_size": "large",
            },
        }
