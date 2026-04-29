from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import select

from api.database import AsyncSessionLocal
from api.models.debt import Debt
from api.services import amortization

from ._common import as_decimal, decimal_to_string, fuzzy_any, signed_decimal_to_string
from .base import is_tool_registered, query_tool


DebtStatusFilter = Literal["active", "paid_off", "all"]


LIST_DEBTS_DESCRIPTION = (
    "Lista las deudas del usuario (préstamos, hipotecas, tarjetas con saldo, "
    "deudas a personas) con su balance actual y pago mensual. Las deudas sin "
    "cronograma formal pueden no tener pago mensual ni cuenta de pagos. Para "
    "el detalle de una deuda específica usá get_debt_details."
)

GET_DEBT_DETAILS_DESCRIPTION = (
    "Detalle completo de una deuda específica con proyección de cancelación: "
    "fecha estimada de cancelación al ritmo actual, intereses restantes, "
    "pagos pendientes. Usá esto cuando el usuario pregunte cuándo termina "
    "de pagar algo o quiera detalle de una deuda en particular."
)


class DebtNotFound(Exception):
    """Raised when a debt name doesn't fuzzy-match any of the user's debts."""


async def list_debts(
    *,
    status: DebtStatusFilter = "active",
    user_id: uuid.UUID,
) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        rows = await _fetch_debts(db, user_id=user_id, status=status)

    debts: list[dict[str, Any]] = []
    total_balance = Decimal("0")
    total_monthly = Decimal("0")
    currency_seen: str | None = None

    for d in rows:
        payments_remaining = _payments_remaining(d)
        currency_seen = currency_seen or d.currency
        debts.append(
            {
                "debt_name": d.name,
                "debt_type": d.debt_type,
                "current_balance": signed_decimal_to_string(d.current_balance),
                "monthly_payment": (
                    decimal_to_string(d.minimum_payment)
                    if d.minimum_payment and as_decimal(d.minimum_payment) > 0
                    else None
                ),
                "interest_rate_annual": _interest_rate_pct_str(d.interest_rate),
                "payments_made": d.payments_made if d.payments_made else 0,
                "payments_remaining": payments_remaining,
                "currency": d.currency,
            }
        )
        total_balance += as_decimal(d.current_balance)
        if d.minimum_payment and as_decimal(d.minimum_payment) > 0:
            total_monthly += as_decimal(d.minimum_payment)

    return {
        "debts": debts,
        "total_count": len(debts),
        "total_current_balance": signed_decimal_to_string(total_balance),
        "total_monthly_payment": decimal_to_string(total_monthly),
        "currency": currency_seen or "CRC",
    }


async def get_debt_details(
    *,
    debt_name: str,
    user_id: uuid.UUID,
) -> dict[str, Any]:
    if not debt_name or not debt_name.strip():
        raise DebtNotFound(
            "Necesito el nombre de la deuda para buscarla. Usá list_debts para ver las opciones."
        )

    async with AsyncSessionLocal() as db:
        stmt = (
            select(Debt)
            .where(
                Debt.user_id == user_id,
                fuzzy_any(Debt.name, [debt_name]),
            )
            .order_by(Debt.is_active.desc(), Debt.created_at.desc())
        )
        debts = list((await db.execute(stmt)).scalars())

    if not debts:
        raise DebtNotFound(
            f"No encontré una deuda llamada '{debt_name}'. "
            "Usá list_debts para ver las deudas disponibles."
        )

    debt = debts[0]
    annual_rate = float(as_decimal(debt.interest_rate))
    balance = float(as_decimal(debt.current_balance))
    monthly_payment = float(as_decimal(debt.minimum_payment or 0))
    has_schedule = (
        debt.payment_due_day is not None
        and monthly_payment > 0
        and balance > 0
    )
    payoff_date = None
    total_interest_remaining = None

    if has_schedule:
        today = datetime.now(ZoneInfo("America/Costa_Rica")).date()
        schedule = amortization.generate_schedule(
            balance=balance,
            annual_rate=annual_rate,
            monthly_payment=monthly_payment,
            due_day=debt.payment_due_day or 1,
            start_date=today,
            includes_insurance=bool(debt.includes_insurance),
            insurance_monthly=float(as_decimal(debt.insurance_monthly or 0)),
        )
        if schedule.payoff_date is not None:
            payoff_date = schedule.payoff_date.isoformat()
            total_interest_remaining = decimal_to_string(
                Decimal(str(schedule.total_interest))
            )

    return {
        "debt_name": debt.name,
        "debt_type": debt.debt_type,
        "current_balance": signed_decimal_to_string(debt.current_balance),
        "original_amount": decimal_to_string(debt.original_amount),
        "monthly_payment": (
            decimal_to_string(debt.minimum_payment) if monthly_payment > 0 else None
        ),
        "interest_rate_annual": _interest_rate_pct_str(debt.interest_rate),
        "payments_made": debt.payments_made or 0,
        "payments_remaining": _payments_remaining(debt),
        "estimated_payoff_date": payoff_date,
        "total_interest_remaining": total_interest_remaining,
        "currency": debt.currency,
    }


async def _fetch_debts(db: Any, *, user_id: uuid.UUID, status: DebtStatusFilter):
    stmt = select(Debt).where(Debt.user_id == user_id).order_by(Debt.created_at.desc())
    if status == "active":
        stmt = stmt.where(Debt.is_active.is_(True))
    elif status == "paid_off":
        stmt = stmt.where(Debt.is_active.is_(False))
    return list((await db.execute(stmt)).scalars())


def _payments_remaining(debt: Debt) -> int | None:
    if debt.term_months is None:
        return None
    made = debt.payments_made or 0
    return max(0, debt.term_months - made)


def _interest_rate_pct_str(rate: Any) -> str:
    """0.085 -> '8.5'. The Debt model stores rates as decimal fractions."""
    pct = (as_decimal(rate) * Decimal("100")).quantize(Decimal("0.01"))
    if pct == pct.to_integral_value():
        return str(pct.quantize(Decimal("1")))
    return format(pct.normalize(), "f")


def register_debt_tools() -> None:
    if not is_tool_registered("list_debts"):
        query_tool(
            name="list_debts",
            description=LIST_DEBTS_DESCRIPTION,
        )(list_debts)
    if not is_tool_registered("get_debt_details"):
        query_tool(
            name="get_debt_details",
            description=GET_DEBT_DETAILS_DESCRIPTION,
        )(get_debt_details)


register_debt_tools()
