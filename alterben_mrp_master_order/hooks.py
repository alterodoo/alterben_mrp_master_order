# -*- coding: utf-8 -*-
from odoo import SUPERUSER_ID, api
from lxml import etree

BUTTON_XML = """<button name="action_open_novedades_wizard"
        type="object"
        string="Novedades"
        class="oe_highlight"/>
""".strip()

def _insert_button_in_view(env, view):
    try:
        arch = etree.fromstring(view.arch_db.encode('utf-8'))
    except Exception:
        return False
    if arch.xpath(".//button[@name='action_open_novedades_wizard']"):
        return False
    inserted = False
    headers = arch.xpath('.//header')
    if headers:
        try:
            btn_el = etree.fromstring(BUTTON_XML)
            headers[0].insert(0, btn_el)
            inserted = True
        except Exception:
            inserted = False
    if not inserted:
        sheets = arch.xpath('.//sheet')
        if sheets:
            try:
                btn_el = etree.fromstring(BUTTON_XML)
                container = etree.Element('div'); container.attrib['class'] = 'oe_button_box'
                container.append(btn_el)
                sheets[0].insert(0, container)
                inserted = True
            except Exception:
                inserted = False
    if inserted:
        view.write({'arch_db': etree.tostring(arch, encoding='unicode')})
    return inserted

def post_init_hook(env):
    if env.uid != SUPERUSER_ID:
        env = env(user=SUPERUSER_ID)
    View = env['ir.ui.view']
    views = View.search([('model', '=', 'mrp.workorder'), ('type', '=', 'form')], order='priority,id')
    for v in views:
        if _insert_button_in_view(env, v):
            break
