"""Read-only query tool — Costa Rican net-salary calculator (rules layer).

Lets the chat answer "¿cuál sería mi salario neto con ₡1.5M?" by routing to the
deterministic ``app.domain.payroll.compute_net_salary``. The tool returns the
full structured breakdown verbatim (``dataclasses.asdict``); the LLM only
narrates it — it must NEVER recompute or invent a figure. This preserves "LLM
extracts / narrates; rules decide": no tax math happens in the model.
"""
from __future__ import annotations

import uuid
from dataclasses import asdict
from typing import Any, Optional

from app.domain.payroll import (
    UnconfiguredYearError,
    compute_net_salary,
)

from .base import is_tool_registered, query_tool


COMPUTE_NET_SALARY_DESCRIPTION = (
    "Calcula de forma determinista el salario NETO mensual en Costa Rica a "
    "partir del salario BRUTO, restando la CCSS obrera (10,83%) y el impuesto "
    "sobre la renta al trabajo (tramos marginales). Usá esto cuando el usuario "
    "pregunte «¿cuánto me queda neto con ₡X?», «¿cuál sería mi salario después "
    "de impuestos?», «de ₡X bruto, ¿cuánto recibo?». Pasá gross_monthly (bruto "
    "mensual en colones, entero > 0). Opcionales: hijos y conyuge (créditos "
    "fiscales que bajan el impuesto), solidarista_pct (aporte a la asociación "
    "solidarista como % del bruto, si lo menciona), year (por defecto el año en "
    "curso) e isr_base_mode (dejalo en 'gross' salvo que pida el cálculo neteando "
    "la CCSS primero, modo 'gross_minus_ccss'). La herramienta devuelve el "
    "desglose: ccss (sem/ivm/banco_popular/total), isr (base, impuesto y detalle "
    "por tramo), solidarista, net_monthly y effective_rate. Narrá esos números "
    "tal cual — NUNCA recalculés ni inventés montos. Si devuelve «error» (p.ej. "
    "año sin tabla configurada), explicá ese mensaje."
)


async def compute_net_salary_tool(
    *,
    gross_monthly: int,
    hijos: int = 0,
    conyuge: bool = False,
    solidarista_pct: float = 0.0,
    isr_base_mode: str = "gross",
    year: Optional[int] = None,
    user_id: uuid.UUID,
) -> dict[str, Any]:
    # Pure computation — no DB, no network. user_id is accepted (the tool
    # contract injects it) but unused: the calc depends only on the inputs.
    try:
        result = compute_net_salary(
            gross_monthly=int(gross_monthly),
            year=year,
            hijos=int(hijos),
            conyuge=bool(conyuge),
            isr_base_mode=isr_base_mode,  # type: ignore[arg-type]
            solidarista_pct=solidarista_pct,
        )
    except UnconfiguredYearError as exc:
        return {"error": str(exc)}
    except ValueError as exc:
        return {"error": str(exc)}
    return asdict(result)


def register_salary_tools() -> None:
    if not is_tool_registered("compute_net_salary"):
        query_tool(
            name="compute_net_salary",
            description=COMPUTE_NET_SALARY_DESCRIPTION,
        )(compute_net_salary_tool)


register_salary_tools()
