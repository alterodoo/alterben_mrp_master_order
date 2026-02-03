from odoo import api, fields, models, _
import re
from odoo.exceptions import UserError, ValidationError

class AssignControlTotalWizard(models.TransientModel):
    _name = "assign.control.total.wizard"
    _description = "Asignar etiquetas Control Total"

    # Campos para pickings
    picking_id = fields.Many2one("stock.picking", string="Picking")
    # Campos para master orders
    master_order_id = fields.Many2one("mrp.master.order", string="Orden Maestra")
    
    line_ids = fields.One2many("assign.control.total.wizard.line", "wizard_id", string="Resumen")

    # Cabecera
    etiquetas_desde = fields.Char(string="Etiquetas desde")
    etiquetas_hasta = fields.Char(string="hasta")
    selected_product_id = fields.Many2one('product.product', string='Producto seleccionado', readonly=True)
    selected_qty = fields.Float(string='Cantidad', readonly=True)

    # Estado en modal
    status_html = fields.Html(string='Estado', readonly=True)
    is_assigned = fields.Boolean(string='Ya asignado', readonly=True)
    is_complete = fields.Boolean(string='Completo', readonly=True)

    @api.model
    def default_get(self, fields_list):
        vals = super().default_get(fields_list)
        ctx = self.env.context or {}
        
        # Manejar contexto de línea de producto (para Master Order)
        line_id = ctx.get('default_line_id')
        if line_id:
            line = self.env['mrp.master.order.line'].browse(line_id)
            if line.exists():
                master_order = line.master_id
                vals.update({
                    'master_order_id': master_order.id,
                    'selected_product_id': line.product_id.id,
                    'selected_qty': line.product_qty,
                })
                
                # Cargar rangos pre-asignados si existen
                if hasattr(line, 'ct_pre_from') and line.ct_pre_from and hasattr(line, 'ct_pre_to') and line.ct_pre_to:
                    vals.update({
                        'etiquetas_desde': line.ct_pre_from,
                        'etiquetas_hasta': line.ct_pre_to,
                    })
                    
                    # Verificar si ya hay etiquetas asignadas
                    self._check_existing_labels(vals, line.product_id, master_order=master_order, line=line)
                
                return vals
        
        # Manejo para pickings (comportamiento original)
        picking = None
        picking_id = vals.get('picking_id')
        
        if not picking_id:
            # Intentar obtener el picking del contexto
            if ctx.get("active_model") == "stock.picking" and ctx.get("active_id"):
                picking = self.env["stock.picking"].browse(ctx.get("active_id"))
                picking_id = picking.id
                vals["picking_id"] = picking_id
            elif ctx.get("default_move_id"):
                _mv = self.env["stock.move"].browse(ctx.get("default_move_id"))
                if _mv and _mv.exists() and _mv.picking_id:
                    picking = _mv.picking_id
                    picking_id = picking.id
                    vals["picking_id"] = picking_id
            elif ctx.get("default_move_line_id"):
                _ml = self.env["stock.move.line"].browse(ctx.get("default_move_line_id"))
                if _ml and _ml.exists() and _ml.picking_id:
                    picking = _ml.picking_id
                    picking_id = picking.id
                    vals["picking_id"] = picking_id
        else:
            picking = self.env['stock.picking'].browse(picking_id)
        
        # Si tenemos un picking, cargar la información del producto
        if picking and picking.exists():
            # Determinar producto/cantidad
            prod = False
            qty = 0.0
            
            if ctx.get("default_move_id"):
                move = self.env["stock.move"].browse(ctx.get("default_move_id"))
                if move and move.exists():
                    prod = move.product_id
                    qty = float(move.product_uom_qty or 0)
            elif ctx.get("default_move_line_id"):
                ml = self.env["stock.move.line"].browse(ctx.get("default_move_line_id"))
                if ml and ml.exists():
                    prod = ml.product_id
                    qty = float(ml.qty_done or ml.product_uom_qty or 0)
            
            # Si no se encontró producto por los métodos anteriores, usar el primer movimiento
            if not prod and picking.move_lines:
                move = picking.move_lines[0]
                prod = move.product_id
                qty = float(move.product_uom_qty or 0)
            
            if prod:
                vals.update({
                    'selected_product_id': prod.id,
                    'selected_qty': qty,
                })
        
        return vals

            Label = self.env['control.total.label']

            # Etiquetas asignadas en este picking+producto
            existing = Label.search([('picking_id','=', picking.id if picking else False), ('product_id','=', prod.id), ('active','=', True)], order='name asc')
            if not existing:
                # Fallback por plantilla del producto (por si variaron variantes)
                existing = Label.search([('picking_id','=', picking.id if picking else False), ('product_id.product_tmpl_id','=', prod.product_tmpl_id.id), ('active','=', True)], order='name asc')

            import re
            def split_code(code):
                m = re.match(r'^(?P<prefix>\D*?)(?P<num>\d+)$', code or '')
                if m:
                    return (m.group('prefix') or ''), int(m.group('num'))
                return (code or ''), None

            if existing:
                nums = []
                pf_common = None
                for rec in existing:
                    pf, nn = split_code(rec.name)
                    if nn is None:
                        continue
                    if pf_common is None:
                        pf_common = pf
                    if pf == pf_common:
                        nums.append(nn)
                assigned_count = len(nums)
                if nums and pf_common is not None:
                    min_code = f"{pf_common}{min(nums)}"
                    max_code = f"{pf_common}{max(nums)}"
                    vals['etiquetas_desde'] = min_code
                    vals['etiquetas_hasta'] = max_code
                vals['is_assigned'] = True
                need = int(qty or 0)
                if need > 0 and assigned_count >= need:
                    vals['is_complete'] = True
                # Mensaje
                if need > 0:
                    if assigned_count < need:
                        faltan = need - assigned_count
                        vals['status_html'] = f"<div class='alert alert-warning'>Asignado {assigned_count} de {need}. Faltan {faltan} etiquetas.</div>"
                    elif assigned_count == need:
                        vals['status_html'] = f"<div class='alert alert-success'>Asignación completa: {assigned_count} de {need} etiquetas.</div>"
                    else:
                        extra = assigned_count - need
                        vals['status_html'] = f"<div class='alert alert-danger'>Exceso: {extra} etiquetas (asignadas {assigned_count} de {need}).</div>"
                else:
                    vals['status_html'] = f"<div class='alert alert-info'>Asignadas {assigned_count} etiquetas.</div>"
            else:
                # Si no hay asignadas, preferir pre-asignación del movimiento
                mv_for_prod = False
                if ctx.get("default_move_id"):
                    mv_for_prod = self.env["stock.move"].browse(ctx.get("default_move_id"))
                if not mv_for_prod and picking and prod:
                    mv_for_prod = picking.move_ids_without_package.filtered(lambda m: m.product_id.id == prod.id)[:1]
                if mv_for_prod and mv_for_prod.exists() and mv_for_prod.ct_pre_from and mv_for_prod.ct_pre_to:
                    vals['etiquetas_desde'] = mv_for_prod.ct_pre_from
                    vals['etiquetas_hasta'] = mv_for_prod.ct_pre_to
                    vals['is_assigned'] = False
                    vals['is_complete'] = False
                    vals['status_html'] = "<div class='alert alert-secondary'>Usando pre-asignación guardada para este producto.</div>"
                    return vals
                # Sugerencia inicial desde último CS-
                last = Label.search([('name','ilike','CS-')], order='name asc')
                start_num = 54801
                prefix = 'CS-'
                max_nn = None
                for rec in last:
                    pf, nn = split_code(rec.name)
                    if nn is not None and pf.startswith('CS'):
                        if max_nn is None or nn > max_nn:
                            max_nn = nn
                            prefix = pf
                if max_nn is not None:
                    start_num = max_nn + 1
                if qty and qty > 0:
                    vals['etiquetas_desde'] = f"{prefix}{start_num}"
                    vals['etiquetas_hasta'] = f"{prefix}{start_num + int(qty) - 1}"
                vals['is_assigned'] = False
                vals['is_complete'] = False
                vals['status_html'] = "<div class='alert alert-secondary'>Este producto no tiene etiquetas asignadas. Dejo la sugerencia a continuación:</div>"

        return vals

    def _parse_range(self, start_txt, end_txt):
        if not start_txt or not end_txt:
            return []
        import re
        s = start_txt.strip()
        e = end_txt.strip()
        m1 = re.match(r'^(?P<prefix>\D*?)(?P<num>\d+)$', s)
        m2 = re.match(r'^(?P<prefix>\D*?)(?P<num>\d+)$', e)
        if m1 and m2 and m1.group('prefix') == m2.group('prefix'):
            prefix = m1.group('prefix')
            n1 = int(m1.group('num'))
            n2 = int(m2.group('num'))
            step = 1 if n2 >= n1 else -1
            width = max(len(m1.group('num')), len(m2.group('num')))
            return [f"{prefix}{str(i).zfill(width)}" for i in range(n1, n2 + step, step)]
        return [s, e]

    def _check_existing_labels(self, vals, product, master_order=None, picking=None):
        """Verifica si ya existen etiquetas asignadas para el producto y actualiza el estado."""
        Label = self.env['control.total.label']
        domain = [('product_id', '=', product.id), ('active', '=', True)]
        
        if master_order:
            domain.append(('master_order_id', '=', master_order.id))
        elif picking:
            domain.append(('picking_id', '=', picking.id))
        
        existing = Label.search(domain, order='name asc')
        
        if existing:
            # Obtener prefijo y números de las etiquetas existentes
            def split_code(code):
                m = re.match(r'^(?P<prefix>\D*?)(?P<num>\d+)$', code or '')
                if m:
                    return (m.group('prefix') or ''), int(m.group('num'))
                return (code or ''), None
            
            nums = []
            pf_common = None
            for rec in existing:
                pf, nn = split_code(rec.name)
                if nn is not None:
                    if pf_common is None:
                        pf_common = pf
                    if pf == pf_common:
                        nums.append(nn)
            
            if nums and pf_common is not None:
                min_code = f"{pf_common}{min(nums)}"
                max_code = f"{pf_common}{max(nums)}"
                vals['etiquetas_desde'] = min_code
                vals['etiquetas_hasta'] = max_code
                vals['is_assigned'] = True
                
                # Verificar si la cantidad asignada es suficiente
                need = int(vals.get('selected_qty', 0) or 0)
                if need > 0 and len(nums) >= need:
                    vals['is_complete'] = True
                
                # Mensaje de estado
                if need > 0:
                    assigned_count = len(nums)
                    if assigned_count < need:
                        faltan = need - assigned_count
                        vals['status_html'] = f"<div class='alert alert-warning'>Asignado {assigned_count} de {need}. Faltan {faltan} etiquetas.</div>"
                    elif assigned_count == need:
                        vals['status_html'] = f"<div class='alert alert-success'>Asignación completa: {assigned_count} de {need} etiquetas.</div>"
                    else:
                        extra = assigned_count - need
                        vals['status_html'] = f"<div class='alert alert-danger'>Exceso: {extra} etiquetas (asignadas {assigned_count} de {need}).</div>"
                else:
                    vals['status_html'] = f"<div class='alert alert-info'>Asignadas {len(nums)} etiquetas.</div>"
    
    def _recompute_picking_ct_complete(self, picking):
        """Marcar CT completo en el picking si TODAS las líneas tienen cantidad cubierta por etiquetas.
        Escribe el booleano si existe (x_ct_completo, x_studio_ct_completo, ct_complete)."""
        if not picking:
            return
        # Cantidades por producto en el picking
        qty_by_prod = {}
        for mv in picking.move_ids_without_package:
            qty_by_prod[mv.product_id.id] = qty_by_prod.get(mv.product_id.id, 0) + float(mv.product_uom_qty or 0)
        # Etiquetas por producto
        Label = self.env['control.total.label']
        complete = True
        for pid, need in qty_by_prod.items():
            cnt = Label.search_count([('picking_id','=', picking.id), ('product_id','=', pid), ('active','=', True)])
            if int(need or 0) > cnt:
                complete = False
                break
        # Escribir si existe el campo
        for fname in ('x_ct_completo','x_studio_ct_completo','ct_complete','x_ct_completado'):
            if fname in picking._fields:
                picking.sudo().write({fname: bool(complete)})
                break

    def _parse_range(self, start_txt, end_txt):
        """Convierte un rango de códigos en una lista de códigos."""
        if not start_txt or not end_txt:
            return []
        
        # Intentar dividir el código en prefijo y número
        def split_code(code):
            m = re.match(r'^(?P<prefix>\D*?)(?P<num>\d+)$', code or '')
            if m:
                return (m.group('prefix') or ''), int(m.group('num'))
            return (code or ''), None
        
        start_pf, start_num = split_code(start_txt.strip())
        end_pf, end_num = split_code(end_txt.strip())
        
        # Si los prefijos no coinciden o no se pudo extraer el número, devolver los códigos tal cual
        if start_pf != end_pf or start_num is None or end_num is None:
            return [start_txt.strip(), end_txt.strip()]
        
        # Generar la secuencia de códigos
        step = 1 if end_num >= start_num else -1
        width = max(len(str(start_num)), len(str(end_num)))
        return [f"{start_pf}{str(i).zfill(width)}" for i in range(start_num, end_num + step, step)]
    
    def action_assign(self):
        self.ensure_one()
        if not self.selected_product_id:
            raise UserError(_("No se pudo determinar el producto seleccionado."))
        if not (self.etiquetas_desde and self.etiquetas_hasta):
            raise UserError(_("Debes ingresar los valores en 'Etiquetas desde' y 'hasta'."))

        codes = self._parse_range(self.etiquetas_desde, self.etiquetas_hasta)
        if not codes:
            raise UserError(_("Rango inválido de etiquetas."))

        qty_needed = int(self.selected_qty or 0)
        if qty_needed and len(codes) != qty_needed:
            raise ValidationError(_("La cantidad (%s) no coincide con el tamaño del rango de etiquetas (%s).") % (qty_needed, len(codes)))


        Label = self.env['control.total.label']
        created = []
        # Reutilización / creación de etiquetas
        for code in codes:
            # Buscar también inactivas para poder reutilizarlas
            LabelCtx = Label.with_context(active_test=False)
            existing = LabelCtx.search([('name', '=', code)], limit=1)
            if existing:
                # Si está asignada a otro picking activo distinto, bloquear
                if existing.active and existing.picking_id and existing.picking_id.id != self.picking_id.id:
                    raise ValidationError(_(
                        "El código %s ya está asignado al despacho %s."
                    ) % (code, existing.picking_id.name))
                # Reutilizar: reactivar, reasignar y registrar nota
                note_msg = (existing.note or "") or ""
                from datetime import datetime
                now_str = fields.Datetime.now().strftime("%%Y-%%m-%%d %%H:%%M:%%S")
                reuse_info = "Reutilizada el %s en %s" % (now_str, self.picking_id.name or '')
                if note_msg:
                    note_msg = note_msg + " | " + reuse_info
                else:
                    note_msg = reuse_info
                existing.write({
                    'active': True,
                    'inactive_reason': False,
                    'picking_id': self.picking_id.id,
                    'product_id': self.selected_product_id.id,
                    'note': note_msg,
                })
            else:
                # Crear nuevo registro
                Label.create({
                    'name': code,
                    'picking_id': self.picking_id.id,
                    'product_id': self.selected_product_id.id,
                })
            created.append(code)
        # Recompute CT completo
        self._recompute_picking_ct_complete(self.picking_id)
        # Notificación
        try:
            if created:
                self.env.user.notify_success(message=_("Se han asignado las etiquetas %s - %s al producto %s.") % (created[0], created[-1], self.selected_product_id.display_name))
        except Exception:
            pass
        return {'type': 'ir.actions.act_window', 'res_model': 'stock.picking', 'res_id': self.picking_id.id, 'view_mode': 'form', 'target': 'current'}


class AssignControlTotalWizardLine(models.TransientModel):
    _name = "assign.control.total.wizard.line"
    _description = "Línea resumen Asignación Control Total"

    wizard_id = fields.Many2one("assign.control.total.wizard")
    product_id = fields.Many2one("product.product", string="Producto")
    qty = fields.Float(string="Cantidad")
    range_text = fields.Char(string="Control Total (rango)")
    codes_input = fields.Text(string="Códigos (uno por línea)")