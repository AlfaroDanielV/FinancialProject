"""B3 — per-item under_coverage gate (driven by the attachment FK).

The gate fires iff an active bill/debt has no envelope. Attaching an obligation
moves it off the unattached list WITHOUT changing committed/surplus — decision 3:
Model A holds, attachment redistributes inside an envelope, never the top line.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select, update

from api.models.debt import Debt
from api.models.envelope import Envelope
from api.models.recurring_bill import RecurringBill
from api.models.recurring_income import RecurringIncome
from api.models.user import User
from api.services.finance.cashflow import compute_monthly_cashflow


def _d(v: str) -> Decimal:
    return Decimal(v)


async def _seed(session, user_id, *, attach: bool) -> Envelope:
    env = Envelope(
        user_id=user_id, name="Servicios", envelope_class="needs",
        limit_amount=_d("300000"), currency="CRC",
    )
    session.add(env)
    await session.flush()
    eid = env.id if attach else None
    session.add_all(
        [
            RecurringIncome(
                user_id=user_id, name="Salario", income_type="salary",
                amount=_d("800000"), currency="CRC", frequency="monthly",
                next_payment_date=date.today(),
            ),
            RecurringBill(
                user_id=user_id, name="Internet", category="servicios",
                amount_expected=_d("150000"), currency="CRC", frequency="monthly",
                start_date=date.today(), envelope_id=eid,
            ),
            Debt(
                user_id=user_id, name="Préstamo", debt_type="personal_loan",
                original_amount=_d("3000000"), current_balance=_d("2000000"),
                interest_rate=_d("0.18"), minimum_payment=_d("100000"),
                payment_due_day=1, envelope_id=eid,
            ),
        ]
    )
    await session.commit()
    return env


@pytest.mark.asyncio
async def test_unattached_obligation_gates_per_item(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    await _seed(session, user_id, attach=False)

    cf = await compute_monthly_cashflow(session, user=user)
    assert cf.gate_reason == "under_coverage"
    assert cf.reliable is False
    assert {o.name for o in cf.unattached_obligations} == {"Internet", "Préstamo"}


@pytest.mark.asyncio
async def test_attached_obligations_clear_the_gate(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    await _seed(session, user_id, attach=True)

    cf = await compute_monthly_cashflow(session, user=user)
    assert cf.gate_reason is None
    assert cf.reliable is True
    assert cf.unattached_obligations == ()


@pytest.mark.asyncio
async def test_decision_3_committed_surplus_identical_attached_vs_unattached(db_with_user):
    """Attachment ONLY moves the gate — committed_outflows + surplus are
    byte-identical whether the obligations are attached or not (Model A)."""
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    env = await _seed(session, user_id, attach=False)
    before = await compute_monthly_cashflow(session, user=user)

    await session.execute(
        update(RecurringBill).where(RecurringBill.user_id == user_id).values(envelope_id=env.id)
    )
    await session.execute(
        update(Debt).where(Debt.user_id == user_id).values(envelope_id=env.id)
    )
    await session.commit()
    after = await compute_monthly_cashflow(session, user=user)

    assert before.committed_outflows == after.committed_outflows
    assert before.surplus == after.surplus
    # Only the gate moved.
    assert before.gate_reason == "under_coverage"
    assert after.gate_reason is None
