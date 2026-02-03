from collections import defaultdict
from odoo import api, fields, models
from odoo.exceptions import ValidationError, UserError


class MrpMasterOrder(models.Model):
    _inherit = "mrp.master.order"

    opt_recalc_done = fields.Boolean(
        string="Recalcular OPT usado",
        default=False,
        copy=False,
        help="Marca si la acción Recalcular OPT ya fue utilizada.",
    )

    line_ids_hp_t1 = fields.One2many("mrp.master.order.line", "master_id_hp_t1", string="HORNO P - T1", copy=True)
    line_ids_hp_t2 = fields.One2many("mrp.master.order.line", "master_id_hp_t2", string="HORNO P - T2", copy=True)
    line_ids_hg_t1 = fields.One2many("mrp.master.order.line", "master_id_hg_t1", string="HORNO G - T1", copy=True)
    line_ids_hg_t2 = fields.One2many("mrp.master.order.line", "master_id_hg_t2", string="HORNO G - T2", copy=True)
    line_ids_corte = fields.One2many("mrp.master.order.line", "master_id_corte", string="CORTE PVB", copy=True)
    line_ids_ensamblado = fields.One2many("mrp.master.order.line", "master_id_ensamblado", string="ENSAMBLADO", copy=True)
    line_ids_prevaciado = fields.One2many("mrp.master.order.line", "master_id_prevaciado", string="PREVACIADO Y LAMINADO", copy=True)
    line_ids_inspeccion_final = fields.One2many("mrp.master.order.line", "master_id_inspeccion_final", string="INSPECCION FINAL", copy=True)

    @api.onchange('line_ids_hp_t1', 'line_ids_hp_t2', 'line_ids_hg_t1', 'line_ids_hg_t2', 'line_ids_corte', 'line_ids_ensamblado', 'line_ids_prevaciado', 'line_ids_inspeccion_final')
    def _onchange_grid_lines(self):
        """Si el usuario edita cualquier línea de las pestañas, marcamos el flag."""
        # Se activa solo en el cliente, al interactuar con el formulario
        if self.state == 'draft':
            self.x_has_manual_changes = True

    def action_load_from_origin(self):
        """Carga productos desde Corte PVB de la orden origen hacia ENSAMBLADO (OPT)."""
        for rec in self:
            if rec.stage_type != 'opt':
                raise ValidationError("Solo aplica a órdenes OPT.")
            if not rec.source_master_order_id:
                raise ValidationError("Seleccione una Orden de origen (Curv/PVB).")
            # Tomamos únicamente las líneas de CORTE PVB de la orden origen
            source_lines = rec.source_master_order_id.line_ids_corte.filtered(lambda l: l.product_id)
            # Limpiar ENSAMBLADO antes de copiar
            rec.line_ids_ensamblado.unlink()
            seq = 1
            for line in source_lines:
                source_qty = line.product_qty or 0.0
                pvb_cut = line.pvb_cortado_qty or line.cantidad_piezas or 0.0
                if source_qty == 1:
                    qty_to_use = 1.0
                elif source_qty > 1:
                    qty_to_use = pvb_cut * 2
                else:
                    qty_to_use = pvb_cut * 2
                vals = {
                    "master_id_ensamblado": rec.id,
                    "sequence": seq,
                    "product_id": line.product_id.id,
                    # Cantidad ensamblar basada en PVB cortado (duplicada si qty origen > 1)
                    "product_qty": qty_to_use,
                    "cantidad_ensamblada": qty_to_use,
                    "arrastre_qty": 0.0,
                    "uom_id": line.uom_id.id,
                    "pedido_original_id": line.pedido_original_id.id or False,
                    "note": line.note,
                    "production_id": line.production_id.id or False,
                }
                self.env["mrp.master.order.line"].create(vals)
                if line.production_id:
                    try:
                        new_origin = f"{rec.source_master_order_id.name}/{rec.name}"
                        if line.production_id.origin != new_origin:
                            line.production_id.write({"origin": new_origin})
                    except Exception:
                        pass
                seq += 1
        return True

    def action_cargar_datos_opt(self):
        """Carga Corte PVB de la orden origen hacia ENSAMBLADO, PREVACIADO e INSPECCION FINAL (OPT)."""
        for rec in self:
            if rec.stage_type != 'opt':
                raise ValidationError("La acción Cargar datos aplica solo a Órdenes OPT.")
            if not rec.source_master_order_id:
                raise ValidationError("Seleccione una Orden de origen (Curv/PVB).")
            source_lines = rec.source_master_order_id.line_ids_corte.filtered(lambda l: l.product_id)
            rec.line_ids_ensamblado.unlink()
            rec.line_ids_prevaciado.unlink()
            rec.line_ids_inspeccion_final.unlink()
            if not source_lines:
                try:
                    rec.message_post(body="No hay líneas en CORTE PVB para copiar a ENSAMBLADO/PREVACIADO/INSPECCION FINAL.")
                except Exception:
                    pass
                continue
            for line in source_lines:
                base_vals = {
                    "product_id": line.product_id.id,
                    "product_qty": line.product_qty,
                    "arrastre_qty": line.arrastre_qty,
                    "uom_id": line.uom_id.id,
                    "pedido_original_id": line.pedido_original_id.id,
                    "note": line.note,
                    "production_id": line.production_id.id if line.production_id else False,
                    "sequence": line.sequence,
                    "cantidad_piezas": line.cantidad_piezas,
                    "cantidad_piezas_text": line.cantidad_piezas_text,
                    "pvb_cortado_qty": line.pvb_cortado_qty,
                    "pvb_cortado_text": line.pvb_cortado_text,
                    "ct_pre_from": False,
                    "ct_pre_to": False,
                }
                self.env["mrp.master.order.line"].create(dict(base_vals, master_id_ensamblado=rec.id))
                self.env["mrp.master.order.line"].create(dict(base_vals, master_id_prevaciado=rec.id))
                self.env["mrp.master.order.line"].create(dict(base_vals, master_id_inspeccion_final=rec.id))
            try:
                rec.message_post(body="Se han copiado las líneas de CORTE PVB a ENSAMBLADO/PREVACIADO/INSPECCION FINAL.")
            except Exception:
                pass
        return True

    def action_cargar_prevaciado(self):
        """Carga ENSAMBLADO hacia PREVACIADO (solo OPT)."""
        for rec in self:
            if rec.stage_type != 'opt':
                raise ValidationError("La acciÃ³n Cargar Prevacidado aplica solo a Ã“rdenes OPT.")
            rec.line_ids_prevaciado.unlink()
            source_lines = rec.line_ids_ensamblado.filtered(lambda l: l.product_id and l.product_qty > 0)
            if not source_lines:
                try:
                    rec.message_post(body="No hay lÃ­neas en ENSAMBLADO para copiar a PREVACIADO.")
                except Exception:
                    pass
                continue
            for line in source_lines:
                base_vals = {
                    "product_id": line.product_id.id,
                    "product_qty": line.product_qty,
                    "arrastre_qty": line.arrastre_qty,
                    "uom_id": line.uom_id.id,
                    "pedido_original_id": line.pedido_original_id.id,
                    "note": line.note,
                    "production_id": line.production_id.id if line.production_id else False,
                    "sequence": line.sequence,
                    "ct_pre_from": False,
                    "ct_pre_to": False,
                }
                self.env["mrp.master.order.line"].create(dict(base_vals, master_id_prevaciado=rec.id))
            try:
                rec.message_post(body="Se han copiado las lÃ­neas de ENSAMBLADO a PREVACIADO.")
            except Exception:
                pass
        return True

    def action_cargar_inspeccion_final(self):
        """Carga ENSAMBLADO hacia INSPECCION FINAL (solo OPT)."""
        for rec in self:
            if rec.stage_type != 'opt':
                raise ValidationError("La acciÃ³n Cargar LiberaciÃ³n aplica solo a Ã“rdenes OPT.")
            rec.line_ids_inspeccion_final.unlink()
            source_lines = rec.line_ids_ensamblado.filtered(lambda l: l.product_id and l.product_qty > 0)
            if not source_lines:
                try:
                    rec.message_post(body="No hay lÃ­neas en ENSAMBLADO para copiar a INSPECCION FINAL.")
                except Exception:
                    pass
                continue
            for line in source_lines:
                base_vals = {
                    "product_id": line.product_id.id,
                    "product_qty": line.product_qty,
                    "arrastre_qty": line.arrastre_qty,
                    "uom_id": line.uom_id.id,
                    "pedido_original_id": line.pedido_original_id.id,
                    "note": line.note,
                    "production_id": line.production_id.id if line.production_id else False,
                    "sequence": line.sequence,
                    "ct_pre_from": False,
                    "ct_pre_to": False,
                }
                self.env["mrp.master.order.line"].create(dict(base_vals, master_id_inspeccion_final=rec.id))
            try:
                rec.message_post(body="Se han copiado las lÃ­neas de ENSAMBLADO a INSPECCION FINAL.")
            except Exception:
                pass
        return True

    def action_recalcular_prevaciado(self):
        """Replica ENSAMBLADO hacia PREVACIADO/INSPECCION FINAL para la etapa OPT."""
        for rec in self:
            if rec.stage_type != 'opt':
                raise ValidationError("La acción Recalcular Prevac/Inspección aplica solo a Órdenes OPT.")

            rec.line_ids_prevaciado.unlink()
            rec.line_ids_inspeccion_final.unlink()

            source_lines = rec.line_ids_ensamblado.filtered(lambda l: l.product_id and l.product_qty > 0)
            if not source_lines:
                try:
                    rec.message_post(body="No hay líneas en ENSAMBLADO para copiar a PREVACIADO y/o INSPECCION FINAL.")
                except Exception:
                    pass
                continue

            for line in source_lines:
                base_vals = {
                    "product_id": line.product_id.id,
                    "product_qty": line.product_qty,
                    "arrastre_qty": line.arrastre_qty,
                    "uom_id": line.uom_id.id,
                    "pedido_original_id": line.pedido_original_id.id,
                    "note": line.note,
                    "production_id": line.production_id.id if line.production_id else False,
                    "sequence": line.sequence,
                    "ct_pre_from": False,
                    "ct_pre_to": False,
                }
                self.env["mrp.master.order.line"].create(dict(base_vals, master_id_prevaciado=rec.id))
                self.env["mrp.master.order.line"].create(dict(base_vals, master_id_inspeccion_final=rec.id))

            try:
                rec.message_post(body="Se han copiado las líneas de ENSAMBLADO a PREVACIADO/INSPECCION FINAL.")
            except Exception:
                pass

        return True

    def action_recalcular_corte(self):
        """Recalcula CORTE PVB a partir de hornos (solo etapa Curvado/PVB)."""
        for rec in self:
            if rec.x_has_manual_changes:
                raise UserError(
                    "Ha realizado cambios manuales en el grid. "
                    "Para evitar la pérdida de datos, la operación de recalcular ha sido bloqueada. "
                    "Guarde sus cambios antes de volver a intentarlo."
                )

            if rec.stage_type != 'curvado_pvb':
                raise ValidationError("Recalcular Corte aplica solo a Órdenes de Curvado/PVB.")

            Product = self.env['product.product']
            final_categ = rec.type_id.final_categ_id
            agg = defaultdict(float)
            # Mapeo: de semi S3- a producto final por default_code sin prefijo
            for l in rec.line_ids_hp_t1 | rec.line_ids_hp_t2 | rec.line_ids_hg_t1 | rec.line_ids_hg_t2:
                if 'COCHE VACIO' in (l.product_id.name or '').upper():
                    try:
                        rec.message_post(body=f"[Recalcular Corte] Producto excluido por ser COCHE VACIO: {l.product_id.display_name}")
                    except Exception:
                        pass
                    continue

                code = (l.product_id.default_code or '').strip()
                final_code = code[3:] if code.startswith('S3-') else code
                domain = [('default_code', '=', final_code)]
                if final_categ:
                    domain.append(('categ_id', 'child_of', final_categ.id))
                matches = Product.search(domain)
                if not matches:
                    # Sin correspondencia: usar el mismo producto para no dejar la pestaña vacía
                    try:
                        rec.message_post(body=f"[Recalcular Corte] Sin correspondencia de final para '{code}'. Se usará el mismo producto.")
                    except Exception:
                        pass
                    matches = l.product_id
                if len(matches) > 1:
                    try:
                        rec.message_post(body=f"[Recalcular Corte] Múltiples productos finales para '{final_code}'. Usando el primero: {matches[0].display_name}.")
                    except Exception:
                        pass
                final_prod = matches[0]
                uom = (final_prod.uom_id.id) or (l.uom_id.id)
                po = l.pedido_original_id.id or False
                agg[(final_prod.id, uom, po)] += l.product_qty or 0.0

            existing_lines = rec.line_ids_corte.filtered(lambda l: l.product_id)
            existing_keys = {
                (line.product_id.id, line.uom_id.id, line.pedido_original_id.id or False): line
                for line in existing_lines
            }
            missing_keys = [key for key in agg if key not in existing_keys]

            # Solo bloquear cuando ya hay lineas en Corte y la orden no esta en borrador;
            # la primera carga (grid vacio) debe permitirse para poblar desde hornos.
            if missing_keys and rec.state != 'draft' and existing_lines:
                prod_ids = [pid for pid, _, _ in missing_keys]
                names = Product.browse(prod_ids).mapped('display_name')
                suffix = f" ({', '.join(names)})" if names else ''
                raise UserError(
                    "Hay productos nuevos en los hornos que no están en Corte de PVB. "
                    "Comuníquese con Planificación de Producción para que agregue manualmente los productos nuevos"
                    f"{suffix}."
                )

            seq = (max(existing_lines.mapped('sequence') or [0]) + 1) if existing_lines else 1
            created_lines = self.env["mrp.master.order.line"]
            for (pid, uom, po) in missing_keys:
                qty = agg.get((pid, uom, po), 0.0)
                production = self.env['mrp.production'].search([
                    ('origin', '=', rec.name),
                    ('product_id', '=', pid),
                    ('state', '!=', 'cancel')
                ], limit=1, order='id desc')

                line_vals = {
                    "master_id_corte": rec.id,
                    "sequence": seq,
                    "product_id": pid,
                    "product_qty": qty,
                    "uom_id": uom,
                    "pedido_original_id": po,
                }

                if production:
                    line_vals['production_id'] = production.id

                suggested = qty / 2.0 if qty and qty >= 2 else (1.0 if qty == 1 else 0.0)
                line_vals.update({
                    "cantidad_piezas": suggested,
                    "cantidad_piezas_text": f"{suggested:.1f}".replace('.', ','),
                    "pvb_cortado_qty": suggested,
                    "pvb_cortado_text": f"{suggested:.1f}".replace('.', ','),
                    "last_pvb_cortado_confirmed": 0.0,
                })

                line = self.env["mrp.master.order.line"].create(line_vals)
                created_lines |= line
                seq += 1

            # Defensa por si en alguna instalación quedó la versión que usa lista
            if isinstance(created_lines, list):
                created_lines = self.env["mrp.master.order.line"].browse([getattr(l, 'id', False) for l in created_lines if getattr(l, 'id', False)])
            if created_lines:
                arr_map = rec._compute_arrastre_map('curvado_pvb', created_lines.mapped('product_id').ids)
                rec._apply_arrastre_to_lines(created_lines, arr_map)
                created_lines._ensure_pvb_defaults()
                created_lines._compute_pvb_data()  # <<-- AÑADIDO PARA CORREGIR EL BUG

            rec.write({'x_has_manual_changes': False})

        return True

    def action_recalculate_opt(self):
        """Recalcula arrastre para OPT y replica hacia Prevac/Inspección."""
        for rec in self:
            if rec.opt_recalc_done:
                raise UserError(
                    "El botón Recalcular OPT ya fue utilizado para esta orden y está bloqueado."
                )
            if rec.x_has_manual_changes:
                raise UserError(
                    "Ha realizado cambios manuales en el grid. "
                    "Para evitar la pérdida de datos, la operación de recalcular ha sido bloqueada. "
                    "Guarde sus cambios antes de volver a intentarlo."
                )

            if rec.stage_type != 'opt':
                raise ValidationError("La acción Recalcular OPT aplica solo a Órdenes OPT.")
            ens_lines = rec.line_ids_ensamblado.filtered(lambda l: l.product_id)
            if not ens_lines and rec.source_master_order_id:
                rec.action_load_from_origin()
                ens_lines = rec.line_ids_ensamblado.filtered(lambda l: l.product_id)
            if not ens_lines:
                raise ValidationError("Agregue productos en la pestaña ENSAMBLADO para recalcular OPT.")
            arr_map = rec._compute_arrastre_map('opt', ens_lines.mapped('product_id').ids)
            rec._apply_arrastre_to_lines(ens_lines, arr_map)
            rec.action_recalcular_prevaciado()

            rec.write({'x_has_manual_changes': False, 'opt_recalc_done': True})

        return True


class MrpMasterOrderLine(models.Model):
    _inherit = "mrp.master.order.line"

    master_id_hp_t1 = fields.Many2one("mrp.master.order", string="Orden Maestra HORNO P - T1", index=True, ondelete="cascade")
    master_id_hp_t2 = fields.Many2one("mrp.master.order", string="Orden Maestra HORNO P - T2", index=True, ondelete="cascade")
    master_id_hg_t1 = fields.Many2one("mrp.master.order", string="Orden Maestra HORNO G - T1", index=True, ondelete="cascade")
    master_id_hg_t2 = fields.Many2one("mrp.master.order", string="Orden Maestra HORNO G - T2", index=True, ondelete="cascade")
    master_id_corte = fields.Many2one("mrp.master.order", string="Orden Maestra CORTE PVB", index=True, ondelete="cascade")
    master_id_ensamblado = fields.Many2one("mrp.master.order", string="Orden Maestra ENSAMBLADO", index=True, ondelete="cascade")
    master_id_prevaciado = fields.Many2one("mrp.master.order", string="Orden Maestra PREVACIADO Y LAMINADO", index=True, ondelete="cascade")
    master_id_inspeccion_final = fields.Many2one("mrp.master.order", string="Orden Maestra INSPECCION FINAL", index=True, ondelete="cascade")

    sequence = fields.Integer("N°", default=0, index=True)
    _order = "sequence, id"

    @api.constrains('master_id', 'master_id_hp_t1', 'master_id_hp_t2', 'master_id_hg_t1', 'master_id_hg_t2', 'master_id_corte', 'master_id_ensamblado', 'master_id_prevaciado', 'master_id_inspeccion_final')
    def _check_single_parent(self):
        for r in self:
            parents = [
                bool(r.master_id), bool(r.master_id_hp_t1), bool(r.master_id_hp_t2), bool(r.master_id_hg_t1), bool(r.master_id_hg_t2),
                bool(r.master_id_corte), bool(r.master_id_ensamblado), bool(r.master_id_prevaciado), bool(r.master_id_inspeccion_final)
            ]
            if sum(parents) > 1:
                raise ValidationError("Cada línea debe pertenecer a una sola pestaña.")

    @api.model_create_multi
    def create(self, vals_list):
        recs = super().create(vals_list)
        for r in recs:
            if not r.sequence:
                parent = (
                    r.master_id_hp_t1 or r.master_id_hp_t2 or r.master_id_hg_t1 or r.master_id_hg_t2 or
                    r.master_id_corte or r.master_id_ensamblado or r.master_id_prevaciado or r.master_id_inspeccion_final or r.master_id
                )
                if parent:
                    last = self.search([
                        '|','|','|','|','|','|','|',
                        ('master_id_hp_t1','=',parent.id),
                        ('master_id_hp_t2','=',parent.id),
                        ('master_id_hg_t1','=',parent.id),
                        ('master_id_hg_t2','=',parent.id),
                        ('master_id_corte','=',parent.id),
                        ('master_id_ensamblado','=',parent.id),
                        ('master_id_prevaciado','=',parent.id),
                        ('master_id_inspeccion_final','=',parent.id),
                        ('master_id','=',parent.id),
                    ], order='sequence desc', limit=1)
                    r.sequence = (last.sequence or 0) + 1
        return recs

    @api.depends('master_id', 'master_id_hp_t1', 'master_id_hp_t2', 'master_id_hg_t1', 'master_id_hg_t2', 'master_id_corte', 'master_id_ensamblado', 'master_id_prevaciado', 'master_id_inspeccion_final')
    def _compute_available_products(self):
        Product = self.env['product.product']
        for line in self:
            parent = (
                line.master_id or line.master_id_hp_t1 or line.master_id_hp_t2 or line.master_id_hg_t1 or line.master_id_hg_t2 or
                line.master_id_corte or line.master_id_ensamblado or line.master_id_prevaciado or line.master_id_inspeccion_final
            )
            # Para OPT: limitar a productos de la orden origen si se indicó
            # Hornos usan categoría de semi; corte/inspeccion_final usan categoría final
            if parent and parent.type_id:
                if line.master_id_corte or line.master_id_ensamblado or line.master_id_prevaciado or line.master_id_inspeccion_final:
                    categ = parent.type_id.final_categ_id or parent.type_id.categ_id
                else:
                    categ = parent.type_id.categ_id
            else:
                categ = False
            if categ:
                line.available_product_ids = Product.search([('categ_id', 'child_of', categ.id)])
            else:
                line.available_product_ids = Product.browse()

    def action_open_production(self):
        """Abrir la orden de fabricación asociada para editar su LdM."""
        self.ensure_one()
        if not self.production_id:
            raise ValidationError(_("No hay Orden de Fabricación generada para esta línea."))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Orden de Fabricación'),
            'res_model': 'mrp.production',
            'res_id': self.production_id.id,
            'view_mode': 'form',
            'target': 'current',
        }

    @api.onchange('master_id', 'master_id_hp_t1', 'master_id_hp_t2', 'master_id_hg_t1', 'master_id_hg_t2', 'master_id_corte', 'master_id_ensamblado', 'master_id_prevaciado', 'master_id_inspeccion_final')
    def _onchange_type_domain(self):
        domain = {}
        parent = (
            self.master_id or self.master_id_hp_t1 or self.master_id_hp_t2 or self.master_id_hg_t1 or self.master_id_hg_t2 or
            self.master_id_corte or self.master_id_ensamblado or self.master_id_prevaciado or self.master_id_inspeccion_final
        )
        if parent and parent.type_id:
            if self.master_id_corte or self.master_id_ensamblado or self.master_id_prevaciado or self.master_id_inspeccion_final:
                categ = parent.type_id.final_categ_id or parent.type_id.categ_id
            else:
                categ = parent.type_id.categ_id
            domain['product_id'] = [('categ_id', 'child_of', categ.id)] if categ else []
        else:
            domain['product_id'] = []
        return {'domain': domain}
