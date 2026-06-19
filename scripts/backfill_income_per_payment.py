"""Opt-in one-time backfill: convert pre-existing salary income amounts to the
per-payment convention introduced with the frequency-division change.

Before the change, a CRC salary stored the MONTHLY net in `recurring_incomes.
amount` with whatever cadence the user picked, so a quincenal salary was
double-counted by the monthly normalizer. The new convention stores the
PER-PAYMENT amount (monthly / payments-per-month). This script divides each
active **salary** row with a non-monthly cadence by its cadence factor.

It is deliberately NOT part of the Alembic migration — run it consciously,
once, only if you have salary rows created before the change. Idempotency is
NOT guaranteed (running twice divides twice), so run it exactly once.

Usage:
    DRY RUN (default):  python -m scripts.backfill_income_per_payment
    APPLY:              python -m scripts.backfill_income_per_payment --apply
"""
from __future__ import annotations

import asyncio
import sys
from decimal import Decimal

from sqlalchemy import select

from api.database import AsyncSessionLocal
from api.models.recurring_income import RecurringIncome
from api.services.income_frequency import PAYMENTS_PER_MONTH


async def main(apply: bool) -> None:
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(RecurringIncome).where(
                    RecurringIncome.income_type == "salary",
                    RecurringIncome.frequency != "monthly",
                    RecurringIncome.archived.is_(False),
                    RecurringIncome.amount.isnot(None),
                )
            )
        ).scalars().all()

        if not rows:
            print("No non-monthly salary rows to backfill.")
            return

        for r in rows:
            factor = PAYMENTS_PER_MONTH.get(r.frequency, Decimal("1"))
            old = Decimal(r.amount)
            new = (old / factor).quantize(Decimal("0.01"))
            print(
                f"  {r.name!r} ({r.frequency}): {old} -> {new}"
                + ("" if apply else "   [dry-run]")
            )
            if apply:
                r.amount = new

        if apply:
            await db.commit()
            print(f"Applied to {len(rows)} row(s).")
        else:
            print(
                f"\n{len(rows)} row(s) would change. Re-run with --apply to commit."
            )


if __name__ == "__main__":
    asyncio.run(main(apply="--apply" in sys.argv))
