"""Phase 6d B2 + Phase 6e B8: recurring incomes CRUD with CR-cycle derivation.

POST behavior for derived types (aguinaldo / salario_escolar):
    - `base_salary_link_id` MUST point to an active salary income belonging
      to the same user.
    - `amount` is computed server-side via `derive_amount_for` and ignored
      if provided in the payload (the user can't override CR semantics).

PATCH does NOT recompute derived amounts — if the user changes the base
salary, they have to re-create the derived row, matching the CASCADE
delete semantics from Resolución 9.8.

Phase 6e B8 adds:
    - `archived` column for the pause/archive split (paused = visible with
      badge + excluded from active totals; archived = hidden by default).
    - `POST /{salary_id}/derive-cycles` — atomic creation of both aguinaldo
      and salario_escolar from a single salary. Idempotent.
"""
import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..dependencies import current_user
from ..models.recurring_income import RecurringIncome
from ..models.user import User
from ..schemas.recurring_incomes import (
    CRCycleDeriveResponse,
    DERIVED_TYPES,
    RecurringIncomeCreate,
    RecurringIncomeResponse,
    RecurringIncomeUpdate,
)
from ..services.finance.incomes import derive_amount_for

router = APIRouter(prefix="/api/v1/recurring-incomes", tags=["recurring-incomes"])


def _next_aguinaldo_date(today: date | None = None) -> date:
    """Aguinaldo is paid mid-December; pick the upcoming Dec 15."""
    today = today or date.today()
    candidate = date(today.year, 12, 15)
    if candidate < today:
        candidate = date(today.year + 1, 12, 15)
    return candidate


def _next_salario_escolar_date(today: date | None = None) -> date:
    """Salario escolar is paid end of January; pick the upcoming Jan 31."""
    today = today or date.today()
    candidate = date(today.year, 1, 31)
    if candidate < today:
        candidate = date(today.year + 1, 1, 31)
    return candidate


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
    include_archived: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    """List recurring incomes, optionally including archived rows.

    Paused rows (is_active=false, archived=false) are always visible — the
    SPA shows them with a badge. Archived rows are filtered out by default;
    `include_archived=true` returns them too.
    """
    stmt = select(RecurringIncome).where(RecurringIncome.user_id == user.id)
    if not include_archived:
        stmt = stmt.where(RecurringIncome.archived.is_(False))
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
    income.archived = True
    await db.commit()
    await db.refresh(income)
    return income


@router.post(
    "/{salary_id}/derive-cycles",
    response_model=CRCycleDeriveResponse,
    status_code=201,
)
async def derive_cr_cycles(
    salary_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    """Phase 6e B8 — atomic creation of aguinaldo + salario_escolar.

    Both rows are derived from the same monthly salary in one DB transaction.
    Idempotent: if a row for the same `income_type` + `base_salary_link_id`
    already exists, it's returned unchanged. Returns flags indicating which
    rows were newly created.
    """
    result = await db.execute(
        select(RecurringIncome).where(
            RecurringIncome.id == salary_id,
            RecurringIncome.user_id == user.id,
        )
    )
    salary = result.scalar_one_or_none()
    if salary is None:
        raise HTTPException(
            status_code=404, detail="Salario base no encontrado."
        )
    if salary.income_type != "salary":
        raise HTTPException(
            status_code=400,
            detail="El id debe apuntar a un income_type='salary'.",
        )
    if salary.amount is None:
        raise HTTPException(
            status_code=400,
            detail="El salario base no tiene un monto registrado.",
        )

    existing_result = await db.execute(
        select(RecurringIncome).where(
            RecurringIncome.user_id == user.id,
            RecurringIncome.base_salary_link_id == salary_id,
            RecurringIncome.income_type.in_(("aguinaldo", "salario_escolar")),
        )
    )
    existing = {row.income_type: row for row in existing_result.scalars().all()}

    derived: dict[str, RecurringIncome] = {}
    created: dict[str, bool] = {}

    for kind, next_date_fn in (
        ("aguinaldo", _next_aguinaldo_date),
        ("salario_escolar", _next_salario_escolar_date),
    ):
        if kind in existing:
            derived[kind] = existing[kind]
            created[kind] = False
            continue
        row = RecurringIncome(
            user_id=user.id,
            name=f"{kind.capitalize().replace('_', ' ')} ({salary.name})",
            income_type=kind,
            amount=derive_amount_for(kind, salary.amount),
            currency=salary.currency,
            frequency="annual",
            next_payment_date=next_date_fn(),
            base_salary_link_id=salary.id,
        )
        db.add(row)
        derived[kind] = row
        created[kind] = True

    await db.commit()
    for row in derived.values():
        await db.refresh(row)

    return CRCycleDeriveResponse(
        aguinaldo=RecurringIncomeResponse.model_validate(derived["aguinaldo"]),
        salario_escolar=RecurringIncomeResponse.model_validate(
            derived["salario_escolar"]
        ),
        created_aguinaldo=created["aguinaldo"],
        created_salario_escolar=created["salario_escolar"],
    )
