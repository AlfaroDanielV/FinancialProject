from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from pydantic import Field
from sqlalchemy import select

from api.models.account import Account
from api.models.bill_occurrence import BillOccurrence
from api.models.recurring_bill import RecurringBill

from app.queries.session import AsyncSessionLocal

from ._common import as_decimal, decimal_to_string, user_currency
from .base import is_tool_registered, query_tool


BillStatusFilter = Literal["upcoming", "overdue", "paid_recently", "all"]
DaysWindow = Annotated[int, Field(ge=1, le=90)]


LIST_RECURRING_BILLS_DESCRIPTION = (
    "Lista los pagos recurrentes del usuario (servicios, suscripciones, "
    "alquiler, préstamos, etc.) filtrables por estado. status='upcoming' "
    "muestra los próximos N días, status='overdue' muestra los vencidos "
    "no pagados, status='paid_recently' muestra los pagados en los últimos "
    "N días."
)


async def list_recurring_bills(
    *,
    status: BillStatusFilter = "all",
    days_ahead: DaysWindow = 30,
    days_back: DaysWindow = 30,
    user_id: uuid.UUID,
) -> dict[str, Any]:
    today = datetime.now(ZoneInfo("America/Costa_Rica")).date()

    async with AsyncSessionLocal() as db:
        currency = await user_currency(db, user_id)
        stmt = (
            select(
                BillOccurrence.due_date,
                BillOccurrence.status,
                BillOccurrence.amount_expected,
                BillOccurrence.amount_paid,
                BillOccurrence.paid_at,
                RecurringBill.name.label("bill_name"),
                RecurringBill.category,
                RecurringBill.amount_expected.label("template_amount"),
                Account.name.label("account_name"),
            )
            .select_from(BillOccurrence)
            .join(
                RecurringBill,
                BillOccurrence.recurring_bill_id == RecurringBill.id,
            )
            .outerjoin(Account, RecurringBill.account_id == Account.id)
            .where(BillOccurrence.user_id == user_id)
        )

        if status == "upcoming":
            horizon = today + timedelta(days=days_ahead)
            stmt = stmt.where(
                BillOccurrence.status == "pending",
                BillOccurrence.due_date >= today,
                BillOccurrence.due_date <= horizon,
            ).order_by(BillOccurrence.due_date.asc())
        elif status == "overdue":
            stmt = stmt.where(
                BillOccurrence.status.in_(["overdue", "pending"]),
                BillOccurrence.due_date < today,
            ).order_by(BillOccurrence.due_date.asc())
        elif status == "paid_recently":
            cutoff_dt = datetime.now(ZoneInfo("America/Costa_Rica")) - timedelta(
                days=days_back
            )
            stmt = stmt.where(
                BillOccurrence.status.in_(["paid", "partially_paid"]),
                BillOccurrence.paid_at.is_not(None),
                BillOccurrence.paid_at >= cutoff_dt,
            ).order_by(BillOccurrence.paid_at.desc())
        else:  # all
            stmt = stmt.order_by(BillOccurrence.due_date.asc())

        rows = list((await db.execute(stmt)).all())

    bills: list[dict[str, Any]] = []
    total_upcoming = Decimal("0")
    for row in rows:
        amount_value = row.amount_expected
        if amount_value is None:
            amount_value = row.template_amount
        days_until_due = (row.due_date - today).days
        resolved_status = _resolve_status(row.status, row.due_date, today)

        if resolved_status == "upcoming":
            total_upcoming += as_decimal(amount_value or 0)

        bills.append(
            {
                "bill_name": row.bill_name,
                "category": row.category,
                "amount": (
                    decimal_to_string(amount_value) if amount_value is not None else None
                ),
                "currency": currency,
                "due_date": row.due_date.isoformat(),
                "status": resolved_status,
                "account_name": row.account_name,
                "days_until_due": days_until_due,
                "paid_at": row.paid_at.isoformat() if row.paid_at else None,
            }
        )

    return {
        "bills": bills,
        "total_count": len(bills),
        "total_amount_upcoming": (
            decimal_to_string(total_upcoming) if total_upcoming > 0 else None
        ),
        "currency": currency,
    }


def _resolve_status(raw_status: str, due_date: date, today: date) -> str:
    """Map DB status to LLM-facing status: upcoming|overdue|paid|paid_recently|skipped|cancelled."""
    if raw_status in ("paid", "partially_paid"):
        return raw_status
    if raw_status in ("skipped", "cancelled"):
        return raw_status
    if raw_status == "overdue":
        return "overdue"
    # raw_status == "pending"
    if due_date < today:
        return "overdue"
    return "upcoming"


def register_recurring_bill_tools() -> None:
    if not is_tool_registered("list_recurring_bills"):
        query_tool(
            name="list_recurring_bills",
            description=LIST_RECURRING_BILLS_DESCRIPTION,
        )(list_recurring_bills)


register_recurring_bill_tools()
