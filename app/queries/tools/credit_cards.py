"""Read-only query tool — credit-card minimum-payment analysis (Phase 7b B4).

Answers "¿cuánto debo en la tarjeta?", "¿qué pasa si solo pago el mínimo?",
"¿cuánto me cobra de intereses la tarjeta?" with the same deterministic
revolving engine the native card screen uses (app/domain/credit) over the
LIVE balance — so the chat answer can never drift from the app. The LLM only
narrates the numbers; it never calculates interest.
"""
from __future__ import annotations

import uuid
from typing import Any, Optional

from rapidfuzz import fuzz, utils

from api.services.credit_cards import (
    build_card_analysis,
    list_active_cards_with_terms,
)

from app.queries.session import AsyncSessionLocal

from .base import is_tool_registered, query_tool


GET_CARD_ANALYSIS_DESCRIPTION = (
    "Análisis determinista de la tarjeta de crédito del usuario sobre su saldo "
    "VIVO: cuánto debe, el pago mínimo de hoy, cuánto le cuesta el mes en "
    "intereses, y qué pasa si paga solo el mínimo (meses para terminar y el "
    "total de intereses), más estrategias (doble del mínimo, pagarla en 12 "
    "meses). Usalo para «¿cuánto debo en la tarjeta?», «¿qué pasa si solo pago "
    "el mínimo?», «¿cuánto me ahorro pagando más?». card_hint es opcional — "
    "solo si el usuario nombra una tarjeta específica. Si never_pays_off=true "
    "decilo SIN suavizar: pagando solo el mínimo NUNCA la termina de pagar "
    "porque el mínimo no cubre ni los intereses del mes. Si la respuesta trae "
    "available_card_names, pedile que aclare cuál tarjeta. No inventés números "
    "ni recalculés intereses: usá solo lo que devuelve esta herramienta. Es "
    "solo informativa — no registra nada."
)

_FUZZY_THRESHOLD = 70


async def get_card_analysis(
    *,
    user_id: uuid.UUID,
    card_hint: Optional[str] = None,
) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        cards = await list_active_cards_with_terms(db, user_id=user_id)

    if not cards:
        return {
            "error": "no_cards_with_terms",
            "message": (
                "El usuario no tiene tarjetas de crédito con términos "
                "registrados. Puede registrar una diciendo «registrá mi "
                "tarjeta de crédito» o agregar los términos desde la pestaña "
                "Cuentas."
            ),
        }

    card = None
    if len(cards) == 1:
        card = cards[0]
    elif card_hint:
        scored = sorted(
            (
                (
                    fuzz.WRatio(
                        card_hint,
                        c.account.name,
                        processor=utils.default_process,
                    ),
                    c,
                )
                for c in cards
            ),
            key=lambda pair: pair[0],
            reverse=True,
        )
        if scored[0][0] >= _FUZZY_THRESHOLD:
            card = scored[0][1]

    if card is None:
        return {
            "error": "ambiguous_card",
            "available_card_names": [c.account.name for c in cards],
        }

    analysis = build_card_analysis(card=card)
    payload = analysis.model_dump(mode="json")
    payload["card_name"] = card.account.name
    return payload


def register_credit_card_tools() -> None:
    if not is_tool_registered("get_card_analysis"):
        query_tool(
            name="get_card_analysis",
            description=GET_CARD_ANALYSIS_DESCRIPTION,
        )(get_card_analysis)


register_credit_card_tools()
