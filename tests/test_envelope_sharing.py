"""Shared envelopes ("Sobres compartidos") — backend contracts.

Covers:
- mint_share_code requires OWNER + ROOT (400 on a sub-sobre, 403 on a non-owner).
- redeem creates a membership, is idempotent, enforces the 9-invited-member cap
  (409), and rejects a bad/expired code (400).
- A member assigns their OWN expense to a shared envelope: the bar is a SHARED
  cap (aggregates ALL members' spend), the member's `your_spent` is only theirs,
  and the OWNER's own bar also drains with the combined spend.
- A member CANNOT edit the envelope (owner-only `_get_envelope` → 404) nor attach
  bills/debts (`is_valid_envelope_target` stays owner-only) — but CAN assign a tx.
- remove_member unlinks the member's tagged tx (never deletes) + revokes access.
- BYTE-LOCK: joining a shared envelope does NOT change a member's
  compute_monthly_cashflow committed_outflows / has_budget / surplus, and a
  member with no own budget still reads as `no_budget`.

Prereqs (same as conftest): docker compose up -d db redis; alembic upgrade head.
"""
from __future__ import annotations

import secrets
import uuid
from datetime import date

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import text

from api.models.envelope import Envelope
from api.models.recurring_income import RecurringIncome
from api.models.transaction import Transaction
from api.models.user import User
from api.redis_client import get_redis
from api.routers.envelopes import _get_envelope
from api.services import envelope_sharing as sharing
from api.services.envelopes import (
    can_assign_transaction_to_envelope,
    compute_envelope_summary,
    is_valid_envelope_target,
)
from api.services.finance.cashflow import compute_monthly_cashflow
from bot.redis_keys import share_code_key


# ── helpers / fixtures ────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def make_user(db_with_user):
    """Factory for extra (member) users. Cleaned up before db_with_user tears
    down the owner — member rows are user-scoped, so the owner fixture's cleanup
    never touches them."""
    session, _ = db_with_user
    created: list[uuid.UUID] = []

    async def _make(name: str = "Miembro") -> User:
        u = User(
            email=f"share-{uuid.uuid4().hex}@example.com",
            full_name=name,
            shortcut_token=secrets.token_urlsafe(48),
        )
        session.add(u)
        await session.commit()
        await session.refresh(u)
        created.append(u.id)
        return u

    yield _make

    await session.rollback()
    for uid in created:
        await session.execute(
            text("DELETE FROM transactions WHERE user_id = :u"), {"u": uid}
        )
        await session.execute(
            text("DELETE FROM envelope_members WHERE user_id = :u"), {"u": uid}
        )
        await session.execute(
            text("DELETE FROM recurring_incomes WHERE user_id = :u"), {"u": uid}
        )
        await session.execute(
            text("DELETE FROM envelopes WHERE user_id = :u"), {"u": uid}
        )
        await session.execute(text("DELETE FROM users WHERE id = :u"), {"u": uid})
    await session.commit()


def _this_month(day: int = 15) -> date:
    today = date.today()
    return date(today.year, today.month, min(day, 28))


async def _root(
    session,
    owner_id,
    *,
    name: str = "Mercado",
    limit: float = 100000,
    cls: str = "needs",
    currency: str = "CRC",
) -> Envelope:
    env = Envelope(
        user_id=owner_id,
        name=name,
        envelope_class=cls,
        limit_amount=limit,
        currency=currency,
        depth=1,
    )
    session.add(env)
    await session.commit()
    await session.refresh(env)
    return env


async def _child(
    session, owner_id, parent: Envelope, *, name: str = "Carnes", limit: float = 20000
) -> Envelope:
    env = Envelope(
        user_id=owner_id,
        parent_id=parent.id,
        name=name,
        envelope_class=parent.envelope_class,
        limit_amount=limit,
        currency=parent.currency,
        depth=parent.depth + 1,
    )
    session.add(env)
    await session.commit()
    await session.refresh(env)
    return env


async def _expense(session, user_id, envelope_id, amount: float) -> Transaction:
    tx = Transaction(
        user_id=user_id,
        amount=amount,
        currency="CRC",
        transaction_date=_this_month(),
        source="manual",
        status="confirmed",
        archived=False,
        envelope_id=envelope_id,
    )
    session.add(tx)
    await session.commit()
    await session.refresh(tx)
    return tx


# ── mint ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mint_requires_owner_and_root(db_with_user, make_user):
    session, owner_id = db_with_user
    owner = await session.get(User, owner_id)
    redis = get_redis()
    root = await _root(session, owner_id)
    child = await _child(session, owner_id, root)

    code, expires_at = await sharing.mint_share_code(
        session, redis, owner=owner, envelope=root
    )
    assert len(code) == 6
    assert expires_at is not None

    # A sub-sobre cannot be shared (root only).
    with pytest.raises(HTTPException) as exc_child:
        await sharing.mint_share_code(session, redis, owner=owner, envelope=child)
    assert exc_child.value.status_code == 400

    # A non-owner cannot mint.
    other = await make_user("Otro")
    with pytest.raises(HTTPException) as exc_owner:
        await sharing.mint_share_code(session, redis, owner=other, envelope=root)
    assert exc_owner.value.status_code == 403


# ── redeem ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_redeem_creates_membership_idempotent(db_with_user, make_user):
    session, owner_id = db_with_user
    owner = await session.get(User, owner_id)
    redis = get_redis()
    root = await _root(session, owner_id)
    member = await make_user("Ana")

    code, _ = await sharing.mint_share_code(session, redis, owner=owner, envelope=root)
    joined = await sharing.redeem_share_code(session, redis, user=member, code=code)
    assert joined.id == root.id
    assert await sharing.is_member(session, user_id=member.id, root_id=root.id)

    trees = await sharing.fetch_shared_trees(session, user_id=member.id)
    assert len(trees) == 1
    assert trees[0].root.id == root.id
    assert trees[0].owner_name == owner.full_name
    assert trees[0].member_count == 2  # owner + member

    # Idempotent: re-redeeming is a no-op, not a second membership.
    again = await sharing.redeem_share_code(session, redis, user=member, code=code)
    assert again.id == root.id
    count = (
        await session.execute(
            text(
                "SELECT count(*) FROM envelope_members "
                "WHERE envelope_id = :e AND user_id = :u"
            ),
            {"e": str(root.id), "u": str(member.id)},
        )
    ).scalar_one()
    assert count == 1


@pytest.mark.asyncio
async def test_redeem_cap_enforced(db_with_user, make_user):
    session, owner_id = db_with_user
    owner = await session.get(User, owner_id)
    redis = get_redis()
    root = await _root(session, owner_id)
    code, _ = await sharing.mint_share_code(session, redis, owner=owner, envelope=root)

    # 9 invited members fit (≤ 10 incl. the owner).
    for i in range(9):
        m = await make_user(f"M{i}")
        await sharing.redeem_share_code(session, redis, user=m, code=code)

    # The 10th invited overflows.
    overflow = await make_user("Overflow")
    with pytest.raises(HTTPException) as exc:
        await sharing.redeem_share_code(session, redis, user=overflow, code=code)
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_redeem_bad_or_expired_code(db_with_user, make_user):
    session, owner_id = db_with_user
    owner = await session.get(User, owner_id)
    redis = get_redis()
    root = await _root(session, owner_id)
    member = await make_user("Ana")

    with pytest.raises(HTTPException) as exc_bad:
        await sharing.redeem_share_code(session, redis, user=member, code="ZZZZZZ")
    assert exc_bad.value.status_code == 400

    # Simulate TTL expiry by dropping the key after minting.
    code, _ = await sharing.mint_share_code(session, redis, owner=owner, envelope=root)
    await redis.delete(share_code_key(code))
    with pytest.raises(HTTPException) as exc_exp:
        await sharing.redeem_share_code(session, redis, user=member, code=code)
    assert exc_exp.value.status_code == 400


# ── shared-cap math ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_shared_cap_aggregates_and_your_spent(db_with_user, make_user):
    session, owner_id = db_with_user
    owner = await session.get(User, owner_id)
    redis = get_redis()
    root = await _root(session, owner_id, name="Mercado", limit=100000)
    member = await make_user("Ana")
    code, _ = await sharing.mint_share_code(session, redis, owner=owner, envelope=root)
    await sharing.redeem_share_code(session, redis, user=member, code=code)

    await _expense(session, owner_id, root.id, -30000)  # owner spends
    # A member may assign their OWN tx to the shared envelope.
    assert await can_assign_transaction_to_envelope(
        session, user_id=member.id, envelope_id=root.id
    )
    await _expense(session, member.id, root.id, -20000)  # member spends

    # Member view: a shared item with aggregate spend + their own portion.
    msum = await compute_envelope_summary(session, user=member)
    shared = [e for e in msum.envelopes if e.is_shared]
    assert len(shared) == 1
    assert shared[0].id == root.id
    assert shared[0].spent == 50000  # combined (owner 30k + member 20k)
    assert shared[0].your_spent == 20000  # only the member's
    assert shared[0].role == "member"
    assert shared[0].shared_by_name == owner.full_name
    assert shared[0].member_count == 2
    # The joined envelope must NOT leak into the member's OWN totals.
    assert msum.total_limit == 0
    assert msum.total_spent == 0

    # A member with only a shared envelope still has NO own budget → no_budget.
    mcf = await compute_monthly_cashflow(session, user=member)
    assert mcf.has_budget is False

    # Owner view: their own bar drains with the COMBINED spend.
    osum = await compute_envelope_summary(session, user=owner)
    own = {e.id: e for e in osum.envelopes if not e.is_shared}
    assert own[root.id].spent == 50000
    assert osum.total_spent == 50000


# ── permissions ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_member_can_assign_but_not_edit_or_attach(db_with_user, make_user):
    session, owner_id = db_with_user
    owner = await session.get(User, owner_id)
    redis = get_redis()
    root = await _root(session, owner_id)
    member = await make_user("Ana")
    code, _ = await sharing.mint_share_code(session, redis, owner=owner, envelope=root)
    await sharing.redeem_share_code(session, redis, user=member, code=code)

    # CAN assign a transaction.
    assert await can_assign_transaction_to_envelope(
        session, user_id=member.id, envelope_id=root.id
    )
    # CANNOT edit the envelope (owner-only fetch → 404).
    with pytest.raises(HTTPException) as exc:
        await _get_envelope(session, user_id=member.id, envelope_id=root.id)
    assert exc.value.status_code == 404
    # CANNOT attach a bill/debt (attachment target stays owner-only).
    assert (
        await is_valid_envelope_target(
            session, user_id=member.id, envelope_id=root.id
        )
        is False
    )
    # The owner still can do all of the above.
    assert await is_valid_envelope_target(
        session, user_id=owner_id, envelope_id=root.id
    )


# ── remove ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_remove_member_unlinks_and_revokes(db_with_user, make_user):
    session, owner_id = db_with_user
    owner = await session.get(User, owner_id)
    redis = get_redis()
    root = await _root(session, owner_id)
    member = await make_user("Ana")
    code, _ = await sharing.mint_share_code(session, redis, owner=owner, envelope=root)
    await sharing.redeem_share_code(session, redis, user=member, code=code)
    tx = await _expense(session, member.id, root.id, -15000)

    await sharing.remove_member(session, root=root, target_user_id=member.id)

    assert not await sharing.is_member(
        session, user_id=member.id, root_id=root.id
    )
    # The member's tagged tx is unlinked, NOT deleted.
    await session.refresh(tx)
    assert tx.envelope_id is None
    # Access revoked.
    assert await sharing.fetch_shared_trees(session, user_id=member.id) == []
    assert not await can_assign_transaction_to_envelope(
        session, user_id=member.id, envelope_id=root.id
    )


# ── byte-lock ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_byte_lock_member_cashflow_unchanged_by_joining(db_with_user, make_user):
    """A shared envelope a user only JOINED must never enter their own budget
    math: committed_outflows / has_budget / surplus / allocations stay identical
    before and after joining (and after spending on it)."""
    session, owner_id = db_with_user
    owner = await session.get(User, owner_id)
    redis = get_redis()
    member = await make_user("Ana")

    # Member's OWN budget + income so the cashflow has real, non-trivial numbers.
    session.add(
        Envelope(
            user_id=member.id,
            name="Propio",
            envelope_class="needs",
            limit_amount=50000,
            currency="CRC",
            depth=1,
        )
    )
    session.add(
        RecurringIncome(
            user_id=member.id,
            name="Salario",
            income_type="salary",
            amount=300000,
            currency="CRC",
            frequency="monthly",
            next_payment_date=_this_month(),
            is_active=True,
            archived=False,
        )
    )
    await session.commit()

    before = await compute_monthly_cashflow(session, user=member)
    assert before.has_budget is True
    assert before.income_known is True

    # Join a shared envelope with a big limit + spend on it.
    root = await _root(session, owner_id, name="Compartido", limit=999999)
    code, _ = await sharing.mint_share_code(session, redis, owner=owner, envelope=root)
    await sharing.redeem_share_code(session, redis, user=member, code=code)
    await _expense(session, member.id, root.id, -40000)

    after = await compute_monthly_cashflow(session, user=member)

    assert after.committed_outflows == before.committed_outflows
    assert after.envelope_allocations == before.envelope_allocations
    assert after.has_budget == before.has_budget
    assert after.surplus == before.surplus
    assert after.savings_allocations == before.savings_allocations
