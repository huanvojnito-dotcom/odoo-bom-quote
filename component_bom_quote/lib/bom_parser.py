# -*- coding: utf-8 -*-
"""Lógica pura (sin dependencias de Odoo) para interpretar listas de materiales.

Este fichero no importa nada de Odoo a propósito: así la lógica de negocio se
puede probar con pytest en segundos (ver ``tests/`` en la raíz del repositorio)
y reutilizar desde otros sistemas (scripts, n8n, etc.).

Funciones principales:

* :func:`normalize_mpn`   - normaliza una referencia de fabricante (MPN).
* :func:`parse_quantity`  - convierte "1.000", "2,5", "100 uds" en números.
* :func:`parse_bom_csv`   - lee un CSV de BOM con cabeceras en español o inglés.
* :func:`parse_free_text` - extrae referencias y cantidades de un email.
* :class:`CatalogMatcher` - busca cada referencia en el catálogo
  (coincidencia exacta, normalizada o aproximada).
"""

import csv
import io
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

# Sufijos de embalaje que no cambian el componente en sí
# (bobina, cinta cortada, sufijos de distribuidores...).
# Solo se eliminan si van separados por "-", "/" o "#", para no romper
# referencias reales como "LM358DR".
_PACKAGING_SUFFIX_RE = re.compile(
    r"[-/#](TR|T&R|REEL|R7|R13|CT|CUT|ND|TA|TU|BULK)$"
)
_SEPARATORS_RE = re.compile(r"[\s\-_./#]")

# Alias de cabeceras aceptados en el CSV (se comparan ya normalizados).
_HEADER_ALIASES = {
    "reference": {
        "mpn", "referencia", "ref", "partnumber", "part", "pn", "codigo",
        "manufacturerpartnumber", "referenciafabricante", "mfrpartnumber",
        "mfrpn", "componente", "articulo",
    },
    "quantity": {
        "qty", "cantidad", "quantity", "cant", "uds", "unidades", "q",
        "pcs", "piezas",
    },
    "description": {"descripcion", "description", "desc", "detalle"},
}

# Umbral mínimo de similitud para aceptar una coincidencia aproximada.
DEFAULT_FUZZY_THRESHOLD = 0.85


@dataclass
class BomItem:
    """Una línea de la lista de materiales solicitada por el cliente."""

    reference: str
    quantity: float = 1.0
    description: str = ""


@dataclass
class MatchResult:
    """Resultado de buscar una referencia en el catálogo."""

    product_id: int | None
    method: str  # 'exact' | 'normalized' | 'fuzzy' | 'none'
    score: float  # 0-100
    alternatives: list = field(default_factory=list)  # ids de productos


# ---------------------------------------------------------------------------
# Normalización
# ---------------------------------------------------------------------------

def normalize_mpn(reference):
    """Normaliza una referencia de fabricante para poder compararla.

    >>> normalize_mpn(" lm358dr-tr ")
    'LM358DR'
    >>> normalize_mpn("1N4148W-7-F")
    '1N4148W7F'
    """
    if not reference:
        return ""
    value = str(reference).strip().upper()
    # Se repite por si hay varios sufijos encadenados ("-ND-TR").
    previous = None
    while previous != value:
        previous = value
        value = _PACKAGING_SUFFIX_RE.sub("", value)
    return _SEPARATORS_RE.sub("", value)


def _normalize_header(header):
    """'Part Number ' -> 'partnumber', 'Descripción' -> 'descripcion'."""
    value = (header or "").strip().lower()
    for src, dst in (("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"),
                     ("ú", "u"), ("ñ", "n")):
        value = value.replace(src, dst)
    return re.sub(r"[^a-z0-9]", "", value)


def parse_quantity(raw, default=1.0):
    """Convierte una cantidad escrita a mano en float.

    Acepta formatos europeos y anglosajones:
    "1.000" -> 1000, "1,000" -> 1000, "2,5" -> 2.5, "100 uds" -> 100.
    Devuelve ``default`` si no se puede interpretar o es <= 0.
    """
    if raw is None:
        return default
    text = str(raw).strip().lower()
    text = re.sub(r"(uds?\.?|unidades|pcs|pzas?|piezas|x)$", "", text).strip()
    text = text.replace(" ", "")
    if not text:
        return default
    # Separador de miles: 1.000 / 1,000 / 12.500.000
    if re.fullmatch(r"\d{1,3}([.,]\d{3})+", text):
        text = re.sub(r"[.,]", "", text)
    else:
        text = text.replace(",", ".")
    try:
        value = float(text)
    except ValueError:
        return default
    return value if value > 0 else default


def _looks_like_mpn(token):
    """Una referencia válida tiene letras y números y al menos 3 caracteres."""
    return (
        len(token) >= 3
        and re.search(r"[A-Za-z]", token) is not None
        and re.search(r"\d", token) is not None
    )


def merge_duplicates(items):
    """Agrupa las líneas repetidas sumando cantidades.

    Los clientes suelen pegar la misma referencia dos veces (por ejemplo,
    en dos placas distintas). Se agrupan por referencia normalizada y se
    respeta el orden de la primera aparición.
    """
    merged = {}
    for item in items:
        key = normalize_mpn(item.reference)
        if not key:
            continue
        if key in merged:
            merged[key].quantity += item.quantity
            if not merged[key].description and item.description:
                merged[key].description = item.description
        else:
            merged[key] = BomItem(item.reference.strip(), item.quantity,
                                  item.description)
    return list(merged.values())


# ---------------------------------------------------------------------------
# Lectura de ficheros y texto
# ---------------------------------------------------------------------------

def decode_bytes(content):
    """Decodifica un fichero subido probando las codificaciones habituales.

    Excel en Windows suele guardar los CSV en cp1252, no en UTF-8.
    """
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def parse_bom_csv(text):
    """Lee un CSV de BOM y devuelve una lista de :class:`BomItem`.

    * Detecta el delimitador automáticamente (``,`` ``;`` o tabulador).
    * Reconoce cabeceras en español e inglés (ver ``_HEADER_ALIASES``).
    * Si no hay cabecera reconocible, asume: columna 1 = referencia,
      columna 2 = cantidad.
    """
    text = (text or "").strip()
    if not text:
        return []
    try:
        dialect = csv.Sniffer().sniff(text[:2048], delimiters=",;\t")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ";" if text.count(";") > text.count(",") else ","

    rows = [row for row in csv.reader(io.StringIO(text), delimiter=delimiter)
            if any(cell.strip() for cell in row)]
    if not rows:
        return []

    # Intentamos identificar las columnas a partir de la cabecera.
    columns = {}
    for index, header in enumerate(rows[0]):
        normalized = _normalize_header(header)
        for key, aliases in _HEADER_ALIASES.items():
            if normalized in aliases and key not in columns:
                columns[key] = index
    if "reference" in columns:
        data_rows = rows[1:]
    else:
        columns = {"reference": 0, "quantity": 1}
        data_rows = rows

    def cell(row, key):
        index = columns.get(key)
        if index is None or index >= len(row):
            return ""
        return row[index].strip()

    items = []
    for row in data_rows:
        reference = cell(row, "reference")
        if not reference:
            continue
        items.append(BomItem(
            reference=reference,
            quantity=parse_quantity(cell(row, "quantity")),
            description=cell(row, "description"),
        ))
    return merge_duplicates(items)


# Patrones para texto libre. Ejemplos que reconocen:
#   "100x LM358DR", "100 uds de NE555P", "250 pcs STM32F103C8T6"
#   "LM358DR - 100 uds", "NE555P: 50", "BC547B x 1.000"
_QTY = r"(?P<qty>\d[\d.,]*)"
_REF = r"(?P<ref>[A-Za-z0-9][A-Za-z0-9\-_./#]{2,})"
_UNITS = r"(?:x|uds?\.?|unidades|pcs|pzas?|piezas)"
_FREE_TEXT_PATTERNS = [
    re.compile(_QTY + r"\s*" + _UNITS + r"\s*(?:de\s+)?" + _REF,
               re.IGNORECASE),
    re.compile(_REF + r"\s*(?:[:\-–=]|x|×)\s*" + _QTY + r"\s*"
               + _UNITS + r"?", re.IGNORECASE),
]


def parse_free_text(text):
    """Extrae referencias y cantidades de un texto libre (p. ej. un email).

    Procesa línea a línea y descarta palabras que no parecen referencias
    (deben contener letras y números). Las líneas con una referencia pero
    sin cantidad se añaden con cantidad 1.
    """
    items = []
    for line in (text or "").splitlines():
        line = line.strip().strip("-*•·").strip()
        if not line:
            continue
        for pattern in _FREE_TEXT_PATTERNS:
            match = pattern.search(line)
            if match and _looks_like_mpn(match.group("ref")):
                items.append(BomItem(
                    reference=match.group("ref").rstrip(".,;:"),
                    quantity=parse_quantity(match.group("qty")),
                ))
                break
        else:
            # Línea con una sola referencia y sin cantidad: "LM358DR"
            tokens = line.split()
            if len(tokens) == 1 and _looks_like_mpn(tokens[0]):
                items.append(BomItem(reference=tokens[0].rstrip(".,;:")))
    return merge_duplicates(items)


# ---------------------------------------------------------------------------
# Búsqueda en el catálogo
# ---------------------------------------------------------------------------

class CatalogMatcher:
    """Busca referencias en un catálogo en memoria.

    ``catalog`` es una lista de tuplas ``(product_id, codigo)``. Un mismo
    producto puede aparecer varias veces (MPN y referencia interna).

    Estrategia, de más a menos fiable:

    1. **exact**      - el texto coincide tal cual (sin distinguir mayúsculas).
    2. **normalized** - coincide tras normalizar (sin guiones, sufijos...).
    3. **fuzzy**      - similitud >= umbral (``difflib.SequenceMatcher``),
       útil para erratas o referencias incompletas ("NE555" -> "NE555P").

    Nota de rendimiento: la búsqueda aproximada recorre todo el catálogo
    (O(n) por referencia). Para catálogos de cientos de miles de productos
    se sustituiría por un índice trigram de PostgreSQL (extensión
    ``pg_trgm``) directamente en la consulta SQL.
    """

    def __init__(self, catalog, fuzzy_threshold=DEFAULT_FUZZY_THRESHOLD,
                 max_alternatives=3):
        self.fuzzy_threshold = fuzzy_threshold
        self.max_alternatives = max_alternatives
        self._exact = {}
        self._normalized = {}
        for product_id, code in catalog:
            if not code:
                continue
            self._exact.setdefault(str(code).strip().upper(), product_id)
            self._normalized.setdefault(normalize_mpn(code), product_id)

    def _ranked_candidates(self, normalized):
        """Devuelve [(score, product_id)] ordenado de mayor a menor."""
        best = {}
        for code, product_id in self._normalized.items():
            score = SequenceMatcher(None, normalized, code).ratio()
            if score > best.get(product_id, 0.0):
                best[product_id] = score
        return sorted(((s, p) for p, s in best.items()), reverse=True)

    def match(self, reference):
        raw = (reference or "").strip().upper()
        if not raw:
            return MatchResult(None, "none", 0.0)

        if raw in self._exact:
            return MatchResult(self._exact[raw], "exact", 100.0)

        normalized = normalize_mpn(raw)
        if normalized in self._normalized:
            return MatchResult(self._normalized[normalized], "normalized",
                               100.0)

        ranked = self._ranked_candidates(normalized)
        # Alternativas: candidatos razonablemente parecidos (>= 60 %).
        alternatives = [p for s, p in ranked if s >= 0.6]
        if ranked and ranked[0][0] >= self.fuzzy_threshold:
            score, product_id = ranked[0]
            return MatchResult(
                product_id, "fuzzy", round(score * 100, 1),
                [p for p in alternatives if p != product_id]
                [: self.max_alternatives],
            )
        return MatchResult(None, "none", 0.0,
                           alternatives[: self.max_alternatives])
