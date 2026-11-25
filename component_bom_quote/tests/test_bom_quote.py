# -*- coding: utf-8 -*-
"""Tests de integración con el ORM de Odoo.

Ejecutar:
    odoo -d test_bom -i component_bom_quote \
         --test-tags /component_bom_quote --stop-after-init
"""
import base64

from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestBomQuote(TransactionCase):

    # Se usan referencias distintas de las de los datos de demostración
    # para no chocar con la restricción de MPN único.

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Template = cls.env["product.template"]
        cls.lm358 = Template.create({
            "name": "OPA2333", "mpn": "OPA2333AIDR", "detailed_type": "product",
        }).product_variant_id
        cls.ne555 = Template.create({
            "name": "TLC555", "mpn": "TLC555CP", "detailed_type": "product",
        }).product_variant_id
        cls.diode = Template.create({
            "name": "BAT54S", "mpn": "BAT54S-7-F",
            "default_code": "INT-DIODE-01", "detailed_type": "product",
        }).product_variant_id
        cls.partner = cls.env["res.partner"].create({
            "name": "Cliente de prueba", "email": "compras@cliente.test",
        })

    def _new_request(self, **values):
        vals = {"partner_id": self.partner.id, "source_type": "text"}
        vals.update(values)
        return self.env["bom.quote.request"].create(vals)

    def test_mpn_normalized_is_computed(self):
        self.assertEqual(self.diode.mpn_normalized, "BAT54S7F")

    def test_duplicate_mpn_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.env["product.template"].create({
                "name": "Duplicado", "mpn": "opa2333aidr-tr",
            })

    def test_sequence_is_assigned(self):
        request = self._new_request(raw_text="10x OPA2333AIDR")
        self.assertTrue(request.name.startswith("BOM/"))

    def test_process_free_text(self):
        request = self._new_request(raw_text=(
            "100x OPA2333AIDR\n"
            "TLC555C - 50 uds\n"
            "BAT54S7F x 1.000\n"
            "XYZ-9999 x 10\n"
        ))
        request.action_process()
        self.assertEqual(request.state, "processed")
        lines = {line.requested_reference: line for line in request.line_ids}
        self.assertEqual(len(lines), 4)

        self.assertEqual(lines["OPA2333AIDR"].product_id, self.lm358)
        self.assertEqual(lines["OPA2333AIDR"].match_method, "exact")

        # "TLC555C" no existe tal cual: coincidencia aproximada con TLC555CP.
        self.assertEqual(lines["TLC555C"].product_id, self.ne555)
        self.assertEqual(lines["TLC555C"].match_method, "fuzzy")
        self.assertTrue(lines["TLC555C"].needs_review)

        self.assertEqual(lines["BAT54S7F"].product_id, self.diode)
        self.assertEqual(lines["BAT54S7F"].match_method, "normalized")
        self.assertEqual(lines["BAT54S7F"].quantity, 1000)

        self.assertFalse(lines["XYZ-9999"].product_id)
        self.assertEqual(request.matched_count, 3)
        self.assertEqual(request.review_count, 2)

    def test_internal_reference_is_matched(self):
        request = self._new_request(raw_text="INT-DIODE-01 x 5")
        request.action_process()
        self.assertEqual(request.line_ids.product_id, self.diode)

    def test_process_csv(self):
        csv_content = (
            "Referencia;Cantidad;Descripción\n"
            "OPA2333AIDR;200;Amplificador\n"
            "TLC555CP;1.500;Timer\n"
            "OPA2333AIDR;50;Repetida en otra placa\n"
        ).encode("cp1252")
        request = self._new_request(
            source_type="csv",
            bom_file=base64.b64encode(csv_content),
            bom_filename="bom.csv",
        )
        request.action_process()
        quantities = {line.product_id: line.quantity
                      for line in request.line_ids}
        self.assertEqual(quantities[self.lm358], 250)  # duplicados sumados
        self.assertEqual(quantities[self.ne555], 1500)

    def test_create_quotation(self):
        request = self._new_request(raw_text="100x OPA2333AIDR\nXYZ-9999 x 10")
        request.action_process()
        request.action_create_quotation()
        order = request.sale_order_id
        self.assertEqual(request.state, "quoted")
        self.assertEqual(order.partner_id, self.partner)
        self.assertEqual(order.origin, request.name)
        self.assertEqual(order.order_line.product_id, self.lm358)
        self.assertEqual(order.order_line.product_uom_qty, 100)

    def test_quotation_requires_processing(self):
        request = self._new_request(raw_text="100x OPA2333AIDR")
        with self.assertRaises(UserError):
            request.action_create_quotation()

    def test_empty_text_raises(self):
        request = self._new_request(raw_text="Hola, ¿qué tal?")
        with self.assertRaises(UserError):
            request.action_process()

    def test_ai_without_configuration_falls_back(self):
        request = self._new_request(raw_text="100x OPA2333AIDR", use_ai=True)
        request.action_process()
        self.assertEqual(request.line_ids.product_id, self.lm358)

    def test_create_from_text_api_helper(self):
        request = self.env["bom.quote.request"].create_from_text(
            self.partner, "100x OPA2333AIDR")
        self.assertEqual(request.state, "processed")
        self.assertFalse(request.user_id)
