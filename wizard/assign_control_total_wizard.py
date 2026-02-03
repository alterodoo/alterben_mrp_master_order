from odoo import api, fields, models, _
import re
from odoo.exceptions import UserError, ValidationError


class AssignControlTotalWizard(models.TransientModel):
    _name = "assign.control.total.wizard"
    _description = "Asignar etiquetas Control Total"

    # Campos para pickings
    picking_id = fields.Many2one("stock.picking", string="Picking")

    # Campos para master orders
    master_order_id = fields.Many2one("mrp.master.order", string="Orden Maestra")

    line_ids = fields.One2many(
        "assign.control.total.wizard.line", "wizard_id", string="Resumen"
    )

    etiquetas_desde = fields.Char(string="Etiquetas desde")
    etiquetas_hasta = fields.Char(string="hasta")
    selected_product_id = fields.Many2one(
        "product.product", string="Producto seleccionado", readonly=True
    )
    selected_qty = fields.Float(string="Cantidad", readonly=True)

    status_html = fields.Html(string="Estado", readonly=True)
    is_assigned = fields.Boolean(string="Ya asignado", readonly=True)
    is_complete = fields.Boolean(string="Completo", readonly=True)

    # Nuevos campos para etiquetas dañadas y adicionales
    etiquetas_danadas = fields.Text(
        string="Etiquetas dañadas",
        help="Ingrese los números de etiquetas dañadas, una por línea (sin el prefijo CS-)",
    )
    etiquetas_adicionales = fields.Text(
        string="Etiquetas adicionales",
        help=(
            "Ingrese las etiquetas de reemplazo, una por línea (sin el prefijo CS-). "
            "Debe haber igual o menor cantidad que las etiquetas dañadas."
        ),
    )
    etiquetas_manuales = fields.Text(
        string="Etiquetas Manuales",
        help="Si se usa este campo, se ignorará el rango 'desde-hasta'. Ingrese los números de etiqueta completos (ej: CS-123), uno por línea."
    )

    @api.model
    def default_get(self, fields_list):
        vals = super().default_get(fields_list)
        ctx = self.env.context or {}

        # ------------ MODO MASTER ORDER ------------
        line_id = ctx.get("default_line_id")
        if line_id:
            line = self.env["mrp.master.order.line"].browse(line_id)
            if line.exists():
                master_order = line.master_id
                vals.update(
                    {
                        "master_order_id": master_order.id,
                        "selected_product_id": line.product_id.id,
                        "selected_qty": line.product_qty,
                    }
                )

                if line.ct_pre_from and line.ct_pre_to:
                    vals.update(
                        {
                            "etiquetas_desde": line.ct_pre_from,
                            "etiquetas_hasta": line.ct_pre_to,
                        }
                    )

                self._check_existing_labels(
                    vals, line.product_id, master_order=master_order, line=line
                )
                return vals

        # ------------ MODO PICKING ------------
        picking = None
        picking_id = vals.get("picking_id")

        if not picking_id:
            if ctx.get("active_model") == "stock.picking" and ctx.get("active_id"):
                picking = self.env["stock.picking"].browse(ctx["active_id"])
                vals["picking_id"] = picking.id
            elif ctx.get("default_move_id"):
                mv = self.env["stock.move"].browse(ctx["default_move_id"])
                if mv and mv.exists():
                    picking = mv.picking_id
                    vals["picking_id"] = picking.id
            elif ctx.get("default_move_line_id"):
                ml = self.env["stock.move.line"].browse(ctx["default_move_line_id"])
                if ml and ml.exists():
                    picking = ml.picking_id
                    vals["picking_id"] = picking.id
        else:
            picking = self.env["stock.picking"].browse(picking_id)

        # Obtener producto y cantidad
        prod = False
        qty = 0.0

        if ctx.get("default_move_id"):
            mv = self.env["stock.move"].browse(ctx["default_move_id"])
            if mv and mv.exists():
                prod = mv.product_id
                qty = mv.product_uom_qty
        elif ctx.get("default_move_line_id"):
            ml = self.env["stock.move.line"].browse(ctx["default_move_line_id"])
            if ml and ml.exists():
                prod = ml.product_id
                qty = ml.qty_done or ml.product_uom_qty

        if not prod and picking and picking.move_lines:
            mv = picking.move_lines[0]
            prod = mv.product_id
            qty = mv.product_uom_qty

        if prod:
            vals["selected_product_id"] = prod.id
            vals["selected_qty"] = qty

        # Helper split_code disponible tanto para EXISTING como para SUGERENCIA
        def split_code(code):
            m = re.match(r"^(?P<prefix>\D*?)(?P<num>\d+)$", code or "")
            if m:
                return (m.group("prefix") or ""), int(m.group("num"))
            return (code or ""), None

        # --- LÓGICA DE ETIQUETAS EXISTENTES EN PICKING ---
        if picking and prod:
            Label = self.env["control.total.label"]

            existing = Label.search(
                [
                    ("picking_id", "=", picking.id),
                    ("product_id", "=", prod.id),
                    ("active", "=", True),
                ],
                order="name asc",
            )

            if not existing:
                existing = Label.search(
                    [
                        ("picking_id", "=", picking.id),
                        ("product_id.product_tmpl_id", "=", prod.product_tmpl_id.id),
                        ("active", "=", True),
                    ],
                    order="name asc",
                )

            if existing:
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

                if nums and pf_common:
                    vals["etiquetas_desde"] = f"{pf_common}{min(nums)}"
                    vals["etiquetas_hasta"] = f"{pf_common}{max(nums)}"

                vals["is_assigned"] = True
                assigned_count = len(nums)
                need = int(qty)

                if need > 0:
                    if assigned_count < need:
                        vals["status_html"] = (
                            "<div class='alert alert-warning'>"
                            f"Asignado {assigned_count} de {need}. "
                            f"Faltan {need - assigned_count}."
                            "</div>"
                        )
                    elif assigned_count == need:
                        vals["status_html"] = (
                            "<div class='alert alert-success'>"
                            f"Asignación completa ({assigned_count}/{need})."
                            "</div>"
                        )
                    else:
                        vals["status_html"] = (
                            "<div class='alert alert-danger'>Exceso de etiquetas.</div>"
                        )

                return vals

        # --- SUGERENCIA SI NO HAY EXISTENTES ---
        Label = self.env["control.total.label"]
        last = Label.search([("name", "ilike", "CS-")], order="name asc")

        start_num = 54801
        prefix = "CS-"
        max_nn = None

        for rec in last:
            pf, nn = split_code(rec.name)
            if nn and pf.startswith("CS"):
                if max_nn is None or nn > max_nn:
                    max_nn = nn
                    prefix = pf

        if max_nn:
            start_num = max_nn + 1

        if prod and qty:
            vals["etiquetas_desde"] = f"{prefix}{start_num}"
            vals["etiquetas_hasta"] = f"{prefix}{start_num + int(qty) - 1}"

        vals["status_html"] = (
            "<div class='alert alert-secondary'>Sugerencia generada automáticamente.</div>"
        )

        return vals

    # ------------------------------------------------------------------

    def _parse_range(self, start_txt, end_txt):
        if not start_txt or not end_txt:
            return []

        m1 = re.match(r"^(?P<prefix>\D*?)(?P<num>\d+)$", start_txt)
        m2 = re.match(r"^(?P<prefix>\D*?)(?P<num>\d+)$", end_txt)

        if m1 and m2 and m1.group("prefix") == m2.group("prefix"):
            prefix = m1.group("prefix")
            n1 = int(m1.group("num"))
            n2 = int(m2.group("num"))
            step = 1 if n2 >= n1 else -1
            width = max(len(m1.group("num")), len(m2.group("num")))
            return [
                f"{prefix}{str(i).zfill(width)}" for i in range(n1, n2 + step, step)
            ]

        return [start_txt, end_txt]

    # ------------------------------------------------------------------

    def _check_existing_labels(
        self, vals, product, master_order=None, line=None, picking=None
    ):
        Label = self.env["control.total.label"]

        domain = [("product_id", "=", product.id), ("active", "=", True)]
        if master_order:
            domain.append(("master_order_id", "=", master_order.id))
        if picking:
            domain.append(("picking_id", "=", picking.id))

        existing = Label.search(domain, order="name asc")

        if not existing:
            return

        def split_code(code):
            m = re.match(r"^(?P<prefix>\D*?)(?P<num>\d+)$", code or "")
            if m:
                return (m.group("prefix") or ""), int(m.group("num"))
            return (code or ""), None

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

        if nums and pf_common:
            vals["etiquetas_desde"] = f"{pf_common}{min(nums)}"
            vals["etiquetas_hasta"] = f"{pf_common}{max(nums)}"
            vals["is_assigned"] = True

    # ------------------------------------------------------------------

    def _recompute_picking_ct_complete(self, picking=None, master_order=None):
        """
        Actualiza el estado de completitud de las etiquetas CT para un picking o una orden maestra.
        """
        if not picking and not master_order:
            return

        Label = self.env["control.total.label"]
        complete = True

        if picking:
            # Lógica para pickings
            qty_by_prod = {}
            for mv in picking.move_ids_without_package:
                qty_by_prod[mv.product_id.id] = qty_by_prod.get(
                    mv.product_id.id, 0
                ) + mv.product_uom_qty

            for pid, need in qty_by_prod.items():
                cnt = Label.search_count(
                    [
                        ("picking_id", "=", picking.id),
                        ("product_id", "=", pid),
                        ("active", "=", True),
                    ]
                )
                if cnt < need:
                    complete = False
                    break

            for fname in (
                "x_ct_completo",
                "x_studio_ct_completo",
                "ct_complete",
                "x_ct_completado",
            ):
                if fname in picking._fields:
                    picking.sudo().write({fname: complete})
                    break

        elif master_order:
            # Lógica para órdenes maestras
            qty_by_prod = {}
            for line in master_order.line_ids.filtered(
                lambda l: hasattr(l, "ct_pre_from") and l.ct_pre_from
            ):
                qty_by_prod[line.product_id.id] = qty_by_prod.get(
                    line.product_id.id, 0
                ) + line.product_qty

            for pid, need in qty_by_prod.items():
                cnt = Label.search_count(
                    [
                        ("master_order_id", "=", master_order.id),
                        ("product_id", "=", pid),
                        ("active", "=", True),
                    ]
                )
                if cnt < need:
                    complete = False
                    break

            for fname in (
                "x_ct_completo",
                "x_studio_ct_completo",
                "ct_complete",
                "x_ct_completado",
            ):
                if fname in master_order._fields:
                    master_order.sudo().write({fname: complete})
                    break

    # ------------------------------------------------------------------

    def _process_damaged_labels(
        self,
        damaged_labels,
        additional_labels,
        product_id,
        picking_id=None,
        master_order_id=None,
    ):
        """Procesa las etiquetas dañadas y adicionales."""
        Label = self.env["control.total.label"]

        line_id = (
            self._context.get("default_line_id")
            or self._context.get("keep_default_line_id")
        )
        if not line_id and hasattr(self, "line_id") and self.line_id:
            line_id = self.line_id.id

        if (
            not line_id
            and "active_model" in self._context
            and "active_id" in self._context
        ):
            if self._context["active_model"] == "mrp.master.order.line":
                line_id = self._context["active_id"]

        # ----- Etiquetas dañadas -----
        damaged_codes = []
        if damaged_labels:
            damaged_numbers = [
                x.strip() for x in damaged_labels.split("\n") if x.strip()
            ]
            damaged_codes = [f"CS-{num}" for num in damaged_numbers if num]

            for code in damaged_codes:
                existing = Label.search([("name", "=", code)], limit=1)
                reason = "roto_despacho" if picking_id else "roto_instalacion"

                label_vals = {
                    "name": code,
                    "active": False,
                    "inactive_reason": reason,
                    "product_id": product_id,
                    "picking_id": picking_id or False,
                    "master_order_id": master_order_id or False,
                    "master_order_line_id": line_id,
                }

                try:
                    if existing:
                        existing.write(label_vals)
                    else:
                        Label.create(label_vals)
                    self.env.cr.commit()
                except Exception as e:
                    self.env.cr.rollback()
                    raise ValidationError(
                        _("Error al guardar la etiqueta dañada %s: %s")
                        % (code, str(e))
                    )

        # ----- Etiquetas adicionales (solo si hubo dañadas) -----
        additional_codes = []
        if additional_labels and damaged_codes:
            additional_numbers = [
                x.strip() for x in additional_labels.split("\n") if x.strip()
            ]
            additional_codes = [f"CS-{num}" for num in additional_numbers if num]

            if len(additional_codes) > len(damaged_codes):
                raise ValidationError(
                    _(
                        "No puede haber más etiquetas adicionales (%d) que etiquetas dañadas (%d)."
                    )
                    % (len(additional_codes), len(damaged_codes))
                )

            for code in additional_codes:
                existing = Label.search([("name", "=", code)], limit=1)

                try:
                    if existing:
                        if existing.active:
                            raise ValidationError(
                                _(
                                    "La etiqueta adicional %s ya existe y está activa."
                                )
                                % code
                            )
                        else:
                            existing.write(
                                {
                                    "active": True,
                                    "inactive_reason": False,
                                    "product_id": product_id,
                                    "picking_id": picking_id or False,
                                    "master_order_id": master_order_id or False,
                                    "master_order_line_id": line_id,
                                }
                            )
                    else:
                        vals = {
                            "name": code,
                            "product_id": product_id,
                            "active": True,
                            "picking_id": picking_id or False,
                            "master_order_id": master_order_id or False,
                            "master_order_line_id": line_id,
                        }
                        Label.create(vals)
                    self.env.cr.commit()
                except Exception as e:
                    self.env.cr.rollback()
                    raise ValidationError(
                        _("Error al guardar la etiqueta adicional %s: %s")
                        % (code, str(e))
                    )

        return damaged_codes, additional_codes

    # ------------------------------------------------------------------

    def action_assign(self):
        self.ensure_one()

        if not self.selected_product_id:
            raise UserError(_("No se pudo determinar el producto seleccionado."))

        Label = self.env["control.total.label"]
        codes = []
        
        ctx = self.env.context or {}
        line_id_ctx = ctx.get("default_line_id")
        master_order_id = self.master_order_id.id if self.master_order_id else ctx.get("default_master_order_id")

        # --- Lógica de procesamiento de etiquetas ---
        # Prioridad 1: Etiquetas Manuales
        if self.etiquetas_manuales:
            raw_codes = [code.strip() for code in self.etiquetas_manuales.split('\n') if code.strip()]
            # Normalizar prefijo: si no trae prefijo, anteponer CS-
            codes = []
            for code in raw_codes:
                if not re.match(r"^[A-Za-z]+-", code):
                    codes.append(f"CS-{code}")
                else:
                    codes.append(code)

            # Validar cantidad
            qty_needed = int(self.selected_qty or 0)
            if qty_needed and len(codes) != qty_needed:
                raise ValidationError(
                    _("La cantidad de etiquetas manuales (%s) no coincide con la cantidad necesaria del producto (%s).")
                    % (len(codes), qty_needed)
                )

            # Validar duplicados en la entrada
            if len(codes) != len(set(codes)):
                raise ValidationError(_("La lista de etiquetas manuales contiene duplicados."))

            # Validar si alguna etiqueta ya existe y está activa
            existing_labels = Label.search([('name', 'in', codes), ('active', '=', True)])
            if existing_labels:
                raise ValidationError(
                    _("Las siguientes etiquetas ya existen y están activas: %s")
                    % (', '.join(existing_labels.mapped('name')))
                )
        
        # Prioridad 2: Rango de etiquetas
        elif self.etiquetas_desde and self.etiquetas_hasta:
            codes = self._parse_range(self.etiquetas_desde, self.etiquetas_hasta)
            if not codes:
                raise UserError(_("Rango inválido."))

            qty_needed = int(self.selected_qty or 0)
            if qty_needed and len(codes) != qty_needed:
                raise ValidationError(
                    _(
                        "La cantidad (%s) no coincide con el tamaño del rango (%s)."
                    )
                    % (qty_needed, len(codes))
                )

        # --- Creación/Actualización de etiquetas si hay códigos para procesar ---
        if codes:
            # Actualizar la línea de Orden Maestra si viene desde allí (solo para rango)
            if line_id_ctx and self.etiquetas_desde and self.etiquetas_hasta and hasattr(self.env["mrp.master.order.line"], "ct_pre_from"):
                line = self.env["mrp.master.order.line"].browse(line_id_ctx)
                if line.exists():
                    line.write({
                        "ct_pre_from": self.etiquetas_desde,
                        "ct_pre_to": self.etiquetas_hasta,
                    })
                    if master_order_id and not line.master_id:
                        line.master_id = master_order_id

            for code in codes:
                LabelCtx = Label.with_context(active_test=False)
                existing = LabelCtx.search([("name", "=", code)], limit=1)

                # Verificar si la etiqueta ya está asignada a otro documento
                is_assigned_to_other = False
                assigned_to = None
                if existing:
                    if (hasattr(existing, "picking_id") and existing.picking_id and 
                        (not self.picking_id or existing.picking_id.id != self.picking_id.id)):
                        is_assigned_to_other = True
                        assigned_to = existing.picking_id.name
                    elif (hasattr(existing, "master_order_id") and master_order_id and 
                          existing.master_order_id and existing.master_order_id.id != master_order_id):
                        is_assigned_to_other = True
                        assigned_to = existing.master_order_id.name if hasattr(existing.master_order_id, "name") else "otro registro"

                    if is_assigned_to_other and existing.active:
                        raise ValidationError(
                            _("El código %s ya está asignado a %s.")
                            % (code, assigned_to or "otro registro")
                        )

                vals = {
                    "active": True,
                    "inactive_reason": False,
                    "product_id": self.selected_product_id.id,
                }
                if hasattr(Label, "picking_id") and self.picking_id:
                    vals["picking_id"] = self.picking_id.id
                if hasattr(Label, "master_order_id") and master_order_id:
                    vals.update({
                        "master_order_id": master_order_id,
                        "master_order_line_id": line_id_ctx,
                    })

                if existing:
                    existing.write(vals)
                else:
                    vals["name"] = code
                    Label.create(vals)

        # ----- Etiquetas dañadas/adicionales (se procesa independientemente) -----
        if self.etiquetas_danadas or self.etiquetas_adicionales:
            self.with_context(keep_default_line_id=line_id_ctx)._process_damaged_labels(
                damaged_labels=self.etiquetas_danadas,
                additional_labels=self.etiquetas_adicionales,
                product_id=self.selected_product_id.id,
                picking_id=self.picking_id.id if self.picking_id else None,
                master_order_id=self.master_order_id.id if hasattr(self, "master_order_id") and self.master_order_id else None,
            )

        # ----- Recalcular completitud -----
        master_order = self.master_order_id if hasattr(self, "master_order_id") else None
        if self.picking_id:
            self._recompute_picking_ct_complete(picking=self.picking_id)
            message = _("¡Las etiquetas se han asignado correctamente al picking %s!") % self.picking_id.name
        elif master_order:
            self._recompute_picking_ct_complete(master_order=master_order)
            message = _("¡Las etiquetas se han asignado correctamente a la orden maestra %s!") % master_order.name
        else:
            message = _("¡Las etiquetas se han procesado correctamente!")

        action = {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Éxito"),
                "message": message,
                "sticky": False,
                "type": "success",
                "next": {"type": "ir.actions.act_window_close"},
            },
        }

        if master_order:
            action["params"]["next"] = {
                "type": "ir.actions.client",
                "tag": "reload",
            }

        return action


class AssignControlTotalWizardLine(models.TransientModel):
    _name = "assign.control.total.wizard.line"
    _description = "Línea resumen Asignación Control Total"

    wizard_id = fields.Many2one("assign.control.total.wizard")
    product_id = fields.Many2one("product.product", string="Producto")
    qty = fields.Float(string="Cantidad")
    range_text = fields.Char(string="Control Total (rango)")
    codes_input = fields.Text(string="Códigos (uno por línea)")
