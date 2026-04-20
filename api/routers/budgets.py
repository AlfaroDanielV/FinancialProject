import uuid
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from zoneinfo import ZoneInfo

from ..config import settings
from ..database import get_db
from ..models.budget import Budget
from ..models.transaction import Transaction
from ..schemas.budgets import (
    BudgetCreate,
    BudgetResponse,
    BudgetStatus,
    BudgetUpdate,
)

router = APIRouter(prefix="/api/v1/budgets", tags=["budgets"])

TZ = ZoneInfo("America/Costa_Rica")


def _get_default_user_id() -> uuid.UUID:
    if not settings.default_user_id:
        raise HTTPException(
            status_code=503,
            detail="DEFAULT_USER_ID not configured. Run scripts/create_user.py first.",
        )
    return uuid.UUID(settings.default_user_id)


def _current_period_window(period: str, today: date) -> tuple[date, date]:
    """Return (start, end) for the current budget period."""
    if period == "weekly":
        # Week starts Monday
        start = today - timedelta(days=today.weekday())
        end = start + timedelta(days=6)
    else:
        # Monthly: 1st to last day of month
        start = today.replace(day=1)
        if today.month == 12:
            end = date(today.year + 1, 1, 1) - timedelta(days=1)
        else:
            end = date(today.year, today.month + 1, 1) - timedelta(days=1)
    return start, end


@router.post("", response_model=BudgetResponse, status_code=201)
async def create_budget(
    payload: BudgetCreate,
    db: AsyncSession = Depends(get_db),
):
    user_id = _get_default_user_id()
    budget = Budget(
        user_id=user_id,
        category=payload.category,
        amount_limit=payload.amount_limit,
        period=payload.period,
        start_date=payload.start_date or date.today(),
    )
    db.add(budget)
    await db.commit()
    await db.refresh(budget)
    return budget


@router.get("", response_model=list[BudgetResponse])
async def list_budgets(
    db: AsyncSession = Depends(get_db),
):
    user_id = _get_default_user_id()
    result = await db.execute(
        select(Budget)
        .where(Budget.user_id == user_id, Budget.is_active == True)  # noqa: E712
        .order_by(Budget.created_at.desc())
    )
    return list(result.scalars().all())


@router.get("/status", response_model=list[BudgetStatus])
async def budget_status(
    db: AsyncSession = Depends(get_db),
):
    user_id = _get_default_user_id()
    today = date.today()

    result = await db.execute(
        select(Budget).where(
            Budget.user_id == user_id, Budget.is_active == True  # noqa: E712
        )
    )
    budgets = list(result.scalars().all())

    statuses = []
    for b in budgets:
        period_start, period_end = _current_period_window(b.period, today)

        # Sum absolute value of expenses in this category during the period
        spent_result = await db.execute(
            select(func.coalesce(func.sum(func.abs(Transaction.amount)), 0)).where(
                Transaction.user_id == user_id,
                Transaction.category == b.category,
                Transaction.transaction_date >= period_start,
                Transaction.transaction_date <= period_end,
                Transaction.amount < 0,  # only expenses
            )
        )
        spent = float(spent_result.scalar_one())
        limit = float(b.amount_limit)
        remaining = limit - spent
        percent_used = (spent / limit * 100) if limit > 0 else 0

        statuses.append(BudgetStatus(
            id=b.id,
            category=b.category,
            amount_limit=limit,
            spent=round(spent, 2),
            remaining=round(remaining, 2),
            percent_used=round(percent_used, 2),
            period=b.period,
            is_over_budget=spent > limit,
        ))

    return statuses


@router.get("/{budget_id}", response_model=BudgetResponse)
async def get_budget(
    budget_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    user_id = _get_default_user_id()
    result = await db.execute(
        select(Budget).where(Budget.id == budget_id, Budget.user_id == user_id)
    )
    budget = result.scalar_one_or_none()
    if not budget:
        raise HTTPException(status_code=404, detail="Presupuesto no encontrado.")
    return budget


@router.patch("/{budget_id}", response_model=BudgetResponse)
async def update_budget(
    budget_id: uuid.UUID,
    payload: BudgetUpdate,
    db: AsyncSession = Depends(get_db),
):
    user_id = _get_default_user_id()
    result = await db.execute(
        select(Budget).where(Budget.id == budget_id, Budget.user_id == user_id)
    )
    budget = result.scalar_one_or_none()
    if not budget:
        raise HTTPException(status_code=404, detail="Presupuesto no encontrado.")

    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(budget, field, value)

    await db.commit()
    await db.refresh(budget)
    return budget


@router.delete("/{budget_id}", response_model=BudgetResponse)
async def delete_budget(
    budget_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    user_id = _get_default_user_id()
    result = await db.execute(
        select(Budget).where(Budget.id == budget_id, Budget.user_id == user_id)
    )
    budget = result.scalar_one_or_none()
    if not budget:
        raise HTTPException(status_code=404, detail="Presupuesto no encontrado.")

    budget.is_active = False
    await db.commit()
    await db.refresh(budget)
    return budget
