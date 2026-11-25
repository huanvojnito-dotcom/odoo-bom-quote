# -*- coding: utf-8 -*-
from odoo import api, fields, models

MATCH_METHODS = [
    ("exact", "Exacta"),
    ("normalized", "Normalizada"),
    ("fuzzy", "Aproximada"),
    ("manual", "Manual"),
    ("none", "Sin coincidencia"),
]


class BomQuoteLine(models.Model):
    """Una referencia solicitada y el producto del catálogo asignado."""

    _name = "bom.quote.line"
    _description = "Línea de solicitud de presupuesto BOM"
    _order = "request_id, sequence, id"

    request_id = fields.Many2one(
        "bom.quote.request", string="Solicitud",
        required=True, ondelete="cascade", index=True,
    )
    sequence = fields.Integer(default=10)
    requested_reference = fields.Char(string="Referencia solicitada",
                                      required=True)
    description = fields.Char(string="Descripción del cliente")
    quantity = fields.Float(string="Cantidad", default=1.0,
                            digits="Product Unit of Measure")
    product_id = fields.Many2one("product.product", string="Producto")
    match_method = fields.Selection(MATCH_METHODS, string="Coincidencia",
                                    default="none")
    match_score = fields.Float(string="Confianza (%)", digits=(5, 1))
    alternative_product_ids = fields.Many2many(
        "product.product",
        relation="bom_quote_line_alternative_rel",
        column1="line_id",
        column2="product_id",
        string="Alternativas",
    )
    # Campo relacionado no almacenado: el stock se lee en tiempo real.
    qty_available = fields.Float(related="product_id.qty_available",
                                 string="Stock disponible")
    stock_status = fields.Selection(
        selection=[
            ("available", "Disponible"),
            ("partial", "Parcial"),
            ("out", "Sin stock"),
            ("unknown", "—"),
        ],
        string="Estado de stock",
        compute="_compute_stock_status",
    )
    needs_review = fields.Boolean(
        string="Revisar",
        compute="_compute_needs_review",
        store=True,
        help="Coincidencias aproximadas o no encontradas: un comercial "
             "debe confirmarlas antes de enviar el presupuesto.",
    )

    @api.depends("product_id", "quantity", "qty_available")
    def _compute_stock_status(self):
        for line in self:
            if not line.product_id:
                line.stock_status = "unknown"
            elif line.qty_available >= line.quantity:
                line.stock_status = "available"
            elif line.qty_available > 0:
                line.stock_status = "partial"
            else:
                line.stock_status = "out"

    @api.depends("match_method")
    def _compute_needs_review(self):
        for line in self:
            line.needs_review = line.match_method in ("fuzzy", "none")

    @api.onchange("product_id")
    def _onchange_product_id(self):
        """Si el comercial elige el producto a mano, queda como 'manual'."""
        if self.product_id and self.match_method in ("none", "fuzzy"):
            self.match_method = "manual"
            self.match_score = 100.0
