"""P10 B4 — `get_net_worth`: read-only, per-currency net worth.

Pure delegation to `api/services/finance/net_worth.py::compute_net_worth`
(the single owner — assets from the balance invariant, liabilities from card
owed + active debt balances, superseded credit_card Debts excluded). The tool
NEVER re-derives a figure and NEVER rolls ₡+$ into one number on the fixed
₡500 placeholder (D3) — each currency reconciles apart and the narration
leads with the display currency.

Advisory-only: registered globally (import-time, like every tool) but shipped
solely via ADVISORY_TOOLSET — never in the normal-mode wire list.
"""
from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any, Optional

from api.services.advice_trace import record_advice_event
from api.services.finance.net_worth import compute_net_worth
from api.models.user import User

from ..session import AsyncSessionLocal
from .base import is_tool_registered, query_tool

GET_NET_WORTH_DESCRIPTION = (
    "Patrimonio neto del usuario POR MONEDA: activos (saldos de cuentas de "
    "fondos; una tarjeta prepagada cuenta como activo) menos pasivos (lo que "
    "debe en tarjetas + saldos de deudas activas). Cada moneda se reporta por "
    "aparte — NUNCA sumés colones y dólares en una sola cifra; presentá la "
    "moneda principal primero y las otras aparte. Los montos son strings "
    "decimales exactos."
)


def _money(value: Optional[Decimal]) -> Optional[str]:
    if value is None:
        return None
    return f"{value:.2f}"


async def get_net_worth(*, user_id: uuid.UUID) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        user = await db.get(User, user_id)
        if user is None:
            return {"error": "user_not_found"}
        reading = await compute_net_worth(db, user=user)

    payload: dict[str, Any] = {
        "display_currency": reading.currency,
        "entries": [
            {
                "currency": e.currency,
                "assets": _money(e.assets),
                "liabilities": _money(e.liabilities),
                "net": _money(e.net),
                "accounts_counted": e.accounts_counted,
                "debts_counted": e.debts_counted,
            }
            for e in reading.entries
        ],
        "note": (
            "Cada moneda reconcilia por aparte; no existe una cifra única "
            "₡+$ (no hay tipo de cambio de mercado configurado)."
        ),
    }
    # Best-effort trace (D11) — swallow-on-fail inside record_advice_event.
    await record_advice_event(
        user_id=user_id,
        kind="net_worth",
        verdict="info",
        inputs={},
        result=payload,
        surface="query_tool",
    )
    return payload


def register_net_worth_tool() -> None:
    if not is_tool_registered("get_net_worth"):
        query_tool(
            name="get_net_worth",
            description=GET_NET_WORTH_DESCRIPTION,
        )(get_net_worth)


register_net_worth_tool()
