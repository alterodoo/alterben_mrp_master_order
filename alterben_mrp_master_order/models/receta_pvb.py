from odoo import models, fields, api


class RecetaPVB(models.Model):
    _name = "receta.pvb"
    _description = "Receta PVB"
    _order = "mold_code"

    active = fields.Boolean(default=True)
    mold_code = fields.Char("Molde")
    product_id = fields.Many2one(
        "product.product",
        string="Producto",
        required=False,
        options="{'no_create': True, 'no_create_edit': True}",
    )
    product_default_code = fields.Char("Referencia del producto")
    alto = fields.Float("Alto")
    ancho = fields.Float("Ancho")
    area_m2 = fields.Float("Area (m2)")
    v1 = fields.Integer("V1")
    v2 = fields.Integer("V2")
    c1 = fields.Char("C1")
    c2 = fields.Char("C2")
    ficha = fields.Char("Ficha")
    num_pvb = fields.Char("Num PVB")
    pvb = fields.Char("PVB")
    ancho_rollo = fields.Float("Ancho del Rollo")
    longitud_corte = fields.Float("Longitud de corte")
    ancho_pvb = fields.Float("Ancho PVB")
    area_pvb_m2 = fields.Float("Area PVB (m2)")
    existencia_minima = fields.Float("Existencia mínima")
    existencia_maxima = fields.Float("Existencia máxima")
    cant_moldes = fields.Float("Cant. moldes")
    piezas_cabina = fields.Float("Piezas en cabina", digits=(16, 1), default=0.0)
    cabina_move_ids = fields.One2many("receta.pvb.cabina.move", "recipe_id", string="Movimientos cabina")

    def name_get(self):
        res = []
        for rec in self:
            prod_name = rec.product_id.display_name if rec.product_id else (rec.product_default_code or '')
            mold = rec.mold_code or ''
            label = f"{mold} — {prod_name}" if mold else prod_name
            res.append((rec.id, label))
        return res

    @staticmethod
    def _find_product_by_code(env, code):
        if not code:
            return False
        Product = env["product.product"]
        return Product.search([("default_code", "=", code)], limit=1)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("product_id") and vals.get("product_default_code"):
                prod = self._find_product_by_code(self.env, vals.get("product_default_code"))
                if prod:
                    vals["product_id"] = prod.id
        return super().create(vals_list)

    def write(self, vals):
        if vals.get("product_default_code") and not vals.get("product_id"):
            prod = self._find_product_by_code(self.env, vals.get("product_default_code"))
            if prod:
                vals["product_id"] = prod.id

        # Log de ajustes manuales en piezas_cabina
        track = {}
        if "piezas_cabina" in vals and not self.env.context.get("cabina_skip_log"):
            track = {rec.id: rec.piezas_cabina for rec in self}

        res = super().write(vals)

        if "piezas_cabina" in vals and not self.env.context.get("cabina_skip_log"):
            Move = self.env["receta.pvb.cabina.move"]
            for rec in self:
                old_qty = track.get(rec.id, 0.0)
                new_qty = rec.piezas_cabina
                if abs(new_qty - old_qty) > 0.00001:
                    Move.create({
                        "recipe_id": rec.id,
                        "product_id": rec.product_id.id if rec.product_id else False,
                        "qty": new_qty - old_qty,
                        "balance": new_qty,
                        "reason": "ajuste",
                        "note": "Ajuste manual en Receta PVB",
                    })
        return res

    @api.model
    def get_by_product(self, product):
        if not product:
            return self.browse()
        rec = self.search([("product_id", "=", product.id)], limit=1)
        if rec:
            return rec
        if getattr(product, "default_code", False):
            rec = self.search([("product_default_code", "=", product.default_code)], limit=1)
        return rec

    def _apply_cabina_delta(self, delta, reason, note=None, master_line=None, production=None, workorder=None, inv_details=None):
        """Actualiza piezas en cabina y registra movimiento."""
        if not delta:
            return
        Move = self.env["receta.pvb.cabina.move"]
        for rec in self:
            new_qty = (rec.piezas_cabina or 0.0) + delta
            rec.with_context(cabina_skip_log=True).write({"piezas_cabina": new_qty})
            Move.create({
                "recipe_id": rec.id,
                "product_id": rec.product_id.id if rec.product_id else False,
                "qty": delta,
                "balance": new_qty,
                "reason": reason,
                "note": note or False,
                "master_order_line_id": master_line.id if master_line else False,
                "production_id": production.id if production else False,
                "workorder_id": workorder.id if workorder else False,
                "inv_details": inv_details or False,
            })

    def action_open_cabina_history(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Movimientos cabina",
            "res_model": "receta.pvb.cabina.move",
            "view_mode": "tree",
            "domain": [("recipe_id", "=", self.id)],
            "context": {"search_default_filter_recipe": self.id},
            "target": "current",
        }


class RecetaPvbCabinaMove(models.Model):
    _name = "receta.pvb.cabina.move"
    _description = "Movimientos de piezas en cabina PVB"
    _order = "create_date desc, id desc"

    recipe_id = fields.Many2one("receta.pvb", string="Receta", required=True, ondelete="cascade")
    product_id = fields.Many2one("product.product", string="Producto", readonly=True)
    qty = fields.Float("Cantidad", digits=(16, 1))
    balance = fields.Float("Balance", digits=(16, 1))
    reason = fields.Selection([
        ("corte", "Corte PVB"),
        ("ensamblado", "Ensamblado"),
        ("inv", "Consumo INV"),
        ("ajuste", "Ajuste manual"),
    ], string="Motivo", default="corte", required=True)
    note = fields.Char("Detalle")
    master_order_line_id = fields.Many2one("mrp.master.order.line", string="Línea OM")
    production_id = fields.Many2one("mrp.production", string="MO")
    workorder_id = fields.Many2one("mrp.workorder", string="WO")
    inv_details = fields.Char("Detalle INV")
