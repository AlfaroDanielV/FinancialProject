import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models.enums import BillCategory
from ..models.recurring_bill import RecurringBill
from ..schemas.recurring_bills import (
    RecurringBillCreate,
    RecurringBillResponse,
    RecurringBillUpdate,
)
from ..services import recurrence

router = APIRouter(prefix="/api/v1/recurring-bills", tags=["recurring-bills"])


_SCHEDULE_FIELDS = {
    "frequency",
    "day_of_month",
    "start_date",
    "end_date",
    "recurrence_rule",
}


@router.post("", response_model=RecurringBillResponse, status_code=201)
async def create_recurring_bill(
    payload: RecurringBillCreate,
    db: AsyncSession = Depends(get_db),
):
    bill = RecurringBill(
        name=payload.name,
        provider=payload.provider,
        category=payload.category.value,
        amount_expected=payload.amount_expected,
        currency=payload.currency,
        is_variable_amount=payload.is_variable_amount,
        account_id=payload.account_id,
        frequency=payload.frequency.value,
        day_of_month=payload.day_of_month,
        recurrence_rule=payload.recurrence_rule,
        start_date=payload.start_date,
        end_date=payload.end_date,
        lead_time_days=payload.lead_time_days,
        notes=payload.notes,
        linked_loan_id=payload.linked_loan_id,
    )
    db.add(bill)
    await db.flush()
    await recurrence.generate_occurrences(bill, db)
    await db.commit()
    await db.refresh(bill)
    return bill


@router.get("", response_model=list[RecurringBillResponse])
async def list_recurring_bills(
    category: Optional[BillCategory] = Query(default=None),
    is_active: Optional[bool] = Query(default=None),
    account_id: Optional[uuid.UUID] = Query(default=None),
    provider: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(RecurringBill)
    if category is not None:
        stmt = stmt.where(RecurringBill.category == category.value)
    if is_active is not None:
        stmt = stmt.where(RecurringBill.is_active.is_(is_active))
    if account_id is not None:
        stmt = stmt.where(RecurringBill.account_id == account_id)
    if provider is not None:
        stmt = stmt.where(RecurringBill.provider == provider)
    stmt = stmt.order_by(RecurringBill.created_at.desc())
    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.get("/{bill_id}", response_model=RecurringBillResponse)
async def get_recurring_bill(
    bill_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    bill = await recurrence.fetch_bill(bill_id, db)
    if bill is None:
        raise HTTPException(status_code=404, detail="Factura recurrente no encontrada.")
    return bill


@router.patch("/{bill_id}", response_model=RecurringBillResponse)
async def update_recurring_bill(
    bill_id: uuid.UUID,
    payload: RecurringBillUpdate,
    db: AsyncSession = Depends(get_db),
):
    bill = await recurrence.fetch_bill(bill_id, db)
    if bill is None:
        raise HTTPException(status_code=404, detail="Factura recurrente no encontrada.")

    update_data = payload.model_dump(exclude_unset=True)
    schedule_changed = bool(_SCHEDULE_FIELDS & update_data.keys())

    for field, value in update_data.items():
        if field in ("category", "frequency") and value is not None:
            setattr(bill, field, value.value if hasattr(value, "value") else value)
        else:
            setattr(bill, field, value)

    await db.flush()

    if schedule_changed:
        await recurrence.delete_future_pending(bill.id, db)
        await recurrence.generate_occurrences(bill, db)

    await db.commit()
    await db.refresh(bill)
    return bill


@router.delete("/{bill_id}", response_model=RecurringBillResponse)
async def soft_delete_recurring_bill(
    bill_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    bill = await recurrence.fetch_bill(bill_id, db)
    if bill is None:
        raise HTTPException(status_code=404, detail="Factura recurrente no encontrada.")

    bill.is_active = False
    await recurrence.cancel_future_pending(bill.id, db)
    await db.commit()
    await db.refresh(bill)
    return bill
