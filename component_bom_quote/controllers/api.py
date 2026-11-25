# -*- coding: utf-8 -*-
"""API REST mínima para integraciones externas (web, n8n, ERP del cliente).

Endpoints:

* ``GET  /api/v1/components/<mpn>/stock`` - stock de una referencia.
* ``POST /api/v1/bom-quotes``             - crea y procesa una solicitud.

Autenticación: cabecera ``X-API-Key`` comparada con el parámetro del
sistema ``component_bom_quote.api_key``. Si el parámetro no existe, la API
queda desactivada (respuesta 401): es seguro por defecto.
"""

import hmac
import json
import logging

from odoo import http
from odoo.http import request

from ..lib.bom_parser import normalize_mpn

_logger = logging.getLogger(__name__)

PARAM_API_KEY = "component_bom_quote.api_key"
MAX_TEXT_LENGTH = 20000


class ComponentApiController(http.Controller):

    # -- utilidades ---------------------------------------------------------

    @staticmethod
    def _authorized():
        expected = request.env["ir.config_parameter"].sudo().get_param(
            PARAM_API_KEY)
        provided = request.httprequest.headers.get("X-API-Key", "")
        # compare_digest evita ataques de temporización (timing attacks).
        return bool(expected) and hmac.compare_digest(expected, provided)

    @staticmethod
    def _json(data, status=200):
        return request.make_json_response(data, status=status)

    # -- endpoints ----------------------------------------------------------

    @http.route("/api/v1/components/<string:mpn>/stock", type="http",
                auth="public", methods=["GET"], csrf=False)
    def component_stock(self, mpn, **kwargs):
        if not self._authorized():
            return self._json({"error": "unauthorized"}, status=401)

        normalized = normalize_mpn(mpn)
        products = request.env["product.product"].sudo().search(
            [("mpn_normalized", "=", normalized), ("sale_ok", "=", True)],
            limit=5,
        )
        return self._json({
            "query": mpn,
            "normalized": normalized,
            "found": bool(products),
            "results": [{
                "mpn": product.mpn,
                "name": product.display_name,
                "manufacturer": product.manufacturer_name or None,
                "package": product.package_type or None,
                "rohs": product.rohs_compliant,
                "qty_available": product.qty_available,
                "uom": product.uom_id.name,
                "datasheet": product.datasheet_url or None,
            } for product in products],
        })

    @http.route("/api/v1/bom-quotes", type="http", auth="public",
                methods=["POST"], csrf=False)
    def create_bom_quote(self, **kwargs):
        """Cuerpo JSON esperado::

            {"email": "compras@cliente.com", "name": "Cliente S.L.",
             "text": "100x LM358DR\\n50 uds NE555P", "use_ai": false}
        """
        if not self._authorized():
            return self._json({"error": "unauthorized"}, status=401)
        try:
            payload = json.loads(request.httprequest.get_data() or b"{}")
        except ValueError:
            return self._json({"error": "invalid_json"}, status=400)

        email = (payload.get("email") or "").strip().lower()
        text = (payload.get("text") or "").strip()
        if not email or not text:
            return self._json(
                {"error": "missing_fields", "required": ["email", "text"]},
                status=400)
        if len(text) > MAX_TEXT_LENGTH:
            return self._json({"error": "text_too_long"}, status=413)

        env = request.env(su=True)
        partner = env["res.partner"].search([("email", "=ilike", email)],
                                            limit=1)
        if not partner:
            partner = env["res.partner"].create({
                "name": payload.get("name") or email,
                "email": email,
            })
        try:
            # El savepoint deshace la solicitud a medias si algo falla.
            with env.cr.savepoint():
                quote = env["bom.quote.request"].create_from_text(
                    partner, text, use_ai=bool(payload.get("use_ai")))
        except Exception as exc:  # noqa: BLE001 - se devuelve como JSON
            _logger.exception("Error en /api/v1/bom-quotes")
            return self._json({"error": "processing_failed",
                               "detail": str(exc)}, status=422)

        return self._json({
            "id": quote.id,
            "reference": quote.name,
            "match_rate": round(quote.match_rate, 1),
            "lines": [{
                "requested": line.requested_reference,
                "quantity": line.quantity,
                "product": line.product_id.display_name or None,
                "match": line.match_method,
                "confidence": line.match_score,
                "stock_status": line.stock_status,
            } for line in quote.line_ids],
        }, status=201)
