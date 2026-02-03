from odoo import api, fields, models, _


class MrpMasterConfirmWizard(models.TransientModel):
    _name = 'mrp.master.confirm.wizard'
    _description = 'Confirmacion de acciones de Orden Maestra'

    master_id = fields.Many2one('mrp.master.order', string='Orden Maestra', required=True)
    action_type = fields.Selection([
        ('confirm', 'Confirmar'),
        ('mark_done', 'Marcar como hecho'),
    ], required=True)
    message = fields.Html('Mensaje', readonly=True)

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        action_type = self.env.context.get('confirm_action') or res.get('action_type')
        master_id = self.env.context.get('default_master_id') or res.get('master_id')
        if action_type:
            res['action_type'] = action_type
        if master_id:
            res['master_id'] = master_id
            master = self.env['mrp.master.order'].browse(master_id)
            res['message'] = self._build_message(master, action_type)
        return res

    def _build_message(self, master, action_type):
        if not master:
            return ''
        if action_type == 'mark_done':
            line_ids = self.env.context.get('mark_done_line_ids')
            if line_ids:
                lines = self.env['mrp.master.order.line'].browse(line_ids)
            else:
                lines = getattr(master, 'line_ids_inspeccion_final', self.env['mrp.master.order.line'])
            productions = lines.mapped('production_id').filtered(lambda p: p and p.state != 'cancel')
            total_qty = sum(lines.mapped('product_qty')) if lines else 0.0
            items = [
                f"Marcar como hechas {len(productions)} MOs de Inspeccion Final.",
                "Se usaran las cantidades actuales de la columna Cant. (Cantidad).",
                "Las MOs en progreso o listas se marcaran como Hechas.",
                f"Total programado (Cantidad): {total_qty:.2f}.",
                "Las MOs canceladas se omitiran.",
            ]
            return (
                "<div>"
                "<p><strong>Usted esta realizando las siguientes acciones:</strong></p>"
                "<ul>"
                + "".join(f"<li>{item}</li>" for item in items)
                + "</ul>"
                "</div>"
            )
        lines = master._get_lines_for_generation() if hasattr(master, '_get_lines_for_generation') else master.line_ids
        total = len(lines)
        pending = lines.filtered(
            lambda l: l.state != 'generated' and not (l.production_id and l.production_id.state != 'cancel')
        )
        items = [
            "Asignar el codigo maestro si aun no existe.",
            f"Validar lineas y generar MOs pendientes ({len(pending)} de {total}).",
            "Marcar la orden como Confirmada.",
            "Incrementar el consecutivo del tipo.",
        ]
        if master.stage_type == 'opt':
            items.append("Sincronizar las MOs en OPT.")
        return (
            "<div>"
            "<p><strong>Usted esta realizando las siguientes acciones:</strong></p>"
            "<ul>"
            + "".join(f"<li>{item}</li>" for item in items)
            + "</ul>"
            "</div>"
        )

    def action_accept(self):
        self.ensure_one()
        if self.action_type == 'confirm':
            self.master_id.button_confirm()
        elif self.action_type == 'mark_done':
            self.master_id.with_context(mrp_tab='inspeccion_final').action_mark_tab_done()
        return {'type': 'ir.actions.act_window_close'}
