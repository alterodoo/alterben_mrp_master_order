# -*- coding: utf-8 -*-
import re
import unicodedata

from odoo import api, models


class QualityTagPatch(models.Model):
    _inherit = 'quality.tag'

    @api.model
    def _normalize_tag_name(self, name):
        text = (name or '').strip()
        text = re.sub(r'^\s*\d+\.\s*', '', text)
        text = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode('ascii')
        text = text.lower()
        text = re.sub(r'[^a-z0-9]+', ' ', text)
        return text.strip()

    @api.model
    def _ensure_novedades_tags(self):
        # Definir todas las etiquetas deseadas
        all_desired_tags = [
            "Distorsion",
            "Contaminacion interna",
            "Rayas",
            "Burbujas",
            "Despostillado y Desconchado",
            "Delaminacion",
            "Manchas en vidrio",
            "Incrustacion",
            "Manchas en Serigrafia",
            "Flecha / Asentamiento",
            "No cumple especificaciones",
            "Importados",
            "Vidrio Resbalado",
            "Problemas del horno",
            "Vidrio pasado",
            "Vidrio Pegado (descarga)",
            "Vidrio mal curvado",
            "Ojo Óptico",  # Nueva etiqueta
            "Roto en proceso",
            "Roto en corte de vidrio",
            "Roto Inspeccion / Bodega",
            "Roto Autoclave",
            "Roto Transporte",
            "Roto en reciclo",
            "Vidrio roto (bisagra)",
            "Roto en carga de vidrio",
        ]
        alias_map = {
            self._normalize_tag_name("Rotura Lijado/Pulido"): "Roto en proceso",
            self._normalize_tag_name("Rotura Lijado Pulido"): "Roto en proceso",
        }

        # Separar etiquetas regulares y etiquetas de rotura
        regular_tags = []
        rotura_tags = []

        for tag_name in all_desired_tags:
            normalized_name = self._normalize_tag_name(tag_name)
            if 'roto' in normalized_name or 'rotura' in normalized_name:
                rotura_tags.append(tag_name)
            else:
                regular_tags.append(tag_name)

        # Combinar: primero las regulares, luego las de rotura
        ordered_tags = regular_tags + rotura_tags

        Tag = self.env['quality.tag'].sudo()

        # Eliminar TODAS las etiquetas existentes para recrearlas limpiamente
        all_current_tags = Tag.search([])
        for tag in all_current_tags:
            try:
                tag.unlink()
            except:
                pass  # Si no se puede eliminar por restricciones, continuar

        # Crear todas las etiquetas en el orden correcto
        for idx, base_name in enumerate(ordered_tags, start=1):
            new_name = f"{idx}. {base_name}"
            Tag.create({'name': new_name})

    @api.model
    def init(self):
        super().init()
        try:
            self._ensure_novedades_tags()
        except Exception:
            # Evitar bloquear el arranque si hay inconsistencias en datos.
            return

    @api.model
    def init(self):
        super().init()
        try:
            self._ensure_novedades_tags()
        except Exception:
            # Evitar bloquear el arranque si hay inconsistencias en datos.
            return
