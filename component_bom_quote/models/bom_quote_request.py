# -*- coding: utf-8 -*-
import base64
import logging

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError

from ..lib import bom_parser, llm_extractor

_logger = logging.getLogger(__name__)

# Claves de configuración (Ajustes > Técnico > Parámetros del sistema).
# La API key nunca se guarda en el código ni en el repositorio.
PARAM_LLM_URL = "component_bom_quote.llm_api_url"
PARAM_LLM_KEY = "component_bom_quote.llm_api_key"
PARAM_LLM_MODEL = "component_bom_quote.llm_model"
PARAM_FUZZY = "component_bom_quote.fuzzy_threshold"


class BomQuoteRequest(models.Model):
    """Solicitud de presupuesto a partir de una lista de materiales.

    Flujo: Borrador -> Procesada (referencias identificadas)
    -> Presupuestada (se ha creado el sale.order).
    """

    _name = "bom.quote.request"
    _description = "Solicitud de presupuesto desde BOM"
    # mail.thread añade el chatter: historial, mensajes y seguimiento.
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "id desc"

    name = fields.Char(string="Referencia", required=True, readonly=True,
                       copy=False, default=lambda self: _("Nuevo"))
    partner_id = fields.Many2one("res.partner", string="Cliente",
                                 required=True, tracking=True)
    company_id = fields.Many2one("res.company", string="Compañía",
                                 required=True,
                                 default=lambda self: self.env.company)
    user_id = fields.Many2one("res.users", string="Comercial",
                              default=lambda self: self.env.user,
                              tracking=True)
    source_type = fields.Selection(
        selection=[("csv", "Archivo CSV"), ("text", "Texto libre / email")],
        string="Origen", required=True, default="csv",
    )
    bom_file = fields.Binary(string="Archivo BOM (CSV)", attachment=True)
    bom_filename = fields.Char(string="Nombre del archivo")
    raw_text = fields.Text(string="Texto de la solicitud")
    use_ai = fields.Boolean(
        string="Interpretar con IA",
        help="Usa un modelo de lenguaje para extraer referencias de textos "
             "desordenados. Si no está configurado o falla, se usa el "
             "analizador clásico.",
    )
    state = fields.Selection(
        selection=[
            ("draft", "Borrador"),
            ("processed", "Procesada"),
            ("quoted", "Presupuestada"),
            ("cancel", "Cancelada"),
        ],
        string="Estado", default="draft", required=True, tracking=True,
    )
    line_ids = fields.One2many("bom.quote.line", "request_id",
                               string="Líneas")
    sale_order_id = fields.Many2one("sale.order", string="Presupuesto",
                                    readonly=True, copy=False)

    line_count = fields.Integer(compute="_compute_stats",
                                string="Nº de líneas")
    matched_count = fields.Integer(compute="_compute_stats",
                                   string="Identificadas")
    review_count = fields.Integer(compute="_compute_stats",
                                  string="Para revisar")
    match_rate = fields.Float(compute="_compute_stats",
                              string="Tasa de identificación (%)")

    @api.depends("line_ids.product_id", "line_ids.needs_review")
    def _compute_stats(self):
        for request in self:
            lines = request.line_ids
            matched = lines.filtered("product_id")
            request.line_count = len(lines)
            request.matched_count = len(matched)
            request.review_count = len(lines.filtered("needs_review"))
            request.match_rate = (
                100.0 * len(matched) / len(lines) if lines else 0.0
            )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("Nuevo")) == _("Nuevo"):
                vals["name"] = self.env["ir.sequence"].next_by_code(
                    "bom.quote.request") or _("Nuevo")
        return super().create(vals_list)

    # ------------------------------------------------------------------
    # Lectura de la BOM
    # ------------------------------------------------------------------

    def _get_bom_items(self):
        """Devuelve la lista de ``BomItem`` según el origen elegido."""
        self.ensure_one()
        if self.source_type == "csv":
            if not self.bom_file:
                raise UserError(_("Adjunta un archivo CSV con la BOM."))
            content = base64.b64decode(self.bom_file)
            return bom_parser.parse_bom_csv(bom_parser.decode_bytes(content))

        if not (self.raw_text or "").strip():
            raise UserError(_("Pega el texto de la solicitud del cliente."))
        if self.use_ai:
            items = self._extract_with_ai(self.raw_text)
            if items:
                return items
        return bom_parser.parse_free_text(self.raw_text)

    def _extract_with_ai(self, text):
        """Extracción con IA. Devuelve [] si no está disponible o falla."""
        params = self.env["ir.config_parameter"].sudo()
        url = params.get_param(PARAM_LLM_URL)
        key = params.get_param(PARAM_LLM_KEY)
        model = params.get_param(PARAM_LLM_MODEL, "gpt-4o-mini")
        if not url or not key:
            self.message_post(body=_(
                "IA no configurada: se ha usado el analizador clásico."))
            return []
        try:
            items = llm_extractor.extract_items(url, key, model, text)
        except llm_extractor.LLMError as exc:
            _logger.warning("Fallo en la extracción con IA: %s", exc)
            self.message_post(body=_(
                "La IA no ha podido procesar el texto (%(error)s). "
                "Se ha usado el analizador clásico.", error=exc))
            return []
        self.message_post(body=_(
            "Referencias extraídas con IA: %(count)s.", count=len(items)))
        return items

    def _build_matcher(self):
        """Carga el catálogo vendible (MPN y referencia interna)."""
        products = self.env["product.product"].search([
            ("sale_ok", "=", True),
            "|", ("mpn", "!=", False), ("default_code", "!=", False),
        ])
        catalog = []
        for product in products:
            if product.mpn:
                catalog.append((product.id, product.mpn))
            if product.default_code:
                catalog.append((product.id, product.default_code))
        threshold = float(self.env["ir.config_parameter"].sudo().get_param(
            PARAM_FUZZY, bom_parser.DEFAULT_FUZZY_THRESHOLD))
        return bom_parser.CatalogMatcher(catalog, fuzzy_threshold=threshold)

    # ------------------------------------------------------------------
    # Acciones de los botones
    # ------------------------------------------------------------------

    def action_process(self):
        """Lee la BOM e identifica cada referencia en el catálogo."""
        for request in self:
            items = request._get_bom_items()
            if not items:
                raise UserError(_(
                    "No se ha encontrado ninguna referencia válida."))
            matcher = request._build_matcher()
            commands = [Command.clear()]
            for sequence, item in enumerate(items, start=1):
                result = matcher.match(item.reference)
                commands.append(Command.create({
                    "sequence": sequence * 10,
                    "requested_reference": item.reference,
                    "description": item.description,
                    "quantity": item.quantity,
                    "product_id": result.product_id or False,
                    "match_method": result.method,
                    "match_score": result.score,
                    "alternative_product_ids": [
                        Command.set(result.alternatives)],
                }))
            request.write({"line_ids": commands, "state": "processed"})
            request.message_post(body=_(
                "BOM procesada: %(total)s referencias, %(matched)s "
                "identificadas, %(review)s para revisar.",
                total=request.line_count,
                matched=request.matched_count,
                review=request.review_count,
            ))
        return True

    def action_create_quotation(self):
        """Crea el presupuesto con las líneas identificadas."""
        self.ensure_one()
        if self.state != "processed":
            raise UserError(_("Primero procesa la BOM."))
        lines = self.line_ids.filtered("product_id")
        if not lines:
            raise UserError(_(
                "Ninguna referencia está asociada a un producto."))

        order = self.env["sale.order"].create({
            "partner_id": self.partner_id.id,
            "company_id": self.company_id.id,
            "user_id": self.user_id.id,
            "origin": self.name,
            "order_line": [
                Command.create({
                    "product_id": line.product_id.id,
                    "product_uom_qty": line.quantity,
                })
                for line in lines
            ],
        })
        missing = self.line_ids - lines
        if missing:
            order.message_post(body=_(
                "Referencias no encontradas en el catálogo: %(refs)s",
                refs=", ".join(missing.mapped("requested_reference")),
            ))
        self.write({"sale_order_id": order.id, "state": "quoted"})
        return self.action_view_sale_order()

    def action_view_sale_order(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "sale.order",
            "res_id": self.sale_order_id.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_reset_to_draft(self):
        self.write({"state": "draft"})

    def action_cancel(self):
        self.write({"state": "cancel"})

    # ------------------------------------------------------------------
    # Uso desde la API REST
    # ------------------------------------------------------------------

    @api.model
    def create_from_text(self, partner, text, use_ai=False, user=None):
        """Crea y procesa una solicitud en un solo paso (para la API).

        Desde la API no hay un comercial "conectado", por eso el comercial
        se pasa explícitamente (o se deja vacío para asignarlo después).
        """
        request = self.create({
            "partner_id": partner.id,
            "source_type": "text",
            "raw_text": text,
            "use_ai": use_ai,
            "user_id": user.id if user else False,
        })
        request.action_process()
        return request
