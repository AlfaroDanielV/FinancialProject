"""«Empezar de cero» — reset flows (fix pack B4).

Option A (`reset_balances`): re-anchor each account to its stated real balance,
history intact (anchor + labeled ajuste). Option B (`wipe_user_history`): delete
movements + derived records + anchors and reset debt/goal progress, while KEEPING
config (accounts/debts/bills/goals) and requiring a typed confirmation phrase at
the endpoint. Prereqs: `docker compose up -d db && alembic upgrade head`.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from api.database import get_db
from api.dependencies import current_user
from api.main import app
from api.models.account import Account
from api.models.account_anchor import AccountAnchor
from api.models.bill_occurrence import BillOccurrence
from api.models.debt import Debt, DebtPayment
from api.models.goal import Goal
from api.models.goal_contribution import GoalContribution
from api.models.recurring_bill import RecurringBill
from api.models.transaction import Transaction
from api.models.transfer import Transfer
from api.models.user import User
from api.services.accounts import compute_account_balances
from api.services.reset import ResetError, reset_balances, wipe_user_history


async def _account(session, user_id, name, *, initial="0"):
    acc = Account(
        user_id=user_id,
        name=name,
        account_type="checking",
        currency="CRC",
        initial_balance=Decimal(initial),
    )
    session.add(acc)
    await session.commit()
    await session.refresh(acc)
    return acc


# ── Option A — reiniciar saldos ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reset_balances_anchors_each_account(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    acc1 = await _account(session, user_id, "BAC", initial="100000")
    acc2 = await _account(session, user_id, "Efectivo", initial="0")
    # A movement so acc1's projected balance (150k) differs from what we state.
    session.add(
        Transaction(
            user_id=user_id,
            account_id=acc1.id,
            amount=Decimal("50000"),
            currency="CRC",
            transaction_date=date.today(),
            source="manual",
            status="confirmed",
        )
    )
    await session.commit()

    results = await reset_balances(
        session,
        user=user,
        items=[(acc1.id, Decimal("500000")), (acc2.id, Decimal("25000"))],
    )
    await session.commit()

    assert len(results) == 2
    balances = await compute_account_balances(session, user_id=user_id)
    assert balances[acc1.id].current == Decimal("500000")
    assert balances[acc2.id].current == Decimal("25000")
    # Two anchors appended; acc1 got an ajuste (delta ≠ 0), acc2 too (0 → 25k).
    anchor_count = (
        await session.execute(
            select(func.count()).select_from(AccountAnchor).where(
                AccountAnchor.user_id == user_id
            )
        )
    ).scalar_one()
    assert anchor_count == 2


@pytest.mark.asyncio
async def test_reset_balances_rejects_foreign_account(db_with_user):
    import uuid

    session, user_id = db_with_user
    user = await session.get(User, user_id)
    with pytest.raises(ResetError):
        await reset_balances(
            session, user=user, items=[(uuid.uuid4(), Decimal("1000"))]
        )


# ── Option B — borrar historial ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_wipe_clears_movements_and_derived_keeps_config(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    acc = await _account(session, user_id, "BAC", initial="0")

    # A plain movement + a balance anchor.
    session.add(
        Transaction(
            user_id=user_id,
            account_id=acc.id,
            amount=Decimal("-30000"),
            currency="CRC",
            transaction_date=date.today(),
            source="manual",
            status="confirmed",
        )
    )
    session.add(
        AccountAnchor(
            user_id=user_id,
            account_id=acc.id,
            value=Decimal("-30000"),
            currency="CRC",
            effective_date=date.today(),
            source="reanchor",
        )
    )
    # A debt with a recorded payment.
    debt = Debt(
        user_id=user_id,
        name="Préstamo",
        debt_type="loan",
        original_amount=Decimal("3000000"),
        current_balance=Decimal("2000000"),
        interest_rate=Decimal("0.18"),
        minimum_payment=Decimal("100000"),
        payment_due_day=1,
        payments_made=5,
    )
    session.add(debt)
    await session.flush()
    session.add(
        DebtPayment(
            debt_id=debt.id,
            payment_date=date.today(),
            amount_paid=Decimal("100000"),
            remaining_balance=Decimal("2000000"),
        )
    )
    # A goal with a contribution.
    goal = Goal(
        user_id=user_id,
        name="Marchamo",
        target_amount=Decimal("200000"),
        current_amount=Decimal("50000"),
    )
    session.add(goal)
    await session.flush()
    session.add(
        GoalContribution(goal_id=goal.id, amount=Decimal("50000"))
    )
    # A recurring bill with a PAID occurrence.
    bill = RecurringBill(
        user_id=user_id,
        name="Internet",
        category="servicios",
        amount_expected=Decimal("25000"),
        currency="CRC",
        frequency="monthly",
        start_date=date.today(),
    )
    session.add(bill)
    await session.flush()
    session.add(
        BillOccurrence(
            user_id=user_id,
            recurring_bill_id=bill.id,
            due_date=date.today(),
            amount_expected=Decimal("25000"),
            amount_paid=Decimal("25000"),
            status="paid",
            paid_at=datetime.utcnow(),
        )
    )
    # A transfer with both legs.
    acc2 = await _account(session, user_id, "Ahorros", initial="0")
    transfer = Transfer(
        user_id=user_id,
        from_account_id=acc.id,
        to_account_id=acc2.id,
        amount=Decimal("10000"),
        currency="CRC",
    )
    session.add(transfer)
    await session.flush()
    for leg_acc, amt in ((acc.id, "-10000"), (acc2.id, "10000")):
        session.add(
            Transaction(
                user_id=user_id,
                account_id=leg_acc,
                amount=Decimal(amt),
                currency="CRC",
                transaction_date=date.today(),
                source="manual",
                status="confirmed",
                transfer_id=transfer.id,
            )
        )
    await session.commit()

    counts = await wipe_user_history(session, user=user)
    await session.commit()

    async def _count(model, where):
        return (
            await session.execute(select(func.count()).select_from(model).where(where))
        ).scalar_one()

    # Movements + derived records + anchors gone.
    assert await _count(Transaction, Transaction.user_id == user_id) == 0
    assert await _count(Transfer, Transfer.user_id == user_id) == 0
    assert (
        await _count(DebtPayment, DebtPayment.debt_id == debt.id)
    ) == 0
    assert (
        await _count(GoalContribution, GoalContribution.goal_id == goal.id)
    ) == 0
    assert await _count(AccountAnchor, AccountAnchor.user_id == user_id) == 0
    assert counts["transactions"] == 3  # the expense + two transfer legs

    # Derived progress reset.
    await session.refresh(debt)
    await session.refresh(goal)
    occ = (
        await session.execute(
            select(BillOccurrence).where(BillOccurrence.recurring_bill_id == bill.id)
        )
    ).scalar_one()
    assert debt.current_balance == Decimal("3000000")  # == original_amount
    assert debt.payments_made == 0
    assert goal.current_amount == Decimal("0")
    assert occ.status == "pending"
    assert occ.transaction_id is None
    assert occ.amount_paid is None

    # Config KEPT.
    assert await _count(Account, Account.user_id == user_id) == 2
    assert await _count(Debt, Debt.user_id == user_id) == 1
    assert await _count(Goal, Goal.user_id == user_id) == 1
    assert await _count(RecurringBill, RecurringBill.user_id == user_id) == 1


# ── Option B — endpoint phrase guard ─────────────────────────────────────────


def _override(session, user_id):
    class _StubUser:
        id = user_id
        status = "active"
        currency = "CRC"
        display_currency = "CRC"

    async def _yield_session():
        yield session

    app.dependency_overrides[current_user] = lambda: _StubUser()
    app.dependency_overrides[get_db] = _yield_session


def _clear():
    app.dependency_overrides.pop(current_user, None)
    app.dependency_overrides.pop(get_db, None)


@pytest.mark.asyncio
async def test_wipe_history_endpoint_requires_phrase(db_with_user):
    session, user_id = db_with_user
    acc = await _account(session, user_id, "BAC", initial="0")
    session.add(
        Transaction(
            user_id=user_id,
            account_id=acc.id,
            amount=Decimal("-5000"),
            currency="CRC",
            transaction_date=date.today(),
            source="manual",
            status="confirmed",
        )
    )
    await session.commit()
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            bad = await ac.post(
                "/api/v1/accounts/wipe-history", json={"confirm": "sí"}
            )
            assert bad.status_code == 400, bad.text

            ok = await ac.post(
                "/api/v1/accounts/wipe-history",
                json={"confirm": "BORRAR HISTORIAL"},
            )
            assert ok.status_code == 200, ok.text
            assert ok.json()["deleted"]["transactions"] == 1
    finally:
        _clear()

    # The wrong-phrase call left the movement intact; the right one removed it.
    remaining = (
        await session.execute(
            select(func.count()).select_from(Transaction).where(
                Transaction.user_id == user_id
            )
        )
    ).scalar_one()
    assert remaining == 0
