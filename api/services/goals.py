"""Phase 7d — goal funding from accounts.

An aporte is a real money movement: it comes FROM an account (validated
against the live balance) and creates a goal-marked negative transaction.
Cancelling a goal refunds the un-refunded sourced contributions back to the
accounts they came from; marking achieved never refunds (the money was used
on the goal's purpose). See vault `Decision - Goal Funding From Accounts`.

Mirror of `api/services/transfers.py`: the router stays thin, the logic is
reusable, and the caller commits.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.account import Account
from ..models.goal import Goal
from ..models.goal_contribution import GoalContribution
from ..models.transaction import Transaction
from ..schemas.goals import (
    GoalCancelPreview,
    GoalContributionCreate,
    GoalRefundItem,
)
from .accounts import compute_account_balances


def _fmt_amount(amount: Decimal, currency: str) -> str:
    if currency == "CRC":
        return "₡" + f"{int(amount):,}".replace(",", ".")
    if currency == "USD":
        return f"${amount:,.2f}"
    return f"{amount} {currency}"


async def create_funded_contribution(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    goal: Goal,
    payload: GoalContributionCreate,
) -> tuple[GoalContribution, Transaction]:
    """Validate the source account + funds, create the goal-marked negative
    transaction and the contribution row, bump current_amount. NO
    auto-achieve — cumplida is always an explicit, confirmed user action
    (refunds are at stake). Caller commits."""
    account = (
        await db.execute(
            select(Account).where(
                Account.id == payload.account_id,
                Account.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if account is None or account.archived:
        raise HTTPException(status_code=400, detail="Cuenta inválida.")
    if account.account_type == "credit":
        raise HTTPException(
            status_code=400,
            detail=(
                "No podés ahorrar desde una tarjeta de crédito. Elegí una "
                "cuenta de fondos."
            ),
        )
    if account.currency != goal.target_currency:
        raise HTTPException(
            status_code=400,
            detail=(
                "La cuenta y la meta tienen monedas distintas. Elegí una "
                f"cuenta en {goal.target_currency}."
            ),
        )

    balances = await compute_account_balances(
        db, user_id=user_id, account_ids=[account.id]
    )
    available = balances.get(account.id)
    current_balance = available.current if available else Decimal("0")
    amount = Decimal(payload.amount)
    if amount > current_balance:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Fondos insuficientes en «{account.name}». Tenés "
                f"{_fmt_amount(current_balance, account.currency)} disponibles."
            ),
        )

    occurred_at = payload.occurred_at or datetime.now(timezone.utc)
    txn = Transaction(
        user_id=user_id,
        account_id=account.id,
        goal_id=goal.id,
        amount=-amount,
        currency=account.currency,
        description=f"Aporte a meta «{goal.name}»",
        category="ahorro",
        transaction_date=occurred_at.date(),
        source="manual",
    )
    db.add(txn)
    await db.flush()

    contribution = GoalContribution(
        goal_id=goal.id,
        transaction_id=txn.id,
        source_account_id=account.id,
        amount=amount,
        occurred_at=occurred_at,
    )
    db.add(contribution)
    goal.current_amount = Decimal(goal.current_amount or 0) + amount
    await db.flush()
    return contribution, txn


@dataclass(frozen=True)
class _RefundGroup:
    account: Optional[Account]
    amount: Decimal
    contributions: list[GoalContribution]


async def _refund_groups(
    db: AsyncSession, *, user_id: uuid.UUID, goal: Goal
) -> tuple[list[_RefundGroup], Decimal]:
    """Un-refunded contributions grouped by source account, plus the
    unrefundable total (sourceless legacy rows / hard-deleted accounts)."""
    rows = (
        await db.execute(
            select(GoalContribution)
            .where(
                GoalContribution.goal_id == goal.id,
                GoalContribution.refund_transaction_id.is_(None),
            )
            .order_by(GoalContribution.occurred_at.asc())
        )
    ).scalars().all()

    by_account: dict[uuid.UUID, list[GoalContribution]] = {}
    unrefundable = Decimal("0")
    for row in rows:
        if row.source_account_id is None:
            unrefundable += Decimal(str(row.amount))
            continue
        by_account.setdefault(row.source_account_id, []).append(row)

    groups: list[_RefundGroup] = []
    if by_account:
        accounts = {
            acc.id: acc
            for acc in (
                await db.execute(
                    select(Account).where(
                        Account.id.in_(by_account.keys()),
                        Account.user_id == user_id,
                    )
                )
            ).scalars()
        }
        for account_id, contribs in by_account.items():
            total = sum(
                (Decimal(str(c.amount)) for c in contribs), Decimal("0")
            )
            groups.append(
                _RefundGroup(
                    account=accounts.get(account_id),
                    amount=total,
                    contributions=contribs,
                )
            )
    return groups, unrefundable


async def compute_cancel_preview(
    db: AsyncSession, *, user_id: uuid.UUID, goal: Goal
) -> GoalCancelPreview:
    groups, unrefundable = await _refund_groups(db, user_id=user_id, goal=goal)
    refunds = [
        GoalRefundItem(
            account_id=g.account.id if g.account else None,
            account_name=g.account.name if g.account else None,
            account_archived=bool(g.account.archived) if g.account else False,
            amount=g.amount,
            contribution_count=len(g.contributions),
        )
        for g in groups
    ]
    return GoalCancelPreview(
        goal_id=goal.id,
        currency=goal.target_currency,
        refundable_total=sum((g.amount for g in groups), Decimal("0")),
        unrefundable_total=unrefundable,
        refunds=refunds,
    )


async def cancel_goal_with_refunds(
    db: AsyncSession, *, user_id: uuid.UUID, goal: Goal
) -> tuple[list[GoalRefundItem], Decimal]:
    """Refund every un-refunded sourced contribution (ONE positive refund
    transaction per source account — archived accounts still receive: the
    money must go home), stamp refund_transaction_id, clamp current_amount,
    mark abandoned. Idempotent: re-running refunds nothing twice. Caller
    commits."""
    groups, unrefundable = await _refund_groups(db, user_id=user_id, goal=goal)

    refunded_total = Decimal("0")
    items: list[GoalRefundItem] = []
    today = date.today()
    for group in groups:
        if group.account is None:
            unrefundable += group.amount
            continue
        refund_txn = Transaction(
            user_id=user_id,
            account_id=group.account.id,
            goal_id=goal.id,
            amount=group.amount,
            currency=group.account.currency,
            description=f"Devolución de meta «{goal.name}»",
            category="ahorro",
            transaction_date=today,
            source="manual",
        )
        db.add(refund_txn)
        await db.flush()
        for contribution in group.contributions:
            contribution.refund_transaction_id = refund_txn.id
        refunded_total += group.amount
        items.append(
            GoalRefundItem(
                account_id=group.account.id,
                account_name=group.account.name,
                account_archived=bool(group.account.archived),
                amount=group.amount,
                contribution_count=len(group.contributions),
            )
        )

    goal.current_amount = max(
        Decimal("0"), Decimal(goal.current_amount or 0) - refunded_total
    )
    goal.status = "abandoned"
    await db.flush()
    return items, unrefundable


async def has_unrefunded_sourced_contributions(
    db: AsyncSession, *, goal_id: uuid.UUID
) -> bool:
    """True while the goal still holds money that a cancel would return —
    the guard that blocks a hard delete."""
    row = await db.execute(
        select(GoalContribution.id)
        .where(
            GoalContribution.goal_id == goal_id,
            GoalContribution.source_account_id.is_not(None),
            GoalContribution.refund_transaction_id.is_(None),
        )
        .limit(1)
    )
    return row.scalar_one_or_none() is not None
