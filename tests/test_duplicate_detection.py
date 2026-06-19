"""Likely-duplicate expense detection + keep/delete resolution.

Deterministic heuristic (operator-locked "monto+fecha, comercio refuerza"):
same currency + magnitude, dates within ±3 days; merchant similarity boosts
but isn't required. Only the newer row is flagged. Flagging raises one
`duplicate_transaction` nudge (idempotent by dedup_key). The user decides
keep vs delete; the rule applies it.

Prereqs: `docker compose up -d db && alembic upgrade head`.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from api.models.transaction import Transaction
from api.models.user import User
from api.models.user_nudge import UserNudge
from api.services.dedup.duplicate_detector import (
    find_likely_duplicate,
    flag_and_notify,
    resolve_duplicate,
)
from api.services.nudges.evaluators import DuplicateTransactionEvaluator


_D = date(2026, 6, 15)


async def _mk_txn(
    session,
    user_id,
    *,
    amount="-5000",
    currency="CRC",
    merchant="Soda Central",
    txn_date=_D,
    status="confirmed",
    archived=False,
    transfer_id=None,
    goal_id=None,
    is_duplicate=False,
) -> Transaction:
    txn = Transaction(
        user_id=user_id,
        amount=Decimal(amount),
        currency=currency,
        merchant=merchant,
        transaction_date=txn_date,
        source="manual",
        status=status,
        archived=archived,
        transfer_id=transfer_id,
        goal_id=goal_id,
        is_duplicate=is_duplicate,
    )
    session.add(txn)
    await session.commit()
    await session.refresh(txn)
    return txn


async def _user(session, user_id) -> User:
    return await session.get(User, user_id)


async def _mk_goal(session, user_id):
    from api.models.goal import Goal

    g = Goal(user_id=user_id, name="Meta", target_amount=Decimal("100000"))
    session.add(g)
    await session.commit()
    await session.refresh(g)
    return g


async def _mk_account(session, user_id, name):
    from api.models.account import Account

    a = Account(
        user_id=user_id,
        name=name,
        account_type="checking",
        currency="CRC",
        initial_balance=Decimal("0"),
    )
    session.add(a)
    await session.commit()
    await session.refresh(a)
    return a


async def _mk_transfer(session, user_id):
    from datetime import datetime, timezone

    from api.models.transfer import Transfer

    a1 = await _mk_account(session, user_id, "Origen")
    a2 = await _mk_account(session, user_id, "Destino")
    t = Transfer(
        user_id=user_id,
        from_account_id=a1.id,
        to_account_id=a2.id,
        amount=Decimal("5000"),
        currency="CRC",
        occurred_at=datetime(2026, 6, 15, tzinfo=timezone.utc),
    )
    session.add(t)
    await session.commit()
    await session.refresh(t)
    return t


async def _nudge_for(session, txn_id) -> UserNudge | None:
    return (
        await session.execute(
            select(UserNudge).where(UserNudge.dedup_key == f"duplicate:{txn_id}")
        )
    ).scalar_one_or_none()


# ── find_likely_duplicate ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_exact_dupe_is_found(db_with_user):
    session, user_id = db_with_user
    a = await _mk_txn(session, user_id, merchant="Soda Central")
    b = await _mk_txn(session, user_id, merchant="SODA CENTRAL S.A.")
    match = await find_likely_duplicate(session, user_id=user_id, txn=b)
    assert match is not None and match.id == a.id


@pytest.mark.asyncio
async def test_different_amount_not_found(db_with_user):
    session, user_id = db_with_user
    await _mk_txn(session, user_id, amount="-5000")
    b = await _mk_txn(session, user_id, amount="-6000")
    assert await find_likely_duplicate(session, user_id=user_id, txn=b) is None


@pytest.mark.asyncio
async def test_outside_date_window_not_found(db_with_user):
    session, user_id = db_with_user
    await _mk_txn(session, user_id, txn_date=date(2026, 6, 1))
    b = await _mk_txn(session, user_id, txn_date=date(2026, 6, 15))
    assert await find_likely_duplicate(session, user_id=user_id, txn=b) is None


@pytest.mark.asyncio
async def test_within_three_days_is_found(db_with_user):
    session, user_id = db_with_user
    a = await _mk_txn(session, user_id, txn_date=date(2026, 6, 12))
    b = await _mk_txn(session, user_id, txn_date=date(2026, 6, 15))  # +3
    match = await find_likely_duplicate(session, user_id=user_id, txn=b)
    assert match is not None and match.id == a.id


@pytest.mark.asyncio
async def test_transfer_leg_not_matched(db_with_user):
    session, user_id = db_with_user
    # An existing transfer leg is never a duplicate candidate.
    transfer = await _mk_transfer(session, user_id)
    await _mk_txn(session, user_id, transfer_id=transfer.id)
    b = await _mk_txn(session, user_id)
    assert await find_likely_duplicate(session, user_id=user_id, txn=b) is None


@pytest.mark.asyncio
async def test_goal_flow_not_matched(db_with_user):
    session, user_id = db_with_user
    goal = await _mk_goal(session, user_id)
    await _mk_txn(session, user_id, goal_id=goal.id)
    b = await _mk_txn(session, user_id)
    assert await find_likely_duplicate(session, user_id=user_id, txn=b) is None


@pytest.mark.asyncio
async def test_archived_and_shadow_not_matched(db_with_user):
    session, user_id = db_with_user
    await _mk_txn(session, user_id, archived=True)
    await _mk_txn(session, user_id, status="shadow")
    b = await _mk_txn(session, user_id)
    assert await find_likely_duplicate(session, user_id=user_id, txn=b) is None


@pytest.mark.asyncio
async def test_income_is_not_flaggable(db_with_user):
    session, user_id = db_with_user
    await _mk_txn(session, user_id, amount="5000")  # an existing income
    b = await _mk_txn(session, user_id, amount="5000")  # new income
    assert await find_likely_duplicate(session, user_id=user_id, txn=b) is None


@pytest.mark.asyncio
async def test_merchant_match_breaks_tie(db_with_user):
    session, user_id = db_with_user
    # Two same-amount, same-date candidates; the merchant match wins.
    no_match = await _mk_txn(session, user_id, merchant="Otra cosa")
    yes_match = await _mk_txn(session, user_id, merchant="Soda Central")
    b = await _mk_txn(session, user_id, merchant="Soda Central")
    match = await find_likely_duplicate(session, user_id=user_id, txn=b)
    assert match is not None and match.id == yes_match.id
    assert match.id != no_match.id


# ── flag_and_notify ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_flag_and_notify_flags_only_newer_and_raises_nudge(db_with_user):
    session, user_id = db_with_user
    user = await _user(session, user_id)
    a = await _mk_txn(session, user_id)
    b = await _mk_txn(session, user_id)

    matched, nudge_id = await flag_and_notify(session, user=user, txn=b)

    assert matched is not None and matched.id == a.id
    assert nudge_id is not None
    await session.refresh(a)
    await session.refresh(b)
    assert b.is_duplicate is True
    assert a.is_duplicate is False  # history never re-flagged

    nudge = await _nudge_for(session, b.id)
    assert nudge is not None
    assert nudge.nudge_type == "duplicate_transaction"
    assert nudge.payload["transaction_id"] == str(b.id)


@pytest.mark.asyncio
async def test_flag_and_notify_no_match_is_noop(db_with_user):
    session, user_id = db_with_user
    user = await _user(session, user_id)
    b = await _mk_txn(session, user_id)  # no prior row
    matched, nudge_id = await flag_and_notify(session, user=user, txn=b)
    assert matched is None and nudge_id is None
    await session.refresh(b)
    assert b.is_duplicate is False


@pytest.mark.asyncio
async def test_flag_and_notify_idempotent(db_with_user):
    session, user_id = db_with_user
    user = await _user(session, user_id)
    await _mk_txn(session, user_id)
    b = await _mk_txn(session, user_id)

    _, first = await flag_and_notify(session, user=user, txn=b)
    _, second = await flag_and_notify(session, user=user, txn=b)

    assert first is not None and first == second  # same nudge, no duplicate
    count = len(
        (
            await session.execute(
                select(UserNudge).where(
                    UserNudge.dedup_key == f"duplicate:{b.id}"
                )
            )
        ).scalars().all()
    )
    assert count == 1


# ── evaluator (safety net) ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_evaluator_emits_candidate_for_flagged_row(db_with_user):
    session, user_id = db_with_user
    await _mk_txn(session, user_id)  # the matched older row
    b = await _mk_txn(session, user_id, is_duplicate=True)

    candidates = await DuplicateTransactionEvaluator().evaluate(
        session, now=None, user_id=user_id  # type: ignore[arg-type]
    )
    keys = {c.dedup_key for c in candidates}
    assert f"duplicate:{b.id}" in keys


# ── resolve_duplicate ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resolve_keep_clears_flag_and_resolves_nudge(db_with_user):
    session, user_id = db_with_user
    user = await _user(session, user_id)
    await _mk_txn(session, user_id)
    b = await _mk_txn(session, user_id)
    await flag_and_notify(session, user=user, txn=b)
    nudge = await _nudge_for(session, b.id)

    deleted = await resolve_duplicate(session, user=user, nudge=nudge, keep=True)
    await session.commit()

    assert deleted is False
    await session.refresh(b)
    assert b.is_duplicate is False
    await session.refresh(nudge)
    assert nudge.status == "acted_on"


@pytest.mark.asyncio
async def test_resolve_delete_removes_txn_and_resolves_nudge(db_with_user):
    session, user_id = db_with_user
    user = await _user(session, user_id)
    await _mk_txn(session, user_id)
    b = await _mk_txn(session, user_id)
    await flag_and_notify(session, user=user, txn=b)
    nudge = await _nudge_for(session, b.id)
    b_id = b.id

    deleted = await resolve_duplicate(session, user=user, nudge=nudge, keep=False)
    await session.commit()

    assert deleted is True
    gone = (
        await session.execute(select(Transaction).where(Transaction.id == b_id))
    ).scalar_one_or_none()
    assert gone is None
    await session.refresh(nudge)
    assert nudge.status == "acted_on"
