# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class MrpAddOpenMoWizard(models.TransientModel):
    _name = "mrp.add.open.mo.wizard"
    _description = "Agregar MOs abiertas a OPT"

    master_id = fields.Many2one("mrp.master.order", string="Orden Maestra", required=True)
    tab = fields.Selection([
        ("ensamblado", "Ensamblado"),
        ("prevaciado", "Prevaciado"),
        ("inspeccion_final", "Inspeccion Final"),
    ], string="Pestana", required=True)
    product_id = fields.Many2one("product.product", string="Producto")
    production_ids = fields.Many2many("mrp.production", string="MOs abiertas")

    def _get_open_mo_domain(self):
        ctx = self.env.context or {}
        domain = [
            ("state", "not in", ("done", "cancel")),
        ]
        allowed_categ_ids = ctx.get("allowed_categ_ids", [])
        if allowed_categ_ids:
            domain.append(("product_id.categ_id", "child_of", allowed_categ_ids))
        if self.product_id:
            domain.append(("product_id", "=", self.product_id.id))
        return domain

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        ctx = self.env.context or {}
        if ctx.get("default_master_id") and "master_id" in fields_list:
            res["master_id"] = ctx.get("default_master_id")
        if ctx.get("default_tab") and "tab" in fields_list:
            res["tab"] = ctx.get("default_tab")
        return res

    @api.onchange("product_id")
    def _onchange_product_id(self):
        domain = self._get_open_mo_domain()
        return {"domain": {"production_ids": domain}}

    def action_apply(self):
        self.ensure_one()
        master = self.master_id
        tab = self.tab
        if not master or not tab:
            return {"type": "ir.actions.act_window_close"}

        if tab == "ensamblado":
            target_field = "master_id_ensamblado"
            lines = getattr(master, "line_ids_ensamblado", self.env["mrp.master.order.line"])
        elif tab == "prevaciado":
            target_field = "master_id_prevaciado"
            lines = getattr(master, "line_ids_prevaciado", self.env["mrp.master.order.line"])
        else:
            target_field = "master_id_inspeccion_final"
            lines = getattr(master, "line_ids_inspeccion_final", self.env["mrp.master.order.line"])

        existing_prod_ids = set(lines.mapped("production_id").ids)
        skipped = []
        for prod in self.production_ids:
            if not prod or prod.id in existing_prod_ids or prod.state in ("done", "cancel"):
                if prod and prod.id in existing_prod_ids:
                    skipped.append(prod.name)
                continue
            pedido_id = False
            if prod and "x_studio_pedido_original" in prod._fields:
                po_name = (prod.x_studio_pedido_original or "").strip()
                if po_name:
                    Pedido = self.env["mrp.pedido.original"].sudo()
                    pedido = Pedido.search([("name", "=", po_name)], limit=1)
                    if not pedido and po_name.startswith("PED-"):
                        pedido = Pedido.create({"name": po_name})
                    pedido_id = pedido.id if pedido else False
            origin_before = (prod.origin or "").strip()
            vals = {
                "product_id": prod.product_id.id,
                "product_qty": prod.product_qty or 0.0,
                "production_id": prod.id,
                "pedido_original_id": pedido_id,
                target_field: master.id,
                "master_id": False,
                "added_from_open_mo": True,
                "origin_before_add": origin_before,
            }
            self.env["mrp.master.order.line"].with_context(default_master_id=False).create(vals)
            # Actualizar origen de la MO agregando la OPT actual para trazabilidad.
            if prod:
                try:
                    current = (prod.origin or "").strip()
                    parts = [p.strip() for p in current.split("/") if p.strip()]
                    if master.name and (master.name not in parts):
                        parts.append(master.name)
                        prod.write({"origin": "/".join(parts) if parts else master.name})
                except Exception:
                    pass

        if skipped:
            raise UserError(
                "MO ya existe en esta pestaña: %s" % ", ".join(sorted(set(skipped)))
            )
        return {"type": "ir.actions.act_window_close"}
