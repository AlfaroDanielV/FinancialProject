"""Phase 7b B2 — TRUE account hard delete with cascade.

Contract (decision note: Account Hard Delete With Cascade):
- `DELETE /accounts/{id}?hard=true&confirm=<exact name>` permanently deletes
  the account and ALL its transactions (400 when the typed name mismatches).
- Debts / recurring bills / goals linked to the account are DETACHED, never
  deleted. debt_payments / bill_occurrences keep their history with the
  transaction link nulled (migration 0028).
- Transfers touching the account are deleted; the surviving leg in the OTHER
  account keeps its amount (balance history intact), loses transfer_id, and is
  annotated "(transferencia — cuenta «X» eliminada)".
- `GET /accounts/{id}/delete-impact` previews the counts.
- Default DELETE (no hard) stays the soft archive.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from api.database import get_db
from api.dependencies import current_user
from api.main import app
from api.models.account import Account
from api.models.bill_occurrence import BillOccurrence
from api.models.debt import Debt, DebtPayment
from api.models.goal import Goal
from api.models.recurring_bill import RecurringBill
from api.models.transaction import Transaction
from api.models.transfer import Transfer
from api.schemas.transfers import TransferCreate
from api.services.transfers import create_transfer_with_transactions


def _override(session, user_id):
    class _StubUser:
        def __init__(self) -> None:
            self.id = user_id
            self.status = "active"
            self.currency = "CRC"
            self.display_currency = "CRC"
            self.timezone = "America/Costa_Rica"

    async def _yield_session():
        yield session

    app.dependency_overrides[current_user] = lambda: _StubUser()
    app.dependency_overrides[get_db] = _yield_session


def _clear():
    app.dependency_overrides.pop(current_user, None)
    app.dependency_overrides.pop(get_db, None)


async def _add_account(session, user_id, name: str) -> Account:
    account = Account(
        user_id=user_id,
        name=name,
        account_type="checking",
        currency="CRC",
        initial_balance=Decimal("0"),
    )
    session.add(account)
    await session.commit()
    await session.refresh(account)
    return account


async def _add_txn(session, user_id, account_id, amount: str) -> Transaction:
    txn = Transaction(
        user_id=user_id,
        account_id=account_id,
        amount=Decimal(amount),
        currency="CRC",
        transaction_date=date.today(),
        source="manual",
    )
    session.add(txn)
    await session.commit()
    await session.refresh(txn)
    return txn


@pytest.mark.asyncio
async def test_hard_delete_cascades_and_detaches(db_with_user):
    session, user_id = db_with_user
    doomed = await _add_account(session, user_id, "Cuenta Basura")
    survivor = await _add_account(session, user_id, "Cuenta Buena")

    # Two plain expenses in the doomed account.
    txn1 = await _add_txn(session, user_id, doomed.id, "-5000")
    await _add_txn(session, user_id, doomed.id, "-7000")

    # A transfer doomed → survivor (creates one leg in each account).
    transfer_result = await create_transfer_with_transactions(
        session,
        user_id=user_id,
        payload=TransferCreate(
            from_account_id=doomed.id,
            to_account_id=survivor.id,
            amount=Decimal("10000"),
            currency="CRC",
        ),
    )
    await session.commit()
    surviving_leg_id = transfer_result.credit_transaction_id

    # Debt linked to the doomed account, with a payment row linked to txn1.
    debt = Debt(
        user_id=user_id,
        account_id=doomed.id,
        name="Préstamo carro",
        debt_type="auto_loan",
        original_amount=Decimal("1000000"),
        current_balance=Decimal("800000"),
        interest_rate=Decimal("0.12"),
        minimum_payment=Decimal("50000"),
        payment_due_day=15,
    )
    session.add(debt)
    await session.flush()
    payment = DebtPayment(
        debt_id=debt.id,
        transaction_id=txn1.id,
        payment_date=date.today(),
        amount_paid=Decimal("5000"),
        remaining_balance=Decimal("795000"),
    )
    session.add(payment)

    # Recurring bill linked to the doomed account, with an occurrence
    # pointing at one of the doomed transactions.
    bill = RecurringBill(
        user_id=user_id,
        account_id=doomed.id,
        name="Internet",
        category="servicios",
        amount_expected=Decimal("25000"),
        currency="CRC",
        is_variable_amount=False,
        frequency="monthly",
        day_of_month=5,
        start_date=date.today(),
        lead_time_days=0,
    )
    session.add(bill)
    await session.flush()
    occurrence = BillOccurrence(
        user_id=user_id,
        recurring_bill_id=bill.id,
        due_date=date.today(),
        amount_expected=Decimal("25000"),
        status="paid",
        transaction_id=txn1.id,
    )
    session.add(occurrence)

    goal = Goal(
        user_id=user_id,
        name="Vacaciones",
        target_amount=Decimal("300000"),
        target_currency="CRC",
        linked_account_id=doomed.id,
    )
    session.add(goal)
    await session.commit()

    # Plain ids — instances expire after the route commits on this session.
    doomed_id, survivor_id = doomed.id, survivor.id
    debt_id, bill_id = debt.id, bill.id
    occurrence_id, goal_id = occurrence.id, goal.id

    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Impact preview shows what's at stake.
            impact = await ac.get(f"/api/v1/accounts/{doomed.id}/delete-impact")
            assert impact.status_code == 200, impact.text
            body = impact.json()
            assert body == {
                "transactions": 3,  # 2 expenses + the debit transfer leg
                "transfers": 1,
                "debts_detached": 1,
                "bills_detached": 1,
                "goals_detached": 1,
            }

            # Wrong typed name → 400, nothing deleted.
            wrong = await ac.delete(
                f"/api/v1/accounts/{doomed.id}",
                params={"hard": "true", "confirm": "otra cosa"},
            )
            assert wrong.status_code == 400
            assert "nombre" in wrong.json()["detail"]

            # Exact name → permanent delete.
            resp = await ac.delete(
                f"/api/v1/accounts/{doomed.id}",
                params={"hard": "true", "confirm": "Cuenta Basura"},
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["impact"]["transactions"] == 3
    finally:
        _clear()

    session.expire_all()

    # Account + its transactions are gone.
    assert (
        await session.execute(
            select(Account).where(Account.id == doomed_id)
        )
    ).scalar_one_or_none() is None
    doomed_txns = (
        await session.execute(
            select(Transaction).where(Transaction.account_id == doomed_id)
        )
    ).scalars().all()
    assert doomed_txns == []

    # Transfer row gone; the surviving leg keeps its amount, loses the
    # transfer link, stays category=transferencia, and is annotated.
    assert (
        await session.execute(
            select(Transfer).where(Transfer.user_id == user_id)
        )
    ).scalars().all() == []
    leg = (
        await session.execute(
            select(Transaction).where(Transaction.id == surviving_leg_id)
        )
    ).scalar_one()
    assert leg.transfer_id is None
    assert Decimal(str(leg.amount)) == Decimal("10000")
    assert leg.category == "transferencia"
    assert "cuenta «Cuenta Basura» eliminada" in (leg.description or "")

    # History rows survive with nulled links; obligations detached.
    fresh_payment = (
        await session.execute(
            select(DebtPayment).where(DebtPayment.debt_id == debt_id)
        )
    ).scalar_one()
    assert fresh_payment.transaction_id is None
    fresh_debt = (
        await session.execute(select(Debt).where(Debt.id == debt_id))
    ).scalar_one()
    assert fresh_debt.account_id is None
    fresh_occurrence = (
        await session.execute(
            select(BillOccurrence).where(BillOccurrence.id == occurrence_id)
        )
    ).scalar_one()
    assert fresh_occurrence.transaction_id is None
    fresh_bill = (
        await session.execute(
            select(RecurringBill).where(RecurringBill.id == bill_id)
        )
    ).scalar_one()
    assert fresh_bill.account_id is None
    fresh_goal = (
        await session.execute(select(Goal).where(Goal.id == goal_id))
    ).scalar_one()
    assert fresh_goal.linked_account_id is None

    # The survivor's balance still includes the credit leg.
    from api.services.accounts import compute_account_balances

    balances = await compute_account_balances(
        session, user_id=user_id, account_ids=[survivor_id]
    )
    assert balances[survivor_id].current == Decimal("10000")


@pytest.mark.asyncio
async def test_default_delete_stays_soft_archive(db_with_user):
    session, user_id = db_with_user
    account = await _add_account(session, user_id, "Cuenta Vieja")
    account_id = account.id
    await _add_txn(session, user_id, account_id, "-1000")

    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.delete(f"/api/v1/accounts/{account_id}")
            assert resp.status_code == 200, resp.text
            assert resp.json()["archived"] is True
    finally:
        _clear()

    session.expire_all()
    fresh = (
        await session.execute(select(Account).where(Account.id == account_id))
    ).scalar_one()
    assert fresh.archived is True and fresh.is_active is False
    txns = (
        await session.execute(
            select(Transaction).where(Transaction.account_id == account_id)
        )
    ).scalars().all()
    assert len(txns) == 1  # nothing deleted


@pytest.mark.asyncio
async def test_undo_after_payment_link_no_longer_violates_fk(db_with_user):
    """Migration 0028 regression: deleting a transaction referenced by a
    debt_payment must SET NULL, not raise (latent bot/undo.py bug)."""
    session, user_id = db_with_user
    account = await _add_account(session, user_id, "Cuenta BCR")
    txn = Transaction(
        user_id=user_id,
        account_id=account.id,
        amount=Decimal("-5000"),
        currency="CRC",
        transaction_date=date.today(),
        source="telegram",
    )
    session.add(txn)
    await session.commit()
    await session.refresh(txn)

    debt = Debt(
        user_id=user_id,
        name="Préstamo",
        debt_type="personal_loan",
        original_amount=Decimal("100000"),
        current_balance=Decimal("90000"),
        interest_rate=Decimal("0.10"),
        minimum_payment=Decimal("10000"),
        payment_due_day=1,
    )
    session.add(debt)
    await session.flush()
    session.add(
        DebtPayment(
            debt_id=debt.id,
            transaction_id=txn.id,
            payment_date=date.today(),
            amount_paid=Decimal("5000"),
            remaining_balance=Decimal("85000"),
        )
    )
    await session.commit()

    from api.services.transactions import delete_telegram_transaction
    from api.models.user import User

    txn_id, debt_id = txn.id, debt.id
    user = (
        await session.execute(select(User).where(User.id == user_id))
    ).scalar_one()
    await delete_telegram_transaction(user=user, transaction_id=txn_id, db=session)

    session.expire_all()
    payment = (
        await session.execute(
            select(DebtPayment).where(DebtPayment.debt_id == debt_id)
        )
    ).scalar_one()
    assert payment.transaction_id is None
