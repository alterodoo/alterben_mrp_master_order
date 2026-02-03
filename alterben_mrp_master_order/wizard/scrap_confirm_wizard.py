# -*- coding: utf-8 -*-
from odoo import models, fields, api, _

class WorkorderScrapConfirmWizard(models.TransientModel):
    _name = 'alterben.workorder.scrap.confirm.wizard'
    _description = 'Confirmar Desecho en Novedad de WO'

    message = fields.Text(readonly=True)

    def action_with_scrap(self):
        wiz_id = self.env.context.get('active_novedades_id')
        if wiz_id:
            wiz = self.env['alterben.workorder.novedades.wizard'].browse(wiz_id)
            return wiz._action_confirm_internal(force_create_scrap=True)
        return {'type': 'ir.actions.act_window_close'}

    def action_without_scrap(self):
        wiz_id = self.env.context.get('active_novedades_id')
        if wiz_id:
            wiz = self.env['alterben.workorder.novedades.wizard'].browse(wiz_id)
            return wiz._action_confirm_internal(force_create_scrap=False)
        return {'type': 'ir.actions.act_window_close'}

    def action_back(self):
        return {'type': 'ir.actions.act_window_close'}
