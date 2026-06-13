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
from decimal import Decimal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..dependencies import current_user
from ..models.recurring_income import RecurringIncome
from ..models.user import User
from ..schemas.recurring_incomes import (
    CRCycleDeriveRequest,
    CRCycleDeriveResponse,
    DERIVED_TYPES,
    RecurringIncomeCreate,
    RecurringIncomeResponse,
    RecurringIncomeUpdate,
)
from ..services.finance.incomes import derive_amount_for
from ..services.income_frequency import PAYMENTS_PER_MONTH

router = APIRouter(prefix="/api/v1/recurring-incomes", tags=["recurring-incomes"])


def _cycle_year(user: User) -> int:
    """Reference accrual year (the user's current year in their timezone)."""
    try:
        tz = ZoneInfo(user.timezone)
    except Exception:  # pragma: no cover - defensive
        tz = ZoneInfo("America/Costa_Rica")
    from datetime import datetime

    return datetime.now(tz).year


def _monthly_gross_for_cycles(salary: RecurringIncome) -> Decimal | None:
    """Monthly GROSS used as the aguinaldo / salario-escolar base.

    Prefer the stored monthly gross (CRC salaries via the calculator). Otherwise
    reconstruct a monthly figure from the per-payment `amount` × cadence (the
    best available base for USD / non-calculated salaries)."""
    if salary.gross_monthly is not None:
        return Decimal(salary.gross_monthly)
    if salary.amount is None:
        return None
    factor = PAYMENTS_PER_MONTH.get(salary.frequency, Decimal("1"))
    return Decimal(salary.amount) * factor


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
        monthly_gross = _monthly_gross_for_cycles(base)
        if monthly_gross is None:
            raise HTTPException(
                status_code=400,
                detail="El salario base no tiene un monto registrado.",
            )
        amount = derive_amount_for(
            payload.income_type,
            monthly_gross=monthly_gross,
            hire_date=base.hire_date,
            as_of_year=_cycle_year(user),
        )

    income = RecurringIncome(
        user_id=user.id,
        name=payload.name,
        income_type=payload.income_type,
        amount=amount,
        gross_monthly=payload.gross_monthly,
        currency=payload.currency,
        frequency=payload.frequency,
        next_payment_date=payload.next_payment_date,
        base_salary_link_id=payload.base_salary_link_id,
        hire_date=payload.hire_date,
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
    body: CRCycleDeriveRequest | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    """Phase 6e B8 — atomic creation of aguinaldo + salario_escolar.

    Both rows are derived from the same monthly GROSS salary, prorated by the
    salary's hire date (CR law), in one DB transaction. A `hire_date` in the
    body is persisted on the salary so a re-derive doesn't re-ask. Amounts are
    (re)computed on every call so a re-derive with a newly supplied hire date
    refreshes existing rows; `created` flags which rows were newly inserted.
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
    monthly_gross = _monthly_gross_for_cycles(salary)
    if monthly_gross is None:
        raise HTTPException(
            status_code=400,
            detail="El salario base no tiene un monto registrado.",
        )

    # Persist a supplied hire date (so a re-derive remembers it), then prorate
    # against the salary's effective hire date.
    if body is not None and body.hire_date is not None:
        salary.hire_date = body.hire_date
    effective_hire = salary.hire_date
    as_of_year = _cycle_year(user)

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
        amount = derive_amount_for(
            kind,
            monthly_gross=monthly_gross,
            hire_date=effective_hire,
            as_of_year=as_of_year,
        )
        if kind in existing:
            row = existing[kind]
            row.amount = amount  # refresh to reflect the latest hire date/gross
            derived[kind] = row
            created[kind] = False
            continue
        row = RecurringIncome(
            user_id=user.id,
            name=f"{kind.capitalize().replace('_', ' ')} ({salary.name})",
            income_type=kind,
            amount=amount,
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
