"""Account lookup helpers used by the Telegram dispatcher.

Surgical extraction — only `resolve_account` is needed by Phase 5b. The
existing REST router continues to own CRUD logic; nothing in it was
refactored as part of this extraction.
"""
from __future__ import annotations

from typing import Optional

from rapidfuzz import fuzz, utils
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.account import Account
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
