# Presupuestos automáticos desde BOM · Odoo 17

[![CI](https://github.com/huanvojnito-dotcom/odoo-bom-quote/actions/workflows/ci.yml/badge.svg)](https://github.com/huanvojnito-dotcom/odoo-bom-quote/actions/workflows/ci.yml)

Módulo de Odoo 17 para **distribuidores de componentes electrónicos**. Convierte la lista de materiales (BOM) que envía un cliente, ya sea un CSV o un email en texto libre, en un **presupuesto de venta listo para revisar**. Identifica cada referencia en el catálogo aunque venga escrita de otra forma, e incluye un modo opcional con **IA**.

> *English summary below.*

## El problema

Un ingeniero de compras envía un email así:

```
Buenos días, necesitamos presupuesto para:
- 100x LM358DR
- NE555 - 50 uds
- 1N4148W7F x 1.000
- XYZ-9999 x 10
```

El comercial tiene que buscar cada referencia a mano. Muchas no coinciden exactamente con el catálogo: les falta el sufijo, tienen guiones distintos o llevan el código de embalaje (`-TR`, `/REEL`, `-ND`…). Después hay que crear el presupuesto línea a línea.

## La solución

1. **Entrada flexible**
   - CSV con cabeceras en español o inglés y separador `;`, `,` o tabulador, detectado automáticamente.
   - Codificación UTF-8 o la que usa Excel en Windows (cp1252).
   - Texto libre pegado desde un email.
2. **Búsqueda de referencias en 3 niveles**, por el MPN del fabricante y por la referencia interna:
   - **Exacta:** `LM358DR` → `LM358DR`.
   - **Normalizada:** `1N4148W7F-TR` → `1N4148W-7-F` (sin separadores ni sufijos de embalaje).
   - **Aproximada (fuzzy):** `NE555` → `NE555P`, con porcentaje de confianza y productos alternativos.
3. **Revisión humana:**
   - Las líneas aproximadas se marcan en amarillo y las no encontradas en rojo.
   - El comercial puede corregirlas antes de enviar.
   - Para cada línea se muestra el stock disponible en tiempo real.
4. **Un clic → presupuesto (`sale.order`)**. Las referencias no encontradas quedan anotadas en el chatter.
5. **IA opcional** para emails desordenados:
   - Usa cualquier API compatible con *chat completions*.
   - La respuesta del modelo se valida como datos no fiables.
   - Si falla, el módulo vuelve automáticamente al analizador clásico.
6. **API REST** para integraciones (web, n8n, ERP del cliente):
   - `GET /api/v1/components/<mpn>/stock`
   - `POST /api/v1/bom-quotes`

## Arquitectura

```
component_bom_quote/
├── lib/                      # Lógica pura en Python, SIN dependencias de Odoo
│   ├── bom_parser.py         #   normalización, CSV, texto libre, matching
│   └── llm_extractor.py      #   cliente de IA + validación de la respuesta
├── models/
│   ├── product_template.py   # _inherit: MPN, fabricante, encapsulado, RoHS
│   ├── bom_quote_request.py  # solicitud (mail.thread, estados, acciones)
│   └── bom_quote_line.py     # líneas: producto, confianza, stock
├── controllers/api.py        # API REST con API key (hmac.compare_digest)
├── views/                    # formularios, lista, búsqueda, herencia de vistas
├── security/                 # permisos por grupo (vendedor / responsable)
├── data/ demo/               # secuencia BOM/2026/0001 y catálogo de ejemplo
└── tests/                    # tests de integración con el ORM (TransactionCase)
tests/                        # tests unitarios de lib/ con pytest (CI en GitHub)
```

**Decisión de diseño:** la lógica de negocio está en `lib/` y no depende de Odoo. Así:
- se prueba con `pytest` en milisegundos (40 tests en CI);
- se reutiliza fuera de Odoo;
- los modelos quedan pequeños y legibles.

## Puesta en marcha

```bash
git clone https://github.com/huanvojnito-dotcom/odoo-bom-quote.git
cd odoo-bom-quote
docker compose up -d          # Odoo 17 + PostgreSQL 15
```

1. Abre http://localhost:8069 y crea una base de datos **con datos de demostración**.
2. Ve a *Aplicaciones* → *Actualizar lista de aplicaciones*, busca **«Presupuestos automáticos desde BOM»** e instálalo.
3. Entra en *Ventas → Pedidos → Presupuestos desde BOM* y abre la solicitud de ejemplo. Pulsa **Procesar BOM** y después **Crear presupuesto**.
4. Para probar la importación de CSV, usa `samples/bom_ejemplo.csv`.

### Tests

```bash
# Tests unitarios (sin Odoo)
pip install -r requirements-dev.txt
pytest -v

# Tests de integración dentro de Odoo
docker compose run --rm odoo odoo -d test_bom -i component_bom_quote \
    --test-tags /component_bom_quote --stop-after-init
```

### Configuración (Ajustes → Técnico → Parámetros del sistema)

| Clave | Uso |
|-------|-----|
| `component_bom_quote.api_key` | Clave de la API REST. Sin ella la API está desactivada. |
| `component_bom_quote.llm_api_url` | Endpoint *chat completions* (p. ej. `https://api.openai.com/v1/chat/completions`) |
| `component_bom_quote.llm_api_key` | Clave del proveedor de IA |
| `component_bom_quote.llm_model` | Modelo (por defecto `gpt-4o-mini`) |
| `component_bom_quote.fuzzy_threshold` | Umbral de similitud (por defecto `0.85`) |

Las claves se guardan en la base de datos, **nunca en el código**.

### Ejemplo de API

```bash
curl -H "X-API-Key: cambia-esta-clave" \
     http://localhost:8069/api/v1/components/LM358DR-TR/stock
```

Hay más ejemplos en `samples/api_examples.http`.

## Mejoras posibles

- Búsqueda aproximada en la base de datos con `pg_trgm` (índice trigram de PostgreSQL) para catálogos muy grandes.
- Importación también de Excel (`.xlsx`).
- Sugerencia de equivalentes (cross-reference) entre fabricantes.
- Cola de tareas para procesar BOM grandes en segundo plano.
- Migración a Odoo 18 (`tree` → `list`, `<chatter/>`).

## Uso de IA en el desarrollo

He desarrollado el proyecto con ayuda de un asistente de IA (Claude), igual que se trabaja con Cursor. Yo definí el problema, la arquitectura y los casos de prueba. Después revisé, ejecuté y corregí el código. Los tests documentan el comportamiento esperado.

---

## English summary

Odoo 17 module for electronic-component distributors. It turns a customer's bill of materials into a draft sales quotation. The input can be a CSV file or a free-text email.

- **Matching in three levels:** each part number is matched against the catalogue by exact, normalised (separators and packaging suffixes removed) and fuzzy comparison. Each match gets a confidence score and alternatives.
- **Review and stock:** a salesperson reviews uncertain lines, with real-time stock shown for each line.
- **AI extraction (optional):** works with an OpenAI-compatible API. The model's output is validated, and the module falls back to the regex parser if the call fails.
- **REST API:** endpoints for stock lookup and for creating requests from external systems.
- **Separate, tested logic:** the business logic lives in a pure-Python library, unit-tested in CI. Integration tests use Odoo's `TransactionCase`.

## Licencia

LGPL-3.0, la misma licencia que Odoo Community.
