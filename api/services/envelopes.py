"""Envelope budgeting spend computation.

Spend is computed live from transactions — there is no stored running balance,
so the bars can never drift from the ledger. One grouped query per summary.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.envelope import Envelope
from ..models.recurring_income import RecurringIncome
from ..models.transaction import Transaction
from ..models.user import User
from ..schemas.envelopes import (
    EnvelopeClassSubtotal,
    EnvelopeSummaryItem,
    EnvelopeSummaryResponse,
)
from .fx import convert

_CLASS_ORDER = ["needs", "wants", "savings", "investing"]

# Normalize a recurring-income cadence to a monthly figure for the
# "total limits vs income" sanity line. Approximate by design.
_FREQ_TO_MONTHLY = {
    "weekly": Decimal("52") / Decimal("12"),
    "biweekly": Decimal("26") / Decimal("12"),
    "monthly": Decimal("1"),
    "annual": Decimal("1") / Decimal("12"),
}


def _user_today(user: User) -> date:
    try:
        tz = ZoneInfo(user.timezone)
    except Exception:  # pragma: no cover - defensive
        tz = ZoneInfo("UTC")
    return datetime.now(tz).date()


def _next_month_start(value: date) -> date:
    year = value.year + 1 if value.month == 12 else value.year
    month = 1 if value.month == 12 else value.month + 1
    return date(year, month, 1)


async def _monthly_income(db: AsyncSession, *, user: User) -> Decimal | None:
    """Best-effort monthly income from active recurring incomes in the user's
    currency, each normalized to a monthly figure. None if the user has none."""
    currency = user.currency or "CRC"
    rows = await db.execute(
        select(RecurringIncome.amount, RecurringIncome.frequency).where(
            RecurringIncome.user_id == user.id,
            RecurringIncome.is_active.is_(True),
            RecurringIncome.archived.is_(False),
            RecurringIncome.currency == currency,
            RecurringIncome.amount.isnot(None),
        )
    )
    total = Decimal("0")
    found = False
    for amount, frequency in rows.all():
        factor = _FREQ_TO_MONTHLY.get(frequency)
        if factor is None or amount is None:
            continue
        total += Decimal(amount) * factor
        found = True
    return total.quantize(Decimal("0.01")) if found else None


async def compute_envelope_summary(
    db: AsyncSession, *, user: User
) -> EnvelopeSummaryResponse:
    today = _user_today(user)
    start = date(today.year, today.month, 1)
    end = _next_month_start(start)
    period = f"{today.year:04d}-{today.month:02d}"
    currency = user.currency or "CRC"

    envelopes = list(
        (
            await db.execute(
                select(Envelope)
                .where(Envelope.user_id == user.id, Envelope.archived.is_(False))
                .order_by(Envelope.envelope_class.asc(), Envelope.name.asc())
            )
        ).scalars().all()
    )

    # Live spend per envelope: confirmed, non-archived, non-transfer EXPENSES
    # (amount < 0) dated in the current month. Grouped by currency too, because a
    # USD expense tagged to a CRC envelope must be converted before it counts
    # (see api/services/fx.py) — otherwise $30 would read as ₡30, not ₡15 000.
    spend_rows = await db.execute(
        select(
            Transaction.envelope_id,
            Transaction.currency,
            func.coalesce(func.sum(func.abs(Transaction.amount)), 0),
        )
        .where(
            Transaction.user_id == user.id,
            Transaction.envelope_id.isnot(None),
            Transaction.amount < 0,
            Transaction.archived.is_(False),
            Transaction.transfer_id.is_(None),
            Transaction.status == "confirmed",
            Transaction.transaction_date >= start,
            Transaction.transaction_date < end,
        )
        .group_by(Transaction.envelope_id, Transaction.currency)
    )
    # {envelope_id: [(tx_currency, summed_abs_amount), ...]}
    spent_raw: dict = {}
    for env_id, tx_currency, total in spend_rows.all():
        spent_raw.setdefault(env_id, []).append((tx_currency, Decimal(total)))

    items: list[EnvelopeSummaryItem] = []
    # Per-class subtotals + the total are reported in the summary (user)
    # currency, so each envelope's figures are converted from its own currency.
    class_acc: dict[str, dict[str, float]] = {}
    for env in envelopes:
        # Per-envelope spend is in the ENVELOPE's currency: convert each
        # transaction-currency bucket into env.currency, then sum.
        spent_dec = Decimal("0")
        for tx_currency, bucket in spent_raw.get(env.id, []):
            spent_dec += convert(bucket, tx_currency, env.currency)
        spent = float(spent_dec)
        limit = float(env.limit_amount)
        pct = (spent / limit) if limit > 0 else 0.0
        items.append(
            EnvelopeSummaryItem(
                id=env.id,
                name=env.name,
                envelope_class=env.envelope_class,
                currency=env.currency,
                limit_amount=round(limit, 2),
                spent=round(spent, 2),
                remaining=round(limit - spent, 2),
                pct=round(pct, 4),
                over_limit=spent > limit,
            )
        )
        acc = class_acc.setdefault(env.envelope_class, {"limit": 0.0, "spent": 0.0})
        acc["limit"] += float(convert(Decimal(str(limit)), env.currency, currency))
        acc["spent"] += float(convert(spent_dec, env.currency, currency))

    by_class = [
        EnvelopeClassSubtotal(
            envelope_class=cls,
            limit_total=round(acc["limit"], 2),
            spent_total=round(acc["spent"], 2),
            over_limit=acc["spent"] > acc["limit"],
        )
        for cls, acc in sorted(
            class_acc.items(),
            key=lambda kv: _CLASS_ORDER.index(kv[0]) if kv[0] in _CLASS_ORDER else 99,
        )
    ]

    income = await _monthly_income(db, user=user)
    # In the summary currency (class subtotals are already converted) so it's
    # comparable to monthly_income; summing item.limit_amount would mix CRC+USD.
    total_limit = sum(sub.limit_total for sub in by_class)
    return EnvelopeSummaryResponse(
        period=period,
        currency=currency,
        envelopes=items,
        by_class=by_class,
        total_limit=round(total_limit, 2),
        monthly_income=float(income) if income is not None else None,
    )
