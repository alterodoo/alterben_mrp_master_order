# -*- coding: utf-8 -*-
from odoo import api, fields, models, _


class StockReturnPickingLine(models.TransientModel):
    _inherit = "stock.return.picking.line"

    x_selected = fields.Boolean(string="Seleccionar", default=False)


class StockReturnPicking(models.TransientModel):
    _inherit = "stock.return.picking"

    x_total_qty_selected = fields.Float(
        string="Total a devolver (seleccionados)",
        compute="_compute_total_qty_selected",
        digits="Product Unit of Measure",
        store=False,
    )

    @api.depends("product_return_moves.x_selected", "product_return_moves.quantity")
    def _compute_total_qty_selected(self):
        for wizard in self:
            total = 0.0
            for line in wizard.product_return_moves:
                if line.x_selected and (line.quantity or 0.0) > 0:
                    total += line.quantity
            wizard.x_total_qty_selected = total

    def action_select_all(self):
        action = None
        for wizard in self:
            if wizard.product_return_moves:
                wizard.product_return_moves.write({"x_selected": True})
            if action is None:
                action = wizard._action_reload_wizard()
        return action or True

    def action_unselect_all(self):
        action = None
        for wizard in self:
            if wizard.product_return_moves:
                wizard.product_return_moves.write({"x_selected": False})
            if action is None:
                action = wizard._action_reload_wizard()
        return action or True

    def action_delete_selected(self):
        action = None
        for wizard in self:
            selected = wizard.product_return_moves.filtered(lambda l: l.x_selected)
            if selected:
                selected.unlink()
            if action is None:
                action = wizard._action_reload_wizard()
        return action or True

    def action_keep_only_selected(self):
        action = None
        for wizard in self:
            to_remove = wizard.product_return_moves.filtered(lambda l: not l.x_selected)
            if to_remove:
                to_remove.unlink()
            if action is None:
                action = wizard._action_reload_wizard()
        return action or True

    def _action_reload_wizard(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Revertir traslado"),
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }
