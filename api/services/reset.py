"""«Empezar de cero» — two reset flows for a user's own data.

A. `reset_balances` (non-destructive): state each account's real balance today.
   Reuses `anchors.apply_anchor` per account (append-only: one anchor + one
   labeled "ajuste de reconciliación" per account), so history is preserved and
   the projected balance becomes the stated value in one step. Zero new balance
   math — this is the existing reconciliation machinery applied in a batch.

B. `wipe_user_history` (destructive): delete the user's movement history and the
   records derived from it, then reset the parent progress fields — WITHOUT
   touching config (accounts / debts / bills / goals / envelopes / incomes /
   categories) or Gmail dedup memory (so old emails don't re-import). The client
   then routes into the Option A balance form to set fresh starting balances.

Both leave the commit to the caller (the router) so each flow is one transaction.
"""
from __future__ import annotations

import uuid
from collections.abc import Sequence
from decimal import Decimal

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.account import Account
from ..models.account_anchor import AccountAnchor
from ..models.bill_occurrence import BillOccurrence
from ..models.debt import Debt, DebtPayment
from ..models.goal import Goal
from ..models.goal_contribution import GoalContribution
from ..models.transaction import Transaction
from ..models.transfer import Transfer
from ..models.user import User
from .anchors import AnchorResult, apply_anchor


class ResetError(Exception):
    """A reset input was invalid (e.g. an unknown/foreign/archived account)."""


async def reset_balances(
    db: AsyncSession,
    *,
    user: User,
    items: Sequence[tuple[uuid.UUID, Decimal]],
) -> list[AnchorResult]:
    """Re-anchor each listed account to its stated real balance (Option A).

    Every item is validated (owned + active) before any write, so the batch is
    all-or-nothing. Caller commits.
    """
    results: list[AnchorResult] = []
    for account_id, value in items:
        account = (
            await db.execute(
                select(Account).where(
                    Account.id == account_id, Account.user_id == user.id
                )
            )
        ).scalar_one_or_none()
        if account is None:
            raise ResetError("Cuenta no encontrada.")
        if account.archived:
            raise ResetError(
                f"No se puede reanclar una cuenta archivada: {account.name}."
            )
        results.append(
            await apply_anchor(
                db,
                user=user,
                account=account,
                value=Decimal(value),
                source="reanchor",
                write_ajuste=True,
            )
        )
    return results


async def wipe_user_history(db: AsyncSession, *, user: User) -> dict[str, int]:
    """Destructive reset (Option B): delete movements + derived records + anchors;
    reset debt/goal progress. Keeps config + Gmail dedup memory. Caller commits.

    FK-safe order: children of `transactions` (debt_payments, goal_contributions)
    go first, then bill occurrences are un-linked and reset to pending, then the
    transactions themselves, then transfers (whose legs were just deleted), then
    the account anchors. Debt balances and goal progress are restored last.
    """
    uid = user.id
    counts: dict[str, int] = {}

    # 1. debt_payments (scoped via the parent debt; no user_id column).
    debt_ids = select(Debt.id).where(Debt.user_id == uid)
    counts["debt_payments"] = (
        await db.execute(
            delete(DebtPayment).where(DebtPayment.debt_id.in_(debt_ids))
        )
    ).rowcount or 0

    # 2. goal_contributions (scoped via the parent goal; no user_id column).
    goal_ids = select(Goal.id).where(Goal.user_id == uid)
    counts["goal_contributions"] = (
        await db.execute(
            delete(GoalContribution).where(GoalContribution.goal_id.in_(goal_ids))
        )
    ).rowcount or 0

    # 3. un-link + reset every bill occurrence to pending (a paid occurrence with
    #    no backing movement would be inconsistent).
    counts["bill_occurrences_reset"] = (
        await db.execute(
            update(BillOccurrence)
            .where(BillOccurrence.user_id == uid)
            .values(
                transaction_id=None,
                amount_paid=None,
                paid_at=None,
                status="pending",
            )
        )
    ).rowcount or 0

    # 4. the movements themselves (transfer legs, goal legs, ajustes, everything).
    counts["transactions"] = (
        await db.execute(
            delete(Transaction).where(Transaction.user_id == uid)
        )
    ).rowcount or 0

    # 5. transfers (their legs are gone; the row is now empty).
    counts["transfers"] = (
        await db.execute(delete(Transfer).where(Transfer.user_id == uid))
    ).rowcount or 0

    # 6. balance anchors — fresh baseline (the Option A form writes new ones).
    counts["account_anchors"] = (
        await db.execute(
            delete(AccountAnchor).where(AccountAnchor.user_id == uid)
        )
    ).rowcount or 0

    # 7. restore debt balances + goal progress to their starting point.
    counts["debts_reset"] = (
        await db.execute(
            update(Debt)
            .where(Debt.user_id == uid)
            .values(current_balance=Debt.original_amount, payments_made=0)
        )
    ).rowcount or 0
    counts["goals_reset"] = (
        await db.execute(
            update(Goal).where(Goal.user_id == uid).values(current_amount=0)
        )
    ).rowcount or 0

    await db.flush()
    return counts
