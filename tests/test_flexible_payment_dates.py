"""Flexible Payment Dates — record a bill/debt payment from chat on any date.

Two new intents: mark_bill_paid (an existing recurring bill) and
record_debt_payment (an existing debt). The deterministic path
(dispatch → propose → commit → undo) is covered without the real LLM via
direct dispatch() calls + a PendingAction. Also unit-tests the ±15-day
occurrence matcher and the name resolvers.

Decisions exercised:
  * A bill payment creates a negative transaction (source='telegram') and
    links the occurrence closest to the payment date within ±15 days, else a
    standalone expense (no phantom occurrence).
  * A debt payment lowers Debt.current_balance only — no account-side movement.
  * Both are reversible via /undo.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from api.models.account import Account
from api.models.bill_occurrence import BillOccurrence
from api.models.debt import Debt, DebtPayment
from api.models.recurring_bill import RecurringBill
from api.models.transaction import Transaction
from api.models.user import User
from api.redis_client import get_redis
from api.services.envelopes import match_bills_by_name, match_debts_by_name
from api.services.llm_extractor import ExtractionResult, Intent
from api.services.recurrence import find_closest_occurrence
from api.services.telegram_dispatcher import (
    AskClarification,
    ProposeAction,
    dispatch,
)
from bot.clarification import ClarificationState, merge_reply
from bot.commit import commit_pending
from bot.pending import PendingAction, new_short_id
from bot.undo import run_undo

TODAY = date.today()


# ── row helpers ───────────────────────────────────────────────────────────────


async def _get_user(session, user_id) -> User:
    return await session.get(User, user_id)


async def _add_account(session, user_id, name, *, account_type="checking") -> Account:
    acc = Account(
        user_id=user_id, name=name, account_type=account_type,
        currency="CRC", initial_balance=Decimal("500000"),
    )
    session.add(acc)
    await session.commit()
    await session.refresh(acc)
    return acc


async def _add_bill(
    session, user_id, *, name="Luz", amount="18000", account_id=None,
    variable=False, envelope_id=None,
) -> RecurringBill:
    bill = RecurringBill(
        user_id=user_id, name=name, category="servicios",
        amount_expected=None if variable else Decimal(amount),
        is_variable_amount=variable, currency="CRC", frequency="monthly",
        start_date=TODAY, account_id=account_id, envelope_id=envelope_id,
    )
    session.add(bill)
    await session.commit()
    await session.refresh(bill)
    return bill


async def _add_occurrence(
    session, user_id, bill_id, due_date, *, status="pending", amount_expected="18000",
) -> BillOccurrence:
    occ = BillOccurrence(
        user_id=user_id, recurring_bill_id=bill_id, due_date=due_date,
        amount_expected=Decimal(amount_expected), status=status,
    )
    session.add(occ)
    await session.commit()
    await session.refresh(occ)
    return occ


async def _add_debt(
    session, user_id, *, name="Préstamo del carro", balance="2000000", payment="100000",
) -> Debt:
    debt = Debt(
        user_id=user_id, name=name, debt_type="personal_loan",
        original_amount=Decimal("3000000"), current_balance=Decimal(balance),
        interest_rate=Decimal("0.18"), minimum_payment=Decimal(payment),
        payment_due_day=1,
    )
    session.add(debt)
    await session.commit()
    await session.refresh(debt)
    return debt


def _bill_ext(**kw) -> ExtractionResult:
    base = dict(
        intent=Intent.MARK_BILL_PAID, dispatcher="write",
        bill_target_hint="Luz", confidence=0.9,
    )
    base.update(kw)
    return ExtractionResult(**base)


def _debt_ext(**kw) -> ExtractionResult:
    base = dict(
        intent=Intent.RECORD_DEBT_PAYMENT, dispatcher="write",
        debt_target_hint="Préstamo del carro", amount=Decimal("50000"),
        confidence=0.9,
    )
    base.update(kw)
    return ExtractionResult(**base)


async def _pending_from(res: ProposeAction) -> PendingAction:
    return PendingAction(
        short_id=new_short_id(), action_type=res.action_type,
        payload=res.payload, summary_es=res.summary_es,
    )


# ── 1. find_closest_occurrence ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_find_closest_occurrence_window_and_tiebreak(db_with_user):
    session, user_id = db_with_user
    bill = await _add_bill(session, user_id)

    far = await _add_occurrence(session, user_id, bill.id, TODAY - timedelta(days=20))
    near = await _add_occurrence(session, user_id, bill.id, TODAY)

    got = await find_closest_occurrence(
        session, user_id=user_id, recurring_bill_id=bill.id, payment_date=TODAY,
    )
    assert got is not None and got.id == near.id  # in-window closest, far excluded
    assert far.id != near.id


@pytest.mark.asyncio
async def test_find_closest_tie_prefers_upcoming(db_with_user):
    session, user_id = db_with_user
    bill = await _add_bill(session, user_id)
    past = await _add_occurrence(session, user_id, bill.id, TODAY - timedelta(days=5))
    upcoming = await _add_occurrence(session, user_id, bill.id, TODAY + timedelta(days=5))

    got = await find_closest_occurrence(
        session, user_id=user_id, recurring_bill_id=bill.id, payment_date=TODAY,
    )
    assert got is not None and got.id == upcoming.id
    assert past.id != upcoming.id


@pytest.mark.asyncio
async def test_find_closest_out_of_window_returns_none(db_with_user):
    session, user_id = db_with_user
    bill = await _add_bill(session, user_id)
    await _add_occurrence(session, user_id, bill.id, TODAY + timedelta(days=30))

    got = await find_closest_occurrence(
        session, user_id=user_id, recurring_bill_id=bill.id, payment_date=TODAY,
    )
    assert got is None  # no phantom occurrence


@pytest.mark.asyncio
async def test_find_closest_ignores_terminal_occurrences(db_with_user):
    session, user_id = db_with_user
    bill = await _add_bill(session, user_id)
    await _add_occurrence(session, user_id, bill.id, TODAY, status="paid")
    await _add_occurrence(
        session, user_id, bill.id, TODAY - timedelta(days=1), status="cancelled"
    )

    got = await find_closest_occurrence(
        session, user_id=user_id, recurring_bill_id=bill.id, payment_date=TODAY,
    )
    assert got is None


# ── 2. name resolvers ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_name_matchers(db_with_user):
    session, user_id = db_with_user
    await _add_bill(session, user_id, name="Luz")
    await _add_bill(session, user_id, name="Internet")
    await _add_debt(session, user_id, name="Hipoteca")

    assert [b.name for b in await match_bills_by_name(session, user_id=user_id, hint="luz")] == ["Luz"]
    assert await match_bills_by_name(session, user_id=user_id, hint="agua") == []
    assert [d.name for d in await match_debts_by_name(session, user_id=user_id, hint="hipoteca")] == ["Hipoteca"]


# ── 3-5. mark_bill_paid dispatch ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dispatch_mark_bill_paid_proposes(db_with_user):
    session, user_id = db_with_user
    acc = await _add_account(session, user_id, "Corriente BCR")
    await _add_bill(session, user_id, name="Luz", amount="18000", account_id=acc.id)
    user = await _get_user(session, user_id)

    res = await dispatch(extraction=_bill_ext(), user=user, today=TODAY, db=session)
    assert isinstance(res, ProposeAction)
    assert res.action_type == "mark_bill_paid"
    assert Decimal(res.payload["amount"]) == Decimal("18000")  # bill's expected amount
    assert res.payload["account_id"] == str(acc.id)
    assert res.payload["payment_date"] == TODAY.isoformat()


@pytest.mark.asyncio
async def test_dispatch_mark_bill_paid_no_match_clarifies_with_options(db_with_user):
    session, user_id = db_with_user
    await _add_account(session, user_id, "Corriente BCR")
    await _add_bill(session, user_id, name="Internet")
    user = await _get_user(session, user_id)

    res = await dispatch(
        extraction=_bill_ext(bill_target_hint="Luz"), user=user, today=TODAY, db=session
    )
    assert isinstance(res, AskClarification)
    assert res.awaiting_field == "bill_target"
    assert "Internet" in res.options


@pytest.mark.asyncio
async def test_dispatch_mark_bill_paid_variable_no_amount_clarifies(db_with_user):
    session, user_id = db_with_user
    acc = await _add_account(session, user_id, "Corriente BCR")
    await _add_bill(session, user_id, name="Agua", variable=True, account_id=acc.id)
    user = await _get_user(session, user_id)

    res = await dispatch(
        extraction=_bill_ext(bill_target_hint="Agua"), user=user, today=TODAY, db=session
    )
    assert isinstance(res, AskClarification)
    assert res.awaiting_field == "amount"


@pytest.mark.asyncio
async def test_dispatch_mark_bill_paid_resolves_date_hint(db_with_user):
    session, user_id = db_with_user
    acc = await _add_account(session, user_id, "Corriente BCR")
    await _add_bill(session, user_id, name="Luz", account_id=acc.id)
    user = await _get_user(session, user_id)

    res = await dispatch(
        extraction=_bill_ext(occurred_at_hint="ayer"), user=user, today=TODAY, db=session
    )
    assert isinstance(res, ProposeAction)
    assert res.payload["payment_date"] == (TODAY - timedelta(days=1)).isoformat()


# ── 6-7. record_debt_payment dispatch ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_dispatch_record_debt_payment_proposes(db_with_user):
    session, user_id = db_with_user
    debt = await _add_debt(session, user_id)
    user = await _get_user(session, user_id)

    res = await dispatch(extraction=_debt_ext(), user=user, today=TODAY, db=session)
    assert isinstance(res, ProposeAction)
    assert res.action_type == "record_debt_payment"
    assert res.payload["debt_id"] == str(debt.id)
    assert res.payload["amount_paid"] == "50000"


@pytest.mark.asyncio
async def test_dispatch_record_debt_payment_requires_amount(db_with_user):
    session, user_id = db_with_user
    await _add_debt(session, user_id)
    user = await _get_user(session, user_id)

    res = await dispatch(
        extraction=_debt_ext(amount=None), user=user, today=TODAY, db=session
    )
    assert isinstance(res, AskClarification)
    assert res.awaiting_field == "amount"


@pytest.mark.asyncio
async def test_dispatch_record_debt_payment_no_match_clarifies(db_with_user):
    session, user_id = db_with_user
    await _add_debt(session, user_id, name="Hipoteca")
    user = await _get_user(session, user_id)

    res = await dispatch(
        extraction=_debt_ext(debt_target_hint="tarjetota"), user=user, today=TODAY, db=session
    )
    assert isinstance(res, AskClarification)
    assert res.awaiting_field == "debt_target"
    assert "Hipoteca" in res.options


@pytest.mark.asyncio
async def test_dispatch_record_debt_payment_rejects_overpayment(db_with_user):
    """An amount above the balance would clamp remaining to 0 and corrupt /undo —
    the dispatcher asks for a non-overpaying amount instead."""
    session, user_id = db_with_user
    await _add_debt(session, user_id, name="Préstamo del carro", balance="100000")
    user = await _get_user(session, user_id)

    res = await dispatch(
        extraction=_debt_ext(amount=Decimal("150000")), user=user, today=TODAY, db=session
    )
    assert isinstance(res, AskClarification)
    assert res.awaiting_field == "amount"
    assert "mayor al" in res.question_es

    # Paying exactly the balance is allowed (discharges, no clamp).
    ok = await dispatch(
        extraction=_debt_ext(amount=Decimal("100000")), user=user, today=TODAY, db=session
    )
    assert isinstance(ok, ProposeAction)


# ── 8-10. mark_bill_paid commit ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_commit_mark_bill_paid_links_closest_occurrence(db_with_user):
    session, user_id = db_with_user
    acc = await _add_account(session, user_id, "Corriente BCR")
    bill = await _add_bill(session, user_id, name="Luz", amount="18000", account_id=acc.id)
    occ = await _add_occurrence(session, user_id, bill.id, TODAY - timedelta(days=5))
    user = await _get_user(session, user_id)

    res = await dispatch(extraction=_bill_ext(), user=user, today=TODAY, db=session)
    txn_id = await commit_pending(
        user=user, pending=await _pending_from(res), db=session, redis=get_redis()
    )

    txn = await session.get(Transaction, txn_id)
    assert txn is not None
    assert Decimal(str(txn.amount)) == Decimal("-18000")
    assert txn.source == "telegram"
    assert txn.account_id == acc.id
    assert txn.transaction_date == TODAY

    await session.refresh(occ)
    assert occ.status == "paid"
    assert occ.transaction_id == txn_id
    # paid_at: a `date` was passed; the column is TIMESTAMP → coerced (P0).
    assert occ.paid_at is not None


@pytest.mark.asyncio
async def test_commit_mark_bill_paid_standalone_when_out_of_window(db_with_user):
    session, user_id = db_with_user
    acc = await _add_account(session, user_id, "Corriente BCR")
    bill = await _add_bill(session, user_id, name="Luz", amount="18000", account_id=acc.id)
    occ = await _add_occurrence(session, user_id, bill.id, TODAY + timedelta(days=30))
    user = await _get_user(session, user_id)

    res = await dispatch(extraction=_bill_ext(), user=user, today=TODAY, db=session)
    txn_id = await commit_pending(
        user=user, pending=await _pending_from(res), db=session, redis=get_redis()
    )

    txn = await session.get(Transaction, txn_id)
    assert txn is not None  # standalone expense still recorded
    await session.refresh(occ)
    assert occ.status == "pending"  # no phantom occurrence link
    assert occ.transaction_id is None


# ── 11. record_debt_payment commit ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_commit_record_debt_payment_lowers_balance_no_txn(db_with_user):
    session, user_id = db_with_user
    debt = await _add_debt(session, user_id, balance="2000000")
    user = await _get_user(session, user_id)

    res = await dispatch(extraction=_debt_ext(amount=Decimal("50000")), user=user, today=TODAY, db=session)
    payment_id = await commit_pending(
        user=user, pending=await _pending_from(res), db=session, redis=get_redis()
    )

    payment = await session.get(DebtPayment, payment_id)
    assert payment is not None
    assert Decimal(str(payment.amount_paid)) == Decimal("50000")
    assert Decimal(str(payment.remaining_balance)) == Decimal("1950000")

    await session.refresh(debt)
    assert Decimal(str(debt.current_balance)) == Decimal("1950000")
    assert debt.payments_made == 1

    # No account-side movement (operator decision).
    txns = (
        await session.execute(select(Transaction).where(Transaction.user_id == user_id))
    ).scalars().all()
    assert txns == []


# ── 12-13. undo ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_undo_mark_bill_paid_restores_occurrence(db_with_user):
    session, user_id = db_with_user
    acc = await _add_account(session, user_id, "Corriente BCR")
    bill = await _add_bill(session, user_id, name="Luz", amount="18000", account_id=acc.id)
    occ = await _add_occurrence(session, user_id, bill.id, TODAY - timedelta(days=5))
    user = await _get_user(session, user_id)

    res = await dispatch(extraction=_bill_ext(), user=user, today=TODAY, db=session)
    txn_id = await commit_pending(
        user=user, pending=await _pending_from(res), db=session, redis=get_redis()
    )

    ok, _ = await run_undo(user=user, db=session, redis=get_redis())
    assert ok

    assert await session.get(Transaction, txn_id) is None  # txn deleted
    await session.refresh(occ)
    assert occ.transaction_id is None
    assert occ.amount_paid is None
    assert occ.paid_at is None
    assert occ.status in ("pending", "overdue")  # restored (overdue: due < today)


@pytest.mark.asyncio
async def test_undo_record_debt_payment_restores_balance(db_with_user):
    session, user_id = db_with_user
    debt = await _add_debt(session, user_id, balance="2000000")
    user = await _get_user(session, user_id)

    res = await dispatch(extraction=_debt_ext(amount=Decimal("50000")), user=user, today=TODAY, db=session)
    payment_id = await commit_pending(
        user=user, pending=await _pending_from(res), db=session, redis=get_redis()
    )

    ok, _ = await run_undo(user=user, db=session, redis=get_redis())
    assert ok

    assert await session.get(DebtPayment, payment_id) is None
    await session.refresh(debt)
    assert Decimal(str(debt.current_balance)) == Decimal("2000000")
    assert debt.payments_made == 0


# ── 14. clarification merge ────────────────────────────────────────────────────


# ── 15. debt next-due advances one cadence per payment ───────────────────────


def test_next_cuota_date_advances_with_payments():
    from app.domain.credit.cuota_schedule import next_cuota_date

    anchor = date(2026, 6, 10)  # registered June 10, due day 1 → first cuota July 1
    today = date(2026, 6, 28)
    assert next_cuota_date(payment_due_day=1, payments_made=0, anchor=anchor, today=today) == date(2026, 7, 1)
    assert next_cuota_date(payment_due_day=1, payments_made=1, anchor=anchor, today=today) == date(2026, 8, 1)
    assert next_cuota_date(payment_due_day=1, payments_made=2, anchor=anchor, today=today) == date(2026, 9, 1)
    # Behind schedule (old anchor, no payments) → next upcoming, NOT a months-old
    # overdue cuota (max(schedule, natural) guard — no feed regression).
    assert next_cuota_date(payment_due_day=1, payments_made=0, anchor=date(2026, 1, 5), today=today) == date(2026, 7, 1)


@pytest.mark.asyncio
async def test_recorded_debt_payment_advances_next_payment_date(db_with_user):
    from dateutil.relativedelta import relativedelta

    from api.schemas.debts import DebtResponse

    session, user_id = db_with_user
    debt = await _add_debt(session, user_id, balance="2000000")
    user = await _get_user(session, user_id)
    before = DebtResponse.model_validate(debt).next_payment_date

    res = await dispatch(
        extraction=_debt_ext(amount=Decimal("50000")), user=user, today=TODAY, db=session
    )
    await commit_pending(
        user=user, pending=await _pending_from(res), db=session, redis=get_redis()
    )
    await session.refresh(debt)

    after = DebtResponse.model_validate(debt).next_payment_date
    assert after == before + relativedelta(months=1)  # advanced one cadence


def test_merge_reply_bill_and_debt_target():
    class _U:
        currency = "CRC"

    bill_state = ClarificationState(
        partial=_bill_ext(bill_target_hint=None).model_dump(mode="json"),
        awaiting_field="bill_target", question_es="¿Cuál gasto fijo?",
    )
    merged = merge_reply(bill_state, "Luz", _U())
    assert merged is not None and merged.bill_target_hint == "Luz"

    debt_state = ClarificationState(
        partial=_debt_ext(debt_target_hint=None).model_dump(mode="json"),
        awaiting_field="debt_target", question_es="¿Cuál deuda?",
    )
    merged2 = merge_reply(debt_state, "Hipoteca", _U())
    assert merged2 is not None and merged2.debt_target_hint == "Hipoteca"
