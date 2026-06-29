"""Pure projection of a debt's next unpaid cuota date.

Debts have NO materialized schedule — cuotas project live (like
`recurrence.get_upcoming_feed`). The next due advances one monthly cadence step
per recorded payment: anchored on the loan's first cuota (its `start_date`, else
the registration date), `schedule_due = first_cuota + payments_made months`. So
recording a payment marches the projection forward one month — instead of being
stuck on `payment_due_day` relative to today, which ignored payments entirely.

We return `max(schedule_due, natural_due)` (natural_due = the next
`payment_due_day` on/after today): a paid-AHEAD loan advances (schedule wins),
while an on-time / BEHIND loan keeps showing the next upcoming due (natural wins,
no regression — we never surface a months-old overdue cuota just because
`payments_made` lags the calendar).

Pure: no DB / LLM / network.
"""
from __future__ import annotations

import calendar
from datetime import date

from dateutil.relativedelta import relativedelta


def _clamp(year: int, month: int, day: int) -> date:
    """Day-of-month clamped to the month's length (a cuota on the 31st falls on
    the 28th/30th in shorter months)."""
    last = calendar.monthrange(year, month)[1]
    return date(year, month, min(day, last))


def first_cuota_on_or_after(anchor: date, payment_due_day: int) -> date:
    """First `payment_due_day` occurrence on or after `anchor`."""
    candidate = _clamp(anchor.year, anchor.month, payment_due_day)
    if candidate >= anchor:
        return candidate
    nxt = date(anchor.year, anchor.month, 1) + relativedelta(months=1)
    return _clamp(nxt.year, nxt.month, payment_due_day)


def next_cuota_date(
    *, payment_due_day: int, payments_made: int, anchor: date, today: date
) -> date:
    """The debt's next unpaid cuota date (see module docstring)."""
    first = first_cuota_on_or_after(anchor, payment_due_day)
    base = date(first.year, first.month, 1) + relativedelta(
        months=max(payments_made or 0, 0)
    )
    schedule_due = _clamp(base.year, base.month, payment_due_day)
    natural_due = first_cuota_on_or_after(today, payment_due_day)
    return max(schedule_due, natural_due)
