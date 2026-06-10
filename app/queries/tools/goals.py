"""Read-only query tools — savings goals (Phase 7a advisor).

Two tools that let the read-only dispatcher act as a goal-planning advisor
WITHOUT inventing numbers:

- ``list_goals`` — enumerate the user's goals (name, target, progress, deadline)
  so "¿qué metas tengo?" works and so "esa meta" can be disambiguated.
- ``assess_goal`` — the grounded planner. Given a goal name, it resolves the
  real Goal row, derives the amount still missing (target − current) and the
  timeline (from the deadline or an explicit override), then runs the SAME
  deterministic affordability engine ``assess_purchase`` uses
  (``api/services/finance/affordability.py``). Income − fixed expenses − debt
  decide whether the monthly figure is realistic; envelope pressure and
  upcoming obligations come back as context. The amount and timeline come from
  the ledger, never from the LLM — this is what stops "ahorrá ₡1.000.000/mes"
  answers that ignore the user's fixed costs and debts.

The LLM only explains what these tools return. It must never state a monthly
savings figure it did not get from ``assess_goal``.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Optional

from pydantic import Field
from sqlalchemy import select

from api.models.goal import Goal
from api.models.user import User
from api.services.finance.affordability import (
    assess_for_user,
    gather_financial_context,
)

from app.queries.session import AsyncSessionLocal

# Reuse the exact serialization assess_purchase uses so the context block the
# LLM sees is byte-identical across both advisor tools (no drift).
from .affordability import _money, _serialize_context
from .base import is_tool_registered, query_tool


LIST_GOALS_DESCRIPTION = (
    "Devuelve las metas de ahorro del usuario (nombre, monto objetivo, cuánto "
    "lleva ahorrado, porcentaje de avance, fecha límite y estado). Usá esto "
    "cuando pregunte «¿qué metas tengo?», «¿cómo voy con mis metas?» o cuando "
    "necesités identificar a cuál meta se refiere antes de armarle un plan. No "
    "inventés metas: si la lista viene vacía, decílo."
)

ASSESS_GOAL_DESCRIPTION = (
    "Arma un plan determinista para alcanzar una META de ahorro existente. Usá "
    "esto SIEMPRE que el usuario pregunte cómo llegar a una meta o cuánto debería "
    "ahorrar para ella («¿cómo alcanzo esa meta?», «¿cómo ahorro para el "
    "iPhone?», «¿cuánto debería guardar al mes para mi meta?»). Pasá goal_name "
    "(coincide por nombre, sin distinguir mayúsculas; si el usuario dijo «esa "
    "meta», usá el nombre que aparezca en la conversación reciente). "
    "Opcionalmente pasá timeline_months_override si el usuario propone un plazo "
    "distinto al de la meta. La herramienta calcula lo que falta (objetivo − "
    "ahorrado) y el plazo (de la fecha límite o del override), y corre el motor "
    "de accesibilidad sobre el SOBRANTE real (surplus = ingreso − lo asignado en "
    "sobres): monthly_needed es lo que habría que guardar al mes y feasible dice "
    "si cabe en el sobrante seguro (margen 80%). NUNCA inventés ese monto "
    "mensual: reportá monthly_needed tal cual. IMPORTANTE — gate_reason: si viene "
    "'no_income'/'no_budget'/'under_coverage', feasible es null y NO afirmés un "
    "veredicto; pedí la acción que corresponde (registrar ingreso / armar sobres / "
    "que los sobres cubran deudas y gastos fijos), que son distintas. Si "
    "feasible=false, ofrecé las alternativas ya calculadas "
    "(min_timeline_months_feasible = en cuántos meses sí alcanzaría; "
    "max_amount_feasible_in_timeline = cuánto sí podría en ese plazo) y, si "
    "savings_allocations > 0, que podría reasignar parte de sus sobres de "
    "ahorro/inversión si quiere priorizar la meta (decisión del usuario). Si la "
    "meta no tiene fecha límite (has_deadline=false), el plan se "
    "evalúa como compra inmediata; usá min_timeline_months_feasible para decir "
    "en cuántos meses llegaría a su ritmo seguro. El campo «context» trae el "
    "estado de los sobres y los pagos próximos: mencionalos como contexto (p.ej. "
    "«te alcanza, pero ojo que ya te pasaste del sobre X»), pero no recalculés el "
    "veredicto con ellos. Si no encontrás la meta, mirá available_goal_names."
)

# Terminal states aren't worth planning toward — a goal that's done or dropped
# shouldn't surface in "qué metas tengo" or get a savings plan.
_TERMINAL_STATUSES = frozenset({"achieved", "completed", "abandoned"})

TimelineOverride = Annotated[Optional[int], Field(default=None, ge=1, le=600)]


def _plannable(goal: Goal) -> bool:
    return (goal.status or "active") not in _TERMINAL_STATUSES


def _months_between(start: date, end: date) -> int:
    """Whole months from start to end, floored at 1 (avoids /0 in forecasts).

    Mirrors api/services/telegram_dispatcher.py::_months_between — reimplemented
    here to avoid importing that heavy write-path module into a query tool.
    """
    months = (end.year - start.year) * 12 + (end.month - start.month)
    return max(1, months)


def _pct_progress(current: Decimal, target: Decimal) -> Optional[int]:
    if target <= 0:
        return None
    return int((current / target * Decimal("100")).to_integral_value())


def _match(goals: list[Goal], name: str) -> list[Goal]:
    """Case-insensitive name match: exact first, else substring either way."""
    q = name.strip().lower()
    exact = [g for g in goals if g.name.lower() == q]
    if exact:
        return exact
    return [g for g in goals if q in g.name.lower() or g.name.lower() in q]


def _goal_brief(goal: Goal) -> dict[str, Any]:
    target = Decimal(goal.target_amount)
    current = Decimal(goal.current_amount or 0)
    return {
        "name": goal.name,
        "currency": goal.target_currency,
        "target_amount": _money(target),
        "current_amount": _money(current),
        "remaining_amount": _money(max(Decimal("0"), target - current)),
        "pct_progress": _pct_progress(current, target),
        "target_date": goal.target_date.isoformat() if goal.target_date else None,
        "monthly_contribution": _money(
            Decimal(goal.monthly_contribution)
            if goal.monthly_contribution is not None
            else None
        ),
        "status": goal.status or "active",
    }


async def _load_plannable_goals(db, user_id: uuid.UUID) -> list[Goal]:
    rows = await db.execute(
        select(Goal)
        .where(Goal.user_id == user_id)
        .order_by(Goal.priority.asc(), Goal.created_at.asc())
    )
    return [g for g in rows.scalars().all() if _plannable(g)]


async def list_goals(*, user_id: uuid.UUID) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        goals = await _load_plannable_goals(db, user_id)
    return {
        "goals": [_goal_brief(g) for g in goals],
        "count": len(goals),
    }


async def assess_goal(
    *,
    goal_name: str,
    timeline_months_override: TimelineOverride = None,
    user_id: uuid.UUID,
) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        user = await db.get(User, user_id)
        if user is None:
            return {"error": "user_not_found"}

        goals = await _load_plannable_goals(db, user.id)
        if not goals:
            return {"matched": False, "available_goal_names": []}

        matched = _match(goals, goal_name) if goal_name.strip() else []
        if not matched:
            return {
                "matched": False,
                "query_name": goal_name,
                "available_goal_names": [g.name for g in goals],
            }
        if len(matched) > 1:
            # Ambiguous — don't guess which goal to plan for. Let the LLM ask.
            return {
                "matched": False,
                "needs_disambiguation": True,
                "query_name": goal_name,
                "candidates": [_goal_brief(g) for g in matched],
            }

        goal = matched[0]
        target = Decimal(goal.target_amount)
        current = Decimal(goal.current_amount or 0)
        remaining = max(Decimal("0"), target - current)
        user_currency = user.currency or "CRC"

        # Timeline: explicit override wins, else derive from the deadline.
        if timeline_months_override and timeline_months_override >= 1:
            timeline = int(timeline_months_override)
            timeline_source = "override"
            has_deadline = goal.target_date is not None
        elif goal.target_date is not None:
            timeline = _months_between(date.today(), goal.target_date)
            timeline_source = "deadline"
            has_deadline = True
        else:
            # No deadline + no override → model as an immediate target. The
            # headline verdict is "this month", but min_timeline_months_feasible
            # tells the user the realistic horizon at their safe disposable.
            timeline = 1
            timeline_source = "none"
            has_deadline = False

        assessment = None
        currency_mismatch = goal.target_currency != user_currency
        if remaining > 0 and not currency_mismatch:
            assessment = await assess_for_user(
                db,
                user=user,
                desired_amount=remaining,
                timeline_months=timeline,
            )

        # Context signals (envelopes + upcoming obligations), user currency.
        # These never change the verdict; the LLM weaves them into the plan.
        context = await gather_financial_context(
            db, user=user, horizon_days=min(max(60, timeline * 30), 365)
        )

    out: dict[str, Any] = {
        "matched": True,
        "goal": _goal_brief(goal),
        "remaining_amount": _money(remaining),
        "timeline_months": timeline,
        "timeline_source": timeline_source,
        "has_deadline": has_deadline,
        "already_funded": remaining <= 0,
        "context": _serialize_context(context),
    }
    if currency_mismatch:
        out["notes"] = [
            "La meta está en una moneda distinta a la de tus ingresos; no puedo "
            "confirmar si te alcanza, solo mostrarte el monto que falta."
        ]
        out["feasible"] = None
        return out
    if assessment is None:
        # Already funded — nothing to plan.
        out["feasible"] = None
        out["monthly_needed"] = _money(Decimal("0"))
        out["notes"] = ["Ya alcanzaste el objetivo de esta meta."]
        return out

    out.update(
        {
            "currency": assessment.currency,
            "monthly_income": _money(assessment.monthly_income),
            "monthly_fixed_expenses": _money(assessment.monthly_fixed_expenses),
            "monthly_debt_payments": _money(assessment.monthly_debt_payments),
            "envelope_allocations": _money(assessment.envelope_allocations),
            "committed_outflows": _money(assessment.committed_outflows),
            "savings_allocations": _money(assessment.savings_allocations),
            "surplus": _money(assessment.surplus),
            "safe_surplus": _money(assessment.safe_surplus),
            "safety_margin_pct": 80,
            "monthly_needed": _money(assessment.monthly_needed),
            "feasible": assessment.feasible,
            "gate_reason": assessment.gate_reason,
            "shortfall": _money(assessment.shortfall),
            "min_timeline_months_feasible": assessment.min_timeline_months_feasible,
            "max_amount_feasible_in_timeline": _money(
                assessment.max_amount_feasible_in_timeline
            ),
            "notes": list(assessment.notes),
        }
    )
    return out


def register_goal_tools() -> None:
    if not is_tool_registered("list_goals"):
        query_tool(name="list_goals", description=LIST_GOALS_DESCRIPTION)(list_goals)
    if not is_tool_registered("assess_goal"):
        query_tool(name="assess_goal", description=ASSESS_GOAL_DESCRIPTION)(assess_goal)


register_goal_tools()
