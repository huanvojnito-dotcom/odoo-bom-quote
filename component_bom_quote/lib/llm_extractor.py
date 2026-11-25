# -*- coding: utf-8 -*-
"""Extracción de referencias con un modelo de lenguaje (opcional).

Se usa una API compatible con el formato *chat completions* (OpenAI,
Mistral, Groq, Ollama, LM Studio...), de modo que el proveedor se cambia
solo con la configuración, sin tocar código.

Principios de diseño:

* **La IA propone, el código valida.** La respuesta del modelo se trata
  como datos no fiables: se exige JSON, se valida cada campo y se
  descarta lo que no tenga sentido.
* **Siempre hay plan B.** Si la API falla o la respuesta no es válida,
  el módulo vuelve al parser de expresiones regulares.
* **Privacidad.** Solo se envía el texto de la solicitud; nunca datos de
  precios, stock ni del cliente.
"""

import json
import re

from . import bom_parser

SYSTEM_PROMPT = (
    "Eres un asistente que extrae componentes electrónicos de solicitudes "
    "de clientes. Devuelve EXCLUSIVAMENTE un JSON con este formato: "
    '{"items": [{"reference": "MPN", "quantity": 100}]}. '
    "reference es la referencia del fabricante tal como aparece en el texto; "
    "quantity es un número positivo (1 si no se indica). "
    "No inventes referencias que no aparezcan en el texto."
)


class LLMError(Exception):
    """Error al llamar al modelo o al interpretar su respuesta."""


def parse_llm_response(content):
    """Convierte la respuesta del modelo en una lista de ``BomItem``.

    Acepta JSON puro o JSON dentro de un bloque ```json ... ```.
    Lanza :class:`LLMError` si el formato no es válido.
    """
    if not content:
        raise LLMError("Respuesta vacía del modelo")
    fenced = re.search(r"```(?:json)?\s*(.*?)```", content, re.DOTALL)
    payload = fenced.group(1) if fenced else content
    try:
        data = json.loads(payload)
    except ValueError as exc:
        raise LLMError("La respuesta no es JSON válido") from exc

    raw_items = data.get("items") if isinstance(data, dict) else data
    if not isinstance(raw_items, list):
        raise LLMError("Falta la lista 'items' en la respuesta")

    items = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        reference = str(raw.get("reference") or "").strip()
        if not bom_parser.normalize_mpn(reference):
            continue
        items.append(bom_parser.BomItem(
            reference=reference,
            quantity=bom_parser.parse_quantity(raw.get("quantity")),
        ))
    return bom_parser.merge_duplicates(items)


def extract_items(api_url, api_key, model, text, timeout=20):
    """Llama al modelo y devuelve la lista de ``BomItem`` extraída.

    ``requests`` se importa aquí para que el resto de la librería
    (y sus tests) no dependa de él.
    """
    import requests

    try:
        response = requests.post(
            api_url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "temperature": 0,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": text[:8000]},
                ],
            },
            timeout=timeout,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
    except (requests.RequestException, KeyError, IndexError, ValueError) as exc:
        raise LLMError(f"Error al llamar a la API del modelo: {exc}") from exc
    return parse_llm_response(content)
