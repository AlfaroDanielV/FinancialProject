"""Fixed-expense attachment (B1) — schema + write-path foundation.

A recurring bill or debt can be attached to an envelope via a nullable FK; the
reservation math (B2) and the per-item under_coverage gate (B3) build on this.
Here we cover the FK round-trip, the attach-target validation, and the
archive-detach (soft archive must explicitly null the link — the FK SET NULL
only fires on a real DELETE).
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from api.models.debt import Debt
from api.models.envelope import Envelope
from api.models.recurring_bill import RecurringBill
from api.models.user import User
from api.services.envelopes import archive_subtree, is_valid_envelope_target


def _envelope(user_id, *, name="Servicios", klass="needs", limit="200000", archived=False):
    return Envelope(
        user_id=user_id, name=name, envelope_class=klass,
        limit_amount=Decimal(limit), currency="CRC", archived=archived,
    )


def _bill(user_id, *, name="ICE", amount="30000", envelope_id=None):
    return RecurringBill(
        user_id=user_id, name=name, category="servicios",
        amount_expected=Decimal(amount), currency="CRC", frequency="monthly",
        start_date=date.today(), envelope_id=envelope_id,
    )


def _debt(user_id, *, name="Préstamo", payment="100000", envelope_id=None):
    return Debt(
        user_id=user_id, name=name, debt_type="personal_loan",
        original_amount=Decimal("3000000"), current_balance=Decimal("2000000"),
        interest_rate=Decimal("0.18"), minimum_payment=Decimal(payment),
        payment_due_day=1, envelope_id=envelope_id,
    )


@pytest.mark.asyncio
async def test_attach_bill_and_debt_round_trip(db_with_user):
    session, user_id = db_with_user
    env = _envelope(user_id)
    session.add(env)
    await session.flush()
    bill = _bill(user_id, envelope_id=env.id)
    debt = _debt(user_id, envelope_id=env.id)
    session.add_all([bill, debt])
    await session.commit()

    got_bill = (
        await session.execute(select(RecurringBill).where(RecurringBill.id == bill.id))
    ).scalar_one()
    got_debt = (
        await session.execute(select(Debt).where(Debt.id == debt.id))
    ).scalar_one()
    assert got_bill.envelope_id == env.id
    assert got_debt.envelope_id == env.id


@pytest.mark.asyncio
async def test_is_valid_envelope_target(db_with_user):
    session, user_id = db_with_user
    env = _envelope(user_id)
    archived = _envelope(user_id, name="Vieja", archived=True)
    session.add_all([env, archived])
    await session.commit()

    assert await is_valid_envelope_target(session, user_id=user_id, envelope_id=env.id) is True
    # archived → not a valid target
    assert await is_valid_envelope_target(
        session, user_id=user_id, envelope_id=archived.id
    ) is False
    # nonexistent → not a valid target
    assert await is_valid_envelope_target(
        session, user_id=user_id, envelope_id=uuid.uuid4()
    ) is False


@pytest.mark.asyncio
async def test_cross_user_envelope_not_attachable(db_with_user):
    session, user_id = db_with_user
    env = _envelope(user_id)
    session.add(env)
    await session.commit()
    # Same envelope, a DIFFERENT caller → not a valid target (same-user guard).
    assert await is_valid_envelope_target(
        session, user_id=uuid.uuid4(), envelope_id=env.id
    ) is False
    assert await is_valid_envelope_target(
        session, user_id=user_id, envelope_id=env.id
    ) is True


@pytest.mark.asyncio
async def test_archive_envelope_detaches_obligations(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    env = _envelope(user_id)
    session.add(env)
    await session.flush()
    bill = _bill(user_id, envelope_id=env.id)
    debt = _debt(user_id, envelope_id=env.id)
    session.add_all([bill, debt])
    await session.commit()

    await archive_subtree(session, user=user, root=env)
    await session.commit()

    await session.refresh(bill)
    await session.refresh(debt)
    # Detached, NOT deleted — the obligation survives.
    assert bill.envelope_id is None
    assert debt.envelope_id is None
    assert await session.get(RecurringBill, bill.id) is not None
    assert await session.get(Debt, debt.id) is not None
