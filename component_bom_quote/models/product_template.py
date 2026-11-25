# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from ..lib.bom_parser import normalize_mpn


class ProductTemplate(models.Model):
    """Añade datos técnicos de componente electrónico al producto.

    Se usa herencia clásica (``_inherit`` sin ``_name``): los campos se
    añaden a la tabla existente ``product_template``. Gracias a la herencia
    por delegación de ``product.product``, estos campos también están
    disponibles (y se pueden buscar) desde las variantes.
    """

    _inherit = "product.template"

    mpn = fields.Char(
        string="Referencia del fabricante (MPN)",
        copy=False,
        help="Manufacturer Part Number, p. ej. LM358DR o STM32F103C8T6.",
    )
    # Campo calculado y almacenado: se recalcula solo cuando cambia el MPN
    # y, al estar indexado, las búsquedas desde la API son rápidas.
    mpn_normalized = fields.Char(
        string="MPN normalizado",
        compute="_compute_mpn_normalized",
        store=True,
        index=True,
    )
    manufacturer_name = fields.Char(string="Fabricante")
    package_type = fields.Selection(
        selection=[
            ("smd", "SMD (montaje superficial)"),
            ("tht", "THT (inserción)"),
            ("module", "Módulo"),
            ("other", "Otro"),
        ],
        string="Encapsulado",
    )
    rohs_compliant = fields.Boolean(string="Cumple RoHS", default=True)
    datasheet_url = fields.Char(string="Datasheet (URL)")

    @api.depends("mpn")
    def _compute_mpn_normalized(self):
        for template in self:
            template.mpn_normalized = normalize_mpn(template.mpn) or False

    @api.constrains("mpn", "company_id")
    def _check_mpn_unique(self):
        """Evita dos productos con el mismo MPN (tras normalizar)."""
        for template in self.filtered("mpn_normalized"):
            duplicate = self.search([
                ("id", "!=", template.id),
                ("mpn_normalized", "=", template.mpn_normalized),
                ("company_id", "in", [template.company_id.id, False]),
            ], limit=1)
            if duplicate:
                raise ValidationError(_(
                    "La referencia %(mpn)s ya existe en el producto "
                    "%(product)s.",
                    mpn=template.mpn,
                    product=duplicate.display_name,
                ))
