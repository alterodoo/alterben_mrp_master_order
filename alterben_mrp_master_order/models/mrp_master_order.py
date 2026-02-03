# -*- coding: utf-8 -*-
import base64
import io
import logging
import math
from collections import defaultdict
from datetime import timedelta
import time
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError, UserError
from odoo.tools.float_utils import float_compare, float_is_zero
import re
import unicodedata

_logger = logging.getLogger(__name__)
TURN_DURATION_SELECTION = [(str(i), str(i)) for i in range(1, 13)]

def _log_timing(label, start, extra=""):
    try:
        elapsed = time.perf_counter() - start
        if elapsed >= 0.05:
            _logger.warning("PERF %s %.3fs %s", label, elapsed, extra)
    except Exception:
        pass

class MrpPedidoOriginal(models.Model):
    _name = "mrp.pedido.original"
    _description = "Catálogo de 'Pedido original' (PED-...)"
    _order = "name"

    name = fields.Char(string='Código maestro', required=False, copy=False, index=True, readonly=True)
    master_order_ids = fields.Many2many(
        "mrp.master.order",
        string="Órdenes maestras usadas",
        compute="_compute_master_orders",
        store=False,
        readonly=True,
    )

    _sql_constraints = [
        ("pedido_original_unique", "unique(name)", "El 'Pedido original' ya existe.")
    ]

    @api.constrains("name")
    def _check_prefix(self):
        for rec in self:
            if rec.name and not rec.name.startswith("PED-"):
                raise ValidationError(_("El 'Pedido original' debe iniciar con 'PED-'."))

    def _compute_master_orders(self):
        line_model = self.env["mrp.master.order.line"]
        result_map = {rec.id: set() for rec in self}
        if not result_map:
            return
        lines = line_model.search([("pedido_original_id", "in", list(result_map.keys()))])
        for line in lines:
            pedido_id = line.pedido_original_id.id
            if not pedido_id:
                continue
            masters = [
                getattr(line, "master_id", False),
                getattr(line, "master_id_hp_t1", False),
                getattr(line, "master_id_hp_t2", False),
                getattr(line, "master_id_hg_t1", False),
                getattr(line, "master_id_hg_t2", False),
                getattr(line, "master_id_corte", False),
                getattr(line, "master_id_ensamblado", False),
                getattr(line, "master_id_prevaciado", False),
                getattr(line, "master_id_inspeccion_final", False),
            ]
            for master in masters:
                if master:
                    result_map[pedido_id].add(master.id)
        for rec in self:
            rec.master_order_ids = [(6, 0, list(result_map.get(rec.id, set())))]

class MrpMasterOrder(models.Model):

    def action_confirm_prompt(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Confirmar Orden Maestra'),
            'res_model': 'mrp.master.confirm.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_master_id': self.id,
                'confirm_action': 'confirm',
            },
        }

    def action_mark_tab_done_prompt(self):
        self.ensure_one()
        lines = self._get_mark_done_lines()
        if not lines:
            raise UserError(_("Seleccione al menos una linea en Inspeccion Final."))
        ctx = dict(self.env.context or {})
        ctx.update({
            'default_master_id': self.id,
            'confirm_action': 'mark_done',
            'mark_done_line_ids': lines.ids,
        })
        return {
            'type': 'ir.actions.act_window',
            'name': _('Marcar como hecho'),
            'res_model': 'mrp.master.confirm.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': ctx,
        }

    def action_mark_tab_done(self):
        """Placeholder: marcar pestaña como hecha (sin lógica por ahora)."""
        self.ensure_one()
        tab = (self.env.context or {}).get('mrp_tab')
        if self.stage_type != 'opt' or tab != 'inspeccion_final':
            return True
        lines = self._get_mark_done_lines()
        if not lines:
            raise UserError(_("Seleccione al menos una linea en Inspeccion Final."))
        for line in lines:
            prod = line.production_id
            if not prod or prod.state in ('done', 'cancel'):
                continue
            self._validate_mark_done_line(line)
            planned = line.product_qty or prod.product_qty
            if not line.product_qty_original:
                line.product_qty_original = planned
            target_qty = line.qty_to_liberar or prod.product_qty
            line.product_qty = target_qty
            prod.product_qty = target_qty
            if hasattr(prod, 'button_mark_done'):
                prod.button_mark_done()
            else:
                prod.write({'state': 'done'})
        return True

    def _get_mark_done_lines(self):
        ctx = self.env.context or {}
        active_model = ctx.get('active_model')
        active_ids = ctx.get('active_ids') or []
        if active_model == 'mrp.master.order.line' and active_ids:
            lines = self.env['mrp.master.order.line'].browse(active_ids)
            return lines.filtered(lambda l: l.master_id_inspeccion_final)
        lines = getattr(self, 'line_ids_inspeccion_final', self.env['mrp.master.order.line'])
        return lines.filtered(lambda l: l.mark_done_selected)

    def _validate_mark_done_line(self, line):
        rounding = line.uom_id.rounding if line.uom_id else 0.01
        if not float_is_zero(line.cantidad_ensamblada or 0.0, precision_rounding=rounding):
            raise UserError(_(
                "No se puede marcar como hecho '%s' porque Cant. a Ensamblar debe ser 0."
            ) % (line.product_id.display_name or line.display_name))
        if not float_is_zero(line.qty_to_prevaciar or 0.0, precision_rounding=rounding):
            raise UserError(_(
                "No se puede marcar como hecho '%s' porque Cant. a Prevaciar debe ser 0."
            ) % (line.product_id.display_name or line.display_name))
        total = (line.qty_to_liberar or 0.0) + (line.destruidos_qty or 0.0)
        if float_compare(total, line.product_qty or 0.0, precision_rounding=rounding) != 0:
            raise UserError(_(
                "No se puede marcar como hecho '%s' porque Cant. a Liberar + Destruidos debe ser igual a la Cantidad de la MO."
            ) % (line.product_id.display_name or line.display_name))

    def action_select_all_inspeccion_final(self):
        self.ensure_one()
        lines = getattr(self, 'line_ids_inspeccion_final', self.env['mrp.master.order.line'])
        if lines:
            all_selected = all(lines.mapped('mark_done_selected'))
            lines.write({'mark_done_selected': not all_selected})
        return True

    def action_open_novedades_inspeccion(self):
        self.ensure_one()
        lines = getattr(self, 'line_ids_inspeccion_final', self.env['mrp.master.order.line'])
        line = lines[:1]
        if not line:
            raise UserError(_("No hay líneas en Inspección Final para registrar novedades."))
        return line.action_open_novedades_inspeccion()

    def action_open_novedades_corte(self):
        self.ensure_one()
        lines = getattr(self, 'line_ids_corte', self.env['mrp.master.order.line'])
        line = lines[:1]
        if not line:
            raise UserError(_("No hay líneas en Corte PVB para registrar novedades."))
        return line.action_open_novedades_corte()

    def action_print_opt_labels(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Imprimir etiquetas"),
            "res_model": "mrp.opt.labels.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_master_id": self.id,
            },
        }



    def action_open_add_open_mo_wizard(self):
        self.ensure_one()
        Category = self.env["product.category"]
        allowed_categ_ids = []
        mtype = self.type_id
        if mtype and (mtype.final_categ_id or mtype.categ_id):
            base_categ = mtype.final_categ_id or mtype.categ_id
            allowed_categ_ids = Category.search([("id", "child_of", base_categ.id)]).ids
        if not allowed_categ_ids:
            allowed_categ_ids = Category.search([]).ids
        return {
            "type": "ir.actions.act_window",
            "name": _("Agregar MOs abiertas"),
            "res_model": "mrp.add.open.mo.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_master_id": self.id,
                "default_tab": (self.env.context or {}).get("mrp_tab"),
                "allowed_categ_ids": allowed_categ_ids,
            },
        }

    def action_generate_warehouse_delivery(self):
        """Generar entrega a bodega con los productos de la pestaña de Inspección Final."""
        self.ensure_one()
        
        # Obtener todos los tipos de operación disponibles para depuración
        all_picking_types = self.env['stock.picking.type'].search([])
        debug_info = ['=== TIPOS DE OPERACIÓN DISPONIBLES ===']
        for pt in all_picking_types:
            debug_info.append(f'ID: {pt.id}, Nombre: {pt.name}, Código: {pt.code}, Secuencia: {pt.sequence_code}')
        _logger.info('\n'.join(debug_info))
        
        # Buscar el tipo de operación específico
        picking_type_name = 'CRILAMYT: Almacenar Producto Terminado'
        debug_msg = f'Buscando tipo de operación: {picking_type_name}'
        _logger.info(debug_msg)
        
        # Intentar con búsqueda exacta
        picking_type = self.env['stock.picking.type'].search([
            ('name', '=', picking_type_name)
        ], limit=1)
        
        # Si no se encuentra, intentar con búsqueda que ignore mayúsculas/minúsculas
        if not picking_type:
            debug_msg = 'No se encontró con búsqueda exacta, intentando con búsqueda que ignora mayúsculas/minúsculas'
            _logger.info(debug_msg)
            picking_type = self.env['stock.picking.type'].search([
                ('name', 'ilike', 'almacenar producto terminado')
            ], limit=1)
        
        # Si aún no se encuentra, intentar con el método anterior para mantener compatibilidad
        if not picking_type:
            debug_msg = 'No se encontró con búsqueda flexible, intentando con búsqueda por código interno'
            _logger.info(debug_msg)
            picking_type = self.env['stock.picking.type'].search([
                ('code', '=', 'internal'),
                ('sequence_code', '=', 'INT')
            ], limit=1)
        
        if not picking_type:
            # Mostrar los tipos disponibles en el mensaje de error
            available_types = '\n'.join([f'- {pt.name} (ID: {pt.id})' for pt in all_picking_types])
            raise UserError(_('''No se encontró el tipo de operación para almacenar producto terminado 1.

Tipos de operación disponibles:
{}'''.format(available_types)))
        
        # Obtener ubicación de origen (por configuración OPT) y destino
        mtype = self.type_id
        location_src_id = mtype.opt_location_src_id if mtype else False
        if not location_src_id:
            location_src_id = self.env['stock.location'].search([
                ('complete_name', '=', 'WH/PREPRODUCCION/PT-AAA')
            ], limit=1)
        # Fallback final: ubicación de producción estándar
        if not location_src_id:
            location_src_id = self.env.ref('stock.stock_location_production', raise_if_not_found=False)
            if not location_src_id or 'production' not in (location_src_id.complete_name or '').lower():
                _logger.info('Ubicación de producción estándar no encontrada, buscando manualmente...')
                virtual_locations = self.env['stock.location'].search([
                    ('complete_name', 'ilike', 'Virtual Locations/Production')
                ])
                if virtual_locations:
                    location_src_id = virtual_locations[0]
                    _logger.info(f'Ubicación de producción encontrada manualmente: {location_src_id.complete_name}')
        
        location_dest_id = picking_type.default_location_dest_id
        
        # Log detailed information about locations for debugging
        _logger.info('=== UBICACIONES ===')
        _logger.info(f'Ubicación de origen: {location_src_id.complete_name if location_src_id else "No encontrada"}')
        _logger.info(f'Ubicación de destino: {location_dest_id.complete_name if location_dest_id else "No encontrada"}')
        
        if not location_src_id:
            error_msg = 'No se pudo determinar la ubicacion de origen.\n\n'
            error_msg += '- Configure la ubicacion origen OPT en Parametros de Ordenes Maestras.\n'
            error_msg += '- O verifique que exista la ubicacion WH/PREPRODUCCION/PT-AAA.\n'
            error_msg += '\nDetalles del tipo de operacion seleccionado:\n'
            if picking_type:
                error_msg += f'ID: {picking_type.id}\n'
                if hasattr(picking_type, 'default_location_src_id') and picking_type.default_location_src_id:
                    error_msg += f'Ubicacion de origen por defecto: {picking_type.default_location_src_id.display_name} (ID: {picking_type.default_location_src_id.id})\n'
                if hasattr(picking_type, 'warehouse_id') and picking_type.warehouse_id:
                    error_msg += f'Almacen: {picking_type.warehouse_id.name} (ID: {picking_type.warehouse_id.id})\n'
            raise UserError(_(error_msg))
        
        # Obtener líneas de la pestaña de Inspección Final con producto
        lines_with_product = self.line_ids_inspeccion_final.filtered(lambda l: l.product_id)

        if not lines_with_product:
            raise UserError(_('No hay productos para entregar.'))

        # Omitir MOs canceladas
        lines_to_deliver = lines_with_product.filtered(
            lambda l: not l.production_id or l.production_id.state != 'cancel'
        )
        if not lines_to_deliver:
            raise UserError(_('No hay productos para entregar (todas las MOs están canceladas).'))

        missing_mo_lines = lines_to_deliver.filtered(lambda l: not l.production_id)
        if missing_mo_lines:
            raise UserError(_('Hay líneas sin MO generada en Inspección Final.'))

        company = self.company_id
        get_loc = lambda attr: getattr(mtype, attr, False) or getattr(company, attr, False)
        dest_configs = [
            ('almacen', 'almacen_qty', get_loc('opt_location_almacen_id'), 'Almacen'),
            ('reciclo', 'reciclo_qty', get_loc('opt_location_reciclo_id'), 'Reciclo'),
            ('segunda', 'segunda_qty', get_loc('opt_location_segunda_id'), 'Segunda'),
            ('cae', 'x_studio_cae', get_loc('opt_location_cae_id'), 'CAE'),
        ]

        moves_by_dest = {}
        missing_locations = set()
        for line in lines_to_deliver:
            for dest_key, field_name, dest_loc, dest_label in dest_configs:
                qty = getattr(line, field_name, 0.0) or 0.0
                if qty <= 0:
                    continue
                if not dest_loc:
                    missing_locations.add(dest_label)
                    continue
                data = moves_by_dest.setdefault(
                    dest_key,
                    {'location': dest_loc, 'label': dest_label, 'moves': [], 'qty_done': []},
                )
                move_vals = {
                    'name': line.product_id.name,
                    'product_id': line.product_id.id,
                    'product_uom_qty': qty,
                    'product_uom': line.uom_id.id,
                    'location_id': location_src_id.id,
                    'location_dest_id': dest_loc.id,
                    'picking_type_id': picking_type.id,
                    'origin': self.name,
                }
                data['moves'].append(move_vals)
                data['qty_done'].append((qty, line.uom_id.id, line.product_id.id))

        if missing_locations:
            raise UserError(
                _('Faltan ubicar ubicaciones en ajustes para: %s.')
                % ', '.join(sorted(missing_locations))
            )

        if not moves_by_dest:
            raise UserError(_('No hay cantidades para entregar. Verifique Reciclo/Almacen/Segunda/CAE.'))

        created_pickings = self.env['stock.picking']
        for data in moves_by_dest.values():
            picking_vals = {
                'picking_type_id': picking_type.id,
                'location_id': location_src_id.id,
                'location_dest_id': data['location'].id,
                'origin': f"{self.name} / {data['label']}",
                'scheduled_date': fields.Datetime.now(),
                'move_ids': [(0, 0, vals) for vals in data['moves']],
            }
            picking = self.env['stock.picking'].create(picking_vals)
            picking.action_confirm()
            for move_line in picking.move_line_ids:
                for qty_done, uom_id, product_id in data['qty_done']:
                    if move_line.product_id.id == product_id and move_line.product_uom_id.id == uom_id:
                        move_line.qty_done = qty_done
            picking.action_confirm()
            created_pickings |= picking

        if created_pickings:
            self.write({'delivery_picking_ids': [(4, pid) for pid in created_pickings.ids]})

        if len(created_pickings) == 1:
            return {
                'name': _('Entrega a Bodega'),
                'view_mode': 'form',
                'res_model': 'stock.picking',
                'res_id': created_pickings.id,
                'type': 'ir.actions.act_window',
                'target': 'current',
            }

        return {
            'name': _('Entregas a Bodega'),
            'view_mode': 'tree,form',
            'res_model': 'stock.picking',
            'domain': [('id', 'in', created_pickings.ids)],
            'type': 'ir.actions.act_window',
            'target': 'current',
        }

        return {
            'name': _('Entregas a Bodega'),
            'view_mode': 'tree,form',
            'res_model': 'stock.picking',
            'domain': [('id', 'in', created_pickings.ids)],
            'type': 'ir.actions.act_window',
            'target': 'current',
        }


    def copy(self, default=None):
        """Duplicado seguro: limpia nombre/estado y siempre retorna el nuevo registro."""
        self.ensure_one()
        default = dict(default or {})
        # Nombre temporal para pasar validaciones durante la creación; se limpia luego.
        default.setdefault('name', 'TEMP-DUP')
        default.setdefault('state', 'draft')
        if 'production_ids' in self._fields:
            default['production_ids'] = [(5, 0, 0)]
        new = super(MrpMasterOrder, self).copy(default)
        # Forzar duplicado de líneas de productos si no vinieron
        try:
            if not new.line_ids and self.line_ids:
                commands = []
                for l in self.line_ids:
                    vals = {'product_id': l.product_id.id,
                            'product_qty': getattr(l, 'product_qty', 1.0),
                            'uom_id': l.uom_id.id if getattr(l, 'uom_id', False) else False,
                            'pedido_original_id': l.pedido_original_id.id if getattr(l, 'pedido_original_id', False) else False,
                            'note': l.note or False,
                            'state': 'draft'}
                    commands.append((0, 0, vals))
                if commands:
                    new.write({'line_ids': commands})
            # Limpieza posterior: nombre vacío y campos transitorios de líneas
            new.write({'name': False})
            for line in new.line_ids:
                vals = {}
                if 'production_id' in line._fields:
                    vals['production_id'] = False
                if vals:
                    line.write(vals)
        except Exception:
            pass
        return new

    def _assign_code_on_confirm(self):
        """Asignar el código maestro sólo al confirmar (si está vacío) usando el prefijo del Tipo."""
        for rec in self:
            if rec.name:
                continue
            try:
                if rec.stage_type == 'opt' and hasattr(rec.type_id, 'get_opt_formatted_code'):
                    rec.name = rec.type_id.get_opt_formatted_code()
                else:
                    rec.name = rec.type_id.get_formatted_code()
            except Exception:
                # Fallback defensivo
                padding = int(self.env['ir.config_parameter'].sudo().get_param('mrp_master.code_padding', default='6'))
                if rec.stage_type == 'opt':
                    pref = (getattr(rec.type_id, 'opt_prefix', False) or rec.type_id.prefix or 'OPT').strip()
                else:
                    pref = (rec.type_id.prefix or 'OC').strip()
                if not pref.endswith('-'):
                    pref += '-'
                seq_val = (rec.type_id.opt_next_number if rec.stage_type == 'opt' else rec.type_id.next_number) or 1
                rec.name = f"{pref}{str(seq_val).zfill(padding)}"

    _name = "mrp.master.order"
    _description = "Orden Maestra de Producción"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "id desc"

    def read(self, fields=None, load=None):
        start = time.perf_counter()
        res = super().read(fields=fields, load=load)
        _log_timing("mrp.master.order.read", start, f"ids={self.ids} fields={len(fields or [])}")
        return res

    def web_read(self, fields_spec=None, specification=None):
        start = time.perf_counter()
        if specification is not None:
            res = super().web_read(specification)
            fields_len = len(specification or [])
        else:
            res = super().web_read(fields_spec)
            fields_len = len(fields_spec or [])
        _log_timing("mrp.master.order.web_read", start, f"ids={self.ids} fields={fields_len}")
        return res

    @api.model
    def fields_view_get(self, view_id=None, view_type='form', toolbar=False, submenu=False):
        start = time.perf_counter()
        res = super().fields_view_get(view_id=view_id, view_type=view_type, toolbar=toolbar, submenu=submenu)
        _log_timing("mrp.master.order.fields_view_get", start, f"type={view_type}")
        return res

    state = fields.Selection([
        ("draft", "Borrador"),
        ("confirmed", "Confirmada"),
        ("done", "Hecha"),
        ("cancel", "Cancelada")
    ], string="Estado", default="draft", tracking=True)

    type_id = fields.Many2one("mrp.master.type", "Tipo", required=True, tracking=True)
    name = fields.Char("Código maestro", required=False, copy=False, index=True, tracking=True)
    stage_type = fields.Selection([
        ('curvado_pvb', 'Curvado / PVB'),
        ('opt', 'Producto Terminado (OPT)'),
    ], string="Etapa", default="curvado_pvb", required=True, tracking=True)
    is_curvado_stage = fields.Boolean(string="Es etapa Curvado/PVB", compute="_compute_stage_flags", store=True)
    is_opt_stage = fields.Boolean(string="Es etapa OPT", compute="_compute_stage_flags", store=True)
    turn_duration_small = fields.Selection(
        TURN_DURATION_SELECTION, string="Duracion turno horno pequeno", default="8", required=True
    )
    turn_duration_big = fields.Selection(
        TURN_DURATION_SELECTION, string="Duracion turno horno grande", default="8", required=True
    )
    source_master_order_id = fields.Many2one(
        "mrp.master.order",
        string="Orden origen (Curv/PVB)",
        domain=[('stage_type', '=', 'curvado_pvb')],
        help="Orden maestra de Curvado/PVB usada como referencia para este OPT."
    )
    date_planned = fields.Datetime("Fecha planificada", default=fields.Datetime.now, required=True, tracking=True)
    location_dest_id = fields.Many2one("stock.location", compute="_compute_location_dest",
                                       string="Ubicacion destino (tomada del Tipo)", store=True, readonly=True)
    company_id = fields.Many2one("res.company", default=lambda self: self.env.company, readonly=True)

    line_ids = fields.One2many("mrp.master.order.line", "master_id", string="Líneas")
    production_ids = fields.One2many("mrp.production", "master_order_id", string="Órdenes de fabricación generadas", readonly=True)
    production_count = fields.Integer("# MOs", compute="_compute_production_count")
    delivery_picking_ids = fields.Many2many(
        "stock.picking",
        "mrp_master_order_delivery_rel",
        "master_id",
        "picking_id",
        string="Entregas a bodega",
        readonly=True,
        copy=False,
    )
    delivery_picking_count = fields.Integer(
        "# Entregas",
        compute="_compute_delivery_picking_count",
        store=False,
    )
    light_mode = fields.Boolean(
        string="Modo ligero",
        default=True,
        help="Cuando esta activo, se muestra una grilla ligera para mejorar el tiempo de carga."
    )
    needs_refresh = fields.Boolean(
        string="Recalculo pendiente",
        copy=False,
        default=False,
        help="Indica que hay cambios pendientes y se recomienda ejecutar Actualizar."
    )
    last_refresh_at = fields.Datetime(
        string="Ultima actualizacion",
        readonly=True,
        help="Marca de tiempo de la ultima actualizacion manual."
    )
    x_has_manual_changes = fields.Boolean(
        string="Hay cambios manuales en la parrilla",
        copy=False,
        default=False,
        help="Bandera técnica para detectar si el usuario ha hecho cambios manuales en alguna de las pestañas de líneas."
    )
    # Campos legacy para compatibilidad con vistas heredadas antiguas
    available_product_ids = fields.Many2many(
        "product.product",
        string="Productos disponibles (legacy)",
        compute="_compute_available_product_ids_legacy",
        store=False,
        readonly=True,
    )
    uom_id = fields.Many2one(
        "uom.uom",
        string="UdM (legacy)",
        compute="_compute_uom_id_legacy",
        store=False,
        readonly=True,
    )
    product_id = fields.Many2one(
        "product.product",
        string="Producto (legacy)",
        compute="_compute_product_id_legacy",
        store=False,
        readonly=True,
    )

    _sql_constraints = [
        ("name_unique", "unique(name)", "El código de la Orden Maestra ya existe.")
    ]

    @api.depends('stage_type')
    def _compute_stage_flags(self):
        start = time.perf_counter()
        for rec in self:
            rec.is_curvado_stage = rec.stage_type == 'curvado_pvb' or not rec.stage_type
            rec.is_opt_stage = rec.stage_type == 'opt'
        _log_timing("mrp.master.order._compute_stage_flags", start, f"ids={self.ids}")

    @api.depends('stage_type', 'type_id', 'type_id.location_dest_id', 'type_id.location_dest_opt_id')
    def _compute_location_dest(self):
        start = time.perf_counter()
        for rec in self:
            if rec.stage_type == 'opt':
                rec.location_dest_id = rec.type_id.location_dest_opt_id or rec.type_id.location_dest_id
            else:
                rec.location_dest_id = rec.type_id.location_dest_id
        _log_timing("mrp.master.order._compute_location_dest", start, f"ids={self.ids}")

    @api.model_create_multi
    def create(self, vals_list):
        # No asignar código al crear; se asigna al confirmar
        return super().create(vals_list)

    def write(self, vals):
        # No reasignar código al cambiar el tipo; se asigna al confirmar
        res = super().write(vals)
        # Si se guardaron cambios, reseteamos la bandera de edición manual.
        # Esto permite que el próximo recálculo se ejecute si el usuario así lo desea.
        if self.x_has_manual_changes:
            super(MrpMasterOrder, self).write({'x_has_manual_changes': False})
        return res

    @api.onchange('type_id')
    def _onchange_type_id(self):
        # Ya no sugerimos código en borrador
        return

    @api.constrains("name")
    def _check_name(self):
        for rec in self:
            if rec.name and '-' not in rec.name:
                raise ValidationError(_('El código maestro debe tener un guion. Ej.: OCP-000123.'))

    def _compute_production_count(self):
        start = time.perf_counter()
        names = [rec.name for rec in self if rec.name]
        count_map = {}
        if names:
            grouped = self.env["mrp.production"].read_group(
                [("origin", "in", list(set(names)))],
                ["origin"],
                ["origin"],
            )
            count_map = {g.get("origin"): g.get("__count", 0) for g in grouped if g.get("origin")}
        for rec in self:
            rec.production_count = count_map.get(rec.name, 0)
        _log_timing("mrp.master.order._compute_production_count", start, f"ids={self.ids} names={len(names)}")

    @api.depends('delivery_picking_ids')
    def _compute_delivery_picking_count(self):
        start = time.perf_counter()
        for rec in self:
            rec.delivery_picking_count = len(rec.delivery_picking_ids)
        _log_timing("mrp.master.order._compute_delivery_picking_count", start, f"ids={self.ids}")

    def action_view_productions(self):
        self.ensure_one()
        action = self.env.ref("mrp.mrp_production_action").sudo().read()[0]
        action["domain"] = [("origin", "=", self.name)]
        action["context"] = {"search_default_groupby_product": 1}
        return action

    def action_view_deliveries(self):
        self.ensure_one()
        pickings = self.delivery_picking_ids
        if not pickings:
            raise UserError(_('No hay entregas generadas.'))
        return {
            'name': _('Entregas a Bodega'),
            'view_mode': 'tree,form',
            'res_model': 'stock.picking',
            'domain': [('id', 'in', pickings.ids)],
            'type': 'ir.actions.act_window',
            'target': 'new',
        }

    def action_set_light_mode(self):
        self.write({'light_mode': True})
        return True

    def action_set_detail_mode(self):
        self.write({'light_mode': False})
        return True

    def action_open_lines_tab(self):
        self.ensure_one()
        tab = (self.env.context or {}).get('mrp_tab')
        domain = []
        name = _("Lineas")
        field_map = {
            'hp_t1': 'master_id_hp_t1',
            'hp_t2': 'master_id_hp_t2',
            'hg_t1': 'master_id_hg_t1',
            'hg_t2': 'master_id_hg_t2',
            'corte': 'master_id_corte',
            'ensamblado': 'master_id_ensamblado',
            'prevaciado': 'master_id_prevaciado',
            'inspeccion_final': 'master_id_inspeccion_final',
        }
        field_name = field_map.get(tab, 'master_id')
        domain = [(field_name, '=', self.id)]
        view_id = self.env.ref('alterben_mrp_master_order.view_mrp_master_order_line_tree_tab').id
        return {
            'type': 'ir.actions.act_window',
            'name': name,
            'res_model': 'mrp.master.order.line',
            'view_mode': 'tree,form',
            'views': [(view_id, 'tree'), (False, 'form')],
            'target': 'current',
            'domain': domain,
            'context': {
                'sum_exclude_canceled': True,
                'mrp_tab': tab,
                f'default_{field_name}': self.id,
            },
        }


    def action_view_workorders(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Órdenes de Trabajo'),
            'res_model': 'mrp.workorder',
            'view_mode': 'tree,form,calendar,pivot,graph',
            'domain': [('production_id.master_order_id', '=', self.id)],
            'context': {'search_default_groupby_production': 1},
        }

    def action_open_print_wizard(self):
        self.ensure_one()
        record = self.exists()
        if not record:
            raise UserError(_("Debe guardar la orden antes de imprimir."))
        record.ensure_one()
        if record.stage_type == 'opt':
            view_id = self.env.ref('alterben_mrp_master_order.view_mrp_master_print_wizard_form_opt').id
        else:
            view_id = self.env.ref('alterben_mrp_master_order.view_mrp_master_print_wizard_form_curvado').id
        return {
            'type': 'ir.actions.act_window',
            'name': _('Imprimir'),
            'res_model': 'mrp.master.print.wizard',
            'view_mode': 'form',
            'view_id': view_id,
            'views': [(view_id, 'form')],
            'target': 'new',
            'context': {
                'default_master_id': record.id,
                'default_stage_type': record.stage_type,
                'force_stage_type': record.stage_type,
            }
        }

    # Helpers para botones por pestaña
    def _get_lines_by_tab(self, rec, tab_key):
        mapping = {
            'hp_t1': getattr(rec, 'line_ids_hp_t1', self.env['mrp.master.order.line']),
            'hp_t2': getattr(rec, 'line_ids_hp_t2', self.env['mrp.master.order.line']),
            'hg_t1': getattr(rec, 'line_ids_hg_t1', self.env['mrp.master.order.line']),
            'hg_t2': getattr(rec, 'line_ids_hg_t2', self.env['mrp.master.order.line']),
            'corte': getattr(rec, 'line_ids_corte', self.env['mrp.master.order.line']),
            'ensamblado': getattr(rec, 'line_ids_ensamblado', self.env['mrp.master.order.line']),
            'prevaciado': getattr(rec, 'line_ids_prevaciado', self.env['mrp.master.order.line']),
            'inspeccion_final': getattr(rec, 'line_ids_inspeccion_final', self.env['mrp.master.order.line']),
        }
        return mapping.get(tab_key, rec.line_ids)

    def _get_export_tab_fields(self, tab_key):
        mapping = {
            'hp_t1': [
                'display_index', 'product_code', 'product_id', 'product_qty', 'arrastre_qty',
                'qty_total', 'cantidad_real', 'pending_qty', 'scrap_qty', 'uom_id',
                'pedido_original_id', 'note', 'production_id', 'mo_state',
            ],
            'hp_t2': [
                'display_index', 'product_code', 'product_id', 'product_qty', 'arrastre_qty',
                'qty_total', 'cantidad_real', 'pending_qty', 'scrap_qty', 'uom_id',
                'pedido_original_id', 'note', 'production_id', 'mo_state',
            ],
            'hg_t1': [
                'display_index', 'product_code', 'product_id', 'product_qty', 'arrastre_qty',
                'qty_total', 'cantidad_real', 'pending_qty', 'scrap_qty', 'uom_id',
                'pedido_original_id', 'note', 'production_id', 'mo_state',
            ],
            'hg_t2': [
                'display_index', 'product_code', 'product_id', 'product_qty', 'arrastre_qty',
                'qty_total', 'cantidad_real', 'pending_qty', 'scrap_qty', 'uom_id',
                'pedido_original_id', 'note', 'production_id', 'mo_state',
            ],
            'corte': [
                'display_index', 'product_code', 'product_id', 'product_qty', 'pedido_original_id',
                'ancho_pvb', 'longitud_calc', 'espesor_pvb', 'color_pvb', 'tipo_pvb',
                'production_id', 'ficha_pvb', 'note', 'cantidad_piezas_text', 'pvb_cortado_text',
                'codigo_rollo', 'sobrante_pvb', 'm2_lote', 'mo_state', 'largo',
                'arrastre_qty', 'qty_total', 'cantidad_real', 'pending_qty', 'scrap_qty', 'uom_id',
            ],
            'ensamblado': [
                'display_index', 'product_code', 'product_id', 'product_qty', 'cantidad_ensamblada',
                'arrastre_qty', 'qty_total', 'cantidad_real', 'pending_qty', 'scrap_qty',
                'uom_id', 'pedido_original_id', 'note', 'production_id', 'mo_state',
            ],
            'prevaciado': [
                'display_index', 'product_code', 'product_id', 'product_qty', 'qty_to_prevaciar',
                'arrastre_qty', 'qty_total', 'cantidad_real', 'pending_qty', 'scrap_qty',
                'uom_id', 'pedido_original_id', 'note', 'production_id', 'mo_state',
            ],
            'inspeccion_final': [
                'display_index', 'mark_done_selected', 'qty_to_liberar', 'product_code', 'product_id',
                'reciclo_qty', 'almacen_qty', 'x_studio_cae', 'segunda_qty', 'destruidos_qty',
                'vitrificacion_ok', 'qty_to_deliver', 'cantidad_real', 'product_qty', 'note',
                'arrastre_qty', 'qty_total', 'pending_qty', 'scrap_qty', 'uom_id',
                'pedido_original_id', 'production_id', 'mo_state',
            ],
        }
        return mapping.get(tab_key, [])

    def _get_export_tab_label(self, tab_key):
        labels = {
            'hp_t1': 'horno_p_t1',
            'hp_t2': 'horno_p_t2',
            'hg_t1': 'horno_g_t1',
            'hg_t2': 'horno_g_t2',
            'corte': 'corte_pvb',
            'ensamblado': 'ensamblado',
            'prevaciado': 'prevaciado',
            'inspeccion_final': 'inspeccion_final',
        }
        return labels.get(tab_key, 'lineas')

    def _get_stage_lines(self, stage_type=None):
        """Return the lines that belong to the requested stage."""
        stage = stage_type or self.stage_type or 'curvado_pvb'
        hp_t1 = getattr(self, 'line_ids_hp_t1', self.env['mrp.master.order.line'])
        hp_t2 = getattr(self, 'line_ids_hp_t2', self.env['mrp.master.order.line'])
        hg_t1 = getattr(self, 'line_ids_hg_t1', self.env['mrp.master.order.line'])
        hg_t2 = getattr(self, 'line_ids_hg_t2', self.env['mrp.master.order.line'])
        corte = getattr(self, 'line_ids_corte', self.env['mrp.master.order.line'])
        ens = getattr(self, 'line_ids_ensamblado', self.env['mrp.master.order.line'])
        prev = getattr(self, 'line_ids_prevaciado', self.env['mrp.master.order.line'])
        insp = getattr(self, 'line_ids_inspeccion_final', self.env['mrp.master.order.line'])
        if stage == 'opt':
            lines = (ens | prev | insp)
            return lines if lines else self.line_ids
        lines = (hp_t1 | hp_t2 | hg_t1 | hg_t2 | corte)
        return lines if lines else self.line_ids

    def _get_lines_for_generation(self):
        """Return the lines to use when generating MOs for this order."""
        self.ensure_one()
        stage = self.stage_type or 'curvado_pvb'
        if stage == 'opt':
            ens = getattr(self, 'line_ids_ensamblado', self.env['mrp.master.order.line'])
            prev = getattr(self, 'line_ids_prevaciado', self.env['mrp.master.order.line'])
            insp = getattr(self, 'line_ids_inspeccion_final', self.env['mrp.master.order.line'])
            if ens:
                return ens
            if prev:
                return prev
            if insp:
                return insp
            return self.line_ids
        hp_t1 = getattr(self, 'line_ids_hp_t1', self.env['mrp.master.order.line'])
        hp_t2 = getattr(self, 'line_ids_hp_t2', self.env['mrp.master.order.line'])
        hg_t1 = getattr(self, 'line_ids_hg_t1', self.env['mrp.master.order.line'])
        hg_t2 = getattr(self, 'line_ids_hg_t2', self.env['mrp.master.order.line'])
        corte = getattr(self, 'line_ids_corte', self.env['mrp.master.order.line'])
        lines = hp_t1 | hp_t2 | hg_t1 | hg_t2 | corte
        return lines if lines else self.line_ids

    def _sync_opt_production_links(self):
        for rec in self:
            if rec.stage_type != 'opt':
                continue
            ens = getattr(rec, 'line_ids_ensamblado', self.env['mrp.master.order.line'])
            prev = getattr(rec, 'line_ids_prevaciado', self.env['mrp.master.order.line'])
            insp = getattr(rec, 'line_ids_inspeccion_final', self.env['mrp.master.order.line'])
            all_lines = ens | prev | insp
            if not all_lines:
                continue
            grouped = {}
            empty = self.env['mrp.master.order.line']
            for line in all_lines:
                seq = getattr(line, 'sequence', 0) or 0
                pid = line.product_id.id if line.product_id else 0
                pedido = line.pedido_original_id.id if line.pedido_original_id else 0
                key = (seq or -1, pid, pedido)
                grouped[key] = grouped.get(key, empty) | line
            for group in grouped.values():
                prod = False
                pick = (group & ens).filtered(lambda l: l.production_id and l.production_id.state != 'cancel')[:1]
                if pick:
                    prod = pick.production_id
                if not prod:
                    pick = (group & prev).filtered(lambda l: l.production_id and l.production_id.state != 'cancel')[:1]
                    if pick:
                        prod = pick.production_id
                if not prod:
                    pick = (group & insp).filtered(lambda l: l.production_id and l.production_id.state != 'cancel')[:1]
                    if pick:
                        prod = pick.production_id
                if not prod:
                    continue
                for line in group:
                    if line.production_id != prod:
                        line.production_id = prod.id
                    if line.state != "generated":
                        line.state = "generated"

    def _compute_arrastre_map(self, stage_type, product_ids):
        """Compute arrastre por producto tomando órdenes previas de la misma etapa."""
        self.ensure_one()
        if not product_ids:
            return {}
        domain = [
            ('stage_type', '=', stage_type),
            ('state', '!=', 'cancel'),
            ('id', '!=', self.id),
        ]
        if self.date_planned:
            domain.append(('date_planned', '<', self.date_planned))
        previous_orders = self.search(domain)
        planned = defaultdict(float)
        real = defaultdict(float)
        product_set = set(product_ids)
        for order in previous_orders:
            lines = order._get_stage_lines(stage_type)
            for line in lines.filtered(lambda l: l.product_id and l.product_id.id in product_set):
                planned[line.product_id.id] += (line.product_qty or 0.0)
                real[line.product_id.id] += line.cantidad_real or 0.0
        result = {}
        for pid, qty in planned.items():
            diff = qty - real.get(pid, 0.0)
            if diff > 0:
                result[pid] = diff
        return result

    def _apply_arrastre_to_lines(self, lines, arrastre_map):
        """Assign arrastre to one line per product to avoid duplicating carry-over."""
        if not lines:
            return
        remaining = dict(arrastre_map or {})
        for line in lines.sorted(key=lambda l: l.id or 0):
            pid = line.product_id.id if line.product_id else False
            if not pid:
                line.arrastre_qty = 0.0
                continue
            arr = remaining.get(pid, 0.0)
            line.arrastre_qty = arr if arr else 0.0
            if arr:
                remaining[pid] = 0.0
            elif pid not in arrastre_map:
                line.arrastre_qty = 0.0

    def action_view_mos_tab(self):
        self.ensure_one()
        tab = self.env.context.get('mrp_tab')
        lines = self._get_lines_by_tab(self, tab)
        prod_ids = lines.mapped('production_id').ids
        action = self.env.ref("mrp.mrp_production_action").sudo().read()[0]
        action["domain"] = [("id", "in", prod_ids)] if prod_ids else [("id", "=", 0)]
        action["context"] = {"search_default_groupby_product": 1}
        return action

    def action_export_xls_tab(self):
        self.ensure_one()
        tab = (self.env.context or {}).get('mrp_tab')
        fields = self._get_export_tab_fields(tab)
        if not fields:
            raise UserError(_("No se pudo determinar las columnas para exportar."))
        lines = self._get_lines_by_tab(self, tab)
        if not lines:
            raise UserError(_("No hay líneas para exportar en esta pestaña."))

        try:
            import xlsxwriter  # type: ignore
        except Exception:
            try:
                from odoo.tools.misc import xlsxwriter  # type: ignore
            except Exception:
                raise UserError(_("No se puede exportar a XLS porque falta la librería Python 'xlsxwriter' en el servidor."))

        field_info = self.env["mrp.master.order.line"].fields_get(fields)
        headers = [field_info[name]["string"] for name in fields]
        data = lines.export_data(fields)["datas"]

        output = io.BytesIO()
        workbook = xlsxwriter.Workbook(output, {"in_memory": True})
        sheet_name = self._get_export_tab_label(tab)[:31] or "XLS"
        sheet = workbook.add_worksheet(sheet_name)
        header_format = workbook.add_format({"bold": True})
        for col_idx, header in enumerate(headers):
            sheet.write(0, col_idx, header, header_format)
        for row_idx, row in enumerate(data, start=1):
            for col_idx, value in enumerate(row):
                sheet.write(row_idx, col_idx, value if value is not None else "")
        workbook.close()
        output.seek(0)

        safe_name = re.sub(r"[^A-Za-z0-9_-]+", "_", self.name or "orden").strip("_") or "orden"
        safe_label = re.sub(r"[^A-Za-z0-9_-]+", "_", self._get_export_tab_label(tab)).strip("_") or "lineas"
        filename = f"{safe_name}_{safe_label}.xlsx"
        attachment = self.env["ir.attachment"].create({
            "name": filename,
            "type": "binary",
            "datas": base64.b64encode(output.read()),
            "mimetype": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "res_model": self._name,
            "res_id": self.id,
        })
        return {
            "type": "ir.actions.act_url",
            "url": "/web/content/%s?download=true" % attachment.id,
            "target": "self",
        }

    def action_view_wos_tab(self):
        self.ensure_one()
        tab = self.env.context.get('mrp_tab')
        lines = self._get_lines_by_tab(self, tab)
        prod_ids = lines.mapped('production_id').ids
        domain = [('production_id', 'in', prod_ids if prod_ids else [0])]
        if tab == 'corte':
            domain.append(('name', '=', 'Corte de PVB'))
        op_filter = (self.env.context or {}).get('mrp_operation_filter')
        if op_filter:
            domain.append(('operation_id.name', 'ilike', op_filter))
        Workorder = self.env['mrp.workorder']
        ctx = {'search_default_groupby_production': 1}
        order = 'production_id, id'
        if 'product_id' in Workorder._fields:
            ctx['search_default_groupby_product_id'] = 1
            order = 'product_id, id'
        return {
            'type': 'ir.actions.act_window',
            'name': _('Órdenes de Trabajo'),
            'res_model': 'mrp.workorder',
            'view_mode': 'tree,form,calendar,pivot,graph',
            'domain': domain,
            'context': ctx,
            'order': order,
        }
    def _increment_type_sequence(self):
        for rec in self:
            t = rec.type_id
            if rec.stage_type == 'opt' and 'opt_next_number' in t._fields:
                t.sudo().write({"opt_next_number": t.opt_next_number + 1})
            else:
                t.sudo().write({"next_number": t.next_number + 1})

    def _find_bom(self, product, company_id):
        Bom = self.env["mrp.bom"]
        try:
            bom = Bom._bom_find(product, company_id)
            if bom:
                return bom
        except TypeError:
            pass
        except Exception:
            pass
        bom = Bom.search([("product_id", "=", product.id), ("company_id", "in", [company_id, False])], limit=1)
        if not bom and product.product_tmpl_id:
            bom = Bom.search([("product_tmpl_id", "=", product.product_tmpl_id.id), ("company_id", "in", [company_id, False])], limit=1)
        return bom

    def _generate_mo_for_line(self, rec, line, index):
        bom = self._find_bom(line.product_id, rec.company_id.id)
        if not bom:
            raise ValidationError(_("Línea %s: El producto %s no tiene LdM.") % (index, line.product_id.display_name))
        if not line.pedido_original_id:
            raise ValidationError(_("Línea %s: Debe especificar un 'Pedido original' (PED-...).") % index)
        # Generar la MO únicamente con la cantidad programada (sin arrastre)
        qty_to_use = line.product_qty
        vals = {
            "product_id": line.product_id.id,
            "product_qty": qty_to_use,
            "product_uom_id": line.uom_id.id,
            "company_id": rec.company_id.id,
            "origin": rec.name,
            "date_start": rec.date_planned,
        }
        if rec.location_dest_id:
            vals["location_dest_id"] = rec.location_dest_id.id
        mo = self.env["mrp.production"].create(vals)
        if "x_studio_pedido_original" in mo._fields:
            mo.write({"x_studio_pedido_original": line.pedido_original_id.name})
        mo.master_order_id = rec.id
        AutoConfirm = self.env["ir.config_parameter"].sudo().get_param("mrp_master.auto_confirm_mo", "True") == "True"
        if AutoConfirm:
            mo.action_confirm()
        line.state = "generated"
        line.production_id = mo.id
        return mo

    def _reset_missing_mos(self, lines):
        """Reabrir líneas marcadas como generadas cuya MO ya no existe o está cancelada."""
        for line in lines.filtered(lambda l: l.state == "generated"):
            prod = line.production_id
            if (not prod) or getattr(prod, "state", False) == "cancel":
                line.state = "draft"
                line.production_id = False

    def button_confirm(self):
        # Permisos: solo Administrador de Fabricación o Ajustes pueden confirmar
        user = self.env.user
        if not (user.has_group('mrp.group_mrp_manager') or user.has_group('base.group_system')):
            raise ValidationError(_('No tiene permisos para confirmar.'))
        self._assign_code_on_confirm()
        for rec in self:
            lines = rec._get_lines_for_generation()
            rec._reset_missing_mos(lines)
            if not lines:
                raise ValidationError(_("Debe agregar al menos una línea."))
            errors = []
            for i, line in enumerate(lines, start=1):
                if line.state == "generated":
                    continue
                if line.production_id and line.production_id.exists() and line.production_id.state != 'cancel':
                    line.state = "generated"
                    continue
                try:
                    self._generate_mo_for_line(rec, line, i)
                except Exception as e:
                    errors.append(str(e))
            if errors:
                raise ValidationError("\n".join(errors))
            rec.state = "confirmed"
            rec._increment_type_sequence()
            rec._sync_opt_production_links()
        return True

    def action_confirm_corte_pvb(self):
        """Confirma corte PVB aplicando delta a stock cabina (piezas en cabina)."""
        for rec in self:
            lines = getattr(rec, 'line_ids_corte', self.env['mrp.master.order.line'])
            if lines:
                lines._ensure_pvb_defaults()
                lines._apply_corte_confirmation()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Corte PVB"),
                "message": _("Cantidades de PVB cortado confirmadas y stock de cabina actualizado."),
                "type": "success",
                "sticky": False,
            },
        }

    def button_generate_pending(self):
        for rec in self:
            lines = rec._get_lines_for_generation()
            rec._reset_missing_mos(lines)
            for line in lines.filtered(lambda l: l.state != "generated" and l.production_id and l.production_id.state != 'cancel'):
                line.state = "generated"
            pending = lines.filtered(lambda l: l.state != "generated")
            if not pending:
                raise ValidationError(_("No hay líneas pendientes por generar."))
            errors = []
            for i, line in enumerate(pending, start=1):
                try:
                    self._generate_mo_for_line(rec, line, i)
                except Exception as e:
                    errors.append(str(e))
            if errors:
                raise ValidationError("\n".join(errors))
            rec._sync_opt_production_links()
        return True

    def action_generate_pending_tab(self):
        self.ensure_one()
        tab = (self.env.context or {}).get('mrp_tab')
        lines = self._get_lines_by_tab(self, tab)
        self._reset_missing_mos(lines)
        for line in lines.filtered(lambda l: l.state != "generated" and l.production_id and l.production_id.state != 'cancel'):
            line.state = "generated"
        pending = lines.filtered(lambda l: l.state != "generated")
        if not pending:
            raise ValidationError(_("No hay líneas pendientes por generar."))
        errors = []
        for i, line in enumerate(pending, start=1):
            try:
                self._generate_mo_for_line(self, line, i)
            except Exception as e:
                errors.append(str(e))
        if errors:
            raise ValidationError("\n".join(errors))
        self._sync_opt_production_links()
        return True

    def action_refresh_lines_data(self):
        """Refrescar datos pesados (pedidos disponibles y sobrante PVB) bajo demanda."""
        self.ensure_one()
        start = fields.Datetime.now()
        lines = (
            getattr(self, 'line_ids_hp_t1', self.env['mrp.master.order.line']) |
            getattr(self, 'line_ids_hp_t2', self.env['mrp.master.order.line']) |
            getattr(self, 'line_ids_hg_t1', self.env['mrp.master.order.line']) |
            getattr(self, 'line_ids_hg_t2', self.env['mrp.master.order.line']) |
            getattr(self, 'line_ids_corte', self.env['mrp.master.order.line']) |
            getattr(self, 'line_ids_ensamblado', self.env['mrp.master.order.line']) |
            getattr(self, 'line_ids_prevaciado', self.env['mrp.master.order.line']) |
            getattr(self, 'line_ids_inspeccion_final', self.env['mrp.master.order.line']) |
            getattr(self, 'line_ids', self.env['mrp.master.order.line'])
        )
        if lines:
            lines_ctx = lines.with_context(skip_needs_refresh=True)
            lines_ctx._compute_available_pedidos()
            lines_ctx._compute_sobrante_pvb()
            lines_ctx._ensure_pvb_defaults()
            lines_ctx._compute_pvb_data()
            lines_ctx._refresh_pvb_quantities()
            lines_ctx._compute_longitud_calc()
            lines_ctx._compute_m2_lote()
        self.write({
            'needs_refresh': False,
            'last_refresh_at': fields.Datetime.now(),
        })
        _logger.info(
            "MRP refresh: master_id=%s lines=%s duration=%s",
            self.id,
            len(lines),
            (fields.Datetime.now() - start) if start else None,
        )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Actualización"),
                "message": _("Datos de pedidos y sobrantes actualizados."),
                "type": "success",
                "sticky": False,
            },
        }

    @api.depends()
    def _compute_available_product_ids_legacy(self):
        for rec in self:
            rec.available_product_ids = [(6, 0, [])]

    @api.depends()
    def _compute_uom_id_legacy(self):
        for rec in self:
            rec.uom_id = False

    @api.depends()
    def _compute_product_id_legacy(self):
        for rec in self:
            rec.product_id = False


class MrpMasterOrderLine(models.Model):

    @api.model
    def read_group(self, domain, fields, groupby, offset=0, limit=None, orderby=False, lazy=True):
        if self.env.context.get('sum_exclude_canceled'):
            domain = list(domain or [])
            domain = ['|', ('production_id', '=', False), ('production_id.state', '!=', 'cancel')] + domain
        return super().read_group(domain, fields, groupby, offset=offset, limit=limit, orderby=orderby, lazy=lazy)

    _name = "mrp.master.order.line"
    _description = "Línea de Orden Maestra de Producción"
    mo_state = fields.Char(string='Estado MO', compute='_compute_mo_state', store=True, readonly=True)
    _order = "id"

    def read(self, fields=None, load=None):
        start = time.perf_counter()
        res = super().read(fields=fields, load=load)
        _log_timing("mrp.master.order.line.read", start, f"ids={len(self)} fields={len(fields or [])}")
        return res

    def web_read(self, fields_spec=None, specification=None):
        start = time.perf_counter()
        if specification is not None:
            res = super().web_read(specification)
            fields_len = len(specification or [])
        else:
            res = super().web_read(fields_spec)
            fields_len = len(fields_spec or [])
        _log_timing("mrp.master.order.line.web_read", start, f"ids={len(self)} fields={fields_len}")
        return res

    master_id = fields.Many2one("mrp.master.order", string="Orden Maestra", required=False, ondelete="cascade", index=True)
    state = fields.Selection([("draft", "Borrador"), ("generated", "Generada")], default="draft", string="Estado", readonly=True, copy=False)

    # Campos de permisos para la vista (controlan readonly)
    can_edit_ensamblado = fields.Boolean(compute='_compute_can_edit_permissions', store=False)
    can_edit_prevaciado = fields.Boolean(compute='_compute_can_edit_permissions', store=False)
    can_edit_inspeccion = fields.Boolean(compute='_compute_can_edit_permissions', store=False)
    show_novedades_corte = fields.Boolean(compute='_compute_show_novedades', store=False)
    show_novedades_inspeccion = fields.Boolean(compute='_compute_show_novedades', store=False)

    @api.depends_context('uid')
    def _compute_can_edit_permissions(self):
        user = self.env.user
        for line in self:
            # Resolver la orden maestra padre desde cualquiera de los campos posibles
            master = (
                line.master_id or 
                getattr(line, 'master_id_hp_t1', False) or getattr(line, 'master_id_hp_t2', False) or
                getattr(line, 'master_id_hg_t1', False) or getattr(line, 'master_id_hg_t2', False) or
                getattr(line, 'master_id_corte', False) or getattr(line, 'master_id_ensamblado', False) or
                getattr(line, 'master_id_prevaciado', False) or getattr(line, 'master_id_inspeccion_final', False)
            )
            mtype = master.type_id if master else False
            
            # Si hay usuarios configurados, solo ellos pueden editar. Si esta vacio, todos pueden (True).
            if mtype and mtype.opt_users_ensamblado_ids:
                line.can_edit_ensamblado = user in mtype.opt_users_ensamblado_ids
            else:
                line.can_edit_ensamblado = True

            if mtype and mtype.opt_users_prevaciado_ids:
                line.can_edit_prevaciado = user in mtype.opt_users_prevaciado_ids
            else:
                line.can_edit_prevaciado = True

            if mtype and mtype.opt_users_inspeccion_ids:
                line.can_edit_inspeccion = user in mtype.opt_users_inspeccion_ids
            else:
                line.can_edit_inspeccion = True

    @api.depends('master_id_corte', 'master_id_inspeccion_final')
    def _compute_show_novedades(self):
        for line in self:
            line.show_novedades_corte = bool(getattr(line, 'master_id_corte', False))
            line.show_novedades_inspeccion = bool(getattr(line, 'master_id_inspeccion_final', False))

    type_id = fields.Many2one(related="master_id.type_id", string="Tipo (rel)", store=True)
    
    tab = fields.Selection([
        ('t1', 'T1'),
        ('t2', 'T2'),
        ('hp_t1', 'Horno P - T1'),
        ('hp_t2', 'Horno P - T2'),
        ('hg_t1', 'Horno G - T1'),
        ('hg_t2', 'Horno G - T2'),
        ('corte', 'Corte'),
        ('ensamblado', 'Ensamblado'),
        ('prevaciado', 'Prevaciado'),
        ('inspeccion_final', 'Inspeccion Final')
    ], string='Pestana', default=lambda self: self.env.context.get('default_tab') or 't1', index=True)
    product_id = fields.Many2one("product.product", string="Producto", required=True, index=True)

    available_product_ids = fields.Many2many('product.product', string='Productos disponibles', compute='_compute_available_products', store=False)
    available_pedido_ids = fields.Many2many('mrp.pedido.original', string='Pedidos disponibles', compute='_compute_available_pedidos', store=False)
    pedido_create_allowed = fields.Boolean(string="Permitir crear pedido", compute="_compute_pedido_create_allowed", store=False)
    display_index = fields.Integer("N", compute="_compute_display_index", store=False)

    @api.depends('type_id')
    def _compute_available_products(self):
        start = time.perf_counter()
        Product = self.env['product.product']
        if self.env.context.get('skip_heavy_compute'):
            for line in self:
                line.available_product_ids = Product.browse()
            return
        cache = {}
        lines_by_type = {}
        for line in self:
            type_rec = line.type_id if (line.type_id and line.type_id.categ_id) else False
            key = type_rec.id if type_rec else False
            if key not in lines_by_type:
                lines_by_type[key] = self.env['mrp.master.order.line']
            lines_by_type[key] |= line
        for key, lines in lines_by_type.items():
            if not key:
                lines.available_product_ids = Product.browse()
                continue
            if key not in cache:
                type_rec = lines[0].type_id
                cache[key] = Product.search([('categ_id', 'child_of', type_rec.categ_id.id)])
            lines.available_product_ids = cache[key]
        _log_timing("mrp.master.order.line._compute_available_products", start, f"lines={len(self)}")

    @api.depends('product_id')
    def _compute_available_pedidos(self):
        start = time.perf_counter()
        Pedido = self.env['mrp.pedido.original']
        Production = self.env['mrp.production']
        all_pedidos = None
        if self.env.context.get('skip_heavy_compute'):
            for line in self:
                line.available_pedido_ids = Pedido.browse()
            return

        def _get_all_pedidos():
            nonlocal all_pedidos
            if all_pedidos is None:
                all_pedidos = Pedido.search([])
            return all_pedidos

        lines_with_product = self.filtered(lambda l: l.product_id)
        lines_no_product = self - lines_with_product
        if lines_no_product:
            all_rec = _get_all_pedidos()
            for line in lines_no_product:
                line.available_pedido_ids = all_rec

        if not lines_with_product:
            return

        lines_to_filter = lines_with_product.filtered(lambda l: l._pedido_validation_enabled())
        lines_no_filter = self - lines_to_filter
        if lines_no_filter:
            all_rec = _get_all_pedidos()
            for line in lines_no_filter:
                line.available_pedido_ids = all_rec

        if not lines_to_filter:
            return

        for line in lines_to_filter:
            line.available_pedido_ids = Pedido.browse()

        lines_no_suffix = lines_to_filter.filtered(
            lambda l: not l._extract_code_suffix(l.product_id.default_code)
        )
        if lines_no_suffix:
            all_rec = _get_all_pedidos()
            for line in lines_no_suffix:
                line.available_pedido_ids = all_rec
        lines_to_filter = lines_to_filter - lines_no_suffix
        if not lines_to_filter:
            return

        now = fields.Datetime.now()
        grouped_by_days = {}
        for line in lines_to_filter:
            days = line._get_pedido_lookup_days()
            grouped_by_days.setdefault(days, self.env['mrp.master.order.line'])
            grouped_by_days[days] |= line

        for days, group_lines in grouped_by_days.items():
            suffix_map = {}
            prod_ids = set()
            for line in group_lines:
                suffix = line._extract_code_suffix(line.product_id.default_code)
                if suffix:
                    suffix_map.setdefault(suffix, []).append(line)
                if line.product_id:
                    prod_ids.add(line.product_id.id)
            if not suffix_map:
                continue
            cutoff = now - timedelta(days=days)
            domain = [
                ('state', '!=', 'cancel'),
                ('origin', '!=', False),
                ('date_start', '>=', cutoff),
            ]
            if prod_ids:
                domain.append(('product_id', 'in', list(prod_ids)))
            mos = Production.search(domain)
            suffixes = set(suffix_map.keys())
            pedido_by_suffix = {s: set() for s in suffixes}
            for mo in mos:
                suffix = group_lines._extract_code_suffix(getattr(mo.product_id, 'default_code', False))
                if not suffix or suffix not in suffixes:
                    continue
                name_val = (getattr(mo, 'origin', False) or '').strip()
                if name_val:
                    pedido_by_suffix[suffix].add(name_val)

            all_names = set()
            for names in pedido_by_suffix.values():
                all_names.update(names)
            existing = Pedido.browse()
            name_to_id = {}
            if all_names:
                existing = Pedido.search([('name', 'in', list(all_names))])
                name_to_id = {rec.name: rec.id for rec in existing}

            for suffix, lines_for_suffix in suffix_map.items():
                names = pedido_by_suffix.get(suffix, set())
                if not names:
                    for line in lines_for_suffix:
                        line.available_pedido_ids = Pedido.browse()
                    continue
                ids = [name_to_id[n] for n in names if n in name_to_id]
                recs = Pedido.browse(ids)
                for line in lines_for_suffix:
                    line.available_pedido_ids = recs
        _log_timing("mrp.master.order.line._compute_available_pedidos", start, f"lines={len(self)}")

    def _pedido_validation_enabled(self):
        tipo = self.type_id
        if not tipo:
            master = (
                self.master_id or getattr(self, 'master_id_hp_t1', False) or getattr(self, 'master_id_hp_t2', False) or
                getattr(self, 'master_id_hg_t1', False) or getattr(self, 'master_id_hg_t2', False) or
                getattr(self, 'master_id_corte', False) or getattr(self, 'master_id_ensamblado', False) or
                getattr(self, 'master_id_prevaciado', False) or getattr(self, 'master_id_inspeccion_final', False)
            )
            tipo = getattr(master, 'type_id', False)
        return bool(tipo and getattr(tipo, 'validate_pedido_product', False))

    def _get_pedido_lookup_days(self):
        """Ventana en días para autollenar pedidos; configurable por tipo, fallback a 30."""
        tipo = self.type_id
        if not tipo:
            master = (
                self.master_id or getattr(self, 'master_id_hp_t1', False) or getattr(self, 'master_id_hp_t2', False) or
                getattr(self, 'master_id_hg_t1', False) or getattr(self, 'master_id_hg_t2', False) or
                getattr(self, 'master_id_corte', False) or getattr(self, 'master_id_ensamblado', False) or
                getattr(self, 'master_id_prevaciado', False) or getattr(self, 'master_id_inspeccion_final', False)
            )
            tipo = getattr(master, 'type_id', False)
        days_val = getattr(tipo, 'pedido_autofill_days', 0) or 0
        try:
            days = int(days_val)
        except Exception:
            days = 0
        return days if days > 0 else 30

    def _compute_pedido_create_allowed(self):
        start = time.perf_counter()
        if self.env.context.get('skip_heavy_compute'):
            for line in self:
                line.pedido_create_allowed = False
            return
        for line in self:
            line.pedido_create_allowed = line._pedido_creation_allowed()
        _log_timing("mrp.master.order.line._compute_pedido_create_allowed", start, f"lines={len(self)}")

    @api.onchange('pedido_original_id')
    def _onchange_pedido_original_id(self):
        for line in self:
            if not line.pedido_original_id:
                continue
            available_ids = set(line.available_pedido_ids.ids or [])
            if available_ids and line.pedido_original_id.id not in available_ids:
                return {
                    'warning': {
                        'title': _('Confirmación'),
                        'message': _(
                            "Está usando el pedido %s que no está en las sugerencias para este producto. "
                            "¿Desea continuar?"
                        ) % (line.pedido_original_id.name or ''),
                    }
                }


    @api.depends(
        'sequence',
        'master_id', 'master_id_hp_t1', 'master_id_hp_t2', 'master_id_hg_t1', 'master_id_hg_t2',
        'master_id_corte', 'master_id_ensamblado', 'master_id_prevaciado', 'master_id_inspeccion_final'
    )
    def _compute_display_index(self):
        """Numerar filas en las grillas de manera consecutiva, independiente del campo sequence."""
        parent_map = {}
        for line in self:
            parent = (
                line.master_id or line.master_id_hp_t1 or line.master_id_hp_t2 or
                line.master_id_hg_t1 or line.master_id_hg_t2 or line.master_id_corte or
                line.master_id_ensamblado or line.master_id_prevaciado or line.master_id_inspeccion_final
            )
            parent_map.setdefault(parent.id if parent else 0, []).append(line)
        for lines in parent_map.values():
            for idx, l in enumerate(sorted(lines, key=lambda r: (r.sequence or 0, r.id or 0)), start=1):
                l.display_index = idx

    tipo_pvb = fields.Char("Tipo PVB", compute="_compute_pvb_data", store=True)
    ancho_pvb = fields.Char("Ancho", compute="_compute_pvb_data", store=True)
    longitud_corte = fields.Char("Longitud de corte", compute="_compute_pvb_data", store=True)
    aux_ancho_bom = fields.Char("Aux Ancho BoM", compute="_compute_pvb_data", store=True)
    aux_ancho_receta = fields.Char("Aux Ancho Receta", compute="_compute_pvb_data", store=True)
    pvb_number = fields.Char("No.")
    codigo_rollo = fields.Char("Codigo Rollo")
    piezas_pvb = fields.Float("Piezas PVB", default=0.0)
    # Stock virtual de cabina PVB
    sobrante_pvb = fields.Float("Sobrante PVB", compute="_compute_sobrante_pvb", store=True, readonly=True, digits=(16, 0))
    espesor_pvb = fields.Char("Espesor (mm)", compute="_compute_receta_pvb_fields", store=True)
    color_pvb = fields.Char("Color", compute="_compute_receta_pvb_fields", store=True)
    ficha_pvb = fields.Char("Ficha", compute="_compute_receta_pvb_fields", store=True)
    longitud_calc = fields.Float("Longitud", compute="_compute_longitud_calc", store=True, digits=(16, 0))
    cantidad_piezas = fields.Float(
        "Cantidad pzas. (num)",
        default=0.0,
        digits=(16, 1),
        help="Sugerido: 1 si product_qty=1; desde 2 en adelante = product_qty/2 (1 decimal).",
    )
    cantidad_piezas_text = fields.Char(
        "Cantidad piezas",
        help="Texto editable (usa coma). Se usa para sincronizar Cant. piezas y PVB cortado.",
    )
    ancho_mismatch = fields.Boolean("Ancho difiere de Receta", compute="_compute_pvb_data", store=True)
    pvb_cortado_qty = fields.Float(
        "PVB cortado (cantidad)",
        default=0.0,
        digits=(16, 1),
        help="Cantidad efectivamente cortada para cabina.",
    )
    pvb_cortado_text = fields.Char(
        "PVB cortado",
        help="Ingrese la cantidad real cortada o 'INV' para consumir inventario en cabina.",
    )
    pvb_inv_details = fields.Char("Detalle INV")
    pvb_inv_pending = fields.Boolean("INV pendiente", default=False)
    last_pvb_cortado_confirmed = fields.Float(
        "Último PVB cortado confirmado",
        default=0.0,
        digits=(16, 1),
        help="Se usa para calcular delta al confirmar y no perder/sobrescribir stock.",
    )
    m2_lote = fields.Float(
        "M2 Lote",
        compute="_compute_m2_lote",
        store=True,
        digits=(16, 2),
        help="PVB cortado * ancho * longitud.",
    )
    product_qty = fields.Float("Cantidad", required=True, default=1.0, help="Cantidad programada para esta línea.")
    product_qty_mo = fields.Float(
        "Cantidad",
        compute="_compute_product_qty_mo",
        inverse="_inverse_product_qty_mo",
        store=True,
        help="Muestra la cantidad desde la MO si existe; editable y sincroniza la MO.",
    )
    cantidad_ensamblada = fields.Float(
        "Cantidad ensamblada",
        help="Cantidad realmente ensamblada; se inicializa con la cantidad programada.",
    )
    arrastre_qty = fields.Float("Arrastre", default=0.0, help="Remanente pendiente de órdenes previas.")
    qty_total = fields.Float("Cantidad total", compute='_compute_qty_total', store=True, help="Cantidad total = Cantidad + Arrastre.")
    qty_to_deliver = fields.Float(
        "Cantidad a Entregar",
        compute='_compute_qty_to_deliver',
        store=True,
        readonly=True,
        help="Cantidad a entregar = Reciclo + Almacen + Segunda + CAE.",
    )
    qty_to_prevaciar = fields.Float("Cant. a Prevaciar", default=0.0, help="Cantidad a procesar en Prevaciar; inicia con la cantidad programada.")
    qty_to_liberar = fields.Float("Cant. a Liberar", default=0.0, help="Cantidad a liberar en Inspeccion Final; inicia con la cantidad programada.")
    qty_to_prevaciar_manual = fields.Boolean("Cant. a Prevaciar manual", default=False)
    qty_to_liberar_manual = fields.Boolean("Cant. a Liberar manual", default=False)
    product_qty_original = fields.Float(
        "Cantidad original",
        help="Cantidad programada inicial; se conserva al ajustar la cantidad a liberar al marcar como hecho.",
    )
    cantidad_real = fields.Float(
        'Cantidad real',
        compute='_compute_cantidad_real',
        store=True,
        readonly=True,
        help="Produccion real = cantidad procesada - desechos.",
    )
    pending_qty = fields.Float(
        "Pendiente",
        compute="_compute_pending_qty",
        store=True,
        readonly=True,
        help="Pendiente = Cantidad - Cantidad real.",
    )
    uom_id = fields.Many2one("uom.uom", string="UdM", related="product_id.uom_id", readonly=False, store=True)
    pedido_original_id = fields.Many2one("mrp.pedido.original", string="Pedido original")
    note = fields.Char("Notas")
    mark_done_selected = fields.Boolean("Seleccionar", default=False)
    reciclo_qty = fields.Integer("Reciclo", default=0, help="Piezas que se reciclan en Inspección Final.")
    almacen_qty = fields.Integer("Almacén", default=0, help="Piezas enviadas a almacén en Inspección Final.")
    x_studio_cae = fields.Integer("CAE", default=0, help="Piezas enviadas a bodega CAE en Inspección Final.")
    segunda_qty = fields.Integer("Segunda", default=0, help="Piezas de segunda calidad registradas en Inspección Final.")
    destruidos_qty = fields.Integer(
        "Destruidos",
        compute="_compute_destruidos_qty",
        store=True,
        readonly=True,
        help="Piezas destruidas (se alimenta desde los desechos registrados).",
    )
    duplicate_in_tab = fields.Boolean("Duplicado en pestaña", compute="_compute_duplicate_in_tab", store=False)
    vitrificacion_ok = fields.Boolean("Vitrificación", help="Marcar si presenta vitrificación.")
    product_code = fields.Char("Código", compute="_compute_product_code", store=True)
    largo = fields.Float("Largo")
    ancho = fields.Float("Ancho")
    scrap_qty = fields.Float("Desechos", compute="_compute_scrap_qty", store=True, readonly=True, help="Desechos registrados en la MO/OT para este producto.")
    scrap_reason = fields.Char("Razón de desecho")
    production_id = fields.Many2one("mrp.production", string="MO creada", readonly=True, copy=False, index=True)
    added_from_open_mo = fields.Boolean("Agregado desde MO abierta", default=False)
    origin_before_add = fields.Char("Origen anterior")

    def _mo_has_activity(self, production):
        if not production:
            return False
        if getattr(production, "state", "") in ("progress", "done"):
            return True
        for wo in getattr(production, "workorder_ids", self.env["mrp.workorder"]):
            if getattr(wo, "qty_produced", 0) or getattr(wo, "state", "") in ("progress", "done"):
                return True
        for mv in getattr(production, "move_finished_ids", self.env["stock.move"]):
            if mv.state == "done" and (getattr(mv, "quantity_done", 0) or getattr(mv, "product_uom_qty", 0)):
                return True
        for mv in getattr(production, "move_raw_ids", self.env["stock.move"]):
            if mv.state == "done" and (getattr(mv, "quantity_done", 0) or getattr(mv, "product_uom_qty", 0)):
                return True
        return False

    def unlink(self):
        masters = self._get_related_masters()
        for line in self:
            if not (line.added_from_open_mo and line.production_id):
                continue
            if self._mo_has_activity(line.production_id):
                continue
            parent = line.master_id_ensamblado or line.master_id_prevaciado or line.master_id_inspeccion_final
            opt_name = parent.name if parent else False
            before = (line.origin_before_add or "").strip()
            current = (line.production_id.origin or "").strip()
            if not current:
                continue
            if before:
                expected = f"{before}/{opt_name}" if opt_name else before
            else:
                expected = opt_name or current
            if expected and current == expected:
                try:
                    line.production_id.write({"origin": before})
                except Exception:
                    pass
        res = super().unlink()
        if masters:
            masters.with_context(skip_needs_refresh=True).write({'needs_refresh': True})
        return res

    def _get_related_masters(self):
        masters = self.env['mrp.master.order']
        for line in self:
            for field_name in (
                'master_id',
                'master_id_hp_t1', 'master_id_hp_t2',
                'master_id_hg_t1', 'master_id_hg_t2',
                'master_id_corte', 'master_id_ensamblado',
                'master_id_prevaciado', 'master_id_inspeccion_final',
            ):
                master = getattr(line, field_name, False)
                if master:
                    masters |= master
        return masters

    def _mark_masters_needs_refresh(self):
        if self.env.context.get('skip_needs_refresh'):
            return
        masters = self._get_related_masters()
        if masters:
            masters.with_context(skip_needs_refresh=True).write({'needs_refresh': True})

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._mark_masters_needs_refresh()
        return records

    def write(self, vals):
        res = super().write(vals)
        self._mark_masters_needs_refresh()
        return res

    def _suggest_cantidad_piezas(self, qty):
        if qty is None:
            return 0.0
        try:
            qty_val = float(qty)
        except Exception:
            return 0.0
        if qty_val <= 0:
            return 0.0
        if qty_val == 1:
            return 1.0
        if qty_val >= 2:
            return qty_val / 2.0
        return 0.0

    def _format_qty_display(self, qty):
        try:
            return f"{float(qty):.1f}".replace('.', ',')
        except Exception:
            return False

    def _format_width_display(self, val):
        try:
            return f"{float(val):.0f}"
        except Exception:
            return val or ''

    @api.depends('product_qty', 'production_id.product_qty')
    def _compute_product_qty_mo(self):
        for line in self:
            if line.production_id:
                line.product_qty_mo = line.production_id.product_qty
            else:
                line.product_qty_mo = line.product_qty

    def _inverse_product_qty_mo(self):
        for line in self:
            val = line.product_qty_mo
            if line.production_id:
                line.production_id.product_qty = val
            else:
                line.product_qty = val

    def _refresh_pvb_quantities(self):
        """Recalcula Cant. piezas y PVB cortado con los valores actuales de la línea."""
        for line in self:
            inv_text = (line.pvb_cortado_text or '').strip().lower()
            is_inv = inv_text.startswith('inv') or bool(line.pvb_inv_details)

            # Si es INV, no tocar las cantidades; solo recalcular m2 con lo existente.
            if is_inv:
                line._sync_from_cant_text()
                continue

            # Preferir el texto manual si existe; si no, sugerir por product_qty.
            line._sync_from_cant_text()
            if not line.cantidad_piezas:
                suggested = line._suggest_cantidad_piezas(line.product_qty)
                line.cantidad_piezas = suggested
                line.cantidad_piezas_text = line._format_qty_display(suggested)
                line.pvb_cortado_qty = suggested
                line.pvb_cortado_text = line._format_qty_display(suggested)
                line.pvb_inv_pending = False

    def _sync_from_cant_text(self):
        """Sincroniza cantidad_piezas_text -> qty y pvb_cortado."""
        for line in self:
            txt = (line.cantidad_piezas_text or '').strip()
            if not txt:
                continue
            if txt.lower() == 'inv':
                line.cantidad_piezas = 0.0
                line.pvb_cortado_qty = 0.0
                line.pvb_cortado_text = 'INV'
                line.pvb_inv_pending = True
                continue
            try:
                val = float(txt.replace(',', '.'))
                line.cantidad_piezas = val
                line.pvb_cortado_qty = val
                line.pvb_cortado_text = line._format_qty_display(val)
                line.cantidad_piezas_text = line._format_qty_display(val)
                line.pvb_inv_pending = False
            except Exception:
                continue

    def _ensure_pvb_defaults(self):
        """Rellenar valores básicos de PVB cuando estén vacíos (para datos antiguos)."""
        for line in self:
            suggested = line._suggest_cantidad_piezas(line.product_qty)
            if not line.cantidad_piezas:
                line.cantidad_piezas = suggested
            if not line.cantidad_piezas_text:
                line.cantidad_piezas_text = line._format_qty_display(line.cantidad_piezas)
            txt = (line.pvb_cortado_text or '').strip()
            if not txt:
                line.pvb_cortado_qty = suggested
                line.pvb_cortado_text = line._format_qty_display(suggested)
                line.pvb_inv_details = False
                line.pvb_inv_pending = False
            else:
                txt_clean = txt.replace('.', ',')
                line.pvb_cortado_text = txt_clean
                if (line.pvb_cortado_qty in (False, None)) and txt.lower() != 'inv':
                    try:
                        parsed = float(txt_clean.replace(',', '.'))
                        line.pvb_cortado_qty = parsed
                        line.pvb_cortado_text = line._format_qty_display(parsed)
                    except Exception:
                        line.pvb_cortado_qty = suggested
                if txt.lower() == 'inv':
                    line.pvb_cortado_qty = 0.0
            # Si hay texto en cant_piezas_text, sincronizar
            line._sync_from_cant_text()
            # Si sigue vacío el texto, forzar desde el numérico
            if not line.cantidad_piezas_text:
                line.cantidad_piezas_text = line._format_qty_display(line.cantidad_piezas)
            if not line.pvb_cortado_text:
                line.pvb_cortado_text = line._format_qty_display(line.pvb_cortado_qty)

    @api.onchange('product_qty')
    def _onchange_product_qty(self):
        for line in self:
            suggested = line._suggest_cantidad_piezas(line.product_qty)
            line.cantidad_piezas = suggested
            line.cantidad_piezas_text = line._format_qty_display(suggested)

            # Mantener INV/manual solo si está marcado; de lo contrario, sincronizar con Cant. piezas
            inv_text = (line.pvb_cortado_text or '').strip().lower()
            is_inv = inv_text.startswith('inv')
            if not line.pvb_inv_details and not is_inv:
                line.pvb_cortado_qty = suggested
                line.pvb_cortado_text = line._format_qty_display(suggested)
                line.pvb_inv_pending = False

    @api.onchange('cantidad_piezas_text')
    def _onchange_cantidad_piezas_text(self):
        for line in self:
            line._sync_from_cant_text()

    @api.onchange('cantidad_piezas')
    def _onchange_cantidad_piezas(self):
        for line in self:
            # For new lines, _origin does not exist.
            if not line._origin:
                line.pvb_cortado_qty = line.cantidad_piezas or 0.0
                line.pvb_cortado_text = line._format_qty_display(line.pvb_cortado_qty)
                line.cantidad_piezas_text = line._format_qty_display(line.cantidad_piezas)
                continue

            if line.pvb_inv_details:
                continue
            
            prev_cut = line._origin.pvb_cortado_qty or 0.0
            if (line.pvb_cortado_qty in (0.0, None)) or abs((line.pvb_cortado_qty or 0.0) - prev_cut) < 0.00001:
                line.pvb_cortado_qty = line.cantidad_piezas or 0.0
                line.pvb_cortado_text = line._format_qty_display(line.pvb_cortado_qty)
                line.pvb_inv_pending = False
                line.pvb_inv_details = False
            line.cantidad_piezas_text = line._format_qty_display(line.cantidad_piezas)

    @api.onchange('pvb_cortado_text')
    def _onchange_pvb_cortado_text(self):
        for line in self:
            txt = (line.pvb_cortado_text or '').strip()
            if not txt:
                line.pvb_cortado_qty = 0.0
                line.pvb_inv_details = False
                line.pvb_inv_pending = False
                continue
            # Intentar parsear número
            try:
                parsed = float(txt.replace(',', '.'))
                line.pvb_cortado_qty = parsed
                line.pvb_cortado_text = line._format_qty_display(parsed)
                line.pvb_inv_details = False
                line.pvb_inv_pending = False
                continue
            except Exception:
                pass
            # INV → disparador de consumo de stock cabina
            if txt.lower().startswith('inv'):
                line.pvb_cortado_qty = 0.0
                line.pvb_inv_details = False
                line.pvb_inv_pending = True
            else:
                # Si escribe un texto como 223X->3, lo guardamos como detalle pero no suma stock
                line.pvb_cortado_qty = 0.0
                line.pvb_inv_details = txt
                line.pvb_inv_pending = False

    @api.depends('product_qty', 'arrastre_qty')
    def _compute_qty_total(self):
        for line in self:
            line.qty_total = (line.product_qty or 0.0) + (line.arrastre_qty or 0.0)

    @api.depends('reciclo_qty', 'almacen_qty', 'segunda_qty', 'x_studio_cae')
    def _compute_qty_to_deliver(self):
        for line in self:
            line.qty_to_deliver = (
                (line.reciclo_qty or 0.0)
                + (line.almacen_qty or 0.0)
                + (line.segunda_qty or 0.0)
                + (line.x_studio_cae or 0.0)
            )

    @api.depends('scrap_qty')
    def _compute_destruidos_qty(self):
        for line in self:
            line.destruidos_qty = int(round(line.scrap_qty or 0.0))

    @api.depends('product_qty', 'cantidad_real')
    def _compute_pending_qty(self):
        for line in self:
            pending = (line.product_qty or 0.0) - (line.cantidad_real or 0.0)
            line.pending_qty = pending if pending > 0 else 0.0

    def _get_scrap_totals_by_production(self, productions):
        totals = {prod.id: 0.0 for prod in productions}
        if not productions:
            return totals
        workorders = productions.workorder_ids
        domain = []
        if workorders:
            domain = ['|', ('workorder_id', 'in', workorders.ids), ('production_id', 'in', productions.ids)]
        else:
            domain = [('production_id', 'in', productions.ids)]
        Scrap = self.env['stock.scrap']
        if 'state' in Scrap._fields:
            domain = [('state', '!=', 'cancel')] + domain
        scrap_records = Scrap.search(domain)
        for sc in scrap_records:
            prod = sc.production_id or sc.workorder_id.production_id
            if not prod or prod.id not in totals:
                continue
            if sc.product_id and prod.product_id and sc.product_id.id != prod.product_id.id:
                continue
            if 'scrap_qty' in Scrap._fields:
                qty = sc.scrap_qty or 0.0
            else:
                qty = getattr(sc, 'quantity', 0.0) or 0.0
            totals[prod.id] += qty
        return totals

    @api.depends(
        'production_id',
        'production_id.workorder_ids',
        'production_id.workorder_ids.scrap_ids.scrap_qty',
        'production_id.workorder_ids.scrap_ids.product_id',
    )
    def _compute_scrap_qty(self):
        productions = self.mapped('production_id').filtered(lambda p: p)
        scrap_by_production = self._get_scrap_totals_by_production(productions)
        for line in self:
            production = line.production_id
            line.scrap_qty = scrap_by_production.get(production.id, 0.0) if production else 0.0

    def _normalize_workcenter_name(self, name):
        text = (name or '').strip()
        text = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode('ascii')
        return text.lower()

    def _get_stage_key_from_workorder(self, workorder):
        name = ''
        if workorder and workorder.workcenter_id:
            name = workorder.workcenter_id.name or ''
        if not name and workorder and workorder.operation_id:
            name = workorder.operation_id.name or ''
        name = self._normalize_workcenter_name(name)
        if 'ensambl' in name:
            return 'ensamblado'
        if 'prevaciado' in name or 'autoclave' in name:
            return 'prevaciado'
        if 'inspeccion final' in name or 'inspeccion' in name:
            return 'inspeccion_final'
        return False

    def _get_scrap_by_stage(self, productions):
        result = {prod.id: {'ensamblado': 0.0, 'prevaciado': 0.0, 'inspeccion_final': 0.0} for prod in productions}
        if not productions:
            return result
        workorders = productions.workorder_ids
        if not workorders:
            return result
        Scrap = self.env['stock.scrap']
        domain = [('workorder_id', 'in', workorders.ids)]
        if 'state' in Scrap._fields:
            domain = [('state', '!=', 'cancel')] + domain
        scrap_records = Scrap.search(domain)
        for sc in scrap_records:
            wo = sc.workorder_id
            prod = wo.production_id if wo else sc.production_id
            if not prod or prod.id not in result:
                continue
            stage = self._get_stage_key_from_workorder(wo)
            if not stage:
                continue
            if sc.product_id and prod.product_id and sc.product_id.id != prod.product_id.id:
                continue
            if 'scrap_qty' in Scrap._fields:
                qty = sc.scrap_qty or 0.0
            else:
                qty = getattr(sc, 'quantity', 0.0) or 0.0
            result[prod.id][stage] += qty
        return result

    def _compute_station_quantities(self):
        productions = self.mapped('production_id').filtered(lambda p: p)
        scrap_by_stage = self._get_scrap_by_stage(productions)
        lines = self.with_context(auto_station_qty=True, skip_station_recompute=True)
        ens_qty_by_prod = {}
        line_by_production = {}
        for line in lines:
            production = line.production_id
            if not production:
                continue
            if line.master_id_ensamblado and production.id not in ens_qty_by_prod:
                ens_qty_by_prod[production.id] = line.cantidad_ensamblada or 0.0
            if production.id not in line_by_production or line.master_id_ensamblado:
                line_by_production[production.id] = line
        for line in lines:
            production = line.production_id
            if not production:
                continue
            stage_scrap = scrap_by_stage.get(production.id, {})
            ens_scrap = stage_scrap.get('ensamblado', 0.0)
            prev_scrap = stage_scrap.get('prevaciado', 0.0)
            ens_qty = ens_qty_by_prod.get(production.id, line.cantidad_ensamblada or 0.0)
            prev_calc = max(ens_qty - ens_scrap, 0.0)
            if not line.qty_to_prevaciar_manual:
                line.qty_to_prevaciar = prev_calc
            prev_base = line.qty_to_prevaciar if line.qty_to_prevaciar_manual else prev_calc
            insp_calc = max((prev_base or 0.0) - prev_scrap, 0.0)
            if not line.qty_to_liberar_manual:
                line.qty_to_liberar = insp_calc
        self._sync_workorder_qty_producing(productions, line_by_production)

    @api.model
    def _recompute_station_qty_for_productions(self, productions):
        prods = productions.filtered(lambda p: p)
        if not prods:
            return
        lines = self.search([('production_id', 'in', prods.ids)])
        if lines:
            lines._compute_station_quantities()

    def _sync_workorder_qty_producing(self, productions, line_by_production=None):
        prods = productions.filtered(lambda p: p)
        if not prods:
            return
        if line_by_production is None:
            line_by_production = {line.production_id.id: line for line in self if line.production_id}
        workorders = prods.workorder_ids
        if not workorders:
            return
        for wo in workorders:
            line = line_by_production.get(wo.production_id.id)
            if not line:
                continue
            stage = self._get_stage_key_from_workorder(wo)
            if stage == 'ensamblado':
                qty = line.cantidad_ensamblada or 0.0
            elif stage == 'prevaciado':
                qty = line.qty_to_prevaciar or 0.0
            elif stage == 'inspeccion_final':
                qty = line.qty_to_liberar or 0.0
            else:
                continue
            vals = {}
            if 'qty_producing' in wo._fields:
                vals['qty_producing'] = qty
            if vals:
                wo.with_context(skip_opt_qty_sync=True).write(vals)

    @api.depends(
        'cantidad_ensamblada',
        'qty_to_prevaciar',
        'qty_to_liberar',
        'scrap_qty',
        'production_id',
        'production_id.workorder_ids.qty_produced',
        'production_id.workorder_ids.scrap_ids.scrap_qty',
        'production_id.workorder_ids.scrap_ids.product_id',
        'production_id.workorder_ids.scrap_ids.workorder_id',
    )
    def _compute_cantidad_real(self):
        productions = self.mapped('production_id').filtered(lambda p: p)
        workorders = productions.workorder_ids
        wo_qty_map = {wo.id: wo.qty_produced or 0.0 for wo in workorders}
        scrap_by_stage = self._get_scrap_by_stage(productions)
        ens_qty_by_prod = {}
        for line in self:
            if line.production_id and line.master_id_ensamblado and line.production_id.id not in ens_qty_by_prod:
                ens_qty_by_prod[line.production_id.id] = line.cantidad_ensamblada or 0.0
        for line in self:
            qty = 0.0
            production = line.production_id
            if production and (line.master_id_ensamblado or line.master_id_prevaciado or line.master_id_inspeccion_final):
                stage_scrap = scrap_by_stage.get(production.id, {})
                if line.master_id_ensamblado:
                    base = ens_qty_by_prod.get(production.id, line.cantidad_ensamblada or 0.0)
                    qty = base - (stage_scrap.get('ensamblado', 0.0) or 0.0)
                elif line.master_id_prevaciado:
                    base = line.qty_to_prevaciar or 0.0
                    qty = base - (stage_scrap.get('prevaciado', 0.0) or 0.0)
                elif line.master_id_inspeccion_final:
                    base = line.qty_to_liberar or 0.0
                    qty = base - (stage_scrap.get('inspeccion_final', 0.0) or 0.0)
            elif production:
                related_wos = production.workorder_ids
                produced = sum(wo_qty_map.get(wo.id, 0.0) for wo in related_wos)
                qty = produced - (line.scrap_qty or 0.0)
            line.cantidad_real = qty if qty > 0 else 0.0

    @api.depends('product_id', 'product_id.default_code')
    def _compute_product_code(self):
        for line in self:
            code = (line.product_id.default_code or '').strip()
            line.product_code = line._extract_code_suffix(code) if code else False

    @api.depends(
        'product_id',
        'master_id_ensamblado', 'master_id_prevaciado', 'master_id_inspeccion_final',
        'master_id_ensamblado.line_ids_ensamblado.product_id',
        'master_id_prevaciado.line_ids_prevaciado.product_id',
        'master_id_inspeccion_final.line_ids_inspeccion_final.product_id',
    )
    def _compute_duplicate_in_tab(self):
        parent_map = {}
        for line in self:
            parent = line.master_id_ensamblado or line.master_id_prevaciado or line.master_id_inspeccion_final
            if not parent:
                line.duplicate_in_tab = False
                continue
            if line.master_id_ensamblado:
                tab = "ensamblado"
            elif line.master_id_prevaciado:
                tab = "prevaciado"
            else:
                tab = "inspeccion_final"
            key = (parent.id, tab)
            if key not in parent_map:
                parent_map[key] = {
                    "parent": parent,
                    "tab": tab,
                    "lines": self.env['mrp.master.order.line'],
                }
            parent_map[key]["lines"] |= line

        for data in parent_map.values():
            parent = data["parent"]
            tab = data["tab"]
            if tab == "ensamblado":
                lines = parent.line_ids_ensamblado
            elif tab == "prevaciado":
                lines = parent.line_ids_prevaciado
            else:
                lines = parent.line_ids_inspeccion_final
            counts = {}
            for l in lines:
                pid = l.product_id.id if l.product_id else False
                if not pid:
                    continue
                counts[pid] = counts.get(pid, 0) + 1
            for line in data["lines"]:
                pid = line.product_id.id if line.product_id else False
                line.duplicate_in_tab = bool(pid and counts.get(pid, 0) > 1)

    def action_open_add_open_mo_wizard_line(self):
        """
        Abre el wizard para agregar MOs abiertas a la orden maestra.
        Este método debería funcionar desde la vista de árbol de líneas.
        """
        # Obtener la línea actual
        line = self[:1] if self else self.env['mrp.master.order.line'].browse(self.env.context.get('active_id'))

        # Intentar obtener el padre y la pestaña desde la línea
        parent = False
        tab = False

        # Intentar encontrar el padre desde los campos relacionales de la línea actual
        if line and len(line) == 1:
            parent = (getattr(line, 'master_id_ensamblado', False) or
                     getattr(line, 'master_id_prevaciado', False) or
                     getattr(line, 'master_id_inspeccion_final', False))

            # Determinar la pestaña según el campo relacional que tenga valor
            if getattr(line, 'master_id_ensamblado', False):
                tab = "ensamblado"
            elif getattr(line, 'master_id_prevaciado', False):
                tab = "prevaciado"
            elif getattr(line, 'master_id_inspeccion_final', False):
                tab = "inspeccion_final"

        # Si no se encontró el padre desde la línea, intentar desde el contexto
        if not parent:
            ctx = self.env.context or {}
            active_model = ctx.get("active_model")
            active_id = ctx.get("active_id")

            # Si estamos en una orden maestra directamente
            if active_model == "mrp.master.order" and active_id:
                parent = self.env["mrp.master.order"].browse(active_id)
                tab = ctx.get("mrp_tab") or ctx.get("default_tab") or "ensamblado"
            # Si estamos en una línea de orden maestra
            elif active_model == "mrp.master.order.line" and active_id:
                tmp_line = self.env["mrp.master.order.line"].browse(active_id)
                parent = (getattr(tmp_line, 'master_id_ensamblado', False) or
                         getattr(tmp_line, 'master_id_prevaciado', False) or
                         getattr(tmp_line, 'master_id_inspeccion_final', False))

                if getattr(tmp_line, 'master_id_ensamblado', False):
                    tab = "ensamblado"
                elif getattr(tmp_line, 'master_id_prevaciado', False):
                    tab = "prevaciado"
                elif getattr(tmp_line, 'master_id_inspeccion_final', False):
                    tab = "inspeccion_final"

        # Si aún no tenemos padre, intentar desde el contexto de la acción
        if not parent:
            # Buscar en el contexto de la acción de ventana
            master_id = ctx.get('default_master_id')
            if master_id:
                parent = self.env['mrp.master.order'].browse(master_id)
                tab = ctx.get("mrp_tab") or ctx.get("default_tab") or "ensamblado"

        # Si aún no encontramos el padre, intentar encontrarlo de otra manera
        if not parent:
            # Buscar en el contexto de la acción actual
            master_id = ctx.get('active_id')
            if master_id and ctx.get('active_model') == 'mrp.master.order':
                parent = self.env['mrp.master.order'].browse(master_id)
                tab = ctx.get("mrp_tab") or ctx.get("default_tab") or "ensamblado"

        # Si aún no encontramos el padre, intentar encontrarlo desde el dominio actual
        if not parent:
            # Si estamos en una acción de ventana que abre esta vista de árbol,
            # el contexto debería contener información sobre la orden maestra padre
            master_id = ctx.get('default_master_id') or ctx.get('master_id')
            if master_id:
                parent = self.env['mrp.master.order'].browse(master_id)
                tab = ctx.get("mrp_tab") or ctx.get("default_tab") or "ensamblado"

        # Si aún no encontramos el padre, lanzar un error más informativo
        if not parent:
            from odoo.exceptions import UserError
            raise UserError(_("No se pudo determinar la Orden Maestra para agregar MOs abiertas. "
                            "Asegúrese de que está trabajando dentro de una Orden Maestra válida."))

        # Llamar al método del padre para abrir el wizard
        return parent.with_context(mrp_tab=tab or "ensamblado").action_open_add_open_mo_wizard()

    def _get_parent_and_tab(self):
        line = self[:1]
        parent = False
        tab = False
        if line:
            if line.master_id_hp_t1:
                parent = line.master_id_hp_t1
                tab = "hp_t1"
            elif line.master_id_hp_t2:
                parent = line.master_id_hp_t2
                tab = "hp_t2"
            elif line.master_id_hg_t1:
                parent = line.master_id_hg_t1
                tab = "hg_t1"
            elif line.master_id_hg_t2:
                parent = line.master_id_hg_t2
                tab = "hg_t2"
            elif line.master_id_corte:
                parent = line.master_id_corte
                tab = "corte"
            elif line.master_id_ensamblado:
                parent = line.master_id_ensamblado
                tab = "ensamblado"
            elif line.master_id_prevaciado:
                parent = line.master_id_prevaciado
                tab = "prevaciado"
            elif line.master_id_inspeccion_final:
                parent = line.master_id_inspeccion_final
                tab = "inspeccion_final"
            elif line.master_id:
                parent = line.master_id
        if not parent:
            ctx = self.env.context or {}
            active_model = ctx.get("active_model")
            active_id = ctx.get("active_id")
            if active_model == "mrp.master.order" and active_id:
                parent = self.env["mrp.master.order"].browse(active_id)
            elif active_model == "mrp.master.order.line" and active_id:
                tmp_line = self.env["mrp.master.order.line"].browse(active_id)
                parent = (
                    tmp_line.master_id_hp_t1 or tmp_line.master_id_hp_t2 or tmp_line.master_id_hg_t1 or
                    tmp_line.master_id_hg_t2 or tmp_line.master_id_corte or tmp_line.master_id_ensamblado or
                    tmp_line.master_id_prevaciado or tmp_line.master_id_inspeccion_final or tmp_line.master_id
                )
            if not parent:
                default_keys = [
                    "default_master_id_hp_t1",
                    "default_master_id_hp_t2",
                    "default_master_id_hg_t1",
                    "default_master_id_hg_t2",
                    "default_master_id_corte",
                    "default_master_id_ensamblado",
                    "default_master_id_prevaciado",
                    "default_master_id_inspeccion_final",
                    "default_master_id",
                ]
                for key in default_keys:
                    val = ctx.get(key)
                    if val:
                        parent = self.env["mrp.master.order"].browse(val)
                        break
            tab = tab or ctx.get("mrp_tab") or ctx.get("default_tab")
        return parent, tab

    def _proxy_to_master(self, method_name):
        parent, tab = self._get_parent_and_tab()
        if not parent:
            return {"type": "ir.actions.act_window_close"}
        ctx = dict(self.env.context or {})
        if tab:
            ctx["mrp_tab"] = tab
        return getattr(parent.with_context(ctx), method_name)()

    def action_generate_pending_tab_line(self):
        return self._proxy_to_master("action_generate_pending_tab")

    def action_view_mos_tab_line(self):
        return self._proxy_to_master("action_view_mos_tab")

    def action_view_wos_tab_line(self):
        return self._proxy_to_master("action_view_wos_tab")

    def action_confirm_corte_pvb_line(self):
        return self._proxy_to_master("action_confirm_corte_pvb")

    def action_recalcular_corte_line(self):
        return self._proxy_to_master("action_recalcular_corte")

    def action_cargar_prevaciado_line(self):
        return self._proxy_to_master("action_cargar_prevaciado")

    def action_cargar_inspeccion_final_line(self):
        return self._proxy_to_master("action_cargar_inspeccion_final")

    def action_mark_tab_done_prompt_line(self):
        return self._proxy_to_master("action_mark_tab_done_prompt")

    def action_load_from_origin_line(self):
        return self._proxy_to_master("action_load_from_origin")

    def action_recalculate_opt_line(self):
        return self._proxy_to_master("action_recalculate_opt")

    def action_generate_warehouse_delivery_line(self):
        return self._proxy_to_master("action_generate_warehouse_delivery")

    def action_view_deliveries_line(self):
        return self._proxy_to_master("action_view_deliveries")

    @api.depends(
        'product_id', 'product_id.default_code', 'product_id.categ_id',
        'production_id',
        'production_id.bom_id',
        'production_id.bom_id.bom_line_ids.product_id',
        'production_id.bom_id.bom_line_ids.product_id.default_code',
        'production_id.bom_id.bom_line_ids.product_id.categ_id',
        'production_id.move_raw_ids.product_id',
        'production_id.move_raw_ids.product_id.default_code',
        'production_id.move_raw_ids.product_id.categ_id',
    )
    def _compute_pvb_data(self):
        Receta = self.env['receta.pvb']
        product_ids = [p.id for p in self.mapped('product_id') if p]
        codes = [p.default_code for p in self.mapped('product_id') if p and p.default_code]
        recs = Receta.search([
            '|',
            ('product_id', 'in', product_ids),
            ('product_default_code', 'in', codes)
        ]) if (product_ids or codes) else Receta.browse()
        map_prod = {rec.product_id.id: rec for rec in recs if rec.product_id}
        map_code = {rec.product_default_code: rec for rec in recs if rec.product_default_code}
        for line in self:
            tipo = False
            ancho_piece = False
            pvb_product = line._get_pvb_component_product()
            if pvb_product:
                parts = line._split_reference_parts(pvb_product.default_code)
                if parts:
                    tipo = "-".join(parts[:2]) if len(parts) >= 2 else parts[0]
                    ancho_piece = parts[2] if len(parts) >= 3 else False
            receta = False
            if line.product_id:
                receta = map_prod.get(line.product_id.id) or map_code.get(line.product_id.default_code)
            # Si no hay ancho desde la BoM/WO, usar el de la receta
            if (ancho_piece in (False, None)) and receta:
                ancho_piece = getattr(receta, 'ancho_pvb', False) or getattr(receta, 'ancho_rollo', False)
            # Si no hay tipo desde la BoM/WO, usar el de la receta
            if not tipo and receta:
                tipo = getattr(receta, 'pvb', False) or getattr(receta, 'num_pvb', False)

            ancho_val = str(ancho_piece) if ancho_piece not in (False, None) else False
            rec_ancho = getattr(receta, 'ancho_rollo', False) if receta else False
            if not ancho_val and rec_ancho:
                ancho_val = str(rec_ancho)
            ancho_mm = line._parse_width_piece(ancho_val) if ancho_val else False
            rec_ancho_mm = self._parse_width_piece(str(rec_ancho)) if rec_ancho else False
            mismatch = False
            if ancho_mm and rec_ancho_mm and abs(ancho_mm - rec_ancho_mm) > 0.01:
                mismatch = True
            line.tipo_pvb = tipo
            display_val = ancho_val or self._format_width_display(rec_ancho_mm if rec_ancho_mm is not False else rec_ancho)
            line.ancho_pvb = display_val
            long_corte = getattr(receta, 'longitud_corte', False) if receta else False
            # Si no hay receta o longitud en receta, mostrar 0 para alertar que falta dato
            line.longitud_corte = self._format_width_display(long_corte) if long_corte else "0"
            line.ancho = ancho_mm if ancho_mm is not False else rec_ancho_mm
            line.ancho_mismatch = mismatch
            line.aux_ancho_bom = self._format_width_display(ancho_mm if ancho_mm is not False else ancho_val)
            line.aux_ancho_receta = self._format_width_display(rec_ancho_mm if rec_ancho_mm is not False else rec_ancho)

    def _split_reference_parts(self, default_code):
        ref = (default_code or '').strip()
        if not ref:
            return []
        return [p.strip() for p in ref.split('-') if p.strip()]

    def _is_pvb_category(self, categ):
        if not categ:
            return False
        name = (getattr(categ, 'complete_name', False) or getattr(categ, 'name', '') or '').upper()
        return 'PVB' in name

    def _get_pvb_component_product(self):
        production = getattr(self, 'production_id', False)
        if production:
            bom = getattr(production, 'bom_id', False)
            if bom:
                for bom_line in bom.bom_line_ids:
                    product = bom_line.product_id
                    if product and self._is_pvb_category(product.categ_id):
                        return product
            raw_moves = getattr(production, 'move_raw_ids', False) or self.env['stock.move']
            for move in raw_moves:
                product = move.product_id
                if product and self._is_pvb_category(product.categ_id):
                    return product
        # Fallback: buscar en la LdM del producto aunque no exista MO generada
        if self.product_id:
            company_id = False
            master = (
                self.master_id or self.master_id_hp_t1 or self.master_id_hp_t2 or
                self.master_id_hg_t1 or self.master_id_hg_t2 or self.master_id_corte or
                self.master_id_ensamblado or self.master_id_prevaciado or self.master_id_inspeccion_final
            )
            if master and getattr(master, 'company_id', False):
                company_id = master.company_id.id
            Bom = self.env['mrp.bom']
            bom = Bom.search([
                ('product_id', '=', self.product_id.id),
                ('company_id', 'in', [company_id, False])
            ], limit=1)
            if not bom and getattr(self.product_id, 'product_tmpl_id', False):
                bom = Bom.search([
                    ('product_tmpl_id', '=', self.product_id.product_tmpl_id.id),
                    ('company_id', 'in', [company_id, False])
                ], limit=1)
            if bom:
                for bom_line in bom.bom_line_ids:
                    product = bom_line.product_id
                    if product and self._is_pvb_category(product.categ_id):
                        return product
        return False

    def _parse_width_piece(self, piece):
        if not piece:
            return False
        cleaned = piece.replace(',', '.').strip()
        match = re.match(r"(\d+(?:\.\d+)?)", cleaned)
        if match:
            try:
                return float(match.group(1))
            except Exception:
                return False
        return False

    @api.depends('product_id')
    def _compute_sobrante_pvb(self):
        Receta = self.env['receta.pvb']
        product_ids = [p.id for p in self.mapped('product_id') if p]
        codes = [p.default_code for p in self.mapped('product_id') if p and p.default_code]
        recs = Receta.search([
            '|',
            ('product_id', 'in', product_ids),
            ('product_default_code', 'in', codes)
        ]) if (product_ids or codes) else Receta
        map_prod = {rec.product_id.id: rec for rec in recs if rec.product_id}
        map_code = {rec.product_default_code: rec for rec in recs if rec.product_default_code}
        for line in self:
            rec = map_prod.get(line.product_id.id) or map_code.get(getattr(line.product_id, 'default_code', False))
            line.sobrante_pvb = rec.piezas_cabina if rec else 0.0

    @api.depends('product_id', 'product_id.default_code')
    def _compute_receta_pvb_fields(self):
        Receta = self.env['receta.pvb']
        product_ids = [p.id for p in self.mapped('product_id') if p]
        codes = [p.default_code for p in self.mapped('product_id') if p and p.default_code]
        recs = Receta.search([
            '|',
            ('product_id', 'in', product_ids),
            ('product_default_code', 'in', codes)
        ]) if (product_ids or codes) else Receta.browse()
        map_prod = {rec.product_id.id: rec for rec in recs if rec.product_id}
        map_code = {rec.product_default_code: rec for rec in recs if rec.product_default_code}
        for line in self:
            rec = False
            if line.product_id:
                rec = map_prod.get(line.product_id.id) or map_code.get(line.product_id.default_code)
            v1 = rec.v1 if rec else False
            v2 = rec.v2 if rec else False
            c1 = rec.c1 if rec else False
            c2 = rec.c2 if rec else False
            ficha = rec.ficha if rec else False
            line.espesor_pvb = f"{v1}+{v2}" if (v1 or v2) else False
            if c1 and c2:
                line.color_pvb = f"{c1}+{c2}"
            elif c1:
                line.color_pvb = c1
            elif c2:
                line.color_pvb = c2
            else:
                line.color_pvb = False
            line.ficha_pvb = ficha or False
            if rec and rec.ancho_pvb:
                line.ancho = rec.ancho_pvb

    @api.depends('product_qty', 'ancho', 'ancho_pvb', 'largo')
    def _compute_longitud_calc(self):
        for line in self:
            qty = line.product_qty or 0.0
            rec = line._get_receta_pvb()
            # Solo tomar longitud desde receta; si no existe receta o dato, dejar 0 para alertar
            base = getattr(rec, 'longitud_corte', False) or 0.0
            if qty > 1 and base and base <= 2000:
                line.longitud_calc = base * 2
            else:
                line.longitud_calc = base

    @api.depends('ancho', 'ancho_pvb', 'largo', 'longitud_calc', 'cantidad_piezas', 'pvb_cortado_qty')
    def _compute_m2_lote(self):
        for line in self:
            # ancho/longitud en mm -> dividir entre 1,000,000 para m2
            width_mm = line.ancho or line._parse_width_piece(line.ancho_pvb) or 0.0
            length_mm = line.longitud_calc or line.largo or 0.0
            pieces = line.pvb_cortado_qty or line.cantidad_piezas or 0.0
            if width_mm and length_mm and pieces:
                line.m2_lote = (width_mm * length_mm * pieces) / 1_000_000.0
            else:
                line.m2_lote = 0.0

    # --- PVB helpers ---
    def _get_receta_pvb(self):
        Receta = self.env['receta.pvb']
        rec = Receta.get_by_product(self.product_id)
        return rec

    def _apply_corte_confirmation(self):
        """Aplica delta contra stock cabina al confirmar Corte PVB."""
        for line in self:
            rec = line._get_receta_pvb()
            if not rec:
                continue
            delta = (line.pvb_cortado_qty or 0.0) - (line.last_pvb_cortado_confirmed or 0.0)
            if abs(delta) < 0.00001:
                continue
            note = False
            master = getattr(line, 'master_id_corte', False) or getattr(line, 'master_id', False)
            if master and master.name:
                note = f"OM {master.name}"
            rec._apply_cabina_delta(
                delta,
                reason="corte",
                note=note,
                master_line=line,
                production=line.production_id,
                workorder=None,
                inv_details=line.pvb_inv_details,
            )
            line.last_pvb_cortado_confirmed = line.pvb_cortado_qty
            if not line.pvb_inv_details:
                line.pvb_cortado_text = line._format_qty_display(line.pvb_cortado_qty)
            line.sobrante_pvb = rec.piezas_cabina

    def action_open_cabina_history(self):
        self.ensure_one()
        rec = self._get_receta_pvb()
        if not rec:
            raise ValidationError(_("No existe Receta PVB vinculada a este producto para mostrar historial."))
        return rec.action_open_cabina_history()

    def action_open_inv_wizard(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Consumir INV (cabina)"),
            "res_model": "pvb.cabina.inv.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_line_id": self.id,
            },
        }

    def _extract_code_suffix(self, default_code):
        """Devuelve el ultimo bloque; si termina en -Tn usa los dos ultimos bloques."""
        ref = (default_code or '').strip()
        if not ref:
            return False
        parts = [p.strip() for p in ref.split('-') if p.strip()]
        if not parts:
            return False
        if re.search(r"-T\d+$", ref, flags=re.IGNORECASE) and len(parts) >= 2:
            return "-".join(parts[-2:])
        return parts[-1]

    def _get_pedido_candidates(self, product):
        Pedido = self.env['mrp.pedido.original']
        if not product:
            return Pedido
        Production = self.env['mrp.production']

        target_suffix = self._extract_code_suffix(product.default_code)
        if not target_suffix:
            return Pedido

        days = self._get_pedido_lookup_days()
        cutoff = fields.Datetime.now() - timedelta(days=days)
        mos = Production.search([
            ('state', '!=', 'cancel'),
            ('origin', '!=', False),
            ('date_start', '>=', cutoff),
        ])
        names = set()
        for mo in mos:
            suffix = self._extract_code_suffix(getattr(mo.product_id, 'default_code', False))
            if suffix and suffix == target_suffix:
                name_val = (getattr(mo, 'origin', False) or '').strip()
                if name_val:
                    names.add(name_val)
        if not names:
            return Pedido
        existing = Pedido.search([('name', 'in', list(names))])
        return existing

    def _pedido_creation_allowed(self):
        master = (
            self.master_id or self.master_id_hp_t1 or self.master_id_hp_t2 or
            self.master_id_hg_t1 or self.master_id_hg_t2 or self.master_id_corte or
            self.master_id_ensamblado or self.master_id_prevaciado or self.master_id_inspeccion_final
        )
        mtype = getattr(master, 'type_id', False)
        return bool(mtype and getattr(mtype, 'allow_pedido_create', False))

    @api.onchange("type_id")
    def _onchange_type_domain(self):
        domain = {}
        if self.type_id and self.type_id.categ_id:
            domain["product_id"] = [("categ_id", "child_of", self.type_id.categ_id.id)]
        else:
            domain["product_id"] = []
        return {"domain": domain}

    @api.model
    def create(self, vals):
        if not vals.get('tab'):
            vals['tab'] = self.env.context.get('default_tab', 't1')
        if 'piezas_pvb' not in vals and vals.get('product_qty'):
            vals['piezas_pvb'] = vals.get('product_qty')
        qty = vals.get('product_qty')
        if 'qty_to_prevaciar' not in vals and qty is not None:
            vals['qty_to_prevaciar'] = qty
        if 'qty_to_liberar' not in vals and qty is not None:
            vals['qty_to_liberar'] = qty
        suggested = False
        if 'cantidad_ensamblada' not in vals and qty is not None:
            vals['cantidad_ensamblada'] = qty
        if 'cantidad_piezas' not in vals:
            suggested = self._suggest_cantidad_piezas(qty)
            vals['cantidad_piezas'] = suggested
        if 'pvb_cortado_qty' not in vals:
            vals['pvb_cortado_qty'] = suggested if suggested is not False else self._suggest_cantidad_piezas(qty)
        if 'pvb_cortado_text' not in vals:
            vals['pvb_cortado_text'] = self._format_qty_display(vals.get('pvb_cortado_qty') or 0.0)
        return super().create(vals)

    def write(self, vals):
        is_auto = self.env.context.get('auto_station_qty')
        manual_prev = ('qty_to_prevaciar' in vals and not is_auto)
        manual_lib = ('qty_to_liberar' in vals and not is_auto)
        if manual_prev:
            vals['qty_to_prevaciar_manual'] = True
        if manual_lib:
            vals['qty_to_liberar_manual'] = True
        update_prev = 'product_qty' in vals and 'qty_to_prevaciar' not in vals
        update_lib = 'product_qty' in vals and 'qty_to_liberar' not in vals
        old_qty = {}
        if update_prev or update_lib:
            old_qty = {line.id: line.product_qty for line in self}
        res = super().write(vals)
        if 'cantidad_ensamblada' in vals and not self.env.context.get('skip_mo_qty_sync'):
            ens_lines = self.filtered(
                lambda l: l.master_id_ensamblado and l.production_id and l.production_id.state not in ('done', 'cancel')
            )
            prod_qty_map = {}
            for line in ens_lines:
                prod_qty_map[line.production_id.id] = line.cantidad_ensamblada or 0.0
            if prod_qty_map:
                productions = self.env['mrp.production'].browse(list(prod_qty_map.keys()))
                for prod in productions:
                    new_qty = prod_qty_map.get(prod.id, 0.0)
                    if abs((prod.product_qty or 0.0) - new_qty) > 0.00001:
                        prod.with_context(skip_mo_qty_sync=True).write({'product_qty': new_qty})
        if update_prev or update_lib:
            for line in self:
                prev_qty = old_qty.get(line.id, 0.0) or 0.0
                if update_prev and not line.qty_to_prevaciar_manual and abs((line.qty_to_prevaciar or 0.0) - prev_qty) < 0.00001:
                    line.qty_to_prevaciar = line.product_qty
                if update_lib and not line.qty_to_liberar_manual and abs((line.qty_to_liberar or 0.0) - prev_qty) < 0.00001:
                    line.qty_to_liberar = line.product_qty
        if not self.env.context.get('skip_station_recompute'):
            if any(k in vals for k in ('cantidad_ensamblada', 'qty_to_prevaciar', 'qty_to_liberar', 'production_id')):
                productions = self.mapped('production_id')
                self.env['mrp.master.order.line']._recompute_station_qty_for_productions(productions)
        return res

    def _normalize_op_text(self, text):
        text = (text or "").strip()
        text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
        return text.lower()

    def _open_novedades_for_operation(self, operation_label):
        self.ensure_one()
        mo = self.production_id
        if not mo:
            raise UserError(_("No hay una Orden de Fabricación asociada a esta línea."))
        op_norm = self._normalize_op_text(operation_label)
        def _match(wo):
            return (
                op_norm in self._normalize_op_text(wo.operation_id.name or "")
                or op_norm in self._normalize_op_text(wo.workcenter_id.name or "")
                or op_norm in self._normalize_op_text(wo.name or "")
            )
        workorders = mo.workorder_ids.filtered(_match)
        wo = workorders[:1]
        if not wo:
            raise UserError(_("No se encontró una Orden de Trabajo con la operación '%s' vinculada a esta línea.") % operation_label)
        wo = wo[0]
        product = mo.product_id
        title = _("Novedades - %s") % (product.display_name or product.name) if product else _("Novedades")
        return {
            'type': 'ir.actions.act_window',
            'name': title,
            'res_model': 'alterben.workorder.novedades.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_workorder_id': wo.id,
                'default_production_id': mo.id,
                'default_workcenter_id': wo.workcenter_id.id,
                'default_product_finished_id': product.id if product else False,
                'default_alert_name': wo.name,
            }
        }

    def action_open_novedades_inspeccion(self):
        return self._open_novedades_for_operation("inspeccion final")

    def action_open_novedades_corte(self):
        return self._open_novedades_for_operation("corte de pvb")

    @api.depends('production_id.state')
    def _compute_mo_state(self):
        # Mapeo traducible de estados de mrp.production
        state_map = {
            'draft': _('Borrador'),
            'confirmed': _('Confirmada'),
            'planned': _('Planificada'),
            'progress': _('En progreso'),
            'to_close': _('Por cerrar'),
            'done': _('Hecha'),
            'cancel': _('Cancelada'),
        }
        for rec in self:
            code = rec.production_id.state if getattr(rec, 'production_id', False) and rec.production_id else False
            rec.mo_state = state_map.get(code, code or False)

    @api.model
    def _recompute_cantidad_real_for_productions(self, productions):
        prods = productions.filtered(lambda p: p)
        if not prods:
            return
        lines = self.search([('production_id', 'in', prods.ids)])
        if lines:
            lines._compute_scrap_qty()
            lines._compute_station_quantities()
            lines._compute_cantidad_real()
    
class MrpProduction(models.Model):
    _inherit = "mrp.production"

    master_order_id = fields.Many2one("mrp.master.order", string="Orden Maestra", index=True, readonly=True)

    def _sync_pedido_original_catalog(self):
        if "x_studio_pedido_original" not in self._fields:
            return
        Pedido = self.env["mrp.pedido.original"].sudo()
        for rec in self:
            name = (getattr(rec, 'x_studio_pedido_original', False) or '').strip()
            if name and name.startswith('PED-'):
                exists = Pedido.search([('name', '=', name)], limit=1)
                if not exists:
                    Pedido.create({'name': name})

    @api.model_create_multi
    def create(self, vals_list):
        recs = super().create(vals_list)
        try:
            recs._sync_pedido_original_catalog()
        except Exception:
            pass
        return recs

    @api.model
    def cron_sync_pedidos_originales_mes(self):
        if "x_studio_pedido_original" not in self._fields:
            return True
        today = fields.Date.context_today(self)
        start = today.replace(day=1)
        # compute next month start
        if start.month == 12:
            next_month_start = start.replace(year=start.year + 1, month=1, day=1)
        else:
            next_month_start = start.replace(month=start.month + 1, day=1)
        domain = [
            ("date_start", ">=", fields.Datetime.to_datetime(start)),
            ("date_start", "<", fields.Datetime.to_datetime(next_month_start)),
            ("x_studio_pedido_original", "ilike", "PED-"),
        ]
        Pedido = self.env["mrp.pedido.original"].sudo()
        mos = self.search(domain)
        names = set()
        for mo in mos:
            name = (getattr(mo, 'x_studio_pedido_original', False) or '').strip()
            if name and name.startswith('PED-'):
                names.add(name)
        if not names:
            return True
        existing = Pedido.search([('name', 'in', list(names))])
        existing_names = set(existing.mapped('name'))
        to_create = [{'name': n} for n in names - existing_names]
        if to_create:
            Pedido.create(to_create)
        return True

    def write(self, vals):
        res = super().write(vals)
        try:
            if 'x_studio_pedido_original' in vals:
                self._sync_pedido_original_catalog()
        except Exception:
            pass
        return res

class MrpWorkorder(models.Model):
    _inherit = 'mrp.workorder'

    can_edit_ensamblado = fields.Boolean(compute='_compute_can_edit_permissions_wo')
    can_edit_prevaciado = fields.Boolean(compute='_compute_can_edit_permissions_wo')
    can_edit_inspeccion = fields.Boolean(compute='_compute_can_edit_permissions_wo')

    @api.depends_context('uid')
    def _compute_can_edit_permissions_wo(self):
        user = self.env.user
        for wo in self:
            master = wo.production_id.master_order_id
            mtype = master.type_id if master else False
            
            if mtype and mtype.opt_users_ensamblado_ids:
                wo.can_edit_ensamblado = user in mtype.opt_users_ensamblado_ids
            else:
                wo.can_edit_ensamblado = True

            if mtype and mtype.opt_users_prevaciado_ids:
                wo.can_edit_prevaciado = user in mtype.opt_users_prevaciado_ids
            else:
                wo.can_edit_prevaciado = True

            if mtype and mtype.opt_users_inspeccion_ids:
                wo.can_edit_inspeccion = user in mtype.opt_users_inspeccion_ids
            else:
                wo.can_edit_inspeccion = True


class MrpOptLabelsWizard(models.TransientModel):
    _name = "mrp.opt.labels.wizard"
    _description = "Wizard etiquetas OPT disponibles por color"

    master_id = fields.Many2one("mrp.master.order", required=True, ondelete="cascade")
    available_verde = fields.Integer(string="VERDE", default=30)
    available_gris = fields.Integer(string="GRIS", default=30)
    available_celeste = fields.Integer(string="CELESTE", default=30)
    available_blanco = fields.Integer(string="BLANCO", default=30)
    available_otro = fields.Integer(string="OTRO", default=30)
    summary_message = fields.Html(
        string="Resumen",
        compute="_compute_summary_message",
        readonly=True,
    )

    @api.depends(
        "master_id",
        "available_verde",
        "available_gris",
        "available_celeste",
        "available_blanco",
        "available_otro",
    )
    def _compute_summary_message(self):
        report = self.env["report.alterben_mrp_master_order.report_opt_labels"]
        color_order = ["VERDE", "GRIS", "CELESTE", "BLANCO", "OTRO"]
        field_map = {
            "VERDE": "available_verde",
            "GRIS": "available_gris",
            "CELESTE": "available_celeste",
            "BLANCO": "available_blanco",
            "OTRO": "available_otro",
        }
        for wizard in self:
            master = wizard.master_id
            if not master:
                wizard.summary_message = ""
                continue
            lines = report._get_lines(master)
            excluded_products = set()
            if getattr(master, "type_id", False) and master.type_id.opt_labels_exclude_product_ids:
                excluded_products = set(master.type_id.opt_labels_exclude_product_ids.ids)
            counts = {c: 0 for c in color_order}
            for line in lines:
                if not line.product_id:
                    continue
                if line.product_id.id in excluded_products:
                    continue
                qty = report._get_label_qty(line)
                if qty <= 0:
                    continue
                color = report._get_label_color(line.product_id.default_code or "")
                counts[color] = counts.get(color, 0) + qty
            summary_lines = []
            for color in color_order:
                total = counts.get(color, 0)
                if total <= 0:
                    continue
                available = int(getattr(wizard, field_map[color], 0) or 0)
                if available < 1:
                    available = 1
                if available > 30:
                    available = 30
                remaining = total - available
                if remaining <= 0:
                    sheets = 1
                else:
                    sheets = 1 + int(math.ceil(remaining / 30.0))
                summary_lines.append(
                    f"<div><strong>{color}:</strong> {sheets} HOJAS (LA PRIMERA CON {available} ESPACIOS DISPONIBLES)</div>"
                )
            if summary_lines:
                wizard.summary_message = (
                    "<div>Coloque en su impresora y en este orden las siguientes hojas:</div>"
                    + "".join(summary_lines)
                )
            else:
                wizard.summary_message = "<div>No hay etiquetas para imprimir.</div>"

    @api.onchange(
        "available_verde",
        "available_gris",
        "available_celeste",
        "available_blanco",
        "available_otro",
    )
    def _onchange_available_counts(self):
        self._compute_summary_message()

    @api.constrains(
        "available_verde",
        "available_gris",
        "available_celeste",
        "available_blanco",
        "available_otro",
    )
    def _check_available_range(self):
        for rec in self:
            for field_name in (
                "available_verde",
                "available_gris",
                "available_celeste",
                "available_blanco",
                "available_otro",
            ):
                val = getattr(rec, field_name, 0) or 0
                if val < 1 or val > 30:
                    raise ValidationError(_("La cantidad disponible debe estar entre 1 y 30."))

    def action_print(self):
        self.ensure_one()
        data = {
            "master_id": self.master_id.id,
            "available_map": {
                "VERDE": int(self.available_verde or 0),
                "GRIS": int(self.available_gris or 0),
                "CELESTE": int(self.available_celeste or 0),
                "BLANCO": int(self.available_blanco or 0),
                "OTRO": int(self.available_otro or 0),
            },
        }
        return self.env.ref("alterben_mrp_master_order.action_report_opt_labels").report_action(
            self.master_id, data=data
        )
