from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class PvbCabinaInvWizard(models.TransientModel):
    _name = "pvb.cabina.inv.wizard"
    _description = "Consumo de piezas PVB desde cabina (INV)"

    line_id = fields.Many2one("mrp.master.order.line", string="Línea de Corte PVB", required=True)
    product_display = fields.Char("Producto", compute="_compute_product_display", store=False)
    product_code = fields.Char("Código", compute="_compute_product_display", store=False)
    consume_line_ids = fields.One2many("pvb.cabina.inv.wizard.line", "wizard_id", string="Consumos")

    @api.depends("line_id", "line_id.product_id", "line_id.product_id.default_code", "line_id.product_id.display_name")
    def _compute_product_display(self):
        for wiz in self:
            prod = wiz.line_id.product_id
            wiz.product_display = prod.display_name if prod else False
            wiz.product_code = prod.default_code if prod else False

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        Receta = self.env["receta.pvb"]

        # Get the source line and its area
        source_area = 0.0
        line_id = self.env.context.get('default_line_id')
        if line_id:
            source_line = self.env['mrp.master.order.line'].browse(line_id)
            if source_line and source_line.product_id:
                # Find the recipe for the source product to get its area
                source_recipe = self.env['receta.pvb'].get_by_product(source_line.product_id)
                if source_recipe and 'area_pvb_m2' in source_recipe:
                    source_area = source_recipe.area_pvb_m2 or 0.0

        # New domain with area validation
        domain = [
            ("piezas_cabina", ">", 0.0),
            ("area_pvb_m2", ">=", source_area),
        ]
        
        recipes = Receta.search(domain)
        lines_vals = []
        for rec in recipes:
            lines_vals.append((0, 0, {
                "recipe_id": rec.id,
                "product_id": rec.product_id.id if rec.product_id else False,
                "available_qty": rec.piezas_cabina,
                "consume_qty": 0.0,
            }))
        if "consume_line_ids" in fields_list:
            res["consume_line_ids"] = lines_vals
        return res

    def action_confirm(self):
        self.ensure_one()
        line = self.line_id
        if not line:
            raise ValidationError(_("No se encontró la línea de corte PVB."))
        moves = []
        for c in self.consume_line_ids:
            if not c.consume_qty or c.consume_qty <= 0:
                continue
            if c.consume_qty > (c.available_qty or 0.0):
                raise ValidationError(_("La cantidad a consumir (%s) supera el disponible (%s) para %s.") % (
                    c.consume_qty, c.available_qty, c.recipe_id.display_name))
            rec = c.recipe_id
            if not rec:
                continue
            rec._apply_cabina_delta(-c.consume_qty, reason="inv", note=f"INV desde {line.display_name}", master_line=line, inv_details=c._inv_detail_text())
            detail = c._inv_detail_text()
            if detail:
                moves.append(detail)
        if moves:
            detail_txt = ", ".join(moves)
            line.write({
                "pvb_inv_details": detail_txt,
                "pvb_cortado_text": detail_txt,
                "cantidad_piezas_text": detail_txt,
                "pvb_inv_pending": False,
                "pvb_cortado_qty": 0.0,
                "cantidad_piezas": 0.0,
            })
            line._compute_sobrante_pvb()
        return {"type": "ir.actions.act_window_close"}


class PvbCabinaInvWizardLine(models.TransientModel):
    _name = "pvb.cabina.inv.wizard.line"
    _description = "Detalle de consumo INV desde cabina"

    wizard_id = fields.Many2one("pvb.cabina.inv.wizard", required=True, ondelete="cascade")
    recipe_id = fields.Many2one("receta.pvb", string="Receta", required=True)
    product_id = fields.Many2one("product.product", string="Producto", readonly=True)
    available_qty = fields.Float("Disponible", digits=(16, 1), readonly=True)
    consume_qty = fields.Float("Consumir", digits=(16, 1), default=0.0)

    def _inv_detail_text(self):
        self.ensure_one()
        label = self.recipe_id.mold_code or self.recipe_id.product_default_code or (self.product_id.default_code if self.product_id else False) or _("SIN MOLDE")
        return f"{label}->{self.consume_qty}"
