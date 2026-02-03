from odoo import api, fields, models, _
from odoo.exceptions import UserError

class StockPicking(models.Model):
    _inherit = "stock.picking"

    control_total_label_count = fields.Integer(compute="_compute_control_total_label_count")

    def _compute_control_total_label_count(self):
        for p in self:
            p.control_total_label_count = self.env["control.total.label"].search_count([("picking_id","=",p.id)])

    def action_view_control_total_labels(self):
        self.ensure_one()
        action = self.env.ref("alterben_mrp_master_order.action_control_total_label").read()[0]
        action["domain"] = [("picking_id","=", self.id)]
        action["context"] = { "default_picking_id": self.id }
        return action

    def action_assign_control_total_wizard(self):
        self.ensure_one()
        return {
            "name": _("Asignar Control Total"),
            "type": "ir.actions.act_window",
            "res_model": "assign.control.total.wizard",
            "view_mode": "form",
            "view_id": self.env.ref("alterben_mrp_master_order.view_assign_control_total_wizard_form").id,
            "target": "new",
            "context": {"default_picking_id": self.id, "dialog_size": "fullscreen"},
        }

   
    def action_preassign_control_total(self):
        """Pre-asigna rangos por producto en el picking, evitando repetidos y usando
        el rango configurado para Picking en Ajustes de compañía.
        Escribe ct_pre_from/ct_pre_to en cada stock.move con cantidad > 0.
        No crea registros en control.total.label; solo deja la preasignación guardada en movimientos."""
        self.ensure_one()
        Label = self.env['control.total.label']

        import re

        def split_code(code):
            m = re.match(r'^(?P<prefix>\D*?)(?P<num>\d+)$', code or '')
            if m:
                return (m.group('prefix') or ''), int(m.group('num'))
            return (code or ''), None

        company = self.company_id or self.env.company
        range_from = company.ct_picking_from or 0
        range_to = company.ct_picking_to or 0

        # Determinar prefijo y último número según etiquetas existentes
        pf_default = 'CS-'
        last_num = None
        
        # Buscar la última etiqueta sin filtrar por compañía
        last_label = Label.search([], order='create_date desc', limit=1)
        if last_label:
            pf_last, last_num = split_code(last_label.name)
            prefix = pf_last or pf_default
        else:
            prefix = pf_default

        def is_code_taken(code):
            return bool(Label.search_count([('name', '=', code)]))

        def next_free(start_n):
            n = start_n
            while True:
                code = f"{prefix}{n}"
                if not is_code_taken(code):
                    return n
                n += 1

        # Punto de partida dentro del rango configurado (si existe)
        if range_from:
            start_n = range_from
            if last_num:
                start_n = max(range_from, last_num + 1)
        else:
            start_n = (last_num + 1) if last_num else 1

        # Movimientos a pre-asignar
        moves = self.move_ids_without_package.filtered(lambda m: m.product_id and m.product_uom_qty > 0)
        cur = next_free(start_n)

        for move in moves:
            qty = int(move.product_uom_qty or 0)
            if qty <= 0:
                move.ct_pre_from = False
                move.ct_pre_to = False
                continue

            if range_to and cur + qty - 1 > range_to:
                raise UserError(_("No hay suficientes etiquetas disponibles en el rango configurado para Picking."))

            nums = []
            taken = 0
            n = cur
            while taken < qty:
                if range_to and n > range_to:
                    raise UserError(_("No hay suficientes etiquetas disponibles en el rango configurado para Picking."))
                code = f"{prefix}{n}"
                if not is_code_taken(code):
                    nums.append(n)
                    taken += 1
                n += 1

            first_n = min(nums)
            last_n = max(nums)
            width = max(5, len(str(first_n)), len(str(last_n)))
            move.ct_pre_from = f"{prefix}{str(first_n).zfill(width)}"
            move.ct_pre_to = f"{prefix}{str(last_n).zfill(width)}"
            cur = n  # Continuar desde el siguiente número disponible

        # Notificación y refresco del formulario
        try:
            self.env.user.notify_success(message=_('Pre-asignación de etiquetas completada.'))
        except Exception:
            # En contextos sin bus/notify no debe romper la acción
            pass
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'stock.picking',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'current',
        }
