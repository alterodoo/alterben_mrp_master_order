# -*- coding: utf-8 -*-
from odoo import models, fields


class MrpImportResultWizard(models.TransientModel):
    _name = 'mrp.import.result.wizard'
    _description = 'Resultado de importación de Órdenes de Fabricación'

    summary = fields.Char(string="Resumen", readonly=True)
    error_message = fields.Text(string="Errores", readonly=True)
    production_ids = fields.Many2many(
        'mrp.production',
        string='Órdenes de fabricación creadas',
        readonly=True
    )
    scrap_ids = fields.Many2many(
        'stock.scrap',
        string='Desechos creados',
        readonly=True
    )
    sale_order_ids = fields.Many2many(
        'sale.order',
        string='Cotizaciones creadas',
        readonly=True
    )
