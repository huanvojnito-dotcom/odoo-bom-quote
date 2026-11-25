# -*- coding: utf-8 -*-
"""Tests unitarios de la lógica pura (sin Odoo). Ejecutar con: pytest"""
import importlib.util
import pathlib
import sys

import pytest

LIB = pathlib.Path(__file__).resolve().parents[1] / "component_bom_quote" / "lib"


def _load(name):
    """Carga un fichero de lib/ sin importar el paquete Odoo completo."""
    spec = importlib.util.spec_from_file_location(
        f"bomlib.{name}", LIB / f"{name}.py",
        submodule_search_locations=None)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# llm_extractor hace "from . import bom_parser": se crea un paquete virtual.
_pkg_spec = importlib.util.spec_from_file_location(
    "bomlib", LIB / "__init__.py", submodule_search_locations=[str(LIB)])
_pkg = importlib.util.module_from_spec(_pkg_spec)
sys.modules["bomlib"] = _pkg
bp = _load("bom_parser")
_pkg.bom_parser = bp
llm = _load("llm_extractor")


# --- normalize_mpn ----------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    ("LM358DR", "LM358DR"),
    (" lm358dr ", "LM358DR"),
    ("LM358DR-TR", "LM358DR"),
    ("1N4148W-7-F", "1N4148W7F"),
    ("ESP32-WROOM-32E", "ESP32WROOM32E"),
    ("296-1395-1-ND", "29613951"),
    ("STM32F103C8T6/REEL", "STM32F103C8T6"),
    ("", ""),
    (None, ""),
])
def test_normalize_mpn(raw, expected):
    assert bp.normalize_mpn(raw) == expected


# --- parse_quantity ---------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    ("100", 100), ("1.000", 1000), ("1,000", 1000), ("12.500", 12500),
    ("2,5", 2.5), ("100 uds", 100), ("250pcs", 250), ("", 1.0),
    ("abc", 1.0), ("0", 1.0), ("-5", 1.0), (None, 1.0),
])
def test_parse_quantity(raw, expected):
    assert bp.parse_quantity(raw) == expected


# --- parse_bom_csv ----------------------------------------------------------

def test_csv_spanish_headers_semicolon():
    text = ("Referencia;Cantidad;Descripción\n"
            "LM358DR;200;Amplificador\n"
            "NE555P;1.500;Timer\n")
    items = bp.parse_bom_csv(text)
    assert [(i.reference, i.quantity) for i in items] == [
        ("LM358DR", 200), ("NE555P", 1500)]
    assert items[0].description == "Amplificador"


def test_csv_english_headers_any_order():
    text = "Qty,Description,Manufacturer Part Number\n10,Timer,NE555P\n"
    items = bp.parse_bom_csv(text)
    assert items[0].reference == "NE555P"
    assert items[0].quantity == 10


def test_csv_without_header():
    items = bp.parse_bom_csv("LM358DR,5\nBC547B,10\n")
    assert [(i.reference, i.quantity) for i in items] == [
        ("LM358DR", 5), ("BC547B", 10)]


def test_csv_merges_duplicates_and_skips_empty_rows():
    text = "mpn;qty\nLM358DR;100\n;\nlm358dr-tr;50\n"
    items = bp.parse_bom_csv(text)
    assert len(items) == 1
    assert items[0].quantity == 150


def test_decode_bytes_cp1252():
    assert bp.decode_bytes("Descripción".encode("cp1252")) == "Descripción"
    assert bp.decode_bytes("﻿mpn".encode("utf-8")) == "mpn"


# --- parse_free_text --------------------------------------------------------

EMAIL = """Buenos días,
necesitamos presupuesto para el siguiente pedido:
- 100x LM358DR
- NE555 - 50 uds
- 250 pcs STM32F103C8T6
- 1N4148W7F x 1.000
- XYZ-9999 x 10
* BC547B
Gracias. Un saludo, Ana (tel. 600 123 456)
"""


def test_free_text_email():
    items = bp.parse_free_text(EMAIL)
    assert [(i.reference, i.quantity) for i in items] == [
        ("LM358DR", 100),
        ("NE555", 50),
        ("STM32F103C8T6", 250),
        ("1N4148W7F", 1000),
        ("XYZ-9999", 10),
        ("BC547B", 1),
    ]


def test_free_text_spanish_units():
    items = bp.parse_free_text("50 unidades de ESP32-WROOM-32E")
    assert items[0].reference == "ESP32-WROOM-32E"
    assert items[0].quantity == 50


def test_free_text_ignores_plain_words():
    assert bp.parse_free_text("Hola, ¿qué tal?\nUn saludo") == []


# --- CatalogMatcher ---------------------------------------------------------

CATALOG = [
    (1, "LM358DR"), (1, "CV-LM358"),
    (2, "NE555P"),
    (3, "1N4148W-7-F"),
    (4, "STM32F103C8T6"),
    (5, "STM32F103CBT6"),
]


@pytest.fixture
def matcher():
    return bp.CatalogMatcher(CATALOG)


def test_exact_match(matcher):
    result = matcher.match("lm358dr")
    assert (result.product_id, result.method, result.score) == (
        1, "exact", 100.0)


def test_internal_code_match(matcher):
    assert matcher.match("CV-LM358").product_id == 1


def test_normalized_match(matcher):
    result = matcher.match("1N4148W7F-TR")
    assert (result.product_id, result.method) == (3, "normalized")


def test_fuzzy_match_with_alternatives(matcher):
    result = matcher.match("STM32F103C8T")
    assert result.method == "fuzzy"
    assert result.product_id == 4
    assert 85 <= result.score < 100
    assert 5 in result.alternatives


def test_no_match(matcher):
    result = matcher.match("XYZ-9999")
    assert (result.product_id, result.method) == (None, "none")


def test_empty_reference(matcher):
    assert matcher.match("").method == "none"


# --- respuesta de la IA -----------------------------------------------------

def test_llm_response_plain_json():
    items = llm.parse_llm_response(
        '{"items": [{"reference": "LM358DR", "quantity": "1.000"},'
        ' {"reference": "NE555P"}]}')
    assert [(i.reference, i.quantity) for i in items] == [
        ("LM358DR", 1000), ("NE555P", 1)]


def test_llm_response_fenced_and_dirty():
    content = ('Aquí tienes:\n```json\n{"items": [{"reference": "BC547B", '
               '"quantity": 10}, {"reference": ""}, "basura"]}\n```')
    items = llm.parse_llm_response(content)
    assert [(i.reference, i.quantity) for i in items] == [("BC547B", 10)]


@pytest.mark.parametrize("content", ["", "no es json", '{"otro": 1}'])
def test_llm_response_invalid(content):
    with pytest.raises(llm.LLMError):
        llm.parse_llm_response(content)
