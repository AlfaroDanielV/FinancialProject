"""Pure projection of a credit card's statement cycle.

Credit cards have NO materialized statement: like debt cuotas
(`app/domain/credit/cuota_schedule.py`) and the card minimum projection
(`recurrence.get_upcoming_feed`), the cycle is derived live. A card cuts a
statement on its `statement_day` (corte); the balance owed at that corte is due
on the following `payment_due_day`. Purchases AFTER the corte accumulate toward
the NEXT statement — they are not "due now".

This module is the deterministic date + settlement logic. The corte balance and
the payments-since-corte are computed against the ledger by the caller
(`api/services/credit_cards.py`) and fed in here. The settlement threshold
follows `payment_mode`: a `full` (de contado) card settles only when the whole
corte balance is covered; a `minimum` card settles at the minimum. The amount
SHOWN in the feed is always the corte total (the caller's choice), independent
of the settlement threshold.

Pure: no DB / LLM / network. Mirrors `cuota_schedule.py`.
"""
from __future__ import annotations

import calendar
from datetime import date
from decimal import Decimal

from dateutil.relativedelta import relativedelta


def _clamp(year: int, month: int, day: int) -> date:
    """Day-of-month clamped to the month's length (a corte on the 31st falls on
    the 28th/30th in shorter months)."""
    last = calendar.monthrange(year, month)[1]
    return date(year, month, min(day, last))


def last_corte(statement_day: int, today: date) -> date:
    """The most recent statement cut (corte) on or before `today`.

    If today is on/after this month's `statement_day`, the corte is this month;
    otherwise it is last month's. Clamped to month length.
    """
    this_month = _clamp(today.year, today.month, statement_day)
    if this_month <= today:
        return this_month
    prev = date(today.year, today.month, 1) - relativedelta(months=1)
    return _clamp(prev.year, prev.month, statement_day)


def statement_due_date(corte: date, payment_due_day: int) -> date:
    """The payment due date for a statement cut on `corte`: the first
    `payment_due_day` STRICTLY after the corte (CR cards: corte 19 → due 30 same
    month; corte 19 → due 5 rolls to next month)."""
    candidate = _clamp(corte.year, corte.month, payment_due_day)
    if candidate > corte:
        return candidate
    nxt = date(corte.year, corte.month, 1) + relativedelta(months=1)
    return _clamp(nxt.year, nxt.month, payment_due_day)


def is_statement_settled(
    *,
    statement_balance: Decimal,
    paid_since_corte: Decimal,
    payment_mode: str,
    minimum: Decimal,
) -> bool:
    """Whether the closed statement's obligation is met.

    Nothing owed → settled. A `full` (de contado) card needs the whole corte
    balance covered; any other mode (`minimum`) needs the minimum. Payments and
    refunds since the corte both count toward `paid_since_corte` (both reduce
    what's owed for the cycle).
    """
    if statement_balance <= 0:
        return True
    threshold = statement_balance if payment_mode == "full" else minimum
    return paid_since_corte >= threshold
