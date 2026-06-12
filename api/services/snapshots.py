"""Phase 7e — month-end envelope + cashflow snapshots.

The live figures (envelope spend/limits, MonthlyCashflow surplus/gates) are
recomputed on every read and silently lose history: edit a limit and "did I
respect my budget in March" becomes unanswerable. This service freezes them
per period.

Capture contract (idempotent — safe on any schedule):

- ``capture_user_snapshots`` upserts the CURRENT period as-of-now (envelope
  rows keyed on the partial UNIQUE ``(envelope_id, period)``; the cashflow
  row on ``UNIQUE(user_id, period)``).
- During the first ``RECAPTURE_GRACE_DAYS`` days of a month it ALSO
  recaptures the just-closed period with ``today=last day of that month`` so
  the frozen row covers the month's full transaction window (the nightly job
  runs at ~3:30am CR — without this, the final capture would miss the last
  day's spend). Envelope limits/reservations are read from current rows; a
  limit edited within the grace window bleeds into the closed month's
  snapshot — accepted, the alternative is a full limit-history table.
- Cashflow recapture uses the same envelope summary window; the
  affordability inputs (income/debt/bills) are structural and read current
  rows by design.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.snapshot import CashflowSnapshot, EnvelopeSnapshot
from api.models.user import User
from api.schemas.envelopes import EnvelopeSummaryResponse
from api.services.envelopes import _user_today, compute_envelope_summary
from api.services.finance.cashflow import compute_monthly_cashflow

log = logging.getLogger("api.services.snapshots")

RECAPTURE_GRACE_DAYS = 3


def _period_of(day: date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


def _last_day_of_prev_month(day: date) -> date:
    return date(day.year, day.month, 1) - timedelta(days=1)


async def _upsert_envelope_rows(
    db: AsyncSession, *, user: User, summary: EnvelopeSummaryResponse
) -> int:
    count = 0
    for item in summary.envelopes:
        values = dict(
            user_id=user.id,
            period=summary.period,
            envelope_id=item.id,
            name=item.name,
            envelope_class=item.envelope_class,
            currency=item.currency,
            parent_id=item.parent_id,
            depth=item.depth,
            limit_amount=Decimal(str(item.limit_amount)),
            spent=Decimal(str(item.spent)),
            direct_spent=Decimal(str(item.direct_spent)),
            reserved=Decimal(str(item.reserved)),
            available=Decimal(str(item.available)),
            over_limit=item.over_limit,
        )
        stmt = pg_insert(EnvelopeSnapshot).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=["envelope_id", "period"],
            index_where=EnvelopeSnapshot.envelope_id.isnot(None),
            set_={
                k: getattr(stmt.excluded, k)
                for k in (
                    "name",
                    "envelope_class",
                    "currency",
                    "parent_id",
                    "depth",
                    "limit_amount",
                    "spent",
                    "direct_spent",
                    "reserved",
                    "available",
                    "over_limit",
                )
            }
            | {"captured_at": stmt.excluded.captured_at},
        )
        await db.execute(stmt)
        count += 1
    return count


async def _upsert_cashflow_row(
    db: AsyncSession, *, user: User, summary: EnvelopeSummaryResponse
) -> None:
    cashflow = await compute_monthly_cashflow(db, user=user)
    payload = {
        "by_class": [
            {
                "envelope_class": sub.envelope_class,
                "limit_total": str(sub.limit_total),
                "spent_total": str(sub.spent_total),
                "over_limit": sub.over_limit,
            }
            for sub in summary.by_class
        ],
        "unattached_obligations": [
            {"name": o.name, "amount": str(o.amount), "source": o.source}
            for o in cashflow.unattached_obligations
        ],
        "excluded_variable_bills": cashflow.excluded_variable_bills,
        "excluded_custom_bills": cashflow.excluded_custom_bills,
        "income_known": cashflow.income_known,
        "has_budget": cashflow.has_budget,
        "monthly_income_snapshot_note": (
            "income/debt/bills are structural (current rows); spend figures "
            "use the period window"
        ),
    }
    values = dict(
        user_id=user.id,
        period=summary.period,
        currency=cashflow.currency,
        monthly_income=(
            cashflow.monthly_income if cashflow.income_known else None
        ),
        debt_payments=cashflow.debt_payments,
        recurring_bills=cashflow.recurring_bills,
        envelope_allocations=cashflow.envelope_allocations,
        committed_outflows=cashflow.committed_outflows,
        surplus=cashflow.surplus,
        savings_allocations=cashflow.savings_allocations,
        gate_reason=cashflow.gate_reason,
        total_limit=Decimal(str(summary.total_limit)),
        total_spent=Decimal(str(summary.total_spent)),
        total_reserved=Decimal(str(summary.total_reserved)),
        total_available=Decimal(str(summary.total_available)),
        payload=payload,
    )
    stmt = pg_insert(CashflowSnapshot).values(**values)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_cashflow_snapshots_user_period",
        set_={
            k: getattr(stmt.excluded, k)
            for k in values
            if k not in ("user_id", "period")
        }
        | {"captured_at": stmt.excluded.captured_at},
    )
    await db.execute(stmt)


async def capture_user_snapshots(
    db: AsyncSession, *, user: User, today: Optional[date] = None
) -> dict[str, int | str]:
    """Upsert the current period's snapshots (and, early in a month, the
    just-closed one). Caller commits. Idempotent."""
    effective_today = today or _user_today(user)
    captured: dict[str, int | str] = {"period": _period_of(effective_today)}

    summary = await compute_envelope_summary(db, user=user, today=effective_today)
    captured["envelope_rows"] = await _upsert_envelope_rows(
        db, user=user, summary=summary
    )
    await _upsert_cashflow_row(db, user=user, summary=summary)
    captured["cashflow_rows"] = 1

    if effective_today.day <= RECAPTURE_GRACE_DAYS:
        prev_day = _last_day_of_prev_month(effective_today)
        prev_summary = await compute_envelope_summary(
            db, user=user, today=prev_day
        )
        captured["prev_period"] = prev_summary.period
        captured["prev_envelope_rows"] = await _upsert_envelope_rows(
            db, user=user, summary=prev_summary
        )
        # The cashflow recapture is envelope-window-correct for the spend
        # totals; structural inputs remain current-rows by design (see module
        # docstring) — so only the envelope grand totals shift.
        await _upsert_cashflow_row(db, user=user, summary=prev_summary)

    return captured
