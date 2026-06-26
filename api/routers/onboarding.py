"""Phase 6d B2: onboarding status + categories.

`GET /onboarding/status` is the single source of truth for the app and bot
to decide what to show next.

Phase 8 B2: activation is the new gate. `is_activated` (1 account + a real
balance + 1 expense) tells the first-run UX whether the user is ready for
daily use. `completeness_score` (the legacy 4-quarters-active fraction) is
kept because other consumers still read it.
"""
from fastapi import APIRouter, Depends
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..data.categories_cr import CATEGORIES_CR
from ..database import get_db
from ..dependencies import current_user
from ..models.account import Account
from ..models.account_anchor import AccountAnchor
from ..models.debt import Debt
from ..models.recurring_bill import RecurringBill
from ..models.recurring_income import RecurringIncome
from ..models.transaction import Transaction
from ..models.user import User
from ..schemas.onboarding import CategoriesResponse, OnboardingStatus
from ..services.anchors import AJUSTE_CATEGORY

router = APIRouter(prefix="/api/v1", tags=["onboarding"])


async def _active_count(db: AsyncSession, model, user_id) -> int:
    conditions = [model.user_id == user_id, model.is_active == True]  # noqa: E712
    if model is Account:
        conditions.append(Account.archived.is_(False))
    result = await db.execute(
        select(func.count())
        .select_from(model)
        .where(*conditions)
    )
    return int(result.scalar_one())


@router.get("/onboarding/status", response_model=OnboardingStatus)
async def onboarding_status(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    accounts = await _active_count(db, Account, user.id)
    incomes = await _active_count(db, RecurringIncome, user.id)
    debts = await _active_count(db, Debt, user.id)
    bills = await _active_count(db, RecurringBill, user.id)

    quarters_present = sum(
        1 for c in (accounts, incomes, debts, bills) if c > 0
    )
    score = quarters_present / 4.0

    # Phase 8 B2 — activation signals.
    has_balance = await _has_balance(db, user.id)
    has_expense = await _has_expense(db, user.id)
    has_accounts = accounts > 0

    return OnboardingStatus(
        has_accounts=has_accounts,
        has_incomes=incomes > 0,
        has_debts=debts > 0,
        has_recurring_bills=bills > 0,
        accounts_count=accounts,
        incomes_count=incomes,
        debts_count=debts,
        recurring_bills_count=bills,
        completeness_score=round(score, 2),
        is_activated=has_accounts and has_balance and has_expense,
        has_balance=has_balance,
        has_expense=has_expense,
    )


async def _has_balance(db: AsyncSession, user_id) -> bool:
    """∃ an active, non-archived account whose balance is anchored — either it
    started with a positive `initial_balance` or it has a reconciliation anchor
    row. Matches the account filter `compute_account_balances` uses."""
    anchor_exists = (
        select(AccountAnchor.id)
        .where(AccountAnchor.account_id == Account.id)
        .exists()
    )
    stmt = (
        select(Account.id)
        .where(
            Account.user_id == user_id,
            Account.is_active == True,  # noqa: E712
            Account.archived.is_(False),
            or_(Account.initial_balance > 0, anchor_exists),
        )
        .limit(1)
    )
    return (await db.execute(stmt)).first() is not None


async def _has_expense(db: AsyncSession, user_id) -> bool:
    """∃ a real expense — mirrors the dashboard expense filter (confirmed,
    non-archived, not a transfer leg / goal flow / reconciliation ajuste)."""
    stmt = (
        select(Transaction.id)
        .where(
            Transaction.user_id == user_id,
            Transaction.amount < 0,
            Transaction.status == "confirmed",
            Transaction.archived.is_(False),
            Transaction.transfer_id.is_(None),
            Transaction.goal_id.is_(None),
            Transaction.category.is_distinct_from(AJUSTE_CATEGORY),
        )
        .limit(1)
    )
    return (await db.execute(stmt)).first() is not None


@router.get("/onboarding/categories", response_model=CategoriesResponse)
@router.get("/categories", response_model=CategoriesResponse, include_in_schema=False)
async def list_categories(_user: User = Depends(current_user)):
    """Default CR-Spanish categories for SPA dropdowns and bot suggestions.

    `transactions.category` is free-form text (CLAUDE.md decision). This
    list is what we *suggest*, not what we enforce.
    """
    return CategoriesResponse(categories=CATEGORIES_CR)
