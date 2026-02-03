from odoo import api, fields, models

class ControlTotalDraft(models.Model):
    _name = "control.total.draft"
    _description = "Borradores de etiquetas Control Total"
    _order = "id desc"

    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company)
    user_id = fields.Many2one("res.users", required=True, default=lambda self: self.env.user)
    picking_id = fields.Many2one("stock.picking", required=True, index=True)
    product_id = fields.Many2one("product.product", required=True, index=True)
    code = fields.Char(required=True)

    _sql_constraints = [
        (
            "uniq_code_per_company",
            "unique(company_id, code)",
            "El código ya existe en borradores para esta compañía.",
        )
    ]
