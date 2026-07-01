"""Loan cargo automático — a personal loan whose cuota is auto-charged to a
linked credit card.

Covers:
- `_validate_charge_target`: own + active + credit + same-currency (else 400).
- `post_due_loan_cargos`: posts a card charge (counts as a gasto, raises the
  live owed balance) + lowers the loan; idempotent within the month; respects
  due day / paid-off / archived card / final-cuota cap.
- The loan steps aside from its own feed / affordability / unattached-gate /
  envelope-reservation surfaces (no double count).
- The cashflow byte-lock: committed_outflows + surplus are identical with vs
  without the loan→card link (only the gate moves).
- `undo_loan_cargo`: deleting the cargo restores the loan + removes the charge.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from api.models.account import Account
from api.models.credit_card_terms import CreditCardTerms
from api.models.debt import Debt, DebtPayment
from api.models.envelope import Envelope
from api.models.recurring_income import RecurringIncome
from api.models.transaction import Transaction
from api.models.user import User
from api.routers.debts import _validate_charge_target
from api.services.accounts import compute_account_balances
from api.services.envelopes import (
    compute_envelope_summary,
    list_unattached_obligations,
)
from api.services.finance.affordability import gather_affordability_inputs
from api.services.finance.cashflow import compute_monthly_cashflow
from api.services.debt_payments import record_debt_payment
from api.services.loan_cargo import (
    LOAN_CARGO_SOURCE,
    post_due_loan_cargos,
    undo_loan_cargo,
)
from api.services.recurrence import get_upcoming_feed


def _d(v: str) -> Decimal:
    return Decimal(v)


async def _user(session, uid) -> User:
    return await session.get(User, uid)


async def _account(session, uid, *, kind="credit", currency="CRC", name="Tarjeta", archived=False) -> Account:
    acc = Account(
        user_id=uid,
        name=name,
        account_type=kind,
        currency=currency,
        initial_balance=_d("0"),
        archived=archived,
    )
    session.add(acc)
    await session.commit()
    await session.refresh(acc)
    return acc


async def _card_terms(session, uid, account_id) -> CreditCardTerms:
    terms = CreditCardTerms(
        user_id=uid,
        account_id=account_id,
        annual_interest_rate=_d("0.45"),
        minimum_payment_pct=_d("0.025"),
        minimum_payment_floor=_d("5000"),
        payment_due_day=10,
        payment_mode="minimum",
    )
    session.add(terms)
    await session.commit()
    return terms


async def _loan(
    session,
    uid,
    *,
    charge_to=None,
    due_day=10,
    balance="2000000",
    minimum="100000",
    currency="CRC",
    payments_made=0,
) -> Debt:
    debt = Debt(
        user_id=uid,
        name="Préstamo Personal",
        debt_type="personal_loan",
        original_amount=_d("3000000"),
        current_balance=_d(balance),
        interest_rate=_d("0.18"),
        minimum_payment=_d(minimum),
        payment_due_day=due_day,
        currency=currency,
        charge_to_account_id=charge_to,
        payments_made=payments_made,
    )
    session.add(debt)
    await session.commit()
    await session.refresh(debt)
    return debt


# ── link validation ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_validate_charge_target_accepts_own_credit_same_currency(db_with_user):
    session, uid = db_with_user
    card = await _account(session, uid, kind="credit", currency="CRC")
    # No raise.
    await _validate_charge_target(
        session, user_id=uid, account_id=card.id, loan_currency="CRC"
    )


@pytest.mark.asyncio
async def test_validate_charge_target_rejects_non_credit(db_with_user):
    session, uid = db_with_user
    checking = await _account(session, uid, kind="checking", currency="CRC")
    with pytest.raises(HTTPException) as exc:
        await _validate_charge_target(
            session, user_id=uid, account_id=checking.id, loan_currency="CRC"
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_validate_charge_target_rejects_currency_mismatch(db_with_user):
    session, uid = db_with_user
    card = await _account(session, uid, kind="credit", currency="USD")
    with pytest.raises(HTTPException) as exc:
        await _validate_charge_target(
            session, user_id=uid, account_id=card.id, loan_currency="CRC"
        )
    assert exc.value.status_code == 400


# ── posting the cargo ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_post_cargo_charges_card_lowers_loan_counts_as_gasto(db_with_user):
    session, uid = db_with_user
    user = await _user(session, uid)
    card = await _account(session, uid, kind="credit")
    loan = await _loan(session, uid, charge_to=card.id, due_day=10, balance="2000000", minimum="100000")

    posted = await post_due_loan_cargos(
        session, user_id=uid, today=date(2026, 6, 15)
    )
    await session.commit()

    assert len(posted) == 1

    # The loan balance dropped and a payment was recorded.
    await session.refresh(loan)
    assert float(loan.current_balance) == 2000000 - 100000
    assert loan.payments_made == 1

    # A real charge landed on the card: amount<0, source=loan_cargo, and it
    # qualifies as a gasto (not a transfer/goal leg).
    txn = (
        await session.execute(
            select(Transaction).where(
                Transaction.account_id == card.id,
                Transaction.source == LOAN_CARGO_SOURCE,
            )
        )
    ).scalar_one()
    assert float(txn.amount) == -100000
    assert txn.transfer_id is None
    assert txn.goal_id is None
    assert txn.status == "confirmed"
    assert txn.archived is False
    assert txn.transaction_date == date(2026, 6, 10)  # this month's due day

    # The card's live "total due" rose by the cuota.
    balances = await compute_account_balances(session, user_id=uid, account_ids=[card.id])
    assert balances[card.id].current == _d("-100000")  # owed 100000


@pytest.mark.asyncio
async def test_post_cargo_is_idempotent_within_the_month(db_with_user):
    session, uid = db_with_user
    card = await _account(session, uid, kind="credit")
    await _loan(session, uid, charge_to=card.id, due_day=10)

    first = await post_due_loan_cargos(session, user_id=uid, today=date(2026, 6, 15))
    await session.commit()
    second = await post_due_loan_cargos(session, user_id=uid, today=date(2026, 6, 20))
    await session.commit()

    assert len(first) == 1
    assert second == []
    charges = (
        await session.execute(
            select(Transaction).where(Transaction.source == LOAN_CARGO_SOURCE)
        )
    ).scalars().all()
    assert len(charges) == 1


@pytest.mark.asyncio
async def test_manual_payment_does_not_suppress_cargo(db_with_user):
    """A user's manual/extra payment on a card-linked loan is a DIFFERENT event
    from the cargo — the bank still charges the card, so the cargo must post."""
    session, uid = db_with_user
    card = await _account(session, uid, kind="credit")
    loan = await _loan(session, uid, charge_to=card.id, due_day=10, balance="2000000", minimum="100000")

    # A manual payment on the 3rd (no loan_cargo transaction linked).
    await record_debt_payment(
        session, user_id=uid, debt=loan, amount_paid=50000, payment_date=date(2026, 6, 3)
    )
    await session.commit()

    posted = await post_due_loan_cargos(session, user_id=uid, today=date(2026, 6, 15))
    await session.commit()

    # The cargo posted despite the earlier manual payment.
    assert len(posted) == 1
    charges = (
        await session.execute(
            select(Transaction).where(Transaction.source == LOAN_CARGO_SOURCE)
        )
    ).scalars().all()
    assert len(charges) == 1


@pytest.mark.asyncio
async def test_today_defaults_to_user_local_date(db_with_user):
    """With today omitted, the service uses the user's local date. Pin the tz to
    UTC and set the due day to today's UTC day so the most-recent due is today
    (age 0, inside the catch-up window) regardless of when the test runs."""
    session, uid = db_with_user
    user = await _user(session, uid)
    user.timezone = "UTC"
    await session.commit()
    due_day = datetime.now(timezone.utc).day
    card = await _account(session, uid, kind="credit")
    await _loan(session, uid, charge_to=card.id, due_day=due_day, balance="500000")

    posted = await post_due_loan_cargos(session, user_id=uid)  # today=None
    await session.commit()
    assert len(posted) == 1


@pytest.mark.asyncio
async def test_post_cargo_skips_before_due_day(db_with_user):
    """This month's due day hasn't arrived and last month's due is beyond the
    catch-up window → nothing to post."""
    session, uid = db_with_user
    card = await _account(session, uid, kind="credit")
    await _loan(session, uid, charge_to=card.id, due_day=20)

    # today=Jun 12: this month's due (Jun 20) not passed; last due (May 20) is
    # 23 days old (> _MAX_CATCHUP_DAYS) → skip.
    posted = await post_due_loan_cargos(session, user_id=uid, today=date(2026, 6, 12))
    await session.commit()
    assert posted == []


@pytest.mark.asyncio
async def test_weekly_catch_up_across_month_boundary(db_with_user):
    """The weekly-safety property: an end-of-month due day is still posted on the
    following week's run AFTER the month rolled over (a calendar-month anchor
    would abandon June's cuota once it's July)."""
    session, uid = db_with_user
    card = await _account(session, uid, kind="credit")
    loan = await _loan(session, uid, charge_to=card.id, due_day=28, balance="2000000", minimum="100000")

    # Weekly run on Jul 4 — June's 28th cuota is the most recent due (Jul 28 not
    # yet passed), 6 days old → posted, dated Jun 28.
    posted = await post_due_loan_cargos(session, user_id=uid, today=date(2026, 7, 4))
    await session.commit()
    assert len(posted) == 1
    txn = (
        await session.execute(
            select(Transaction).where(Transaction.source == LOAN_CARGO_SOURCE)
        )
    ).scalar_one()
    assert txn.transaction_date == date(2026, 6, 28)
    await session.refresh(loan)
    assert float(loan.current_balance) == 1900000

    # A second run the same week does NOT double-post (cargo >= Jun 28 exists).
    again = await post_due_loan_cargos(session, user_id=uid, today=date(2026, 7, 11))
    await session.commit()
    assert again == []


@pytest.mark.asyncio
async def test_stale_prior_cycle_not_backfilled(db_with_user):
    """A due date older than the catch-up window is NOT backfilled (a run outage
    or a freshly-linked loan whose due passed weeks ago) — wait for the next."""
    session, uid = db_with_user
    card = await _account(session, uid, kind="credit")
    await _loan(session, uid, charge_to=card.id, due_day=28, balance="2000000")

    # today=Jul 25: most recent due is Jun 28 (Jul 28 not passed), 27 days old
    # (> _MAX_CATCHUP_DAYS) → skip.
    posted = await post_due_loan_cargos(session, user_id=uid, today=date(2026, 7, 25))
    await session.commit()
    assert posted == []


@pytest.mark.asyncio
async def test_post_cargo_skips_paid_off_and_archived_card(db_with_user):
    session, uid = db_with_user
    # Paid off → nothing to charge.
    card1 = await _account(session, uid, kind="credit", name="T1")
    await _loan(session, uid, charge_to=card1.id, due_day=10, balance="0")
    # Linked card archived → skip.
    card2 = await _account(session, uid, kind="credit", name="T2", archived=True)
    await _loan(session, uid, charge_to=card2.id, due_day=10, balance="500000")

    posted = await post_due_loan_cargos(session, user_id=uid, today=date(2026, 6, 15))
    await session.commit()
    assert posted == []


@pytest.mark.asyncio
async def test_post_cargo_caps_final_cuota_to_balance(db_with_user):
    session, uid = db_with_user
    card = await _account(session, uid, kind="credit")
    loan = await _loan(
        session, uid, charge_to=card.id, due_day=10, balance="40000", minimum="100000"
    )

    posted = await post_due_loan_cargos(session, user_id=uid, today=date(2026, 6, 15))
    await session.commit()

    assert len(posted) == 1
    await session.refresh(loan)
    assert float(loan.current_balance) == 0  # capped, fully paid
    txn = (
        await session.execute(
            select(Transaction).where(Transaction.source == LOAN_CARGO_SOURCE)
        )
    ).scalar_one()
    assert float(txn.amount) == -40000


# ── stepping aside (no double count) ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_card_linked_loan_excluded_from_feed(db_with_user):
    session, uid = db_with_user
    card = await _account(session, uid, kind="credit")
    await _loan(session, uid, charge_to=card.id, due_day=10)

    feed = await get_upcoming_feed(
        session,
        uid,
        from_date=date(2026, 6, 1),
        to_date=date(2026, 7, 31),
    )
    assert all(e.item_type != "debt" for e in feed)


@pytest.mark.asyncio
async def test_card_linked_loan_excluded_from_affordability_and_gate(db_with_user):
    session, uid = db_with_user
    user = await _user(session, uid)
    card = await _account(session, uid, kind="credit")
    await _loan(session, uid, charge_to=card.id, due_day=10, minimum="100000")

    inputs = await gather_affordability_inputs(session, user=user)
    # The loan minimum is NOT in the debt-commitment figure (card carries it).
    assert float(inputs.monthly_debt_payments) == 0.0

    unattached = await list_unattached_obligations(session, user_id=uid)
    assert all(name != "Préstamo Personal" for name, _amt, _src in unattached)


@pytest.mark.asyncio
async def test_card_linked_loan_not_reserved_in_envelope(db_with_user):
    session, uid = db_with_user
    user = await _user(session, uid)
    env = Envelope(
        user_id=uid, name="Deudas", envelope_class="needs",
        limit_amount=_d("500000"), currency="CRC",
    )
    session.add(env)
    await session.flush()
    # Loan attached to BOTH the envelope and a card → the card wins (no reserve).
    card = await _account(session, uid, kind="credit")
    loan = await _loan(session, uid, charge_to=card.id, due_day=10, minimum="100000")
    loan.envelope_id = env.id
    await session.commit()

    summary = await compute_envelope_summary(session, user=user)
    target = next(e for e in summary.envelopes if e.id == env.id)
    assert float(target.reserved) == 0.0


# ── byte-lock ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_byte_lock_committed_surplus_unchanged_by_card_link(db_with_user):
    """Linking a loan to a card moves only the gate — committed_outflows and
    surplus are byte-identical (Model A)."""
    session, uid = db_with_user
    user = await _user(session, uid)
    session.add(
        RecurringIncome(
            user_id=uid, name="Salario", income_type="salary",
            amount=_d("800000"), currency="CRC", frequency="monthly",
            next_payment_date=date.today(),
        )
    )
    env = Envelope(
        user_id=uid, name="Servicios", envelope_class="needs",
        limit_amount=_d("300000"), currency="CRC",
    )
    session.add(env)
    await session.flush()
    loan = await _loan(session, uid, due_day=10, minimum="100000")  # unlinked
    await session.commit()

    before = await compute_monthly_cashflow(session, user=user)

    card = await _account(session, uid, kind="credit")
    loan.charge_to_account_id = card.id
    await session.commit()

    after = await compute_monthly_cashflow(session, user=user)

    assert before.committed_outflows == after.committed_outflows
    assert before.surplus == after.surplus
    # The loan left the unattached list (it's now serviced by the card).
    assert any(o.name == "Préstamo Personal" for o in before.unattached_obligations)
    assert all(o.name != "Préstamo Personal" for o in after.unattached_obligations)


# ── undo (manual delete of a cargo) ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_undo_cargo_restores_loan_and_removes_charge(db_with_user):
    session, uid = db_with_user
    card = await _account(session, uid, kind="credit")
    loan = await _loan(session, uid, charge_to=card.id, due_day=10, balance="2000000", minimum="100000")

    await post_due_loan_cargos(session, user_id=uid, today=date(2026, 6, 15))
    await session.commit()
    await session.refresh(loan)
    assert float(loan.current_balance) == 1900000
    assert loan.payments_made == 1

    txn = (
        await session.execute(
            select(Transaction).where(Transaction.source == LOAN_CARGO_SOURCE)
        )
    ).scalar_one()

    await undo_loan_cargo(session, user_id=uid, txn=txn)
    await session.commit()

    await session.refresh(loan)
    assert float(loan.current_balance) == 2000000
    assert loan.payments_made == 0
    remaining = (
        await session.execute(
            select(Transaction).where(Transaction.source == LOAN_CARGO_SOURCE)
        )
    ).scalars().all()
    assert remaining == []
    payments = (
        await session.execute(
            select(DebtPayment).where(DebtPayment.debt_id == loan.id)
        )
    ).scalars().all()
    assert payments == []
