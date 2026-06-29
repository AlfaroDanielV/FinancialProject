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
from sqlalchemy import case, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.account import Account
from ..models.account_anchor import AccountAnchor
from ..models.debt import Debt
from ..models.goal import Goal
from ..models.recurring_bill import RecurringBill
from ..models.transaction import Transaction
from ..models.transfer import Transfer
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
    """Return per-account current and month-start balances (anchor-based).

    Balance is projected forward from the account's latest reconciliation
    anchor (bank reconciliation, not bottom-up reconstruction)::

        current     = anchor.value + Σ(txns transaction_date > effective_date)
        month_start = anchor.value + Σ(effective_date < txn_date < month_start)

    Confirmed, non-archived txns only. The strict `>` is day-level (rule #6): a
    txn dated ON the anchor day is pre-anchor (already in the stated balance).
    An account with NO anchor (created before onboarding wired one) falls back
    to `initial_balance + Σ(all)` — byte-identical to the pre-anchor behavior,
    so nothing changes until a user actually anchors/re-anchors. Transfers are
    NOT excluded — a transfer's two legs move balance between accounts.
    """
    ids = list(account_ids) if account_ids is not None else None
    if ids is not None and not ids:
        return {}

    # Per-account base value: the account's initial_balance (pre-anchor
    # fallback), overridden by its latest anchor's value when one exists.
    base_stmt = select(
        Account.id,
        Account.initial_balance,
    ).where(Account.user_id == user_id)
    if ids is not None:
        base_stmt = base_stmt.where(Account.id.in_(ids))
    base_result = await db.execute(base_stmt)
    base_by_account: dict[uuid.UUID, Decimal] = {
        account_id: Decimal(initial or 0)
        for account_id, initial in base_result.fetchall()
    }
    if not base_by_account:
        return {}
    scope_ids = list(base_by_account.keys())

    # Latest anchor per account — DISTINCT ON (account_id) ordered by the
    # composite index (never a correlated subquery → no N+1). The anchor value
    # overrides the base; effective_date is the exclusive lower bound for sums.
    anchor_stmt = (
        select(
            AccountAnchor.account_id,
            AccountAnchor.value,
            AccountAnchor.effective_date,
        )
        .where(AccountAnchor.account_id.in_(scope_ids))
        .order_by(
            AccountAnchor.account_id,
            AccountAnchor.effective_date.desc(),
            AccountAnchor.created_at.desc(),
        )
        .distinct(AccountAnchor.account_id)
    )
    for account_id, value, _eff in (await db.execute(anchor_stmt)).all():
        base_by_account[account_id] = Decimal(value)

    # Post-anchor sums. The per-account lower bound comes from the latest-anchor
    # subquery join; an account with no anchor (eff IS NULL) includes every row.
    latest_anchor = anchor_stmt.subquery()
    eff = latest_anchor.c.effective_date
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
        .select_from(Transaction)
        .outerjoin(
            latest_anchor, latest_anchor.c.account_id == Transaction.account_id
        )
        .where(
            Transaction.user_id == user_id,
            Transaction.account_id.in_(scope_ids),
            Transaction.status == "confirmed",
            Transaction.archived.is_(False),
            or_(eff.is_(None), Transaction.transaction_date > eff),
        )
        .group_by(Transaction.account_id)
    )
    sums_result = await db.execute(sums_stmt)
    sums_by_account: dict[uuid.UUID, tuple[Decimal, Decimal]] = {
        account_id: (Decimal(total or 0), Decimal(before or 0))
        for account_id, total, before in sums_result.fetchall()
    }

    balances: dict[uuid.UUID, AccountBalances] = {}
    for account_id, base in base_by_account.items():
        total, before_month = sums_by_account.get(
            account_id, (Decimal("0"), Decimal("0"))
        )
        balances[account_id] = AccountBalances(
            current=base + total,
            month_start=base + before_month,
        )
    return balances


async def balance_as_of(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    account_id: uuid.UUID,
    as_of: date,
) -> Decimal:
    """The account balance as of the end of `as_of` (inclusive, day-level).

    Same anchor model as `compute_account_balances` but with an upper bound:

        balance(as_of) = base + Σ(txns with eff < transaction_date <= as_of)

    where `base` is the value of the latest anchor whose `effective_date <=
    as_of` (an anchor dated AFTER `as_of` is a later correction and is ignored
    for a past as-of), falling back to `initial_balance` when none applies. The
    `> eff` lower bound stays strict (rule #6); the `<= as_of` upper bound is
    inclusive (a txn dated ON the corte is part of that statement).

    Used to derive a credit card's statement (corte) balance live — when the
    user reconciled the statement, the anchor sits exactly on the corte, so this
    returns `anchor.value` (= the exact statement balance) with no drift.
    """
    base_row = (
        await db.execute(
            select(Account.initial_balance).where(
                Account.user_id == user_id, Account.id == account_id
            )
        )
    ).first()
    if base_row is None:
        return Decimal("0")
    base = Decimal(base_row[0] or 0)

    anchor = (
        await db.execute(
            select(AccountAnchor.value, AccountAnchor.effective_date)
            .where(
                AccountAnchor.account_id == account_id,
                AccountAnchor.effective_date <= as_of,
            )
            .order_by(
                AccountAnchor.effective_date.desc(),
                AccountAnchor.created_at.desc(),
            )
            .limit(1)
        )
    ).first()
    eff: date | None = None
    if anchor is not None:
        base = Decimal(anchor[0])
        eff = anchor[1]

    conditions = [
        Transaction.user_id == user_id,
        Transaction.account_id == account_id,
        Transaction.status == "confirmed",
        Transaction.archived.is_(False),
        Transaction.transaction_date <= as_of,
    ]
    if eff is not None:
        conditions.append(Transaction.transaction_date > eff)
    total = (
        await db.execute(
            select(func.coalesce(func.sum(Transaction.amount), 0)).where(
                *conditions
            )
        )
    ).scalar_one()
    return base + Decimal(total or 0)


# ── Phase 7b B2: TRUE hard delete with cascade ────────────────────────────────


@dataclass(frozen=True)
class AccountDeleteImpact:
    """What a hard delete would remove / detach. Shown to the user BEFORE
    confirming (every financial action must be auditable)."""

    transactions: int
    transfers: int
    debts_detached: int
    bills_detached: int
    goals_detached: int


async def compute_delete_impact(
    db: AsyncSession, *, user_id: uuid.UUID, account_id: uuid.UUID
) -> AccountDeleteImpact:
    async def _count(stmt) -> int:
        return int((await db.execute(stmt)).scalar_one() or 0)

    return AccountDeleteImpact(
        transactions=await _count(
            select(func.count()).where(
                Transaction.user_id == user_id,
                Transaction.account_id == account_id,
            )
        ),
        transfers=await _count(
            select(func.count()).where(
                Transfer.user_id == user_id,
                or_(
                    Transfer.from_account_id == account_id,
                    Transfer.to_account_id == account_id,
                ),
            )
        ),
        debts_detached=await _count(
            select(func.count()).where(
                Debt.user_id == user_id, Debt.account_id == account_id
            )
        ),
        bills_detached=await _count(
            select(func.count()).where(
                RecurringBill.user_id == user_id,
                RecurringBill.account_id == account_id,
            )
        ),
        goals_detached=await _count(
            select(func.count()).where(
                Goal.user_id == user_id, Goal.linked_account_id == account_id
            )
        ),
    )


async def hard_delete_account(
    db: AsyncSession, *, user_id: uuid.UUID, account: Account
) -> AccountDeleteImpact:
    """Permanently delete an account and ALL its transactions (Phase 7b B2).

    Cascade contract (decision note: Account Hard Delete With Cascade):
    - The account's transactions are DELETED. Their referrers survive with the
      link nulled (debt_payments/bill_occurrences via migration 0028;
      goal_contributions/gmail_messages_seen were already SET NULL).
    - Debts, recurring bills, and goals linked to the account are DETACHED,
      never deleted.
    - Transfers touching the account are deleted; the surviving leg in the
      OTHER account keeps its amount (that account's balance history must not
      change), loses its transfer_id via the FK, and is annotated so the user
      can tell why it became a plain row.

    One DB transaction; the caller commits. Returns the impact counts (also
    used by the delete-impact preview endpoint).
    """
    impact = await compute_delete_impact(
        db, user_id=user_id, account_id=account.id
    )

    # 1. Annotate the surviving leg of every transfer that touches this
    #    account BEFORE deleting the transfer rows (afterwards transfer_id is
    #    NULL and they're indistinguishable from plain transactions).
    transfer_ids = [
        row
        for row in (
            await db.execute(
                select(Transfer.id).where(
                    Transfer.user_id == user_id,
                    or_(
                        Transfer.from_account_id == account.id,
                        Transfer.to_account_id == account.id,
                    ),
                )
            )
        ).scalars()
    ]
    if transfer_ids:
        note = f" (transferencia — cuenta «{account.name}» eliminada)"
        await db.execute(
            update(Transaction)
            .where(
                Transaction.transfer_id.in_(transfer_ids),
                Transaction.account_id != account.id,
            )
            .values(
                description=func.coalesce(Transaction.description, "") + note
            )
        )
        # 2. Delete the transfer rows → surviving legs' transfer_id nulls via
        #    the FK (transactions.transfer_id ON DELETE SET NULL).
        await db.execute(delete(Transfer).where(Transfer.id.in_(transfer_ids)))

    # 3. Detach obligations + goals (their FKs are NO ACTION / app-managed).
    await db.execute(
        update(Debt)
        .where(Debt.user_id == user_id, Debt.account_id == account.id)
        .values(account_id=None)
    )
    await db.execute(
        update(RecurringBill)
        .where(
            RecurringBill.user_id == user_id,
            RecurringBill.account_id == account.id,
        )
        .values(account_id=None)
    )
    await db.execute(
        update(Goal)
        .where(Goal.user_id == user_id, Goal.linked_account_id == account.id)
        .values(linked_account_id=None)
    )

    # 4. Delete the account's transactions (incl. its own former transfer
    #    legs, whose transfer_id is now NULL).
    await db.execute(
        delete(Transaction).where(
            Transaction.user_id == user_id,
            Transaction.account_id == account.id,
        )
    )

    # 5. Delete the account. lazy_detection_events.matched_account_id is
    #    SET NULL at the DB level; credit_card_terms (Phase 7b B3) CASCADEs.
    await db.delete(account)
    return impact
