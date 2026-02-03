# -*- coding: utf-8 -*-
from datetime import datetime, time, timedelta
import unicodedata
from odoo import api, fields, models, _
from odoo.exceptions import UserError


def _end_of_day(date_value):
    if not date_value:
        return False
    return datetime.combine(date_value, time.max)


def _get_report_sales_days(env):
    """Dias para ventas sin despacho (desde hoy hacia atras)."""
    Type = env["mrp.master.type"].sudo()
    mtype = Type.search([("active", "=", True)], limit=1)
    if mtype and getattr(mtype, "report_sales_days", False):
        return int(mtype.report_sales_days or 0)
    return 30


def _extract_suffix(code):
    if not code:
        return ""
    parts = code.split("-")
    if len(parts) >= 3 and parts[-1].startswith("T") and parts[-1][1:].isdigit():
        return "-".join(parts[-3:])
    if len(parts) >= 2:
        return "-".join(parts[-2:])
    return code


def _classify_code(code):
    if not code:
        return "pt", ""
    if code.startswith("VE-"):
        return "ignore", ""
    if code.startswith("S3-"):
        return "s3", code[3:]
    if code.startswith("S2-VI-"):
        return "s2", code[len("S2-VI-") :]
    if code.startswith("VI-"):
        return "s1", code[len("VI-") :]
    return "pt", code


def _get_turn_capacity(turns, hours, size, env=None):
    turns = int(turns or 0)
    hours = int(hours or 0)
    per_turn = None
    if env is not None:
        mtype = env["mrp.master.type"].sudo().search([("active", "=", True)], limit=1)
        if mtype:
            if size == "small":
                per_turn = mtype.rpt_units_small_8 if hours == 8 else mtype.rpt_units_small_12
            else:
                per_turn = mtype.rpt_units_large_8 if hours == 8 else mtype.rpt_units_large_12
    if per_turn is None:
        if size == "small":
            per_turn = 88 if hours == 8 else 132
        else:
            per_turn = 24 if hours == 8 else 36
    return turns * per_turn


def _validate_turns(turns, hours):
    if int(turns or 0) == 3 and int(hours or 0) == 12:
        raise UserError(_("No se permiten 3 turnos de 12 horas."))


def _get_category_size(product):
    complete = (product.categ_id.complete_name or "").upper()
    norm = unicodedata.normalize('NFKD', complete).encode('ascii', 'ignore').decode('ascii')
    norm = norm.replace(" ", "")
    if "AUTOMOTRIZ/MGRANDES" in norm:
        return "large"
    if "AUTOMOTRIZ/MPEQUENAS" in norm:
        return "small"
    return "other"


def _get_orderpoint_map(env, product_ids):
    if not product_ids:
        return {}
    Orderpoint = env["stock.warehouse.orderpoint"]
    orderpoints = Orderpoint.search([("product_id", "in", product_ids)])
    result = {}
    for op in orderpoints:
        pid = op.product_id.id
        if pid not in result:
            result[pid] = {
                "min": op.product_min_qty or 0.0,
                "max": op.product_max_qty or 0.0,
            }
    return result


def _get_mold_map(env, product_ids):
    if not product_ids:
        return {}
    Receta = env["receta.pvb"]
    recs = Receta.search([("product_id", "in", product_ids)])
    return {rec.product_id.id: (rec.cant_moldes or 0.0) for rec in recs if rec.product_id}


def _get_sales_maps(env, product_ids, report_date):
    if not product_ids:
        return {}, {}
    SaleLine = env["sale.order.line"].sudo()
    SaleOrder = env["sale.order"].sudo()
    SaleReport = env["sale.report"].sudo() if "sale.report" in env else None
    Product = env["product.product"].sudo()
    # La fecha del reporte es solo informativa; no filtra ventas.
    date_start = None
    domain = [
        ("product_id", "in", product_ids),
        ("order_id.state", "in", ["sale", "done"]),
    ]
    qty_uom_field = "product_uom_qty" if "product_uom_qty" in SaleLine._fields else None
    qty_inv_field = "qty_invoiced" if "qty_invoiced" in SaleLine._fields else None
    qty_del_field = "qty_delivered" if "qty_delivered" in SaleLine._fields else None
    qty_field = "qty_to_deliver" if "qty_to_deliver" in SaleLine._fields else None

    total_map = {}
    prio_map = {}

    # Calculo directo: Cantidad (product_uom_qty) - Entregado (qty_delivered).
    if qty_uom_field and qty_del_field and getattr(SaleLine._fields[qty_uom_field], "store", False) and getattr(SaleLine._fields[qty_del_field], "store", False):
        grouped = SaleLine.read_group(domain, ["product_id", f"{qty_uom_field}:sum", f"{qty_del_field}:sum"], ["product_id"])
        total_map = {
            g["product_id"][0]: max(0.0, (g.get(qty_uom_field) or 0.0) - (g.get(qty_del_field) or 0.0))
            for g in grouped if g.get("product_id")
        }
        prio_map = {}
        return total_map, prio_map

    lines = SaleLine.search(domain)
    total_map = {}
    prio_map = {}
    for line in lines:
        qty_to_deliver = (line.product_uom_qty or 0.0) - (line.qty_delivered or 0.0)
        if qty_to_deliver <= 0:
            continue
        pid = line.product_id.id
        total_map[pid] = total_map.get(pid, 0.0) + qty_to_deliver
        # Prioridad ahora es interna, no depende del pedido.
    return total_map, prio_map


def _get_in_process_maps(env, report_date):
    Production = env["mrp.production"]
    date_end = _end_of_day(report_date)
    domain = [("state", "in", ["confirmed", "progress", "planned"])]
    if date_end:
        if "date_planned_start" in Production._fields:
            date_field = "date_planned_start"
        elif "date_start" in Production._fields:
            date_field = "date_start"
        else:
            date_field = "create_date"
        domain.append((date_field, "<=", fields.Datetime.to_string(date_end)))
    productions = Production.search(domain)
    pt = {}
    s1 = {}
    s2 = {}
    s3 = {}
    for prod in productions:
        code = (prod.product_id.default_code or "").strip()
        kind, raw_code = _classify_code(code)
        if kind == "ignore":
            continue
        suffix = _extract_suffix(raw_code)
        qty = prod.product_qty or 0.0
        if kind == "pt":
            pt[suffix] = pt.get(suffix, 0.0) + qty
        elif kind == "s1":
            s1[suffix] = s1.get(suffix, 0.0) + qty
        elif kind == "s2":
            s2[suffix] = s2.get(suffix, 0.0) + qty
        elif kind == "s3":
            s3[suffix] = s3.get(suffix, 0.0) + qty
    return pt, s1, s2, s3


def _allocate_capacity(items, capacity, key):
    remaining = capacity
    for item in sorted(items, key=lambda i: (i.get("priority_rank", 0), i[key]), reverse=True):
        if remaining <= 0:
            break
        if (item.get("required") or 0.0) <= 0.0:
            continue
        remaining_req = max(0.0, (item.get("required") or 0.0) - (item.get("produce") or 0.0))
        need = min(item[key], remaining_req)
        if need <= 0:
            continue
        cap_left = item["cap_left"]
        alloc = min(need, remaining, cap_left)
        if alloc <= 0:
            continue
        item["produce"] += alloc
        item["cap_left"] -= alloc
        remaining -= alloc
    return remaining


class MRPReportProductionDaily(models.TransientModel):
    _name = "mrp.report.production.daily"
    _description = "Reporte Produccion Diaria"
    _rec_name = "name"

    name = fields.Char(string="Nombre", default="Reporte diario de produccion")
    report_date = fields.Date(string="Fecha", default=fields.Date.context_today, required=True)
    report_type = fields.Selection(
        [("suggested", "Sugerido"), ("general", "General")],
        string="Tipo de reporte",
        default="suggested",
        required=True,
    )
    size_filter = fields.Selection(
        [("all", "Todos"), ("small", "M pequeñas"), ("large", "M grandes")],
        string="Tamaño",
        default="all",
        required=True,
    )
    turns_small = fields.Selection([("1", "1"), ("2", "2"), ("3", "3")], string="Turnos M pequeñas", default="2", required=True)
    hours_per_turn_small = fields.Selection([("8", "8"), ("12", "12")], string="Horas por turno (M pequeñas)", default="8", required=True)
    turns_large = fields.Selection([("1", "1"), ("2", "2"), ("3", "3")], string="Turnos M grandes", default="2", required=True)
    hours_per_turn_large = fields.Selection([("8", "8"), ("12", "12")], string="Horas por turno (M grandes)", default="8", required=True)
    max_mold_changes_small = fields.Integer(string="Max. cambios moldes (peq)", default=5, required=True)
    max_mold_changes_large = fields.Integer(string="Max. cambios moldes (grandes)", default=4, required=True)
    line_ids = fields.One2many("mrp.report.production.daily.line", "wizard_id", string="Líneas")
    total_small_count = fields.Integer(string="Total pequeñas", compute="_compute_totals", store=False)
    total_large_count = fields.Integer(string="Total grandes", compute="_compute_totals", store=False)
    total_small_produce = fields.Integer(string="Producir pequeñas", compute="_compute_totals", store=False)
    total_large_produce = fields.Integer(string="Producir grandes", compute="_compute_totals", store=False)
    total_small_required = fields.Integer(string="Requerido pequeñas", compute="_compute_totals", store=False)
    total_large_required = fields.Integer(string="Requerido grandes", compute="_compute_totals", store=False)

    def _check_turn_rules(self):
        _validate_turns(self.turns_small, self.hours_per_turn_small)
        _validate_turns(self.turns_large, self.hours_per_turn_large)

    def action_open_lines(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Produccion diaria"),
            "res_model": "mrp.report.production.daily.line",
            "view_mode": "tree",
            "domain": [("wizard_id", "=", self.id)],
        }

    def action_print(self):
        self.ensure_one()
        if not self.line_ids:
            self.action_generate()
        return self.env.ref("alterben_mrp_master_order.action_report_production_daily_pdf").report_action(self)

    def action_open_report_params(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Parametros de reportes"),
            "res_model": "mrp.report.params.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {},
        }

    def action_generate(self):
        self.ensure_one()
        self._check_turn_rules()
        self.line_ids.unlink()

        Product = self.env["product.product"]
        Category = self.env["product.category"]
        small_cat = Category.search([("complete_name", "ilike", "AUTOMOTRIZ / M PEQUEÑAS")], limit=1)
        if not small_cat:
            small_cat = Category.search([("complete_name", "ilike", "AUTOMOTRIZ / M PEQUENAS")], limit=1)
        if not small_cat:
            small_cat = Category.search([("complete_name", "ilike", "AUTOMOTRIZ/M PEQUEÑAS")], limit=1)
        if not small_cat:
            small_cat = Category.search([("complete_name", "ilike", "AUTOMOTRIZ/M PEQUENAS")], limit=1)
        large_cat = Category.search([("complete_name", "ilike", "AUTOMOTRIZ / M GRANDES")], limit=1)
        if not large_cat:
            large_cat = Category.search([("complete_name", "ilike", "AUTOMOTRIZ/M GRANDES")], limit=1)
        cat_ids = []
        if small_cat:
            cat_ids.append(small_cat.id)
        if large_cat:
            cat_ids.append(large_cat.id)
        products = Product.search([("categ_id", "child_of", cat_ids)]) if cat_ids else Product.browse()
        if not products:
            raise UserError(_("No se encontraron productos en las categorias AUTOMOTRIZ/M PEQUEÑAS o AUTOMOTRIZ/M GRANDES."))

        product_ids = products.ids
        # La fecha solo se usa para mostrar en el PDF, no para filtrar el cálculo interno.
        sales_map, _priority_map = _get_sales_maps(self.env, product_ids, None)
        op_map = _get_orderpoint_map(self.env, product_ids)
        mold_map = _get_mold_map(self.env, product_ids)
        pt_map, s1_map, s2_map, s3_map = _get_in_process_maps(self.env, None)

        items_small = []
        items_large = []
        lines = []

        for product in products:
            size = _get_category_size(product)
            if size not in ("small", "large"):
                continue
            if self.size_filter != "all" and size != self.size_filter:
                continue
            code = (product.default_code or "").strip()
            suffix = _extract_suffix(code)
            pt_qty = pt_map.get(suffix, 0.0)
            s1_qty = s1_map.get(suffix, 0.0)
            s2_qty = s2_map.get(suffix, 0.0)
            s3_qty = s3_map.get(suffix, 0.0)
            in_process = pt_qty + s1_qty + s2_qty + s3_qty

            stock = product.qty_available or 0.0
            sales = sales_map.get(product.id, 0.0)
            if sales > stock:
                priority_rank = 3
            elif sales == stock and sales > 0:
                priority_rank = 2
            elif sales > 0:
                priority_rank = 1
            else:
                priority_rank = 0
            sales_to_cover = max(0.0, sales)
            prio_to_cover = sales if priority_rank == 3 else 0.0

            op = op_map.get(product.id, {})
            min_qty = op.get("min", 0.0)
            max_qty = op.get("max", 0.0)
            if min_qty > 0 and (stock + in_process - sales) >= min_qty:
                required_qty = 0.0
            else:
                if max_qty > 0:
                    required_qty = max(0.0, max_qty + sales - stock - in_process)
                else:
                    required_qty = max(0.0, sales - stock - in_process)

            molds = mold_map.get(product.id, 0.0)
            cap = molds * 8 if molds else 0.0
            cap_left = cap if cap > 0 else 999999.0

            item = {
                "product": product,
                "size": size,
                "stock": stock,
                "sales": sales_to_cover,
                "prio_sales": prio_to_cover,
                "priority_rank": priority_rank,
                "in_process": in_process,
                "min": min_qty,
                "max": max_qty,
                "required": required_qty,
                "molds": molds,
                "cap_left": cap_left,
                "produce": 0.0,
            }
            if size == "small":
                items_small.append(item)
            else:
                items_large.append(item)

        def _select_top_items(items, max_items):
            if max_items <= 0:
                return []
            ordered = sorted(
                items,
                key=lambda i: (i.get("priority_rank", 0), i.get("required", 0.0), i.get("sales", 0.0)),
                reverse=True,
            )
            selected = []
            for item in ordered:
                if (item.get("required") or 0.0) <= 0.0:
                    continue
                selected.append(item)
                if len(selected) >= max_items:
                    break
            return selected

        def _fill(items, capacity):
            remaining = capacity
            remaining = _allocate_capacity(items, remaining, "prio_sales")
            remaining = _allocate_capacity(items, remaining, "sales")
            need_min_items = []
            for item in items:
                if item["min"] <= 0 and item["max"] <= 0:
                    continue
                need_min = max(0.0, item["min"] - (item["stock"] + item["in_process"] - item["sales"]))
                item["need_min"] = need_min
                need_min_items.append(item)
            remaining = _allocate_capacity(need_min_items, remaining, "need_min")
            need_max_items = []
            for item in items:
                if item["max"] <= 0:
                    continue
                need_max = max(0.0, item["max"] + item["sales"] - item["stock"] - item["in_process"])
                item["need_max"] = need_max
                need_max_items.append(item)
            remaining = _allocate_capacity(need_max_items, remaining, "need_max")
            return remaining

        cap_small = _get_turn_capacity(self.turns_small, self.hours_per_turn_small, "small", env=self.env)
        cap_large = _get_turn_capacity(self.turns_large, self.hours_per_turn_large, "large", env=self.env)

        items_small_sel = items_small
        items_large_sel = items_large
        if self.report_type == "suggested":
            max_small = 22 + int(self.max_mold_changes_small or 0)
            max_large = 6 + int(self.max_mold_changes_large or 0)
            items_small_sel = _select_top_items(items_small, max_small)
            items_large_sel = _select_top_items(items_large, max_large)

        _fill(items_small_sel, cap_small)
        _fill(items_large_sel, cap_large)

        def _ensure_needs(items):
            for item in items:
                if "need_min" not in item:
                    item["need_min"] = max(0.0, item["min"] - (item["stock"] + item["in_process"] - item["sales"]))
                if "need_max" not in item:
                    item["need_max"] = max(0.0, item["max"] + item["sales"] - item["stock"])

        def _needs_production(item):
            return (
                (item.get("required") or 0.0) > 0.0
            )

        def _build_lines(items, is_excess, only_produce):
            for item in items:
                if only_produce and item["produce"] <= 0:
                    continue
                if not only_produce and not _needs_production(item):
                    continue
                lines.append({
                    "wizard_id": self.id,
                    "product_id": item["product"].id,
                    "product_code": item["product"].default_code or "",
                    "product_name": item["product"].name or "",
                    "max_qty": item["max"],
                    "min_qty": item["min"],
                    "stock_qty": item["stock"],
                    "sales_qty": item["sales"],
                    "priority_sales_qty": item["priority_rank"],
                    "in_process_qty": item["in_process"],
                    "molds_qty": item["molds"],
                    "produce_qty": item["produce"],
                    "required_qty": item["required"],
                    "size_category": item["size"],
                    "is_excess": is_excess,
                })

        _ensure_needs(items_small_sel)
        _ensure_needs(items_large_sel)

        if self.report_type == "general":
            _build_lines(items_small, False, False)
            _build_lines(items_large, False, False)
        else:
            _build_lines(items_small_sel, False, True)
            _build_lines(items_large_sel, False, True)

        def _build_excess(items, capacity):
            extra_cap = capacity * 0.15
            remaining = extra_cap
            candidates = []
            for item in items:
                if item["min"] <= 0 and item["max"] <= 0:
                    continue
                if item["max"] > 0:
                    need = max(0.0, item["max"] + item["sales"] - item["stock"] - item["produce"])
                else:
                    need = 0.0
                if need <= 0:
                    continue
                candidates.append({
                    "ref": item,
                    "need_ex": need,
                    "cap_left": item["cap_left"],
                    "extra": 0.0,
                })
            for c in sorted(candidates, key=lambda x: x["need_ex"], reverse=True):
                if remaining <= 0:
                    break
                alloc = min(c["need_ex"], remaining, c["cap_left"])
                if alloc <= 0:
                    continue
                c["extra"] = alloc
                remaining -= alloc
            for c in candidates:
                if c["extra"] <= 0:
                    continue
                item = c["ref"]
                lines.append({
                    "wizard_id": self.id,
                    "product_id": item["product"].id,
                    "product_code": item["product"].default_code or "",
                    "product_name": item["product"].display_name or "",
                    "max_qty": item["max"],
                    "min_qty": item["min"],
                    "stock_qty": item["stock"],
                    "sales_qty": item["sales"],
                    "priority_sales_qty": item["prio_sales"],
                    "in_process_qty": item["in_process"],
                    "molds_qty": item["molds"],
                    "produce_qty": c["extra"],
                    "required_qty": item["required"],
                    "size_category": item["size"],
                    "is_excess": True,
                })

        if self.report_type != "general":
            _build_excess(items_small_sel, cap_small)
            _build_excess(items_large_sel, cap_large)

        if not lines:
            def _build_fallback(items):
                for item in items:
                    if (
                        (item["sales"] or 0.0) <= 0.0
                        and (item["prio_sales"] or 0.0) <= 0.0
                        and (item["min"] or 0.0) <= 0.0
                        and (item["max"] or 0.0) <= 0.0
                        and (item["in_process"] or 0.0) <= 0.0
                        and (item["molds"] or 0.0) <= 0.0
                        and (item["stock"] or 0.0) <= 0.0
                    ):
                        continue
                    lines.append({
                        "wizard_id": self.id,
                        "product_id": item["product"].id,
                        "product_code": item["product"].default_code or "",
                        "product_name": item["product"].name or "",
                        "max_qty": item["max"],
                        "min_qty": item["min"],
                        "stock_qty": item["stock"],
                        "sales_qty": item["sales"],
                        "priority_sales_qty": item["prio_sales"],
                        "in_process_qty": item["in_process"],
                        "molds_qty": item["molds"],
                        "produce_qty": 0.0,
                        "required_qty": item["required"],
                        "size_category": item["size"],
                        "is_excess": False,
                    })
            _build_fallback(items_small)
            _build_fallback(items_large)

        if lines:
            self.env["mrp.report.production.daily.line"].create(lines)
        return {
            "type": "ir.actions.act_window",
            "name": _("Produccion diaria"),
            "res_model": "mrp.report.production.daily",
            "res_id": self.id,
            "view_mode": "form",
            "target": "current",
        }

    @api.depends(
        "line_ids",
        "line_ids.size_category",
        "line_ids.produce_qty",
        "line_ids.required_qty",
    )
    def _compute_totals(self):
        for rec in self:
            small = rec.line_ids.filtered(lambda l: l.size_category == "small")
            large = rec.line_ids.filtered(lambda l: l.size_category == "large")
            rec.total_small_count = len(small)
            rec.total_large_count = len(large)
            rec.total_small_produce = int(sum(small.mapped("produce_qty")) or 0)
            rec.total_large_produce = int(sum(large.mapped("produce_qty")) or 0)
            rec.total_small_required = int(sum(small.mapped("required_qty")) or 0)
            rec.total_large_required = int(sum(large.mapped("required_qty")) or 0)


class MRPReportProductionDailyLine(models.TransientModel):
    _name = "mrp.report.production.daily.line"
    _description = "Linea Produccion Diaria"
    _order = "is_excess, priority_sales_qty desc, produce_qty desc"

    wizard_id = fields.Many2one("mrp.report.production.daily", required=True, ondelete="cascade")
    row_number = fields.Integer(string="#", compute="_compute_row_number", store=False)
    product_id = fields.Many2one("product.product", string="Producto", required=True)
    product_code = fields.Char("Referencia", compute="_compute_product_info", store=True)
    product_name = fields.Char("Nombre", compute="_compute_product_info", store=True)
    size_category = fields.Selection([("small", "M pequeñas"), ("large", "M grandes")], string="Tamaño", compute="_compute_product_info", store=False)
    max_qty = fields.Float("Max", digits=(16, 0))
    min_qty = fields.Float("Min", digits=(16, 0))
    stock_qty = fields.Float("Existencia", digits=(16, 0))
    sales_qty = fields.Float("Pedido (ventas)", digits=(16, 0))
    priority_sales_qty = fields.Float("Ventas prioridad", digits=(16, 0))
    in_process_qty = fields.Float("En proceso", digits=(16, 0))
    molds_qty = fields.Float("Cant. moldes", digits=(16, 0))
    produce_qty = fields.Float("Producir", digits=(16, 0))
    required_qty = fields.Float("Requerido", digits=(16, 0))
    is_excess = fields.Boolean("Excedente", default=False)
    available_product_ids = fields.Many2many(
        "product.product", string="Productos disponibles", compute="_compute_available_products", store=False
    )

    def _compute_row_number(self):
        for line in self:
            line.row_number = 0
        wizards = self.mapped("wizard_id")
        for wiz in wizards:
            ordered = self.search([("wizard_id", "=", wiz.id)], order=self._order)
            idx = 1
            for line in ordered:
                line.row_number = idx
                idx += 1

    @api.depends("product_id")
    def _compute_product_info(self):
        for line in self:
            prod = line.product_id
            line.product_code = prod.default_code or ""
            line.product_name = prod.name or ""
            line.size_category = _get_category_size(prod) if prod else False

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if "priority_sales_qty" in vals:
                val = int(vals.get("priority_sales_qty") or 0)
                if val < 0:
                    val = 0
                if val > 3:
                    val = 3
                vals["priority_sales_qty"] = val
        return super().create(vals_list)

    def write(self, vals):
        if "priority_sales_qty" in vals:
            val = int(vals.get("priority_sales_qty") or 0)
            if val < 0:
                val = 0
            if val > 3:
                val = 3
            vals["priority_sales_qty"] = val
        return super().write(vals)

    def _compute_available_products(self):
        Category = self.env["product.category"]
        small_cat = Category.search([("complete_name", "ilike", "AUTOMOTRIZ / M PEQUEÑAS")], limit=1)
        if not small_cat:
            small_cat = Category.search([("complete_name", "ilike", "AUTOMOTRIZ / M PEQUENAS")], limit=1)
        if not small_cat:
            small_cat = Category.search([("complete_name", "ilike", "AUTOMOTRIZ/M PEQUEÑAS")], limit=1)
        if not small_cat:
            small_cat = Category.search([("complete_name", "ilike", "AUTOMOTRIZ/M PEQUENAS")], limit=1)
        large_cat = Category.search([("complete_name", "ilike", "AUTOMOTRIZ / M GRANDES")], limit=1)
        if not large_cat:
            large_cat = Category.search([("complete_name", "ilike", "AUTOMOTRIZ/M GRANDES")], limit=1)
        cat_ids = []
        if small_cat:
            cat_ids.append(small_cat.id)
        if large_cat:
            cat_ids.append(large_cat.id)
        products = self.env["product.product"].search([("categ_id", "child_of", cat_ids)]) if cat_ids else self.env["product.product"].browse()
        for line in self:
            line.available_product_ids = products


class MRPReportParamsWizard(models.TransientModel):
    _name = "mrp.report.params.wizard"
    _description = "Parametros de reportes"

    report_sales_days = fields.Integer(string="Dias de busqueda de ventas sin despacho")
    rpt_units_small_8 = fields.Integer(string="Unidades por turno (pequeñas, 8h)")
    rpt_units_small_12 = fields.Integer(string="Unidades por turno (pequeñas, 12h)")
    rpt_units_large_8 = fields.Integer(string="Unidades por turno (grandes, 8h)")
    rpt_units_large_12 = fields.Integer(string="Unidades por turno (grandes, 12h)")

    def _get_master_type(self):
        return self.env["mrp.master.type"].sudo().search([("active", "=", True)], limit=1)

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        mtype = self._get_master_type()
        if not mtype:
            return res
        res.update({
            "report_sales_days": mtype.report_sales_days or 0,
            "rpt_units_small_8": mtype.rpt_units_small_8 or 0,
            "rpt_units_small_12": mtype.rpt_units_small_12 or 0,
            "rpt_units_large_8": mtype.rpt_units_large_8 or 0,
            "rpt_units_large_12": mtype.rpt_units_large_12 or 0,
        })
        return res

    def action_apply(self):
        self.ensure_one()
        mtype = self._get_master_type()
        if not mtype:
            return {"type": "ir.actions.act_window_close"}
        mtype.write({
            "report_sales_days": self.report_sales_days,
            "rpt_units_small_8": self.rpt_units_small_8,
            "rpt_units_small_12": self.rpt_units_small_12,
            "rpt_units_large_8": self.rpt_units_large_8,
            "rpt_units_large_12": self.rpt_units_large_12,
        })
        return {"type": "ir.actions.act_window_close"}


class MRPReportInProcess(models.TransientModel):
    _name = "mrp.report.in_process"
    _description = "Reporte Productos en Proceso"
    _rec_name = "name"

    name = fields.Char(string="Nombre", default="Productos en proceso")
    report_date = fields.Date(string="Fecha", default=fields.Date.context_today, required=True)
    show_valued = fields.Boolean(string="Valorado", default=False)
    size_filter = fields.Selection(
        [("all", "Todos"), ("small", "M pequeñas"), ("large", "M grandes")],
        string="Tamaño",
        default="all",
        required=True,
    )
    turns_small = fields.Selection([("1", "1"), ("2", "2"), ("3", "3")], string="Turnos M pequeñas", default="2", required=True)
    hours_per_turn_small = fields.Selection([("8", "8"), ("12", "12")], string="Horas por turno (M pequeñas)", default="8", required=True)
    turns_large = fields.Selection([("1", "1"), ("2", "2"), ("3", "3")], string="Turnos M grandes", default="2", required=True)
    hours_per_turn_large = fields.Selection([("8", "8"), ("12", "12")], string="Horas por turno (M grandes)", default="8", required=True)
    line_ids = fields.One2many("mrp.report.in_process.line", "wizard_id", string="Líneas")

    def _check_turn_rules(self):
        _validate_turns(self.turns_small, self.hours_per_turn_small)
        _validate_turns(self.turns_large, self.hours_per_turn_large)

    def action_open_lines(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Productos en proceso"),
            "res_model": "mrp.report.in_process.line",
            "view_mode": "tree",
            "domain": [("wizard_id", "=", self.id)],
            "context": {"show_valued": bool(self.show_valued)},
        }

    def action_print(self):
        self.ensure_one()
        if not self.line_ids:
            self.action_generate()
        return self.env.ref("alterben_mrp_master_order.action_report_in_process_pdf").report_action(self)

    def action_generate(self):
        self.ensure_one()
        self._check_turn_rules()
        self.line_ids.unlink()

        Product = self.env["product.product"]
        Category = self.env["product.category"]
        small_cat = Category.search([("complete_name", "ilike", "AUTOMOTRIZ / M PEQUEÑAS")], limit=1)
        if not small_cat:
            small_cat = Category.search([("complete_name", "ilike", "AUTOMOTRIZ / M PEQUENAS")], limit=1)
        if not small_cat:
            small_cat = Category.search([("complete_name", "ilike", "AUTOMOTRIZ/M PEQUEÑAS")], limit=1)
        if not small_cat:
            small_cat = Category.search([("complete_name", "ilike", "AUTOMOTRIZ/M PEQUENAS")], limit=1)
        large_cat = Category.search([("complete_name", "ilike", "AUTOMOTRIZ / M GRANDES")], limit=1)
        if not large_cat:
            large_cat = Category.search([("complete_name", "ilike", "AUTOMOTRIZ/M GRANDES")], limit=1)
        cat_ids = []
        if small_cat:
            cat_ids.append(small_cat.id)
        if large_cat:
            cat_ids.append(large_cat.id)
        products = Product.search([("categ_id", "child_of", cat_ids)]) if cat_ids else Product.browse()
        if not products:
            raise UserError(_("No se encontraron productos en las categorias AUTOMOTRIZ/M PEQUEÑAS o AUTOMOTRIZ/M GRANDES."))

        # La fecha solo se usa para mostrar en PDF, no para filtrar cálculos internos.
        pt_map, s1_map, s2_map, s3_map = _get_in_process_maps(self.env, None)
        lines = []
        for product in products:
            size = _get_category_size(product)
            if size not in ("small", "large"):
                continue
            if self.size_filter != "all" and size != self.size_filter:
                continue
            code = (product.default_code or "").strip()
            suffix = _extract_suffix(code)
            pt_qty = pt_map.get(suffix, 0.0)
            s1_qty = s1_map.get(suffix, 0.0)
            s2_qty = s2_map.get(suffix, 0.0)
            s3_qty = s3_map.get(suffix, 0.0)
            total = pt_qty + s1_qty + s2_qty + s3_qty
            if total <= 0:
                continue
            lines.append({
                "wizard_id": self.id,
                "product_id": product.id,
                "product_code": product.default_code or "",
                "product_name": product.display_name or "",
                "stock_qty": product.qty_available or 0.0,
                "qty_pt": pt_qty,
                "qty_s1": s1_qty,
                "qty_s2": s2_qty,
                "qty_s3": s3_qty,
                "qty_total": total,
            })
        if lines:
            self.env["mrp.report.in_process.line"].create(lines)
        else:
            raise UserError(_("No hay datos para la fecha seleccionada."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Productos en proceso"),
            "res_model": "mrp.report.in_process",
            "res_id": self.id,
            "view_mode": "form",
            "target": "current",
        }


class MRPReportInProcessLine(models.TransientModel):
    _name = "mrp.report.in_process.line"
    _description = "Linea Productos en Proceso"
    _order = "qty_total desc"

    wizard_id = fields.Many2one("mrp.report.in_process", required=True, ondelete="cascade")
    row_number = fields.Integer(string="#", compute="_compute_row_number", store=False)
    show_valued = fields.Boolean(related="wizard_id.show_valued", store=True)
    product_id = fields.Many2one("product.product", string="Producto", required=True)
    product_code = fields.Char("Referencia")
    product_name = fields.Char("Nombre")
    size_category = fields.Selection(
        [("small", "M pequeñas"), ("large", "M grandes")],
        string="Tamaño",
        compute="_compute_size_category",
        store=False,
    )
    stock_qty = fields.Float("Existencia", digits=(16, 0))
    qty_s1 = fields.Float("En proceso S1", digits=(16, 0))
    qty_s2 = fields.Float("En proceso S2", digits=(16, 0))
    qty_s3 = fields.Float("En proceso S3", digits=(16, 0))
    qty_pt = fields.Float("En proceso PT", digits=(16, 0))
    qty_total = fields.Float("En proceso total", digits=(16, 0))
    cost_unit = fields.Float("Costo unit", digits=(16, 2), compute="_compute_costs", store=False)
    cost_s1_total = fields.Float("Costo S1", digits=(16, 2), compute="_compute_costs", store=False)
    cost_s2_total = fields.Float("Costo S2", digits=(16, 2), compute="_compute_costs", store=False)
    cost_s3_total = fields.Float("Costo S3", digits=(16, 2), compute="_compute_costs", store=False)
    cost_pt_total = fields.Float("Costo PT", digits=(16, 2), compute="_compute_costs", store=False)
    cost_total = fields.Float("Costo total", digits=(16, 2), compute="_compute_costs", store=False)

    @api.depends("product_id")
    def _compute_size_category(self):
        for line in self:
            value = _get_category_size(line.product_id) if line.product_id else False
            line.size_category = value if value in ("small", "large") else False

    def _compute_row_number(self):
        for line in self:
            line.row_number = 0
        wizards = self.mapped("wizard_id")
        for wiz in wizards:
            ordered = self.search([("wizard_id", "=", wiz.id)], order=self._order)
            idx = 1
            for line in ordered:
                line.row_number = idx
                idx += 1

    @api.depends("product_id", "qty_s1", "qty_s2", "qty_s3", "qty_pt", "qty_total")
    def _compute_costs(self):
        for line in self:
            unit = line.product_id.standard_price if line.product_id else 0.0
            line.cost_unit = unit
            line.cost_s1_total = (line.qty_s1 or 0.0) * unit
            line.cost_s2_total = (line.qty_s2 or 0.0) * unit
            line.cost_s3_total = (line.qty_s3 or 0.0) * unit
            line.cost_pt_total = (line.qty_pt or 0.0) * unit
            line.cost_total = (line.qty_total or 0.0) * unit


class MRPReportRawMaterials(models.TransientModel):
    _name = "mrp.report.raw_materials"
    _description = "Reporte Materias Primas"

    report_date = fields.Date(string="Fecha", default=fields.Date.context_today, required=True)
    size_filter = fields.Selection(
        [("all", "Todos"), ("small", "M pequeñas"), ("large", "M grandes")],
        string="Tamaño",
        default="all",
        required=True,
    )
    turns_small = fields.Selection([("1", "1"), ("2", "2"), ("3", "3")], string="Turnos M pequeñas", default="2", required=True)
    hours_per_turn_small = fields.Selection([("8", "8"), ("12", "12")], string="Horas por turno (M pequeñas)", default="8", required=True)
    turns_large = fields.Selection([("1", "1"), ("2", "2"), ("3", "3")], string="Turnos M grandes", default="2", required=True)
    hours_per_turn_large = fields.Selection([("8", "8"), ("12", "12")], string="Horas por turno (M grandes)", default="8", required=True)
    line_ids = fields.One2many("mrp.report.raw_materials.line", "wizard_id", string="Líneas")

    def _check_turn_rules(self):
        _validate_turns(self.turns_small, self.hours_per_turn_small)
        _validate_turns(self.turns_large, self.hours_per_turn_large)

    def action_open_lines(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Materias primas"),
            "res_model": "mrp.report.raw_materials.line",
            "view_mode": "tree",
            "domain": [("wizard_id", "=", self.id)],
        }

    def action_print(self):
        self.ensure_one()
        if not self.line_ids:
            self.action_generate()
        return self.env.ref("alterben_mrp_master_order.action_report_raw_materials_pdf").report_action(self)

    def action_generate(self):
        self.ensure_one()
        self._check_turn_rules()
        self.line_ids.unlink()

        plan_wizard = self.env["mrp.report.production.daily"].create({
            "report_date": self.report_date,
            "turns_small": self.turns_small,
            "hours_per_turn_small": self.hours_per_turn_small,
            "turns_large": self.turns_large,
            "hours_per_turn_large": self.hours_per_turn_large,
        })
        plan_wizard.action_generate()
        plan_lines = plan_wizard.line_ids.filtered(lambda l: not l.is_excess and l.produce_qty > 0)
        if self.size_filter != "all":
            plan_lines = plan_lines.filtered(lambda l: l.size_category == self.size_filter)

        Bom = self.env["mrp.bom"]
        def _find_bom(product):
            if not product:
                return False
            if hasattr(Bom, "_bom_find"):
                try:
                    return Bom._bom_find(product=product)
                except TypeError:
                    try:
                        return Bom._bom_find(product_tmpl=product.product_tmpl_id)
                    except TypeError:
                        pass
            if getattr(product, "product_tmpl_id", False):
                return Bom.search([("product_tmpl_id", "=", product.product_tmpl_id.id)], limit=1)
            return Bom.search([("product_id", "=", product.id)], limit=1)
        components = {}
        for line in plan_lines:
            product = line.product_id
            bom = _find_bom(product)
            if not bom:
                continue
            factor = (line.produce_qty or 0.0) / (bom.product_qty or 1.0)
            for bl in bom.bom_line_ids:
                comp = bl.product_id
                qty = (bl.product_qty or 0.0) * factor
                if not comp:
                    continue
                components[comp.id] = components.get(comp.id, 0.0) + qty

        Production = self.env["mrp.production"]
        date_end = _end_of_day(self.report_date)
        domain = [("state", "in", ["confirmed", "progress"]) ]
        if date_end:
            if "date_planned_start" in Production._fields:
                date_field = "date_planned_start"
            elif "date_start" in Production._fields:
                date_field = "date_start"
            else:
                date_field = "create_date"
            domain.append((date_field, "<=", fields.Datetime.to_string(date_end)))
        productions = Production.search(domain)
        comp_in_process = {}
        for prod in productions:
            bom = _find_bom(prod.product_id)
            if not bom:
                continue
            factor = (prod.product_qty or 0.0) / (bom.product_qty or 1.0)
            for bl in bom.bom_line_ids:
                comp = bl.product_id
                qty = (bl.product_qty or 0.0) * factor
                if not comp:
                    continue
                comp_in_process[comp.id] = comp_in_process.get(comp.id, 0.0) + qty

        Location = self.env["stock.location"]
        loc_mp = Location.search([("complete_name", "=", "WH/Existencias/MP")], limit=1)
        if not loc_mp:
            loc_mp = Location.search([("complete_name", "ilike", "/MP")], limit=1)
        loc_pre = Location.search([("complete_name", "=", "WH/Existencias/PREPRODUCCION")], limit=1)
        if not loc_pre:
            loc_pre = Location.search([("complete_name", "ilike", "PREPRODUCCION")], limit=1)
        Quant = self.env["stock.quant"]
        stock_mp = {}
        stock_pre = {}
        if loc_mp:
            mp_loc_ids = Location.search([("id", "child_of", loc_mp.id)]).ids
            grouped = Quant.read_group([("location_id", "in", mp_loc_ids)], ["product_id", "quantity:sum"], ["product_id"])
            stock_mp = {g["product_id"][0]: g["quantity"] for g in grouped if g.get("product_id")}
        if loc_pre:
            pre_loc_ids = Location.search([("id", "child_of", loc_pre.id)]).ids
            grouped = Quant.read_group([("location_id", "in", pre_loc_ids)], ["product_id", "quantity:sum"], ["product_id"])
            stock_pre = {g["product_id"][0]: g["quantity"] for g in grouped if g.get("product_id")}

        lines = []
        product_ids = set(components.keys())
        op_map = _get_orderpoint_map(self.env, list(product_ids))
        for pid, qty in components.items():
            product = self.env["product.product"].browse(pid)
            op = op_map.get(pid, {})
            lines.append({
                "wizard_id": self.id,
                "product_id": pid,
                "product_code": product.default_code or "",
                "product_name": product.display_name or "",
                "min_qty": op.get("min", 0.0),
                "max_qty": op.get("max", 0.0),
                "required_qty": qty,
                "stock_mp_qty": stock_mp.get(pid, 0.0),
                "stock_pre_qty": stock_pre.get(pid, 0.0),
                "in_process_qty": comp_in_process.get(pid, 0.0),
                "is_extra": False,
            })

        extra_ids = set(stock_mp.keys()) | set(stock_pre.keys())
        extra_ids = extra_ids - product_ids
        for pid in extra_ids:
            product = self.env["product.product"].browse(pid)
            if not product:
                continue
            op = op_map.get(pid, {})
            lines.append({
                "wizard_id": self.id,
                "product_id": pid,
                "product_code": product.default_code or "",
                "product_name": product.display_name or "",
                "min_qty": op.get("min", 0.0),
                "max_qty": op.get("max", 0.0),
                "required_qty": 0.0,
                "stock_mp_qty": stock_mp.get(pid, 0.0),
                "stock_pre_qty": stock_pre.get(pid, 0.0),
                "in_process_qty": comp_in_process.get(pid, 0.0),
                "is_extra": True,
            })

        if lines:
            self.env["mrp.report.raw_materials.line"].create(lines)
        else:
            raise UserError(_("No hay datos para la fecha seleccionada."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Materias primas"),
            "res_model": "mrp.report.raw_materials",
            "res_id": self.id,
            "view_mode": "form",
            "target": "current",
        }


class MRPReportRawMaterialsLine(models.TransientModel):
    _name = "mrp.report.raw_materials.line"
    _description = "Linea Materias Primas"
    _order = "is_extra, required_qty desc"

    wizard_id = fields.Many2one("mrp.report.raw_materials", required=True, ondelete="cascade")
    row_number = fields.Integer(string="#", compute="_compute_row_number", store=False)
    product_id = fields.Many2one("product.product", string="Producto", required=True)
    product_code = fields.Char("Referencia")
    product_name = fields.Char("Nombre")
    min_qty = fields.Float("Min", digits=(16, 0))
    max_qty = fields.Float("Max", digits=(16, 0))
    uom_id = fields.Many2one("uom.uom", string="UdM", related="product_id.uom_id", store=False)
    size_category = fields.Selection(
        [("small", "M pequeñas"), ("large", "M grandes")],
        string="Tamaño",
        compute="_compute_size_category",
        store=False,
    )
    required_qty = fields.Float("Requerido", digits=(16, 0))
    stock_mp_qty = fields.Float("Stock MP", digits=(16, 0))
    stock_pre_qty = fields.Float("Stock Preproduccion", digits=(16, 0))
    in_process_qty = fields.Float("En proceso", digits=(16, 0))
    is_extra = fields.Boolean("No considerado", default=False)

    @api.depends("product_id")
    def _compute_size_category(self):
        for line in self:
            value = _get_category_size(line.product_id) if line.product_id else False
            line.size_category = value if value in ("small", "large") else False

    def _compute_row_number(self):
        for line in self:
            line.row_number = 0
        wizards = self.mapped("wizard_id")
        for wiz in wizards:
            ordered = self.search([("wizard_id", "=", wiz.id)], order=self._order)
            idx = 1
            for line in ordered:
                line.row_number = idx
                idx += 1


class MRPReportSalesNoStock(models.TransientModel):
    _name = "mrp.report.sales_no_stock"
    _description = "Reporte Ventas sin Stock"

    report_date = fields.Date(string="Fecha", default=fields.Date.context_today, required=True)
    size_filter = fields.Selection(
        [("all", "Todos"), ("small", "M pequeñas"), ("large", "M grandes")],
        string="Tamaño",
        default="all",
        required=True,
    )
    turns_small = fields.Selection([("1", "1"), ("2", "2"), ("3", "3")], string="Turnos M pequeñas", default="2", required=True)
    hours_per_turn_small = fields.Selection([("8", "8"), ("12", "12")], string="Horas por turno (M pequeñas)", default="8", required=True)
    turns_large = fields.Selection([("1", "1"), ("2", "2"), ("3", "3")], string="Turnos M grandes", default="2", required=True)
    hours_per_turn_large = fields.Selection([("8", "8"), ("12", "12")], string="Horas por turno (M grandes)", default="8", required=True)
    line_ids = fields.One2many("mrp.report.sales_no_stock.line", "wizard_id", string="Líneas")
    categ_id = fields.Many2one("product.category", string="Categoría", required=False)

    def _check_turn_rules(self):
        _validate_turns(self.turns_small, self.hours_per_turn_small)
        _validate_turns(self.turns_large, self.hours_per_turn_large)

    def action_open_lines(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Ventas sin stock"),
            "res_model": "mrp.report.sales_no_stock.line",
            "view_mode": "tree",
            "domain": [("wizard_id", "=", self.id)],
        }

    def action_print(self):
        self.ensure_one()
        if not self.line_ids:
            self.action_generate()
        return self.env.ref("alterben_mrp_master_order.action_report_sales_no_stock_pdf").report_action(self)

    def action_generate(self):
        self.ensure_one()
        self._check_turn_rules()
        self.line_ids.unlink()

        Product = self.env["product.product"]
        SaleLine = self.env["sale.order.line"]
        date_end = _end_of_day(self.report_date)
        domain = [("order_id.state", "in", ["sale", "done"])]
        if date_end:
            domain.append(("order_id.date_order", "<=", fields.Datetime.to_string(date_end)))
        qty_field = "qty_to_deliver" if "qty_to_deliver" in SaleLine._fields else None

        if qty_field and getattr(SaleLine._fields[qty_field], "store", False):
            grouped = SaleLine.read_group(domain, ["product_id", f"{qty_field}:sum"], ["product_id"])
            sales_map = {g["product_id"][0]: g[qty_field] for g in grouped if g.get("product_id")}
            prio_map = {}
        else:
            sales_map = {}
            prio_map = {}
            for line in SaleLine.search(domain):
                qty_to_deliver = (line.product_uom_qty or 0.0) - (line.qty_delivered or 0.0)
                if qty_to_deliver <= 0:
                    continue
                pid = line.product_id.id
                sales_map[pid] = sales_map.get(pid, 0.0) + qty_to_deliver
            prio_map = {}

        lines = []
        pt_map, s1_map, s2_map, s3_map = _get_in_process_maps(self.env, None)
        allowed_categ_ids = set()
        if self.categ_id:
            allowed_categ_ids = set(self.env["product.category"].search([("id", "child_of", self.categ_id.id)]).ids)
            allowed_categ_ids.add(self.categ_id.id)
        for pid, qty in sales_map.items():
            product = Product.browse(pid)
            size = _get_category_size(product) if product else "other"
            if self.size_filter != "all" and size != self.size_filter:
                continue
            if allowed_categ_ids and product.categ_id:
                if product.categ_id.id not in allowed_categ_ids:
                    continue
            stock = product.qty_available or 0.0
            if qty <= stock:
                continue
            if qty > stock:
                priority_rank = 3
            elif qty == stock and qty > 0:
                priority_rank = 2
            elif qty > 0:
                priority_rank = 1
            else:
                priority_rank = 0
            code = (product.default_code or "").strip()
            suffix = _extract_suffix(code)
            in_process = (pt_map.get(suffix, 0.0) + s1_map.get(suffix, 0.0) + s2_map.get(suffix, 0.0) + s3_map.get(suffix, 0.0))
            lines.append({
                "wizard_id": self.id,
                "product_id": pid,
                "product_code": product.default_code or "",
                "product_name": product.display_name or "",
                "stock_qty": stock,
                "sales_qty": qty,
                "priority_sales_qty": priority_rank,
                "shortfall_qty": max(0.0, qty - stock),
                "in_process_qty": in_process,
            })
        if lines:
            self.env["mrp.report.sales_no_stock.line"].create(lines)
        else:
            raise UserError(_("No hay datos para la fecha seleccionada."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Ventas sin stock"),
            "res_model": "mrp.report.sales_no_stock",
            "res_id": self.id,
            "view_mode": "form",
            "target": "current",
        }


class MRPReportSalesNoStockLine(models.TransientModel):
    _name = "mrp.report.sales_no_stock.line"
    _description = "Linea Ventas sin Stock"
    _order = "shortfall_qty desc"

    wizard_id = fields.Many2one("mrp.report.sales_no_stock", required=True, ondelete="cascade")
    row_number = fields.Integer(string="#", compute="_compute_row_number", store=False)
    product_id = fields.Many2one("product.product", string="Producto", required=True)
    product_code = fields.Char("Referencia")
    product_name = fields.Char("Nombre")
    uom_id = fields.Many2one("uom.uom", string="UdM", related="product_id.uom_id", store=False)
    size_category = fields.Selection(
        [("small", "M pequeñas"), ("large", "M grandes"), ("other", "Otros")],
        string="Tamaño",
        compute="_compute_size_category",
        store=False,
    )
    stock_qty = fields.Float("Existencia", digits=(16, 0))
    sales_qty = fields.Float("Ventas", digits=(16, 0))
    priority_sales_qty = fields.Float("Ventas prioridad", digits=(16, 0))
    shortfall_qty = fields.Float("Faltante", digits=(16, 0))
    in_process_qty = fields.Float("En proceso", digits=(16, 0))

    @api.depends("product_id")
    def _compute_size_category(self):
        for line in self:
            line.size_category = _get_category_size(line.product_id) if line.product_id else False

    def _compute_row_number(self):
        for line in self:
            line.row_number = 0
        wizards = self.mapped("wizard_id")
        for wiz in wizards:
            ordered = self.search([("wizard_id", "=", wiz.id)], order=self._order)
            idx = 1
            for line in ordered:
                line.row_number = idx
                idx += 1

