"""Phase 6d B2: recurring incomes CRUD with CR-cycle derivation.

POST behavior for derived types (aguinaldo / salario_escolar):
    - `base_salary_link_id` MUST point to an active salary income belonging
      to the same user.
    - `amount` is computed server-side via `derive_amount_for` and ignored
      if provided in the payload (the user can't override CR semantics).

PATCH does NOT recompute derived amounts — if the user changes the base
salary, they have to re-create the derived row, matching the CASCADE
delete semantics from Resolución 9.8.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..dependencies import current_user
from ..models.recurring_income import RecurringIncome
from ..models.user import User
from ..schemas.recurring_incomes import (
    DERIVED_TYPES,
    RecurringIncomeCreate,
    RecurringIncomeResponse,
    RecurringIncomeUpdate,
)
from ..services.finance.incomes import derive_amount_for

router = APIRouter(prefix="/api/v1/recurring-incomes", tags=["recurring-incomes"])


@router.post("", response_model=RecurringIncomeResponse, status_code=201)
async def create_recurring_income(
    payload: RecurringIncomeCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    amount = payload.amount

    if payload.income_type in DERIVED_TYPES:
        result = await db.execute(
            select(RecurringIncome).where(
                RecurringIncome.id == payload.base_salary_link_id,
                RecurringIncome.user_id == user.id,
            )
        )
        base = result.scalar_one_or_none()
        if base is None:
            raise HTTPException(
                status_code=404,
                detail="Salario base no encontrado. Registralo primero.",
            )
        if base.income_type != "salary":
            raise HTTPException(
                status_code=400,
                detail=(
                    "base_salary_link_id debe apuntar a un income_type='salary'."
                ),
            )
        if base.amount is None:
            raise HTTPException(
                status_code=400,
                detail="El salario base no tiene un monto registrado.",
            )
        amount = derive_amount_for(payload.income_type, base.amount)

    income = RecurringIncome(
        user_id=user.id,
        name=payload.name,
        income_type=payload.income_type,
        amount=amount,
        currency=payload.currency,
        frequency=payload.frequency,
        next_payment_date=payload.next_payment_date,
        base_salary_link_id=payload.base_salary_link_id,
        notes=payload.notes,
    )
    db.add(income)
    await db.commit()
    await db.refresh(income)
    return income


@router.get("", response_model=list[RecurringIncomeResponse])
async def list_recurring_incomes(
    include_inactive: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    stmt = select(RecurringIncome).where(RecurringIncome.user_id == user.id)
    if not include_inactive:
        stmt = stmt.where(RecurringIncome.is_active == True)  # noqa: E712
    stmt = stmt.order_by(RecurringIncome.next_payment_date)
    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.get("/{income_id}", response_model=RecurringIncomeResponse)
async def get_recurring_income(
    income_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    result = await db.execute(
        select(RecurringIncome).where(
            RecurringIncome.id == income_id,
            RecurringIncome.user_id == user.id,
        )
    )
    income = result.scalar_one_or_none()
    if income is None:
        raise HTTPException(status_code=404, detail="Ingreso no encontrado.")
    return income


@router.patch("/{income_id}", response_model=RecurringIncomeResponse)
async def update_recurring_income(
    income_id: uuid.UUID,
    payload: RecurringIncomeUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    result = await db.execute(
        select(RecurringIncome).where(
            RecurringIncome.id == income_id,
            RecurringIncome.user_id == user.id,
        )
    )
    income = result.scalar_one_or_none()
    if income is None:
        raise HTTPException(status_code=404, detail="Ingreso no encontrado.")

    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(income, field, value)

    await db.commit()
    await db.refresh(income)
    return income


@router.delete("/{income_id}", response_model=RecurringIncomeResponse)
async def delete_recurring_income(
    income_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    result = await db.execute(
        select(RecurringIncome).where(
            RecurringIncome.id == income_id,
            RecurringIncome.user_id == user.id,
        )
    )
    income = result.scalar_one_or_none()
    if income is None:
        raise HTTPException(status_code=404, detail="Ingreso no encontrado.")

    income.is_active = False
    await db.commit()
    await db.refresh(income)
    return income
