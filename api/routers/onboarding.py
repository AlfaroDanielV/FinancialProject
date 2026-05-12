"""Phase 6d B2: onboarding status + categories.

`GET /onboarding/status` is the single source of truth for the SPA and bot
to decide what to show next. `completeness_score` is a simple
4-quarters-active fraction; the bot uses it for the /start branching
logic in B10.
"""
from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..data.categories_cr import CATEGORIES_CR
from ..database import get_db
from ..dependencies import current_user
from ..models.account import Account
from ..models.debt import Debt
from ..models.recurring_bill import RecurringBill
from ..models.recurring_income import RecurringIncome
from ..models.user import User
from ..schemas.onboarding import CategoriesResponse, OnboardingStatus

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

    return OnboardingStatus(
        has_accounts=accounts > 0,
        has_incomes=incomes > 0,
        has_debts=debts > 0,
        has_recurring_bills=bills > 0,
        accounts_count=accounts,
        incomes_count=incomes,
        debts_count=debts,
        recurring_bills_count=bills,
        completeness_score=round(score, 2),
    )


@router.get("/onboarding/categories", response_model=CategoriesResponse)
@router.get("/categories", response_model=CategoriesResponse, include_in_schema=False)
async def list_categories(_user: User = Depends(current_user)):
    """Default CR-Spanish categories for SPA dropdowns and bot suggestions.

    `transactions.category` is free-form text (CLAUDE.md decision). This
    list is what we *suggest*, not what we enforce.
    """
    return CategoriesResponse(categories=CATEGORIES_CR)
