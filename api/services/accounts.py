"""Account lookup helpers used by the Telegram dispatcher.

Surgical extraction — only `resolve_account` is needed by Phase 5b. The
existing REST router continues to own CRUD logic; nothing in it was
refactored as part of this extraction.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable, Optional

from rapidfuzz import fuzz, utils
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.account import Account
from ..models.transaction import Transaction
from ..models.user import User

# Above this ratio we consider a fuzzy name a confident hit. Below, the
# dispatcher should ask rather than guess. 80 on rapidfuzz's WRatio is
# strict enough to distinguish "BAC" from "BCR" while still matching
# "bac credomatic" → "BAC Credomatic".
_FUZZY_THRESHOLD = 80


async def list_active(user: User, db: AsyncSession) -> list[Account]:
    result = await db.execute(
        select(Account)
        .where(Account.user_id == user.id, Account.is_active.is_(True))
        .order_by(Account.created_at.asc())
    )
    return list(result.scalars().all())


async def resolve_account(
    user: User, hint: Optional[str], db: AsyncSession
) -> Optional[Account]:
    """Pick an account for a bot-logged transaction.

    Rules:
    1. User has zero active accounts → None (caller commits without account).
    2. User has one active account → that one, regardless of hint.
    3. User has many → best fuzzy match on `hint` if the top score clears
       the threshold AND beats the runner-up by a margin. Otherwise None
       so the dispatcher can ask.

    The caller — not this function — decides how to handle None.
    """
    accounts = await list_active(user, db)
    if not accounts:
        return None
    if len(accounts) == 1:
        return accounts[0]
    if not hint:
        return None

    scored = [
        (fuzz.WRatio(hint, acc.name, processor=utils.default_process), acc)
        for acc in accounts
    ]
    scored.sort(key=lambda pair: pair[0], reverse=True)
    top_score, top = scored[0]
    if top_score < _FUZZY_THRESHOLD:
        return None
    # Refuse when the top two are effectively tied — ambiguous.
    if len(scored) > 1 and (top_score - scored[1][0]) < 10:
        return None
    return top


@dataclass(frozen=True)
class AccountBalances:
    current: Decimal
    month_start: Decimal


def _month_start(today: date | None = None) -> date:
    today = today or date.today()
    return date(today.year, today.month, 1)


async def compute_account_balances(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    account_ids: Optional[Iterable[uuid.UUID]] = None,
    today: date | None = None,
) -> dict[uuid.UUID, AccountBalances]:
    """Return per-account current and month-start balances.

    Both balances reuse the same convention as the dashboard:
    `initial_balance + Σ confirmed transactions` for the user, scoped per
    account via `transactions.account_id`. Transfers are NOT excluded — a
    transfer's two linked transactions move balance between accounts and
    must net out to zero across the user.
    """
    ids = list(account_ids) if account_ids is not None else None
    if ids is not None and not ids:
        return {}

    base_stmt = select(
        Account.id,
        Account.initial_balance,
    ).where(Account.user_id == user_id)
    if ids is not None:
        base_stmt = base_stmt.where(Account.id.in_(ids))
    base_result = await db.execute(base_stmt)
    initial_by_account: dict[uuid.UUID, Decimal] = {
        account_id: Decimal(initial or 0)
        for account_id, initial in base_result.fetchall()
    }
    if not initial_by_account:
        return {}

    month_start = _month_start(today)
    sums_stmt = (
        select(
            Transaction.account_id,
            func.coalesce(func.sum(Transaction.amount), 0).label("total"),
            func.coalesce(
                func.sum(
                    case(
                        (
                            (Transaction.transaction_date < month_start),
                            Transaction.amount,
                        ),
                        else_=0,
                    )
                ),
                0,
            ).label("before_month"),
        )
        .where(
            Transaction.user_id == user_id,
            Transaction.account_id.in_(initial_by_account.keys()),
            Transaction.status == "confirmed",
            Transaction.archived.is_(False),
        )
        .group_by(Transaction.account_id)
    )
    sums_result = await db.execute(sums_stmt)
    sums_by_account: dict[uuid.UUID, tuple[Decimal, Decimal]] = {
        account_id: (Decimal(total or 0), Decimal(before or 0))
        for account_id, total, before in sums_result.fetchall()
    }

    balances: dict[uuid.UUID, AccountBalances] = {}
    for account_id, initial in initial_by_account.items():
        total, before_month = sums_by_account.get(
            account_id, (Decimal("0"), Decimal("0"))
        )
        balances[account_id] = AccountBalances(
            current=initial + total,
            month_start=initial + before_month,
        )
    return balances
