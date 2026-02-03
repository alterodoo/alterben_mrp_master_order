from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class MrpMasterPrintWizard(models.TransientModel):
    _name = 'mrp.master.print.wizard'
    _description = 'Asistente de impresion de Orden Maestra'

    master_id = fields.Many2one('mrp.master.order', string='Orden Maestra', required=True)
    stage_type = fields.Selection(related='master_id.stage_type', string='Etapa', store=False, readonly=True)

    report_kind_opt = fields.Selection(
        [
            ('ensamblaje', 'Ensamblaje'),
            ('prevaciado', 'Laminado'),
            ('inspeccion_final', 'Inspeccion Final'),
        ],
        string='Tipo de reporte',
        required=True,
        default='ensamblaje',
    )

    report_kind_curvado = fields.Selection(
        [
            ('curvado', 'Curvado'),
            ('corte_pvb', 'Corte PVB'),
            ('pvb_medidas_figura', 'PVB+ Medidas de figura'),
            ('referencia_produccion', 'Referencia de produccion'),
        ],
        string='Tipo de reporte',
        required=True,
        default='curvado',
    )

    curvado_sub = fields.Selection([
        ('hp_t1', 'HORNO PEQUENO - TURNO 1'),
        ('hp_t2', 'HORNO PEQUENO - TURNO 2'),
        ('hg_t1', 'HORNO GRANDE - TURNO 1'),
        ('hg_t2', 'HORNO GRANDE - TURNO 2'),
    ], string='Horno/Turno (Curvado)', default='hp_t1')

    notes = fields.Text('Notas adicionales')
    report_date = fields.Date('Fecha de informe')
    process_employee_id = fields.Many2one('hr.employee', string='Encargado del proceso')

    def _get_report_kind(self):
        self.ensure_one()
        return self.report_kind_opt if self.stage_type == 'opt' else self.report_kind_curvado

    def action_print(self):
        self.ensure_one()
        report_kind = self._get_report_kind()
        # Validacion por etapa para no mezclar reportes
        if self.stage_type == 'curvado_pvb' and report_kind in ('ensamblaje', 'inspeccion_final'):
            raise ValidationError(_('Este reporte aplica solo a Ordenes de Producto Terminado (OPT).'))
        if self.stage_type == 'opt' and report_kind in ('curvado', 'corte_pvb', 'pvb_medidas_figura', 'referencia_produccion'):
            raise ValidationError(_('Este reporte aplica solo a Ordenes de Curvado/PVB.'))

        if report_kind == 'curvado':
            if not self.curvado_sub:
                raise ValidationError(_('Debe seleccionar el Horno/Turno para Curvado.'))
            return self.env.ref('alterben_mrp_master_order.action_report_curvado').report_action(
                self.master_id,
                data={
                    'master_id': self.master_id.id,
                    'tab': self.curvado_sub,
                    'notes': self.notes or '',
                    'username': self.env.user.name,
                }
            )
        if report_kind == 'corte_pvb':
            return self.env.ref('alterben_mrp_master_order.action_report_corte_pvb').report_action(
                self.master_id,
                data={
                    'master_id': self.master_id.id,
                    'notes': self.notes or '',
                    'username': self.env.user.name,
                }
            )
        if report_kind == 'pvb_medidas_figura':
            return self.env.ref('alterben_mrp_master_order.action_report_pvb_medidas_figura').report_action(
                self.master_id,
                data={
                    'master_id': self.master_id.id,
                    'notes': self.notes or '',
                    'username': self.env.user.name,
                }
            )
        if report_kind == 'ensamblaje':
            return self.env.ref('alterben_mrp_master_order.action_report_ensamblaje').report_action(
                self.master_id,
                data={
                    'master_id': self.master_id.id,
                    'notes': self.notes or '',
                    'username': self.env.user.name,
                }
            )
        if report_kind == 'prevaciado':
            return self.env.ref('alterben_mrp_master_order.action_report_prevaciado').report_action(
                self.master_id,
                data={
                    'master_id': self.master_id.id,
                    'notes': self.notes or '',
                    'username': self.env.user.name,
                }
            )
        if report_kind == 'inspeccion_final':
            return self.env.ref('alterben_mrp_master_order.action_report_inspeccion_final').report_action(
                self.master_id,
                data={
                    'master_id': self.master_id.id,
                    'notes': self.notes or '',
                    'username': self.env.user.name,
                }
            )
        if report_kind == 'referencia_produccion':
            if not self.report_date:
                raise ValidationError(_('Debe ingresar la fecha del informe.'))
            if not self.process_employee_id:
                raise ValidationError(_('Debe seleccionar el encargado del proceso.'))
            return self.env.ref('alterben_mrp_master_order.action_report_referencia_produccion').report_action(
                self.master_id,
                data={
                    'master_id': self.master_id.id,
                    'report_date': self.report_date,
                    'process_employee_name': self.process_employee_id.name or '',
                }
            )
        return True
