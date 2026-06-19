"""Read-only query tool — affordability / pushback (Phase 7).

Answers "¿me alcanza para X?" / "¿puedo con una compra de ₡Y en N meses?" by
running the deterministic affordability engine
(``api/services/finance/affordability.py``) against the user's real income,
fixed bills and debt payments. The engine decides feasibility; the LLM only
explains the result honestly and, when it doesn't fit, offers the real
alternatives this tool already computed (extend the timeline / lower the
amount). The LLM must NOT invent numbers — it reports what this tool returns.
"""
from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Annotated, Any, Optional

from pydantic import Field
from sqlalchemy import select

from api.models.debt import Debt
from api.models.user import User
from api.services.advice_trace import record_advice_event, verdict_from
from api.services.finance.affordability import (
    FinancialContext,
    assess_for_user,
    gather_financial_context,
)
from api.services.fx import convert

from app.queries.session import AsyncSessionLocal

from .base import is_tool_registered, query_tool


ASSESS_PURCHASE_DESCRIPTION = (
    "Evalúa de forma determinista si al usuario le alcanza para una compra o "
    "meta de ahorro. El veredicto se calcula contra su SOBRANTE mensual real "
    "(surplus = ingreso − lo que ya tiene asignado en sobres/presupuesto, "
    "committed_outflows), con un margen de seguridad del 80%. Usá esto cuando "
    "pregunte «¿me alcanza para…?», «¿puedo con una compra de ₡X?», «¿puedo "
    "ahorrar ₡Y en N meses?». Pasá amount y, si lo menciona, timeline_months; sin "
    "plazo se evalúa como compra de este mes. IMPORTANTE — gate_reason: si viene "
    "'no_income', 'no_budget' o 'under_coverage', feasible es null y NO debés "
    "afirmar un sobrante ni un veredicto; en su lugar pedile la acción que "
    "corresponde, y son DISTINTAS: no_income → que registre su ingreso; no_budget "
    "→ que arme sus sobres (presupuesto); under_coverage → que ajuste sus sobres "
    "para que cubran sus deudas y gastos fijos. Si gate_reason es null, reportá "
    "con honestidad: si feasible=false ofrecé las alternativas ya calculadas "
    "(min_timeline_months_feasible = en cuántos meses sí alcanzaría; "
    "max_amount_feasible_in_timeline = cuánto sí podría en ese plazo) Y, si "
    "savings_allocations > 0, agregá que PODRÍA reasignar parte de sus sobres de "
    "ahorro/inversión (₡savings_allocations al mes) si quiere priorizar esto — es "
    "decisión del usuario, no lo des por hecho. El campo «context» trae señales "
    "(envelope_pressure, upcoming_obligations): mencionalas como contexto (p.ej. "
    "«ojo que ya te pasaste del sobre X y viene Y el DD/MM»), PERO no recalculés "
    "el veredicto con ellas ni inventés montos: usá solo los números de esta "
    "herramienta."
)

PurchaseAmount = Annotated[float, Field(gt=0)]


def _money(value: Optional[Decimal]) -> Optional[str]:
    return f"{value:.2f}" if value is not None else None


def _serialize_context(ctx: FinancialContext) -> dict[str, Any]:
    pressure = None
    if ctx.envelope_pressure is not None:
        p = ctx.envelope_pressure
        pressure = {
            "currency": p.currency,
            "total_limit": _money(p.total_limit),
            "total_spent": _money(p.total_spent),
            "total_remaining": _money(p.total_remaining),
            "pct_consumed": p.pct_consumed,
            "over_limit": [
                {
                    "name": o.name,
                    "overage": _money(o.overage),
                    "currency": o.currency,
                }
                for o in p.over_limit
            ],
        }
    return {
        "envelope_pressure": pressure,
        "upcoming_obligations": [
            {
                "title": o.title,
                "due_date": o.due_date,
                "amount": _money(o.amount),
                "currency": o.currency,
                "is_overdue": o.is_overdue,
                "kind": o.kind,
            }
            for o in ctx.upcoming_obligations
        ],
        "upcoming_total": _money(ctx.upcoming_total),
    }


async def assess_purchase(
    *,
    amount: PurchaseAmount,
    timeline_months: Optional[int] = None,
    user_id: uuid.UUID,
) -> dict[str, Any]:
    months = timeline_months if (timeline_months and timeline_months >= 1) else 1
    async with AsyncSessionLocal() as db:
        user = await db.get(User, user_id)
        if user is None:
            return {"error": "user_not_found"}
        result = await assess_for_user(
            db,
            user=user,
            desired_amount=Decimal(str(amount)),
            timeline_months=months,
        )
        # Context signals (envelope execution + upcoming bills/events). They do
        # NOT change the verdict above; the LLM weaves them into its explanation.
        context = await gather_financial_context(
            db, user=user, horizon_days=min(max(60, months * 30), 365)
        )

    payload = {
        "currency": result.currency,
        "desired_amount": _money(result.desired_amount),
        "timeline_months": result.timeline_months,
        "monthly_income": _money(result.monthly_income),
        "monthly_fixed_expenses": _money(result.monthly_fixed_expenses),
        "monthly_debt_payments": _money(result.monthly_debt_payments),
        "envelope_allocations": _money(result.envelope_allocations),
        "committed_outflows": _money(result.committed_outflows),
        "savings_allocations": _money(result.savings_allocations),
        "surplus": _money(result.surplus),
        "safe_surplus": _money(result.safe_surplus),
        "safety_margin_pct": 80,
        "monthly_needed": _money(result.monthly_needed),
        "feasible": result.feasible,
        "gate_reason": result.gate_reason,
        "shortfall": _money(result.shortfall),
        "min_timeline_months_feasible": result.min_timeline_months_feasible,
        "max_amount_feasible_in_timeline": _money(
            result.max_amount_feasible_in_timeline
        ),
        "notes": list(result.notes),
        "context": _serialize_context(context),
    }
    # Phase 7e advice trace — best-effort, never alters the answer.
    await record_advice_event(
        user_id=user_id,
        kind="assess_purchase",
        verdict=verdict_from(
            feasible=result.feasible, gate_reason=result.gate_reason
        ),
        inputs={"amount": amount, "timeline_months": timeline_months},
        result=payload,
        surface="query_tool",
    )
    return payload


GET_SAVINGS_CAPACITY_DESCRIPTION = (
    "Devuelve, de forma determinista, cuánto le SOBRA al usuario al mes para "
    "ahorrar: surplus = ingreso − lo ya asignado en sobres (committed_outflows), y "
    "el sobrante seguro (margen del 80%, safe_surplus). Usá esto SIEMPRE que pida "
    "planear ahorros sin meta concreta («¿cuánto puedo ahorrar al mes?», «¿cuánto "
    "me queda libre?», «¿cómo reparto mi plata?»). IMPORTANTE — gate_reason: si "
    "viene 'no_income'/'no_budget'/'under_coverage', NO muestres un número de "
    "sobrante; pedí la acción que corresponde, y son DISTINTAS: no_income → "
    "registrar el ingreso; no_budget → armar los sobres; under_coverage → que los "
    "sobres cubran las deudas y gastos fijos. Si gate_reason es null, reportá "
    "surplus / safe_surplus tal cual. El campo «debts» trae cada deuda con su cuota "
    "(mencionalas); «savings_allocations» es lo que ya va a sobres de "
    "ahorro/inversión. NO inventés montos."
)

_CENTS = Decimal("0.01")


async def get_savings_capacity(*, user_id: uuid.UUID) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        user = await db.get(User, user_id)
        if user is None:
            return {"error": "user_not_found"}
        # Reuse the engine for the disposable/safe math so this can't drift from
        # assess_purchase/assess_goal. desired_amount=0 → the feasibility fields
        # are vacuous (ignored); we only want the income/fixed/debt/disposable.
        result = await assess_for_user(
            db, user=user, desired_amount=Decimal("0"), timeline_months=1
        )
        currency = result.currency
        # Per-debt breakdown for visibility (sum == monthly_debt_payments).
        rows = await db.execute(
            select(Debt.name, Debt.minimum_payment, Debt.currency).where(
                Debt.user_id == user.id,
                Debt.is_active.is_(True),
            )
        )
        debts: list[dict[str, Any]] = []
        for name, payment, debt_currency in rows.all():
            if payment is None:
                continue
            amount = Decimal(payment)
            if amount <= 0:
                continue
            debts.append(
                {
                    "name": name,
                    "monthly_payment": _money(
                        convert(amount, debt_currency, currency).quantize(_CENTS)
                    ),
                    "currency": currency,
                }
            )

    payload = {
        "currency": currency,
        "monthly_income": _money(result.monthly_income),
        "monthly_fixed_expenses": _money(result.monthly_fixed_expenses),
        "monthly_debt_payments": _money(result.monthly_debt_payments),
        "envelope_allocations": _money(result.envelope_allocations),
        "committed_outflows": _money(result.committed_outflows),
        "savings_allocations": _money(result.savings_allocations),
        "surplus": _money(result.surplus),
        "safe_surplus": _money(result.safe_surplus),
        "safety_margin_pct": 80,
        "gate_reason": result.gate_reason,
        "debts": debts,
        "notes": list(result.notes),
    }
    # Phase 7e advice trace — informational verdict unless gated.
    await record_advice_event(
        user_id=user_id,
        kind="savings_capacity",
        verdict=(
            f"gated:{result.gate_reason}" if result.gate_reason else "info"
        ),
        inputs={},
        result=payload,
        surface="query_tool",
    )
    return payload


def register_affordability_tools() -> None:
    if not is_tool_registered("assess_purchase"):
        query_tool(
            name="assess_purchase",
            description=ASSESS_PURCHASE_DESCRIPTION,
        )(assess_purchase)
    if not is_tool_registered("get_savings_capacity"):
        query_tool(
            name="get_savings_capacity",
            description=GET_SAVINGS_CAPACITY_DESCRIPTION,
        )(get_savings_capacity)


register_affordability_tools()
