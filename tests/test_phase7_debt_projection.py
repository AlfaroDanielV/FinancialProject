"""B5 — debts surface as fixed expenses by PROJECTION (no materialized rows).

Registering a debt makes its cuota appear in the upcoming feed (derived from
payment_due_day + minimum_payment) with NO RecurringBill row; a paid-off /
archived debt simply stops projecting (zero cleanup). debt_payments (the
affordability total) and recurring_bills stay disjoint — no obligation in both.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from api.models.debt import Debt
from api.models.notification_event import NotificationEvent
from api.models.notification_rule import NotificationRule
from api.models.recurring_bill import RecurringBill
from api.services.recurrence import (
    _monthly_due_dates,
    compute_pending_notifications,
    get_upcoming_feed,
)


def _debt(user_id, *, name="Préstamo", payment="100000", due_day=15, active=True, archived=False):
    return Debt(
        user_id=user_id, name=name, debt_type="personal_loan",
        original_amount=Decimal("3000000"), current_balance=Decimal("2000000"),
        interest_rate=Decimal("0.18"), minimum_payment=Decimal(payment),
        payment_due_day=due_day, is_active=active, archived=archived,
    )


def _window():
    today = date.today()
    return today.replace(day=1), today + timedelta(days=60)


def test_monthly_due_dates_clamp_to_month_end():
    # A cuota due on the 31st falls on Feb 28 in a non-leap year.
    assert _monthly_due_dates(31, date(2026, 2, 1), date(2026, 2, 28)) == [date(2026, 2, 28)]
    # Multiple months in a wider window.
    assert _monthly_due_dates(15, date(2026, 3, 1), date(2026, 5, 31)) == [
        date(2026, 3, 15), date(2026, 4, 15), date(2026, 5, 15),
    ]


@pytest.mark.asyncio
async def test_registered_debt_projects_without_a_bill_row(db_with_user):
    session, user_id = db_with_user
    session.add(_debt(user_id, name="Préstamo BAC", due_day=15))
    await session.commit()

    from_date, to_date = _window()
    feed = await get_upcoming_feed(session, user_id, from_date=from_date, to_date=to_date)
    debts = [e for e in feed if e.item_type == "debt"]
    assert len(debts) == 1
    assert debts[0].title == "Préstamo BAC"
    assert debts[0].amount == 100000.0
    assert debts[0].debt_id is not None
    # No RecurringBill was materialized for the debt.
    bill_count = (
        await session.execute(
            select(func.count()).select_from(RecurringBill).where(
                RecurringBill.user_id == user_id
            )
        )
    ).scalar_one()
    assert bill_count == 0


@pytest.mark.asyncio
async def test_archived_debt_does_not_project(db_with_user):
    session, user_id = db_with_user
    session.add(_debt(user_id, active=False, archived=True))
    await session.commit()

    from_date, to_date = _window()
    feed = await get_upcoming_feed(session, user_id, from_date=from_date, to_date=to_date)
    assert [e for e in feed if e.item_type == "debt"] == []


@pytest.mark.asyncio
async def test_debt_generates_projected_notifications_idempotently(db_with_user):
    session, user_id = db_with_user
    # The global_default rule (registration seeds this in prod) — 4 advance tiers.
    session.add(
        NotificationRule(
            user_id=user_id, scope="global_default",
            advance_days=[7, 3, 1, 0], is_active=True,
        )
    )
    session.add(_debt(user_id, due_day=15))
    await session.commit()

    created = await compute_pending_notifications(session, user_id)
    await session.commit()
    assert created == 4  # one per advance tier
    rows = (
        await session.execute(
            select(NotificationEvent).where(
                NotificationEvent.user_id == user_id,
                NotificationEvent.debt_id.isnot(None),
            )
        )
    ).scalars().all()
    assert len(rows) == 4
    assert all(r.payload_snapshot["kind"] == "debt" for r in rows)
    assert all(r.bill_occurrence_id is None and r.custom_event_id is None for r in rows)

    # Idempotent: re-running creates nothing (same debt_id + advance + trigger).
    again = await compute_pending_notifications(session, user_id)
    await session.commit()
    assert again == 0


@pytest.mark.asyncio
async def test_archived_debt_generates_no_notifications(db_with_user):
    session, user_id = db_with_user
    session.add(
        NotificationRule(
            user_id=user_id, scope="global_default",
            advance_days=[7, 3, 1, 0], is_active=True,
        )
    )
    session.add(_debt(user_id, active=False, archived=True))
    await session.commit()

    created = await compute_pending_notifications(session, user_id)
    await session.commit()
    assert created == 0
