"""Default transaction categories for CR-Spanish onboarding.

Resolución 9.5 (docs/phase-6d-decisions.md): lista corta de 10. Custom
categories quedan fuera de Phase 6d (van a 6e).

`transactions.category` sigue siendo free-form text — esta lista es lo
que el SPA dropdown y el bot ofrecen como sugerencia, no un enum
enforced en DB.
"""
from __future__ import annotations

CATEGORIES_CR: list[str] = [
    "alimentación",
    "transporte",
    "servicios",
    "salud",
    "ocio",
    "vivienda",
    "ingreso_salario",
    "ingreso_extra",
    "deudas",
    "otros",
]
