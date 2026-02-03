# -*- coding: utf-8 -*-
from ast import literal_eval
import unicodedata
from odoo import api, fields, models
from odoo.exceptions import UserError


class MrpWorkorder(models.Model):
    _inherit = 'mrp.workorder'

    pvb_cortado_qty_wo = fields.Float(
        string="PVB cortado",
        compute="_compute_pvb_corte_fields",
        inverse="_inverse_pvb_cortado_qty_wo",
        store=False,
        readonly=False,
        digits=(16, 1),
    )
    pvb_corte_note = fields.Char(
        string="Notas",
        compute="_compute_pvb_corte_fields",
        inverse="_inverse_pvb_corte_note",
        store=False,
        readonly=False,
    )
    is_pvb_corte = fields.Boolean(
        compute="_compute_is_pvb_corte",
        store=False,
    )

    opt_qty_ensamblar = fields.Float(
        string="Cant. Ens",
        compute="_compute_opt_stage_qtys",
        inverse="_inverse_opt_qty_ensamblar",
        store=False,
        readonly=False,
    )
    opt_qty_prevaciar = fields.Float(
        string="Cant. Prev.",
        compute="_compute_opt_stage_qtys",
        inverse="_inverse_opt_qty_prevaciar",
        store=False,
        readonly=False,
    )
    opt_qty_liberar = fields.Float(
        string="Cant. Lib",
        compute="_compute_opt_stage_qtys",
        inverse="_inverse_opt_qty_liberar",
        store=False,
        readonly=False,
    )
    qty_producing_wo = fields.Float(
        string="Qty Producing",
        compute="_compute_qty_producing_wo",
        store=False,
    )
    can_edit_opt_ensamblado = fields.Boolean(
        compute="_compute_opt_edit_permissions",
        store=False,
    )
    can_edit_opt_prevaciado = fields.Boolean(
        compute="_compute_opt_edit_permissions",
        store=False,
    )
    can_edit_opt_inspeccion = fields.Boolean(
        compute="_compute_opt_edit_permissions",
        store=False,
    )

    def _get_opt_stage_key(self):
        """Infer stage from operation/workcenter name to keep backward compatibility."""
        name = (self.operation_id.name if getattr(self, "operation_id", False) else False) or (
            self.workcenter_id.name if self.workcenter_id else ""
        )
        name = (name or "").strip()
        name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
        name = name.lower()
        if "ensambl" in name:
            return "ensamblado"
        if "prevaciado" in name or "autoclave" in name or "laminad" in name:
            return "prevaciado"
        if "inspeccion" in name:
            return "inspeccion_final"
        return False

    def _is_pvb_corte_operation(self):
        name = (self.operation_id.name if getattr(self, "operation_id", False) else False) or (
            self.workcenter_id.name if self.workcenter_id else ""
        )
        return (name or "").strip() == "Corte de PVB"

    @api.depends("operation_id", "workcenter_id")
    def _compute_is_pvb_corte(self):
        for wo in self:
            wo.is_pvb_corte = wo._is_pvb_corte_operation()

    def _get_corte_line(self):
        Line = self.env["mrp.master.order.line"]
        production = self.production_id
        if not production:
            return Line
        return Line.search([
            ("production_id", "=", production.id),
            ("master_id_corte", "!=", False),
        ], limit=1)

    @api.depends("production_id", "operation_id", "workcenter_id")
    def _compute_pvb_corte_fields(self):
        for wo in self:
            if not wo._is_pvb_corte_operation():
                wo.pvb_cortado_qty_wo = 0.0
                wo.pvb_corte_note = False
                continue
            line = wo._get_corte_line()
            if line:
                qty = line.pvb_cortado_qty
                if qty is None or abs(qty or 0.0) < 0.00001:
                    qty = line.cantidad_piezas or 0.0
                wo.pvb_cortado_qty_wo = qty or 0.0
                wo.pvb_corte_note = line.note or False
            else:
                wo.pvb_cortado_qty_wo = 0.0
                wo.pvb_corte_note = False

    def _inverse_pvb_cortado_qty_wo(self):
        for wo in self:
            if not wo._is_pvb_corte_operation():
                continue
            line = wo._get_corte_line()
            if not line:
                continue
            qty = wo.pvb_cortado_qty_wo or 0.0
            vals = {"pvb_cortado_qty": qty}
            try:
                vals["pvb_cortado_text"] = line._format_qty_display(qty)
            except Exception:
                pass
            line.write(vals)

    def _inverse_pvb_corte_note(self):
        for wo in self:
            if not wo._is_pvb_corte_operation():
                continue
            line = wo._get_corte_line()
            if not line:
                continue
            line.write({"note": wo.pvb_corte_note or False})

    def _get_stage_from_line(self, line):
        if not line:
            return False
        if getattr(line, "master_id_ensamblado", False):
            return "ensamblado"
        if getattr(line, "master_id_prevaciado", False):
            return "prevaciado"
        if getattr(line, "master_id_inspeccion_final", False):
            return "inspeccion_final"
        return False

    def _compute_qty_producing_wo(self):
        for wo in self:
            qty = 0.0
            if hasattr(wo, "qty_producing"):
                qty = wo.qty_producing or 0.0
            elif hasattr(wo, "qty_produced"):
                qty = wo.qty_produced or 0.0
            wo.qty_producing_wo = qty

    @api.depends("production_id")
    def _compute_opt_stage_qtys(self):
        Line = self.env["mrp.master.order.line"]
        prods = self.mapped("production_id").filtered(lambda p: p)
        by_prod = {prod.id: {"ens": 0.0, "prev": 0.0, "lib": 0.0} for prod in prods}
        if prods:
            lines = Line.search([("production_id", "in", prods.ids)])
            for line in lines:
                prod_id = line.production_id.id if line.production_id else False
                if not prod_id:
                    continue
                if line.master_id_ensamblado:
                    by_prod[prod_id]["ens"] = line.cantidad_ensamblada or 0.0
                if line.master_id_prevaciado:
                    by_prod[prod_id]["prev"] = line.qty_to_prevaciar or 0.0
                if line.master_id_inspeccion_final:
                    by_prod[prod_id]["lib"] = line.qty_to_liberar or 0.0
        for wo in self:
            data = by_prod.get(wo.production_id.id) if wo.production_id else None
            if data:
                wo.opt_qty_ensamblar = data["ens"]
                wo.opt_qty_prevaciar = data["prev"]
                wo.opt_qty_liberar = data["lib"]
            else:
                wo.opt_qty_ensamblar = 0.0
                wo.opt_qty_prevaciar = 0.0
                wo.opt_qty_liberar = 0.0

    def _is_user_allowed(self, stage_key, line=None):
        allowed = self._get_allowed_users(line or self._get_opt_line_for_stage(stage_key), stage_key)
        return (not allowed) or (self.env.user in allowed)

    @api.depends("production_id", "workcenter_id", "operation_id")
    def _compute_opt_edit_permissions(self):
        for wo in self:
            line = wo._get_opt_line_for_stage("ensamblado") or wo._get_opt_line_for_stage("prevaciado") or wo._get_opt_line_for_stage("inspeccion_final")
            stage = wo._get_stage_from_line(line) or wo._get_opt_stage_key()
            allowed_ens = wo._get_allowed_users(line, "ensamblado")
            allowed_prev = wo._get_allowed_users(line, "prevaciado")
            allowed_insp = wo._get_allowed_users(line, "inspeccion_final")
            wo.can_edit_opt_ensamblado = stage == "ensamblado" and (not allowed_ens or self.env.user in allowed_ens)
            wo.can_edit_opt_prevaciado = stage == "prevaciado" and (not allowed_prev or self.env.user in allowed_prev)
            wo.can_edit_opt_inspeccion = stage == "inspeccion_final" and (not allowed_insp or self.env.user in allowed_insp)

    def _get_opt_line_for_stage(self, stage_key):
        Line = self.env["mrp.master.order.line"]
        production = self.production_id
        if not production:
            return Line
        domain = [("production_id", "=", production.id)]
        if stage_key == "ensamblado":
            domain.append(("master_id_ensamblado", "!=", False))
        elif stage_key == "prevaciado":
            domain.append(("master_id_prevaciado", "!=", False))
        else:
            domain.append(("master_id_inspeccion_final", "!=", False))
        return Line.search(domain, limit=1)

    def _get_allowed_users(self, line, stage_key):
        """Return allowed users for stage based on master type configuration."""
        if not line or not line.master_id or not line.master_id.type_id:
            return self.env["res.users"]  # empty set => allowed for all
        mtype = line.master_id.type_id
        if stage_key == "ensamblado":
            return mtype.opt_users_ensamblado_ids
        if stage_key == "prevaciado":
            return mtype.opt_users_prevaciado_ids
        return mtype.opt_users_inspeccion_ids

    def _apply_opt_qty(self, stage_key, field_name, line_field_name):
        for wo in self:
            production = wo.production_id
            line = wo._get_opt_line_for_stage(stage_key)
            stage = wo._get_stage_from_line(line) or wo._get_opt_stage_key()
            if stage != stage_key:
                continue
            allowed = wo._get_allowed_users(line, stage_key)
            if allowed and self.env.user not in allowed:
                raise UserError("No tiene permisos para modificar esta cantidad.")
            if not line and not production:
                continue
            qty = getattr(wo, field_name) or 0.0
            Line = self.env["mrp.master.order.line"]
            ens_line = Line
            prev_line = Line
            lib_line = Line
            if production:
                ens_line = Line.search([
                    ("production_id", "=", production.id),
                    ("master_id_ensamblado", "!=", False),
                ], limit=1)
                prev_line = Line.search([
                    ("production_id", "=", production.id),
                    ("master_id_prevaciado", "!=", False),
                ], limit=1)
                lib_line = Line.search([
                    ("production_id", "=", production.id),
                    ("master_id_inspeccion_final", "!=", False),
                ], limit=1)

            # Cascada: Ensamblado -> Prevaciado -> Liberación
            if stage_key == "ensamblado":
                target_ens = ens_line or line
                if target_ens and abs((getattr(target_ens, "cantidad_ensamblada", 0.0) or 0.0) - qty) >= 0.00001:
                    target_ens.write({"cantidad_ensamblada": qty})
                target_prev = prev_line or line
                if target_prev and "qty_to_prevaciar" in target_prev._fields:
                    if abs((getattr(target_prev, "qty_to_prevaciar", 0.0) or 0.0) - qty) >= 0.00001:
                        target_prev.write({"qty_to_prevaciar": qty})
                target_lib = lib_line or line
                if target_lib and "qty_to_liberar" in target_lib._fields:
                    if abs((getattr(target_lib, "qty_to_liberar", 0.0) or 0.0) - qty) >= 0.00001:
                        target_lib.write({"qty_to_liberar": qty})
                continue

            if stage_key == "prevaciado":
                target_prev = prev_line or line
                if target_prev and "qty_to_prevaciar" in target_prev._fields:
                    if abs((getattr(target_prev, "qty_to_prevaciar", 0.0) or 0.0) - qty) >= 0.00001:
                        target_prev.write({"qty_to_prevaciar": qty})
                target_lib = lib_line or line
                if target_lib and "qty_to_liberar" in target_lib._fields:
                    if abs((getattr(target_lib, "qty_to_liberar", 0.0) or 0.0) - qty) >= 0.00001:
                        target_lib.write({"qty_to_liberar": qty})
                continue

            # inspeccion_final
            target_lib = lib_line or line
            if target_lib and "qty_to_liberar" in target_lib._fields:
                if abs((getattr(target_lib, "qty_to_liberar", 0.0) or 0.0) - qty) >= 0.00001:
                    target_lib.write({"qty_to_liberar": qty})

    def _inverse_opt_qty_ensamblar(self):
        self._apply_opt_qty("ensamblado", "opt_qty_ensamblar", "cantidad_ensamblada")

    def _inverse_opt_qty_prevaciar(self):
        self._apply_opt_qty("prevaciado", "opt_qty_prevaciar", "qty_to_prevaciar")

    def _inverse_opt_qty_liberar(self):
        self._apply_opt_qty("inspeccion_final", "opt_qty_liberar", "qty_to_liberar")

    def action_open_novedades_wizard(self):
        self.ensure_one()
        product = self.production_id.product_id if self.production_id else False
        title = 'Novedades'
        if product:
            title = f"Novedades - {product.display_name or product.name}"
        return {
            'type': 'ir.actions.act_window',
            'name': title,
            'res_model': 'alterben.workorder.novedades.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_workorder_id': self.id,
                'default_production_id': self.production_id.id,
                'default_workcenter_id': self.workcenter_id.id,
                'default_product_finished_id': self.production_id.product_id.id,
                'default_alert_name': self.name,
            }
        }

    def _get_novedades_alert_domain(self):
        self.ensure_one()
        mo = self.production_id
        Alert = self.env['quality.alert'].sudo()
        domain = []
        if 'production_id' in Alert._fields and mo:
            domain.append(('production_id', '=', mo.id))
        if 'workorder_id' in Alert._fields:
            if domain:
                domain = ['|'] + domain + [('workorder_id', '=', self.id)]
            else:
                domain = [('workorder_id', '=', self.id)]
        elif mo and 'title' in Alert._fields:
            if domain:
                domain = ['|'] + domain + [('title', '=', mo.name)]
            else:
                domain = [('title', '=', mo.name)]
        if 'product_tmpl_id' in Alert._fields and mo and mo.product_id:
            domain = ['&', ('product_tmpl_id', '=', mo.product_id.product_tmpl_id.id)] + domain
        return domain

    def get_novedades_count(self):
        self.ensure_one()
        Alert = self.env['quality.alert'].sudo()
        domain = self._get_novedades_alert_domain()
        return Alert.search_count(domain)

    def get_novedades_summary(self):
        """Return a compact tooltip string summarizing alerts' tag counts and scrap/finished qty for this WO."""
        self.ensure_one()
        mo = self.production_id
        # Collect quality alerts related to this WO / MO
        Alert = self.env['quality.alert'].sudo()
        domain = self._get_novedades_alert_domain()
        alerts = Alert.search(domain, limit=200)
        # Count tags
        tag_counts = {}
        if 'tag_ids' in Alert._fields:
            for al in alerts:
                for t in al.tag_ids:
                    tag_counts[t.name] = tag_counts.get(t.name, 0) + 1
        # Fallback to reason if no tags
        if not tag_counts and 'reason_id' in Alert._fields:
            for al in alerts:
                if al.reason_id:
                    nm = al.reason_id.display_name
                    tag_counts[nm] = tag_counts.get(nm, 0) + 1
        # Scrap summary
        Scrap = self.env['stock.scrap'].sudo()
        scrap_domain = ['|', ('workorder_id', '=', self.id), ('production_id', '=', mo.id)]
        scrap_qty = 0.0
        # Sum in product UoM; stock.scrap has 'scrap_qty' in UoM of product
        if 'scrap_qty' in Scrap._fields:
            for sc in Scrap.search(scrap_domain, limit=200):
                try:
                    scrap_qty += sc.scrap_qty or 0.0
                except Exception:
                    scrap_qty += sc.quantity or 0.0 if 'quantity' in Scrap._fields else 0.0
        elif 'quantity' in Scrap._fields:
            for sc in Scrap.search(scrap_domain, limit=200):
                scrap_qty += sc.quantity or 0.0

        # Finished qty from WO
        qty_finished = getattr(self, 'qty_produced', 0.0) or 0.0

        # Build text
        parts = []
        if tag_counts:
            tags_txt = ", ".join(f"{k}: {v}" for k, v in sorted(tag_counts.items(), key=lambda kv: (-kv[1], kv[0])))
            parts.append(f"Alertas - {tags_txt}")
        else:
            parts.append("Alertas - sin registros")
        parts.append("----")
        parts.append(f"Desecho MP - {scrap_qty}")
        parts.append(f"PT registrados - {qty_finished}")
        return "\n".join(parts)

    def action_open_novedades_summary(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Resumen de Novedades',
            'res_model': 'workorder.novedades.summary.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'active_id': self.id},
        }

    def action_open_produce_wizard(self):
        """Abrir el asistente estandar de Registrar Produccion de la OP desde la OT."""
        self.ensure_one()
        production = self.production_id.exists()
        if not production:
            raise UserError("La OT no tiene una Orden de Fabricacion valida (puede haber sido eliminada).")
        product = production.product_id.exists()
        if not product:
            raise UserError("La Orden de Fabricacion no tiene un producto valido (puede haber sido eliminado).")
        # Abrir wizard propio de produccion parcial por OT
        remaining = max((production.product_qty or 0.0) - (production.qty_produced or 0.0), 0.0)
        qty_suggested = getattr(self, 'qty_remaining', False) or remaining
        if remaining and qty_suggested and qty_suggested > remaining:
            qty_suggested = remaining
        view_id = False
        try:
            view_id = self.env.ref('alterben_mrp_master_order.view_mrp_workorder_produce_wizard_form').id
        except Exception:
            view_id = False
        action = {
            'type': 'ir.actions.act_window',
            'res_model': 'mrp.workorder.produce.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_workorder_id': self.id,
                'default_production_id': production.id,
                'default_qty': qty_suggested,
            }
        }
        if view_id:
            action['view_id'] = view_id
            action['views'] = [(view_id, 'form')]
        return action

    def write(self, vals):
        prev_state = {wo.id: wo.state for wo in self}
        prev_qty = {wo.id: getattr(wo, 'qty_produced', 0.0) or 0.0 for wo in self}
        res = super().write(vals)
        if any(k in vals for k in ('qty_produced', 'state')):
            productions = self.mapped('production_id')
            self.env['mrp.master.order.line']._recompute_cantidad_real_for_productions(productions)
            # Descuenta piezas de cabina al completar Ensamblado
            self._consume_cabina_on_ensamblado(prev_state, prev_qty)
        return res

    def _consume_cabina_on_ensamblado(self, prev_state, prev_qty):
        Receta = self.env['receta.pvb']
        for wo in self:
            old_state = prev_state.get(wo.id)
            new_state = wo.state
            if old_state == 'done' or new_state != 'done':
                continue
            if not self._is_ensamblado_operation(wo):
                continue
            qty = getattr(wo, 'qty_produced', 0.0) or 0.0
            if not qty:
                continue
            product = wo.production_id.product_id if wo.production_id else False
            if not product:
                continue
            rec = Receta.get_by_product(product)
            if not rec:
                continue
            note = f"WO {wo.name}"
            rec._apply_cabina_delta(-qty, reason="ensamblado", note=note, workorder=wo, production=wo.production_id)

    def _is_ensamblado_operation(self, wo):
        """Detecta si la operacion/centro contiene 'ensambl' (insensible a mayusculas)."""
        name = (wo.operation_id.name if getattr(wo, 'operation_id', False) else False) or (
            wo.workcenter_id.name if wo.workcenter_id else ''
        )
        return bool(name and 'ensambl' in name.lower())
