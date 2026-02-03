# merged __init__ from: /mnt/data/merge_master_order_round2/alterben_control_total/alterben_control_total/__init__.py, /mnt/data/merge_master_order_round2/alterben_mrp_master_order/alterben_mrp_master_order/__init__.py, /mnt/data/merge_master_order_round2/alterben_mrp_workorder_novedades/alterben_mrp_workorder_novedades/__init__.py
# Compatibility: ensure missing manifest assets does not crash asset build (Odoo 17).
import odoo.modules.module as _odoo_module
import odoo.modules.module as _odoo_module_loading

if not getattr(_odoo_module, "_ab_assets_patch", False):
    _orig_get_manifest_cached = _odoo_module._get_manifest_cached

    def _get_manifest_cached_patched(*args, **kwargs):
        manifest = _orig_get_manifest_cached(*args, **kwargs)
        if "assets" not in manifest:
            manifest = dict(manifest)
            manifest["assets"] = {}
        if "installable" not in manifest:
            manifest = dict(manifest)
            manifest["installable"] = True
        if "depends" not in manifest:
            manifest = dict(manifest)
            manifest["depends"] = []
        return manifest

    _odoo_module._get_manifest_cached = _get_manifest_cached_patched
    _odoo_module._ab_assets_patch = True

# Compatibility: ignore missing addon modules during upgrade (e.g., studio_customization).
if not getattr(_odoo_module_loading, "_ab_skip_missing_addons", False):
    _orig_load_openerp_module = _odoo_module_loading.load_openerp_module

    def _load_openerp_module_patched(name):
        try:
            return _orig_load_openerp_module(name)
        except ModuleNotFoundError as exc:
            msg = str(exc)
            if msg.startswith("No module named 'odoo.addons.") and msg.endswith(name + "'"):
                # Skip missing addon module; keep going with others.
                return None
            raise

    _odoo_module_loading.load_openerp_module = _load_openerp_module_patched
    _odoo_module_loading._ab_skip_missing_addons = True

from . import models
from . import wizard
# -*- coding: utf-8 -*-

from .hooks import post_init_hook
