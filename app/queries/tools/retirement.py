"""P10 B6 — `project_retirement`: CR pension projection, three SUPEN bands.

Thin wrapper over the pure rules layer `app/domain/retirement` (IVM defined
benefit + ROP defined contribution under the operadora's pesimista/esperado/
optimista scenarios). The LLM only routes and narrates — it never computes a
pension figure.

NARRATION GUARDRAILS (doc §6.1, enforced by prompt + this description):
- ALWAYS the three bands — never a single confident number; NEVER drop the
  pesimista band.
- Reproduce the SUPEN disclaimer (carried on the payload).
- Name the constants vintage («con supuestos de enero 2026 de Popular
  Pensiones») so a stale projection never reads as current.

Input sourcing («fetch registered data; never ask for a value on file»):
- salario_bruto_mensual: falls back to the registered CRC salary GROSS
  (`recurring_incomes.gross_monthly`, Phase 7g). The NET monthly income is
  never silently used as gross — if no gross is on file, the tool asks.
- edad_actual / cuotas_ivm / saldo_rop are NOT on file (no birthdate, no ROP
  account concept) — the LLM must ask the user for them.
- salario_referencia (SPR = mejores 300 salarios) is not reconstructable;
  defaults to the current gross AS AN APPROXIMATION, flagged in the payload.

⛳ B6 gate: the encoded constants are a sourced draft — operator-validated
against the primary Reglamento IVM + the OPC methodology manual before this
tool is user-visible (the advisory feature flag holds it dark meanwhile).
"""
from __future__ import annotations

import uuid
from dataclasses import asdict
from typing import Annotated, Any, Optional

from pydantic import Field
from sqlalchemy import select

from api.models.recurring_income import RecurringIncome
from api.models.user import User
from api.services.advice_trace import record_advice_event
from app.domain.retirement import (
    UnconfiguredYearError,
    UnknownOperatorError,
    project_retirement as engine_project_retirement,
)

from ..session import AsyncSessionLocal
from .base import is_tool_registered, query_tool

PROJECT_RETIREMENT_DESCRIPTION = (
    "Proyección determinista de pensión CR a los 65: IVM (CCSS, beneficio "
    "definido) + ROP (tres escenarios SUPEN: pesimista, esperado, optimista). "
    "Narrá SIEMPRE las tres bandas con su incertidumbre — nunca una sola "
    "cifra, nunca omitás la banda pesimista — e incluí el descargo (campo "
    "disclaimer) y la vigencia de los supuestos (campo supuestos). Requiere: "
    "edad_actual y cuotas_ivm (meses cotizados; preguntáselos al usuario) y "
    "saldo_rop (de su estado de cuenta ROP; 0 si no lo sabe, decilo). El "
    "salario bruto se toma del ingreso registrado si existe; si falta, "
    "preguntá el bruto mensual (NUNCA usés el neto como bruto). Si el usuario "
    "no sabe algo, la proyección igual sale con lo que haya, con sus "
    "aproximaciones marcadas."
)


async def _registered_gross_monthly(db, user_id: uuid.UUID) -> Optional[int]:
    """Σ gross_monthly of active CRC salaries — the Phase 7g on-file gross.

    Returns None when no row carries a gross (the NET amount is never a
    substitute — CCSS/ROP math is gross-based)."""
    rows = (
        await db.execute(
            select(RecurringIncome.gross_monthly).where(
                RecurringIncome.user_id == user_id,
                RecurringIncome.is_active.is_(True),
                RecurringIncome.archived.is_(False),
                RecurringIncome.income_type == "salary",
                RecurringIncome.currency == "CRC",
                RecurringIncome.gross_monthly.is_not(None),
            )
        )
    ).scalars().all()
    if not rows:
        return None
    return int(sum(rows))


async def project_retirement(
    *,
    edad_actual: Annotated[int, Field(ge=18, le=64)],
    cuotas_ivm: Annotated[int, Field(ge=0, le=720)],
    saldo_rop: Annotated[float, Field(ge=0)] = 0,
    salario_bruto_mensual: Optional[Annotated[float, Field(gt=0)]] = None,
    salario_referencia: Optional[Annotated[float, Field(gt=0)]] = None,
    sexo: Optional[str] = None,
    meses_postergacion: Annotated[int, Field(ge=0, le=120)] = 0,
    user_id: uuid.UUID,
) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        user = await db.get(User, user_id)
        if user is None:
            return {"error": "user_not_found"}
        gross = (
            int(salario_bruto_mensual)
            if salario_bruto_mensual is not None
            else await _registered_gross_monthly(db, user_id)
        )

    if gross is None:
        return {
            "needs_input": "salario_bruto_mensual",
            "note": (
                "No hay salario BRUTO registrado (solo neto, o ninguno). "
                "Preguntale al usuario su salario bruto mensual en colones — "
                "el aporte al ROP y el IVM se calculan sobre el bruto, nunca "
                "sobre el neto."
            ),
        }

    spr_es_aproximacion = salario_referencia is None
    spr = int(salario_referencia) if salario_referencia is not None else gross
    sexo_norm = sexo.strip().upper() if sexo else None
    if sexo_norm not in ("M", "F"):
        sexo_norm = None

    try:
        proj = engine_project_retirement(
            salario_referencia=spr,
            cuotas=cuotas_ivm,
            saldo_rop=int(saldo_rop),
            salario_mensual=gross,
            edad_actual=edad_actual,
            sexo=sexo_norm,  # type: ignore[arg-type]
            meses_postergacion=meses_postergacion,
        )
    except (UnconfiguredYearError, UnknownOperatorError) as e:
        return {
            "error": "constants_not_configured",
            "detail": str(e),
            "note": (
                "Los supuestos para este año/operadora no están configurados "
                "— decile al usuario que la proyección no está disponible "
                "todavía, sin inventar cifras."
            ),
        }
    except ValueError as e:
        return {"error": "invalid_input", "detail": str(e)}

    payload: dict[str, Any] = asdict(proj)
    payload["spr_es_aproximacion"] = spr_es_aproximacion
    if spr_es_aproximacion:
        payload["spr_nota"] = (
            "El salario de referencia (promedio de los mejores 300 salarios) "
            "se aproximó con el bruto actual — la cifra real de la CCSS puede "
            "diferir. Decilo al narrar."
        )
    payload["narration_rules"] = (
        "Presentá las TRES bandas (pesimista, esperado, optimista) con su "
        "incertidumbre; nunca una sola cifra; incluí el disclaimer y la "
        "vigencia de los supuestos."
    )

    await record_advice_event(
        user_id=user_id,
        kind="retirement_projection",
        verdict="info",
        inputs={
            "edad_actual": edad_actual,
            "cuotas_ivm": cuotas_ivm,
            "saldo_rop": saldo_rop,
            "salario_bruto_mensual": gross,
            "salario_referencia": spr,
            "spr_es_aproximacion": spr_es_aproximacion,
            "sexo": sexo_norm,
            "meses_postergacion": meses_postergacion,
        },
        result={
            "bandas": payload.get("bandas"),
            "supuestos": payload.get("supuestos"),
        },
        surface="query_tool",
    )
    return payload


def register_retirement_tools() -> None:
    if not is_tool_registered("project_retirement"):
        query_tool(
            name="project_retirement",
            description=PROJECT_RETIREMENT_DESCRIPTION,
        )(project_retirement)


register_retirement_tools()
