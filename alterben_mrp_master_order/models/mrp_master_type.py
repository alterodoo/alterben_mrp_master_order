from odoo import api, fields, models, _


class MrpMasterType(models.Model):
    _name = "mrp.master.type"
    _description = "Tipo de producto terminado (Orden Maestra)"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "name"

    name = fields.Char(
        "Nombre",
        required=True,
        tracking=True,
        help="Nombre del conjunto de parámetros de Orden Maestra.",
    )
    prefix = fields.Char(
        "Prefijo",
        required=True,
        tracking=True,
        help="Prefijo para el código Curvado/PVB (p.ej. OCC, OCP, OCG).",
    )
    next_number = fields.Integer(
        "Siguiente número",
        default=1,
        required=True,
        tracking=True,
        help="Contador interno para el siguiente código Curvado/PVB.",
    )
    opt_prefix = fields.Char(
        "Prefijo OPT",
        default="OPT",
        tracking=True,
        help="Prefijo para Órdenes OPT (Producto Terminado).",
    )
    opt_next_number = fields.Integer(
        "Siguiente número OPT",
        default=1,
        required=True,
        tracking=True,
        help="Contador interno para el siguiente código OPT.",
    )
    location_dest_id = fields.Many2one(
        "stock.location",
        "Ubicación destino Curvado/PVB",
        tracking=True,
        help="Ubicación de destino para movimientos de Curvado/PVB.",
    )
    location_dest_opt_id = fields.Many2one(
        "stock.location",
        "Ubicación destino OPT",
        tracking=True,
        help="Ubicación de destino por defecto para productos terminados (OPT).",
    )
    opt_location_src_id = fields.Many2one(
        "stock.location",
        "Ubicación origen OPT",
        tracking=True,
        help="Ubicación origen por defecto para entregas de producto terminado (OPT).",
        default=lambda self: self._default_opt_location_src(),
    )
    import_scrap_location_id = fields.Many2one(
        "stock.location",
        "Ubicación de desecho para importación de órdenes de fabricación",
        tracking=True,
        help="Ubicación usada para registrar desechos al importar órdenes de fabricación."
    )
    categ_id = fields.Many2one(
        "product.category",
        "Categoría de producto",
        tracking=True,
        help="Categoría base para Curvado/PVB (semi elaborado).",
    )
    final_categ_id = fields.Many2one(
        "product.category",
        "Categoría de producto final",
        tracking=True,
        help="Categoría base para Producto Terminado (OPT).",
    )
    auto_validate_scrap = fields.Boolean(
        "Validar automáticamente desechos registrados? (en proceso)",
        default=False,
        tracking=True,
        help="Si está activo, valida automáticamente los desechos registrados en proceso.",
    )
    allow_pedido_create = fields.Boolean(
        "Activar creación de número de pedido en Órdenes Maestras",
        default=False,
        tracking=True,
        help="Si está activo, en las líneas de la Orden Maestra se permitirá crear un Pedido original nuevo; si está desactivado, solo se podrán seleccionar pedidos existentes."
    )
    auto_validate_import_scrap = fields.Boolean(
        "Validar automáticamente desechos importados de optimización",
        default=False,
        tracking=True,
        help="Si está activo, los desechos registrados desde la importación se validan automáticamente."
    )
    allow_import_scrap_without_stock = fields.Boolean(
        "Permitir validar desechos importados sin stock",
        default=False,
        tracking=True,
        help="Si esta activo, la importacion puede validar desechos aunque no haya stock suficiente."
    )
    validate_pedido_product = fields.Boolean(
        "Validar existencia de producto en pedido",
        default=False,
        tracking=True,
        help="Si esta activo, el campo Pedido original se filtra por productos que coincidan por referencia."
    )
    pedido_autofill_days = fields.Integer(
        "Número de días autollenado de pedidos",
        default=30,
        tracking=True,
        help="Rango (en días hacia atrás) para sugerir pedidos originales al seleccionar productos en las líneas de Orden Maestra."
    )
    report_sales_days = fields.Integer(
        "Dias de busqueda de ventas sin despacho",
        default=30,
        tracking=True,
        help="Rango (en dias hacia atras) para calcular ventas pendientes de despacho en reportes."
    )
    rpt_units_small_8 = fields.Integer(
        "Unidades por turno (pequeñas, 8h)",
        default=88,
        tracking=True,
        help="Capacidad por turno para M pequeñas cuando la jornada es de 8 horas.",
    )
    rpt_units_small_12 = fields.Integer(
        "Unidades por turno (pequeñas, 12h)",
        default=132,
        tracking=True,
        help="Capacidad por turno para M pequeñas cuando la jornada es de 12 horas.",
    )
    rpt_units_large_8 = fields.Integer(
        "Unidades por turno (grandes, 8h)",
        default=24,
        tracking=True,
        help="Capacidad por turno para M grandes cuando la jornada es de 8 horas.",
    )
    rpt_units_large_12 = fields.Integer(
        "Unidades por turno (grandes, 12h)",
        default=36,
        tracking=True,
        help="Capacidad por turno para M grandes cuando la jornada es de 12 horas.",
    )
    active = fields.Boolean(
        default=True,
        tracking=True,
        help="Si se desactiva, este conjunto de parámetros no se considera en cálculos ni sugerencias.",
    )

    control_total_allowed_categ_ids = fields.Many2many(
        "product.category",
        "mrp_master_type_ct_allowed_categ_rel",
        "type_id",
        "categ_id",
        string="Categorias permitidas para Control Total",
        help="Solo productos en estas categorias permitiran asignar etiquetas Control Total desde el despacho.",
    )

    ct_picking_from = fields.Integer(
        string="Rango etiquetas Picking (desde)",
        help="Numero inicial sugerido para las etiquetas de Seguro Control Total usadas en despachos de inventario.",
    )
    ct_picking_to = fields.Integer(
        string="Rango etiquetas Picking (hasta)",
        help="Numero final sugerido para las etiquetas de Seguro Control Total usadas en despachos de inventario.",
    )
    ct_mrp_from = fields.Integer(
        string="Rango etiquetas Orden Maestra (desde)",
        help="Numero inicial sugerido para las etiquetas de Seguro Control Total usadas en la Orden Maestra (produccion).",
    )
    ct_mrp_to = fields.Integer(
        string="Rango etiquetas Orden Maestra (hasta)",
        help="Numero final sugerido para las etiquetas de Seguro Control Total usadas en la Orden Maestra (produccion).",
    )
    opt_location_reciclo_id = fields.Many2one(
        "stock.location",
        string="Ubicacion Reciclo (OPT)",
        help="Ubicacion destino para productos reciclados desde Inspeccion Final.",
    )
    opt_location_almacen_id = fields.Many2one(
        "stock.location",
        string="Ubicacion Almacen (OPT)",
        help="Ubicacion destino para productos a almacen desde Inspeccion Final.",
    )
    opt_location_segunda_id = fields.Many2one(
        "stock.location",
        string="Ubicacion Segunda (OPT)",
        help="Ubicacion destino para productos de segunda desde Inspeccion Final.",
    )
    opt_location_cae_id = fields.Many2one(
        "stock.location",
        string="Ubicacion CAE (OPT)",
        help="Ubicacion destino para productos enviados a CAE desde Inspeccion Final.",
    )
    opt_labels_exclude_product_ids = fields.Many2many(
        "product.product",
        "mrp_master_type_opt_labels_exclude_rel",
        "type_id",
        "product_id",
        string="Productos excluidos de etiquetas",
        help="Productos que no deben generar etiquetas en la impresion.",
    )
    opt_users_ensamblado_ids = fields.Many2many(
        "res.users",
        "mrp_master_type_opt_users_ens_rel",
        "type_id",
        "user_id",
        string="Usuarios permitidos Ensamblado (OPT)",
        help="Usuarios autorizados a modificar la cantidad de Ensamblado en Ordenes de Trabajo.",
    )
    opt_users_prevaciado_ids = fields.Many2many(
        "res.users",
        "mrp_master_type_opt_users_prev_rel",
        "type_id",
        "user_id",
        string="Usuarios permitidos Prevaciado (OPT)",
        help="Usuarios autorizados a modificar la cantidad de Prevaciado en Ordenes de Trabajo.",
    )
    opt_users_inspeccion_ids = fields.Many2many(
        "res.users",
        "mrp_master_type_opt_users_insp_rel",
        "type_id",
        "user_id",
        string="Usuarios permitidos Inspeccion Final (OPT)",
        help="Usuarios autorizados a modificar la cantidad de Liberacion en Ordenes de Trabajo.",
    )

    _sql_constraints = [
        ("name_unique", "unique(name)", "El nombre del Tipo debe ser único."),
    ]

    @api.constrains("ct_picking_from", "ct_picking_to", "ct_mrp_from", "ct_mrp_to")
    def _check_ct_ranges(self):
        for rec in self:
            if rec.ct_picking_from and rec.ct_picking_to and rec.ct_picking_from > rec.ct_picking_to:
                raise ValidationError(
                    "El rango de etiquetas para Picking no es valido: el valor 'desde' no puede ser mayor que 'hasta'."
                )
            if rec.ct_mrp_from and rec.ct_mrp_to and rec.ct_mrp_from > rec.ct_mrp_to:
                raise ValidationError(
                    "El rango de etiquetas para Produccion no es valido: el valor 'desde' no puede ser mayor que 'hasta'."
                )

    @api.model
    def _default_opt_location_src(self):
        Location = self.env["stock.location"].sudo()
        loc = Location.search([("complete_name", "=", "WH/PREPRODUCCION/PT-AAA")], limit=1)
        return loc.id if loc else False

    def get_formatted_code(self, number=None):
        self.ensure_one()
        padding = int(self.env["ir.config_parameter"].sudo().get_param("mrp_master.code_padding", default="6"))
        num = self.next_number if number is None else number
        return f"{self.prefix}-{str(num).zfill(padding)}"

    def get_opt_formatted_code(self, number=None):
        self.ensure_one()
        padding = int(self.env["ir.config_parameter"].sudo().get_param("mrp_master.code_padding", default="6"))
        num = self.opt_next_number if number is None else number
        pref = (self.opt_prefix or self.prefix or 'OPT').strip()
        if not pref.endswith('-'):
            pref += '-'
        return f"{pref}{str(num).zfill(padding)}"
