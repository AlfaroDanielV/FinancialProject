"""Permanent transaction delete + duplicate keep/delete resolution.

DELETE /transactions/{id} hard-deletes a movement (distinct from archive),
guarded so it can't silently orphan a payment. The duplicate nudge's act
(delete) / dismiss (keep) resolve through the same delete primitive and must
NOT trip the auto-silence machinery (a kept false positive can't mute
duplicate detection).

Prereqs: `docker compose up -d db && alembic upgrade head`.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from api.database import get_db
from api.main import app
from api.models.transaction import Transaction
from api.models.user import User
from api.models.user_nudge import UserNudgeSilence
from api.services.auth.magic_link import generate_link
from api.services.dedup.duplicate_detector import flag_and_notify


_D = date(2026, 6, 15)


async def _mk_txn(
    session, user_id, *, status="confirmed", transfer_id=None, goal_id=None,
    merchant="Soda Central",
) -> Transaction:
    txn = Transaction(
        user_id=user_id,
        amount=Decimal("-5000"),
        currency="CRC",
        merchant=merchant,
        transaction_date=_D,
        source="manual",
        status=status,
        transfer_id=transfer_id,
        goal_id=goal_id,
    )
    session.add(txn)
    await session.commit()
    await session.refresh(txn)
    return txn


async def _mk_goal(session, user_id):
    from api.models.goal import Goal

    g = Goal(user_id=user_id, name="Meta", target_amount=Decimal("100000"))
    session.add(g)
    await session.commit()
    await session.refresh(g)
    return g


async def _mk_transfer(session, user_id):
    from datetime import datetime, timezone

    from api.models.account import Account
    from api.models.transfer import Transfer

    accts = []
    for nm in ("Origen", "Destino"):
        a = Account(
            user_id=user_id, name=nm, account_type="checking",
            currency="CRC", initial_balance=Decimal("0"),
        )
        session.add(a)
        accts.append(a)
    await session.commit()
    for a in accts:
        await session.refresh(a)
    t = Transfer(
        user_id=user_id, from_account_id=accts[0].id, to_account_id=accts[1].id,
        amount=Decimal("5000"), currency="CRC",
        occurred_at=datetime(2026, 6, 15, tzinfo=timezone.utc),
    )
    session.add(t)
    await session.commit()
    await session.refresh(t)
    return t


async def _link_debt_payment(session, user_id, txn_id):
    from api.models.debt import Debt, DebtPayment

    debt = Debt(
        user_id=user_id, name="Préstamo", debt_type="personal_loan",
        original_amount=Decimal("1000000"), current_balance=Decimal("900000"),
        interest_rate=Decimal("0.18"), minimum_payment=Decimal("50000"),
        payment_due_day=15,
    )
    session.add(debt)
    await session.commit()
    await session.refresh(debt)
    pay = DebtPayment(
        debt_id=debt.id, transaction_id=txn_id, payment_date=_D,
        amount_paid=Decimal("50000"), remaining_balance=Decimal("850000"),
    )
    session.add(pay)
    await session.commit()


def _override_db(session):
    async def _yield():
        yield session

    app.dependency_overrides[get_db] = _yield


async def _token(session, user_id) -> str:
    link = await generate_link(session, user_id=user_id, purpose="onboarding")
    _override_db(session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/auth/magic-link/exchange", json={"token": link.raw_token}
        )
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


# ── DELETE /transactions/{id} ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_confirmed_row(db_with_user):
    session, user_id = db_with_user
    txn = await _mk_txn(session, user_id)
    token = await _token(session, user_id)
    headers = {"Authorization": f"Bearer {token}"}
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.delete(f"/api/v1/transactions/{txn.id}", headers=headers)
            assert resp.status_code == 200, resp.text
            assert resp.json()["deleted"] is True
        gone = (
            await session.execute(
                select(Transaction).where(Transaction.id == txn.id)
            )
        ).scalar_one_or_none()
        assert gone is None
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.mark.asyncio
async def test_delete_404_for_unknown(db_with_user):
    session, user_id = db_with_user
    token = await _token(session, user_id)
    headers = {"Authorization": f"Bearer {token}"}
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.delete(
                f"/api/v1/transactions/{uuid.uuid4()}", headers=headers
            )
            assert resp.status_code == 404
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("setup", ["shadow", "transfer", "goal", "debt"])
async def test_delete_guarded_rows_409(db_with_user, setup):
    session, user_id = db_with_user
    if setup == "shadow":
        txn = await _mk_txn(session, user_id, status="shadow")
    elif setup == "transfer":
        t = await _mk_transfer(session, user_id)
        txn = await _mk_txn(session, user_id, transfer_id=t.id)
    elif setup == "goal":
        g = await _mk_goal(session, user_id)
        txn = await _mk_txn(session, user_id, goal_id=g.id)
    else:  # debt
        txn = await _mk_txn(session, user_id)
        await _link_debt_payment(session, user_id, txn.id)

    token = await _token(session, user_id)
    headers = {"Authorization": f"Bearer {token}"}
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.delete(f"/api/v1/transactions/{txn.id}", headers=headers)
            assert resp.status_code == 409, resp.text
        # The row survives the rejected delete.
        still = (
            await session.execute(
                select(Transaction).where(Transaction.id == txn.id)
            )
        ).scalar_one_or_none()
        assert still is not None
    finally:
        app.dependency_overrides.pop(get_db, None)


# ── duplicate nudge act/dismiss via REST ──────────────────────────────────────


@pytest.mark.asyncio
async def test_nudge_act_deletes_the_duplicate(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    await _mk_txn(session, user_id)
    dupe = await _mk_txn(session, user_id)
    _, nudge_id = await flag_and_notify(session, user=user, txn=dupe)
    assert nudge_id is not None

    token = await _token(session, user_id)
    headers = {"Authorization": f"Bearer {token}"}
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/nudges/{nudge_id}/act", headers=headers
            )
            assert resp.status_code == 200, resp.text
        gone = (
            await session.execute(
                select(Transaction).where(Transaction.id == dupe.id)
            )
        ).scalar_one_or_none()
        assert gone is None
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.mark.asyncio
async def test_nudge_dismiss_keeps_and_never_silences(db_with_user):
    """Conserving two duplicates must NOT silence the type (the generic
    mark_dismissed would, after 2). Validates the silence-bypass design."""
    session, user_id = db_with_user
    user = await session.get(User, user_id)

    token = await _token(session, user_id)
    headers = {"Authorization": f"Bearer {token}"}
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            for _ in range(2):
                base = await _mk_txn(session, user_id)
                dupe = await _mk_txn(session, user_id)
                _, nudge_id = await flag_and_notify(session, user=user, txn=dupe)
                resp = await ac.post(
                    f"/api/v1/nudges/{nudge_id}/dismiss", headers=headers
                )
                assert resp.status_code == 200, resp.text
                # kept: still present, flag cleared
                await session.refresh(dupe)
                assert dupe.is_duplicate is False
                assert resp.json()["silence_created"] is False

        silences = (
            await session.execute(
                select(UserNudgeSilence).where(
                    UserNudgeSilence.user_id == user_id,
                    UserNudgeSilence.nudge_type == "duplicate_transaction",
                )
            )
        ).scalars().all()
        assert silences == []
    finally:
        app.dependency_overrides.pop(get_db, None)
