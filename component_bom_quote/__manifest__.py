# -*- coding: utf-8 -*-
{
    "name": "Presupuestos automáticos desde BOM",
    "summary": "Convierte listas de materiales (CSV o email) en presupuestos, "
               "con búsqueda inteligente de referencias e IA opcional",
    "description": """
Pensado para distribuidores de componentes electrónicos:

* Datos técnicos en el producto: MPN, fabricante, encapsulado, RoHS, datasheet.
* Importación de BOM en CSV (cabeceras en español o inglés, ; , o tabulador).
* Lectura de solicitudes en texto libre (emails), con IA opcional.
* Búsqueda de referencias: exacta, normalizada y aproximada (fuzzy).
* Creación del presupuesto (sale.order) con un clic.
* API REST para consultar stock y crear solicitudes desde otros sistemas.
    """,
    "version": "17.0.1.0.0",
    "category": "Sales/Sales",
    "author": "Attila János Vojnár",
    "website": "https://github.com/huanvojnito-dotcom/odoo-bom-quote",
    "license": "LGPL-3",
    "depends": ["sale_management", "stock", "mail"],
    "data": [
        "security/ir.model.access.csv",
        "data/ir_sequence_data.xml",
        "views/product_template_views.xml",
        "views/bom_quote_request_views.xml",
        "views/menus.xml",
    ],
    "demo": [
        "demo/demo_data.xml",
    ],
    "installable": True,
    "application": False,
}
