from odoo import api, fields, models, _


class MrpMasterOrder(models.Model):
    _inherit = "mrp.master.order"

    ct_complete_master = fields.Boolean(
        string="Etiquetas Inspección final completas",
        compute="_compute_ct_complete_master",
        store=False,
    )
    ct_banner_html = fields.Html(
        string="CT Banner",
        compute="_compute_ct_banner_master",
        store=False,
    )

    def _compute_ct_complete_master(self):
        Label = self.env['control.total.label']
        for order in self:
            lines = getattr(order, "line_ids_inspeccion_final", self.env["mrp.master.order.line"])
            if not lines:
                order.ct_complete_master = False
                continue
                
            all_lines_complete = True
            for line in lines:
                deliver_qty = int(line.qty_to_deliver or 0)
                if deliver_qty <= 0:
                    continue
                if not line.ct_pre_from or not line.ct_pre_to:
                    all_lines_complete = False
                    break
                    
                # Verificar si hay etiquetas activas para esta línea
                domain = [
                    '|',
                    ('master_order_line_id', '=', line.id),
                    '&',
                    ('master_order_id', '=', order.id),
                    '&',
                    ('name', '>=', line.ct_pre_from),
                    '&',
                    ('name', '<=', line.ct_pre_to),
                    ('active', '=', True)
                ]
                
                label_count = Label.search_count(domain)
                
                if label_count == 0:
                    all_lines_complete = False
                    break
                    
                # Verificar que la cantidad de etiquetas coincida con la cantidad del producto
                if label_count < deliver_qty:
                    all_lines_complete = False
                    break
            
            order.ct_complete_master = all_lines_complete

    def _compute_ct_banner_master(self):
        for order in self:
            # Solo mostrar el banner si estamos en la pestaña de inspección final
            current_tab = self.env.context.get('mrp_tab')
            if current_tab != 'inspeccion_final':
                order.ct_banner_html = False
                continue

            lines = getattr(order, "line_ids_inspeccion_final", self.env["mrp.master.order.line"])
            if not lines:
                order.ct_banner_html = False
                continue
            if order.ct_complete_master:
                order.ct_banner_html = (
                    "<div class='alert alert-success' style='margin-bottom:8px;'>"
                    "✅ Etiquetas pre-asignadas para Inspección final"
                    "</div>"
                )
            else:
                order.ct_banner_html = (
                    "<div class='alert alert-danger' style='margin-bottom:8px;'>"
                    "⚠️ Pendiente pre-asignación de etiquetas de Seguro Control Total en Inspección final"
                    "</div>"
                )

    def action_preassign_control_total_master(self):
        """Pre-asigna rangos por producto en la pestaña Inspección final,
        usando el rango configurado para producción en Ajustes de compañía."""
        self.ensure_one()
        company = self.company_id or self.env.company
        mtype = self.type_id

        import re

        def split_code(code):
            m = re.match(r"^(?P<prefix>\D*?)(?P<num>\d+)$", code or "")
            if m:
                return (m.group("prefix") or ""), int(m.group("num"))
            return (code or ""), None

        # Configuración de rangos para producción
        range_from = (getattr(mtype, "ct_mrp_from", 0) or 0) if mtype else 0
        range_to = (getattr(mtype, "ct_mrp_to", 0) or 0) if mtype else 0

        # Usar valores por defecto sin acceder a control.total.label
        prefix = "CS-"
        
        # Obtener el último número de etiqueta usado
        last_label = self.env['control.total.label'].search(
            [('name', 'like', prefix + '%')], 
            order='name desc', 
            limit=1
        )
        
        # Iniciar desde el siguiente número disponible o desde el rango configurado
        if last_label and last_label.name.startswith(prefix):
            try:
                last_num = int(last_label.name[len(prefix):].split('-')[-1])
                start_num = max(range_from, last_num + 1) if range_from else (last_num + 1)
            except (ValueError, IndexError):
                start_num = range_from if range_from else 1
        else:
            start_num = range_from if range_from else 1

        # Líneas de Inspección Final
        lines = getattr(
            self,
            "line_ids_inspeccion_final",
            self.env["mrp.master.order.line"],
        ).filtered(lambda l: l.product_id and (l.qty_to_deliver or 0) > 0)

        current_num = start_num
        
        for line in lines:
            qty = int(line.qty_to_deliver or 0)
            if qty <= 0:
                line.ct_pre_from = False
                line.ct_pre_to = False
                continue
                
            # Calcular el rango de etiquetas
            start = current_num
            end = current_num + qty - 1
            
            # Verificar límite superior si está configurado
            if range_to and end > range_to:
                raise UserError(
                    _("No hay suficientes etiquetas disponibles en el rango configurado para producción.")
                )
                
            # Asegurar que el ancho sea consistente
            width = max(5, len(str(start)), len(str(end)))
            
            # Asignar los valores
            line.ct_pre_from = f"{prefix}{str(start).zfill(width)}"
            line.ct_pre_to = f"{prefix}{str(end).zfill(width)}"
            
            # Actualizar el contador para la siguiente línea
            current_num = end + 1

        try:
            self.env.user.notify_success(
                message=_("Pre-asignación de etiquetas completada en Inspección final.")
            )
        except Exception:
            # En entornos sin bus, evitar que falle la acción
            pass

        view_ref = "alterben_mrp_master_order.view_mrp_master_order_form_opt_split"
        if self.stage_type != "opt":
            view_ref = "alterben_mrp_master_order.view_mrp_master_order_form_curvado_split"
        view = self.env.ref(view_ref)
        return {
            "type": "ir.actions.act_window",
            "res_model": "mrp.master.order",
            "res_id": self.id,
            "view_mode": "form",
            "views": [(view.id, "form")],
            "target": "current",
            "context": dict(self.env.context, form_view_ref=view_ref),
        }

    def action_open_control_total_wizard(self):
        """Permite abrir el wizard de Seguro CT desde la cabecera de la Orden Maestra (por si alguna vista lo llama)."""
        self.ensure_one()
        ctx = self.env.context or {}

        line = False
        line_id = ctx.get("default_line_id")
        if line_id:
            line = self.env["mrp.master.order.line"].browse(line_id)
            if not line.exists() or getattr(line, "master_id", False) != self:
                line = False

        # Si no viene línea por contexto, tomamos la primera de Inspección Final
        if not line:
            line = getattr(self, "line_ids_inspeccion_final", self.env["mrp.master.order.line"])[:1]

        if not line:
            # No hay línea para trabajar → notificamos y no rompemos
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Advertencia"),
                    "message": _("No se pudo determinar una línea de Inspección final para Seguro CT."),
                    "sticky": False,
                    "type": "warning",
                },
            }

        wizard_ctx = {
            "default_master_order_id": self.id,
            "default_line_id": line.id,
            "default_product_id": line.product_id.id,
            "default_qty": line.qty_to_deliver,
        }

        # Si ya hay rangos en la línea, los pasamos como default
        if hasattr(line, "ct_pre_from") and hasattr(line, "ct_pre_to"):
            wizard_ctx.update(
                {
                    "default_etiquetas_desde": line.ct_pre_from,
                    "default_etiquetas_hasta": line.ct_pre_to,
                }
            )

        return {
            "name": _("Asignar Control Total - %s") % (line.product_id.display_name or ""),
            "type": "ir.actions.act_window",
            "res_model": "assign.control.total.wizard",
            "view_mode": "form",
            "view_id": self.env.ref(
                "alterben_mrp_master_order.view_assign_control_total_wizard_form"
            ).id,
            "target": "new",
            "context": wizard_ctx,
        }


class MrpMasterOrderLineCT(models.Model):
    _inherit = "mrp.master.order.line"

    ct_pre_from = fields.Char(string="Pre-asignado desde", copy=False)
    ct_pre_to = fields.Char(string="Pre-asignado hasta", copy=False)
    ct_label_range = fields.Char(
        string="Rango de etiquetas",
        compute="_compute_ct_label_range",
        store=False,
    )
    ct_preassigned = fields.Boolean(
        string="Etiquetas pre-asignadas",
        compute="_compute_ct_preassigned",
        store=False,
    )

    @api.depends("ct_pre_from", "ct_pre_to")
    def _compute_ct_label_range(self):
        for line in self:
            if line.ct_pre_from and line.ct_pre_to:
                line.ct_label_range = f"{line.ct_pre_from} -> {line.ct_pre_to}"
            else:
                line.ct_label_range = False

    @api.depends("ct_pre_from", "ct_pre_to")
    def _compute_ct_preassigned(self):
        Label = self.env['control.total.label']
        for line in self:
            # Verificar si hay etiquetas activas para esta línea de orden maestra
            has_active_labels = False
            if line.ct_pre_from and line.ct_pre_to:
                # Buscar etiquetas activas en el rango de esta línea
                has_active_labels = bool(Label.search_count([
                    ('master_order_id', '=', line.master_id.id if line.master_id else False),
                    ('name', '>=', line.ct_pre_from),
                    ('name', '<=', line.ct_pre_to),
                    ('active', '=', True)
                ], limit=1))
            
            # Si hay etiquetas activas, forzar ct_preassigned = True
            if has_active_labels:
                line.ct_preassigned = True
            else:
                # Si no hay etiquetas activas, verificar la preasignación
                line.ct_preassigned = bool(line.ct_pre_from and line.ct_pre_to)

    def init(self):
        # Asegurar que la tabla tenga las columnas necesarias
        if not self._fields["ct_pre_from"].store:
            self._fields["ct_pre_from"].store = True
        if not self._fields["ct_pre_to"].store:
            self._fields["ct_pre_to"].store = True

    def action_open_control_total_wizard(self):
        """Abre el wizard de asignación de Control Total para esta línea (botón 'Seguro CT')."""
        self.ensure_one()
        line = self

        wizard_ctx = {
            "default_master_order_id": line.master_id.id if "master_id" in line._fields else False,
            "default_line_id": line.id,
            "default_product_id": line.product_id.id,
            "default_qty": line.qty_to_deliver,
        }

        if hasattr(line, "ct_pre_from") and hasattr(line, "ct_pre_to"):
            wizard_ctx.update(
                {
                    "default_etiquetas_desde": line.ct_pre_from,
                    "default_etiquetas_hasta": line.ct_pre_to,
                }
            )

        return {
            "name": _("Asignar Control Total - %s") % (line.product_id.display_name or ""),
            "type": "ir.actions.act_window",
            "res_model": "assign.control.total.wizard",
            "view_mode": "form",
            "view_id": self.env.ref(
                "alterben_mrp_master_order.view_assign_control_total_wizard_form"
            ).id,
            "target": "new",
            "context": wizard_ctx,
        }
