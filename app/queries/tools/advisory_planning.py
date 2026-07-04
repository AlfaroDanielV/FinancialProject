"""P10 B5 — advisory planning tools: `assess_counterfactual` + `build_multiyear_plan`.

Pure COMPOSITION over the existing deterministic engines — no new math:

- `assess_counterfactual` re-frames the affordability engine's already-computed
  alternatives (`shortfall`, `min_timeline_months_feasible`,
  `max_amount_feasible_in_timeline`) as concrete levers, plus the top envelope
  reallocation candidates. Answers Q4 («si no me alcanza, ¿qué necesitaría?»).
- `build_multiyear_plan` composes the affordability engine at a long horizon
  (saving the prima) with the financing simulator (the mortgage cuota after
  buying). Answers Q1 (house). **LINEAR v1 — no inflation, no compounding, no
  salary growth** (stated in the payload; the narrator must state it too).

Advisory-only tools: registered globally, shipped solely via ADVISORY_TOOLSET.
"""
from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Annotated, Any, Optional

from pydantic import Field

from api.models.user import User
from api.services.advice_trace import record_advice_event, verdict_from
from api.services.finance.affordability import assess_for_user, gate_guidance

from ..session import AsyncSessionLocal
from .base import is_tool_registered, query_tool
from .financing import assess_financing
from .reallocation import suggest_reallocation_candidates

Amount = Annotated[float, Field(gt=0)]

ASSESS_COUNTERFACTUAL_DESCRIPTION = (
    "Para una compra/meta que NO alcanza (o para explorar opciones): devuelve "
    "las palancas deterministas concretas — esperar más tiempo "
    "(min_timeline_months_feasible), una meta más pequeña "
    "(max_amount_feasible_in_timeline), cuánto sobrante mensual falta "
    "(monthly_shortfall) y de qué sobres se podría reasignar presupuesto "
    "(reallocation_candidates). Todos los números vienen del motor de "
    "affordability — narrálos tal cual, nunca inventés una palanca numérica. "
    "Si viene gate_reason, pedí la acción correctiva en vez de afirmar un "
    "veredicto."
)

BUILD_MULTIYEAR_PLAN_DESCRIPTION = (
    "Plan plurianual LINEAL para una compra grande (típicamente casa): fase 1 "
    "= ahorrar la prima en el plazo dado (evaluada contra el sobrante real "
    "mensual); fase 2 = la cuota del financiamiento del resto (French, "
    "determinista) y si cabe en el sobrante. Sin inflación ni crecimiento "
    "salarial (v1 — decilo SIEMPRE al narrar). Si no das annual_rate_pct la "
    "fase 2 no se calcula: pedile la tasa al usuario, nunca la inventés. "
    "target_amount es el precio total; down_payment_pct el % de prima "
    "(convención CR común: veinte por ciento si el usuario no dice otra cosa "
    "— confirmalo con él)."
)


def _money(value: Optional[Decimal]) -> Optional[str]:
    if value is None:
        return None
    return f"{value:.2f}"


async def assess_counterfactual(
    *,
    amount: Amount,
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

    # The reallocation candidates tool opens its own session.
    candidates = await suggest_reallocation_candidates(user_id=user_id)

    payload: dict[str, Any] = {
        "currency": result.currency,
        "desired_amount": _money(result.desired_amount),
        "timeline_months": result.timeline_months,
        "feasible": result.feasible,
        "gate_reason": result.gate_reason,
        "monthly_needed": _money(result.monthly_needed),
        "safe_surplus": _money(result.safe_surplus),
        "levers": {
            "wait_longer_months": result.min_timeline_months_feasible,
            "buy_cheaper_max_amount": _money(result.max_amount_feasible_in_timeline),
            "monthly_shortfall": _money(result.shortfall),
            "reallocation_candidates": candidates.get("candidates", [])[:3],
        },
        "notes": list(result.notes),
    }
    if result.gate_reason:
        payload["gate_guidance"] = gate_guidance(result.gate_reason)
    await record_advice_event(
        user_id=user_id,
        kind="counterfactual",
        verdict=verdict_from(feasible=result.feasible, gate_reason=result.gate_reason),
        inputs={"amount": amount, "timeline_months": timeline_months},
        result=payload,
        surface="query_tool",
    )
    return payload


async def build_multiyear_plan(
    *,
    target_amount: Amount,
    timeline_years: Annotated[int, Field(ge=1, le=40)],
    down_payment_pct: Annotated[float, Field(gt=0, le=100)] = 20.0,
    annual_rate_pct: Optional[Annotated[float, Field(gt=0, le=100)]] = None,
    term_months: Annotated[int, Field(ge=12, le=480)] = 360,
    user_id: uuid.UUID,
) -> dict[str, Any]:
    target = Decimal(str(target_amount))
    prima = (target * Decimal(str(down_payment_pct)) / Decimal("100")).quantize(
        Decimal("0.01")
    )
    months = timeline_years * 12

    async with AsyncSessionLocal() as db:
        user = await db.get(User, user_id)
        if user is None:
            return {"error": "user_not_found"}
        savings_phase = await assess_for_user(
            db, user=user, desired_amount=prima, timeline_months=months
        )

    payload: dict[str, Any] = {
        "currency": savings_phase.currency,
        "target_amount": _money(target),
        "timeline_years": timeline_years,
        "down_payment_pct": down_payment_pct,
        "linear_no_inflation": True,
        "plan_note": (
            "Plan LINEAL: sin inflación, sin crecimiento salarial, sin "
            "rendimiento del ahorro. Es un orden de magnitud honesto, no una "
            "proyección financiera."
        ),
        "savings_phase": {
            "down_payment_needed": _money(prima),
            "monthly_needed": _money(savings_phase.monthly_needed),
            "feasible": savings_phase.feasible,
            "gate_reason": savings_phase.gate_reason,
            "safe_surplus": _money(savings_phase.safe_surplus),
            "shortfall": _money(savings_phase.shortfall),
            "min_timeline_months_feasible": savings_phase.min_timeline_months_feasible,
            "notes": list(savings_phase.notes),
        },
    }
    if savings_phase.gate_reason:
        payload["savings_phase"]["gate_guidance"] = gate_guidance(
            savings_phase.gate_reason
        )

    if annual_rate_pct is not None:
        # The financing simulator opens its own session and never registers
        # the debt — pure simulation (cuota + interés total + si cabe).
        payload["financing_phase"] = await assess_financing(
            price=float(target),
            annual_rate_pct=annual_rate_pct,
            term_months=term_months,
            down_payment=float(prima),
            user_id=user_id,
        )
    else:
        payload["financing_phase"] = None
        payload["financing_note"] = (
            "Falta la tasa anual del crédito para calcular la cuota — "
            "preguntásela al usuario (o que la confirme con su banco). "
            "Nunca supongás una tasa."
        )

    await record_advice_event(
        user_id=user_id,
        kind="multiyear_plan",
        verdict=verdict_from(
            feasible=savings_phase.feasible, gate_reason=savings_phase.gate_reason
        ),
        inputs={
            "target_amount": target_amount,
            "timeline_years": timeline_years,
            "down_payment_pct": down_payment_pct,
            "annual_rate_pct": annual_rate_pct,
            "term_months": term_months,
        },
        result=payload,
        surface="query_tool",
    )
    return payload


def register_advisory_planning_tools() -> None:
    if not is_tool_registered("assess_counterfactual"):
        query_tool(
            name="assess_counterfactual",
            description=ASSESS_COUNTERFACTUAL_DESCRIPTION,
        )(assess_counterfactual)
    if not is_tool_registered("build_multiyear_plan"):
        query_tool(
            name="build_multiyear_plan",
            description=BUILD_MULTIYEAR_PLAN_DESCRIPTION,
        )(build_multiyear_plan)


register_advisory_planning_tools()
