"""Read-only query tool — registered recurring income.

Answers "¿cuánto gano al mes?" / "¿tengo registrado mi salario?" from the rows
the user already stored, so the dispatcher never has to ASK the user for a
salary it already has on file. The monthly total reuses the single normalizer
``api/services/envelopes.py::_monthly_income`` (same-currency only; quincenal =
2 payments/month; aguinaldo + salario escolar excluded as annual lump cycles),
so this answer can never drift from the affordability / cashflow math. The LLM
narrates the numbers verbatim — it must NOT invent or recompute income.
"""
from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import select

from api.models.recurring_income import RecurringIncome
from api.models.user import User
from api.services.envelopes import _FREQ_TO_MONTHLY, _monthly_income

from app.queries.session import AsyncSessionLocal

from .base import is_tool_registered, query_tool


LIST_REGISTERED_INCOME_DESCRIPTION = (
    "Devuelve los ingresos recurrentes que el usuario YA tiene registrados y su "
    "ingreso MENSUAL total en su moneda. Usá esto cuando pregunte «¿cuánto gano "
    "al mes?», «¿cuál es mi ingreso?», «¿tengo registrado mi salario?» — y "
    "SIEMPRE que necesités su ingreso para responder otra cosa (p. ej. si le "
    "alcanza para sus deudas): NUNCA le preguntés al usuario cuánto gana si ya "
    "tiene ingresos registrados; consultá esta herramienta primero. monthly_income "
    "ya viene normalizado (quincenal = 2 pagos/mes; aguinaldo y salario escolar "
    "EXCLUIDOS por ser montos anuales, no flujo mensual). La lista trae cada "
    "ingreso con su monto por pago, frecuencia y moneda; los ingresos en OTRA "
    "moneda se listan pero NO se suman al total (counted_in_monthly=false). Si "
    "monthly_income es null, el usuario no tiene ingresos registrados en su "
    "moneda. Narrá los números tal cual — no los inventés ni recalculés."
)


def _money(value: Optional[Decimal]) -> Optional[str]:
    return f"{value:.2f}" if value is not None else None


async def list_registered_income(*, user_id: uuid.UUID) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        user = await db.get(User, user_id)
        if user is None:
            return {"error": "user_not_found"}
        currency = user.currency or "CRC"
        # Same-currency monthly total — single source, can't drift from cashflow.
        monthly_income = await _monthly_income(db, user=user)
        rows = await db.execute(
            select(RecurringIncome)
            .where(
                RecurringIncome.user_id == user.id,
                RecurringIncome.is_active.is_(True),
                RecurringIncome.archived.is_(False),
            )
            .order_by(RecurringIncome.income_type.asc(), RecurringIncome.name.asc())
        )
        incomes: list[dict[str, Any]] = []
        for inc in rows.scalars().all():
            factor = _FREQ_TO_MONTHLY.get(inc.frequency)
            is_cycle = inc.income_type in ("aguinaldo", "salario_escolar")
            counted = (
                inc.currency == currency
                and not is_cycle
                and inc.amount is not None
                and factor is not None
            )
            monthly_equiv = (
                (Decimal(inc.amount) * factor).quantize(Decimal("0.01"))
                if (counted and inc.amount is not None and factor is not None)
                else None
            )
            incomes.append(
                {
                    "name": inc.name,
                    "income_type": inc.income_type,
                    "amount_per_payment": _money(inc.amount),
                    "frequency": inc.frequency,
                    "currency": inc.currency,
                    "next_payment_date": inc.next_payment_date.isoformat()
                    if inc.next_payment_date
                    else None,
                    "monthly_equivalent": _money(monthly_equiv),
                    "counted_in_monthly": counted,
                }
            )

    return {
        "currency": currency,
        "monthly_income": _money(monthly_income),
        "incomes": incomes,
        "note": (
            "monthly_income suma solo ingresos en la moneda del usuario; "
            "aguinaldo y salario escolar quedan excluidos (montos anuales)."
        ),
    }


def register_income_tools() -> None:
    if not is_tool_registered("list_registered_income"):
        query_tool(
            name="list_registered_income",
            description=LIST_REGISTERED_INCOME_DESCRIPTION,
        )(list_registered_income)


register_income_tools()
