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

from api.models.user import User
from api.services.finance.affordability import (
    FinancialContext,
    assess_for_user,
    gather_financial_context,
)

from app.queries.session import AsyncSessionLocal

from .base import is_tool_registered, query_tool


ASSESS_PURCHASE_DESCRIPTION = (
    "Evalúa de forma determinista si al usuario le alcanza para una compra o "
    "meta de ahorro, usando sus ingresos recurrentes, gastos fijos y pagos de "
    "deudas reales. Usá esto cuando pregunte «¿me alcanza para…?», «¿puedo con "
    "una compra de ₡X?», «¿puedo ahorrar ₡Y en N meses?». Pasá amount (monto "
    "deseado) y, si lo menciona, timeline_months (en cuántos meses); si no da "
    "plazo, se evalúa como una compra de este mes. El motor aplica un margen de "
    "seguridad del 80% del disponible. Reportá el resultado con honestidad: si "
    "NO alcanza (feasible=false), decilo claro y ofrecé las alternativas que ya "
    "vienen calculadas (min_timeline_months_feasible = en cuántos meses sí "
    "alcanzaría; max_amount_feasible_in_timeline = cuánto sí podría en ese "
    "plazo). Si feasible es null no hay ingresos registrados: pedile al usuario "
    "que los registre. El campo «context» trae señales adicionales: "
    "envelope_pressure (cómo va con sus sobres/presupuestos este mes, incluidos "
    "los que ya se pasaron del tope) y upcoming_obligations (pagos recurrentes o "
    "eventos próximos). Mencioná esas señales como contexto en tu respuesta "
    "(p.ej. «te alcanza, pero ojo que ya te pasaste del sobre X y viene Y el "
    "DD/MM»), PERO no recalculés el veredicto con ellas ni inventés montos: el "
    "veredicto sale del cálculo income−gastos fijos−deudas; usá solo los números "
    "que devuelve esta herramienta."
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

    return {
        "currency": result.currency,
        "desired_amount": _money(result.desired_amount),
        "timeline_months": result.timeline_months,
        "monthly_income": _money(result.monthly_income),
        "monthly_fixed_expenses": _money(result.monthly_fixed_expenses),
        "monthly_debt_payments": _money(result.monthly_debt_payments),
        "monthly_disposable": _money(result.monthly_disposable),
        "safe_monthly_disposable": _money(result.safe_monthly_disposable),
        "safety_margin_pct": 80,
        "monthly_needed": _money(result.monthly_needed),
        "feasible": result.feasible,
        "shortfall": _money(result.shortfall),
        "min_timeline_months_feasible": result.min_timeline_months_feasible,
        "max_amount_feasible_in_timeline": _money(
            result.max_amount_feasible_in_timeline
        ),
        "notes": list(result.notes),
        "context": _serialize_context(context),
    }


def register_affordability_tools() -> None:
    if not is_tool_registered("assess_purchase"):
        query_tool(
            name="assess_purchase",
            description=ASSESS_PURCHASE_DESCRIPTION,
        )(assess_purchase)


register_affordability_tools()
