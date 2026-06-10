"""Read-only query tool — financing / loan simulation (Phase 7 advisor).

Answers the EXPLORATORY financing question ("¿me conviene financiar?", "¿cuánto
sería la cuota de un préstamo de ₡X a N años al T%?", "si doy la prima y financio
el resto…") WITHOUT registering anything. This is the advisor counterpart to
registering a debt (``Intent.CREATE_DEBT`` → native form): here the user is still
deciding, so the read-only dispatcher simulates the loan deterministically
(French amortization — the same math the debt module uses) and runs the monthly
cuota through the Phase 7 affordability engine (does it fit the safe disposable?).
The LLM only explains the numbers — it never invents them and never registers a
debt. It closes the loop the affordability tool already opens: when
``assess_purchase`` asks "¿estás considerando financiarlo?", THIS is the tool
that answers.
"""
from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Annotated, Any, Optional

from pydantic import Field

from api.models.user import User
from api.services.amortization import compute_french_payment
from api.services.finance.affordability import SAFETY_MARGIN, gate_guidance
from api.services.finance.cashflow import compute_monthly_cashflow

from app.queries.session import AsyncSessionLocal

from .base import is_tool_registered, query_tool


ASSESS_FINANCING_DESCRIPTION = (
    "Simulá de forma determinista un préstamo/financiamiento que el usuario "
    "está CONSIDERANDO (todavía no lo registra). Usá esto cuando explore "
    "financiar una compra: «¿me conviene financiar?», «¿cuánto sería la cuota de "
    "un préstamo de ₡X a N años al T%?», «si doy una prima y financio el "
    "resto…». Pasá price (precio total), annual_rate_pct (tasa anual en "
    "porcentaje, p.ej. 45), term_months (plazo en MESES: «20 años» → 240) y, si "
    "lo menciona, down_payment (la prima). Calcula la cuota mensual (amortización "
    "francesa), el interés total, cuántas veces el precio terminaría pagando, y "
    "compara la cuota contra su SOBRANTE seguro (surplus = ingreso − lo asignado "
    "en sobres, con margen del 80%). Reportá con honestidad: si la cuota NO cabe "
    "(cuota_fits_surplus=false) decilo claro y mostrá el faltante (cuota_shortfall); "
    "el interés total deja ver si la tasa es abusiva. IMPORTANTE — gate_reason: si "
    "viene 'no_income'/'no_budget'/'under_coverage', cuota_fits_surplus es null y NO "
    "afirmés si cabe; pedí la acción que corresponde (registrar ingreso / armar "
    "sobres / que los sobres cubran deudas y gastos fijos), que son distintas. No "
    "inventés números: usá solo los que devuelve esta herramienta. Esta herramienta "
    "NO registra la deuda — si el usuario decide hacerlo, que diga «registrá el "
    "préstamo»."
)

Price = Annotated[float, Field(gt=0)]
_CENTS = Decimal("0.01")


def _money(value: Optional[Decimal]) -> Optional[str]:
    return f"{value:.2f}" if value is not None else None


async def assess_financing(
    *,
    price: Price,
    annual_rate_pct: float,
    term_months: int,
    down_payment: float = 0.0,
    user_id: uuid.UUID,
) -> dict[str, Any]:
    months = int(term_months) if term_months and term_months >= 1 else 1
    down = max(0.0, float(down_payment or 0.0))
    financed = max(0.0, float(price) - down)
    monthly_rate = float(annual_rate_pct) / 100.0 / 12.0

    cuota = compute_french_payment(financed, monthly_rate, months)
    total_paid = cuota * months
    total_interest = max(0.0, total_paid - financed)
    interest_multiple = (total_interest / float(price)) if price else None
    cuota_d = Decimal(str(round(cuota, 2)))

    async with AsyncSessionLocal() as db:
        user = await db.get(User, user_id)
        if user is None:
            return {"error": "user_not_found"}
        cashflow = await compute_monthly_cashflow(db, user=user)

    notes: list[str] = []
    if not cashflow.reliable:
        # Gated → no trustworthy surplus → don't claim whether the cuota fits.
        # Same posture + same distinct copy as every other surface.
        safe: Optional[Decimal] = None
        fits: Optional[bool] = None
        shortfall: Optional[Decimal] = None
        notes.append(gate_guidance(cashflow.gate_reason))
    else:
        safe = (cashflow.surplus * SAFETY_MARGIN).quantize(_CENTS)
        fits = cuota_d <= safe
        shortfall = max(Decimal("0"), (cuota_d - safe)).quantize(_CENTS)

    return {
        "currency": cashflow.currency,
        "price": f"{float(price):.2f}",
        "down_payment": f"{down:.2f}",
        "financed_principal": f"{financed:.2f}",
        "annual_rate_pct": float(annual_rate_pct),
        "term_months": months,
        "monthly_payment": f"{cuota:.2f}",
        "total_paid": f"{total_paid:.2f}",
        "total_interest": f"{total_interest:.2f}",
        "interest_multiple_of_price": (
            round(interest_multiple, 2) if interest_multiple is not None else None
        ),
        "monthly_income": _money(cashflow.monthly_income) if cashflow.income_known else None,
        "committed_outflows": _money(cashflow.committed_outflows),
        "surplus": _money(cashflow.surplus) if cashflow.income_known else None,
        "safe_surplus": _money(safe),
        "cuota_fits_surplus": fits,
        "cuota_shortfall": _money(shortfall),
        "safety_margin_pct": 80,
        "gate_reason": cashflow.gate_reason,
        "notes": [n for n in notes if n],
    }


def register_financing_tools() -> None:
    if not is_tool_registered("assess_financing"):
        query_tool(
            name="assess_financing",
            description=ASSESS_FINANCING_DESCRIPTION,
        )(assess_financing)


register_financing_tools()
