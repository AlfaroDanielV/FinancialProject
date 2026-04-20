import uuid
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..database import get_db
from ..models.bill_occurrence import BillOccurrence
from ..models.enums import BillCategory, BillOccurrenceStatus
from ..models.recurring_bill import RecurringBill
from ..schemas.recurring_bills import (
    BillOccurrenceResponse,
    MarkPaidRequest,
    MarkPaidResponse,
    SkipRequest,
)
from ..services import recurrence

router = APIRouter(prefix="/api/v1/bill-occurrences", tags=["bill-occurrences"])


@router.get("", response_model=list[BillOccurrenceResponse])
async def list_bill_occurrences(
    status: Optional[BillOccurrenceStatus] = Query(default=None),
    from_date: Optional[date] = Query(default=None, alias="from_date"),
    to_date: Optional[date] = Query(default=None, alias="to_date"),
    recurring_bill_id: Optional[uuid.UUID] = Query(default=None),
    category: Optional[BillCategory] = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(BillOccurrence)
    if category is not None:
        stmt = stmt.join(RecurringBill).where(
            RecurringBill.category == category.value
        )
    if status is not None:
        stmt = stmt.where(BillOccurrence.status == status.value)
    if from_date is not None:
        stmt = stmt.where(BillOccurrence.due_date >= from_date)
    if to_date is not None:
        stmt = stmt.where(BillOccurrence.due_date <= to_date)
    if recurring_bill_id is not None:
        stmt = stmt.where(BillOccurrence.recurring_bill_id == recurring_bill_id)
    stmt = stmt.order_by(BillOccurrence.due_date.asc())
    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.get("/{occurrence_id}", response_model=BillOccurrenceResponse)
async def get_bill_occurrence(
    occurrence_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(BillOccurrence)
        .options(selectinload(BillOccurrence.recurring_bill))
        .where(BillOccurrence.id == occurrence_id)
    )
    occ = result.scalar_one_or_none()
    if occ is None:
        raise HTTPException(status_code=404, detail="Ocurrencia no encontrada.")
    return occ


@router.post("/{occurrence_id}/mark-paid", response_model=MarkPaidResponse)
async def mark_paid(
    occurrence_id: uuid.UUID,
    payload: MarkPaidRequest,
    db: AsyncSession = Depends(get_db),
):
    try:
        result = await recurrence.link_transaction_to_occurrence(
            occurrence_id,
            payload.transaction_id,
            db,
            amount_paid=payload.amount_paid,
            paid_at=payload.paid_at,
            notes=payload.notes,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e

    await db.commit()
    await db.refresh(result.occurrence)
    return MarkPaidResponse(
        occurrence=BillOccurrenceResponse.model_validate(result.occurrence),
        amount_delta_pct=result.amount_delta_pct,
        warning=result.warning,
    )


@router.post("/{occurrence_id}/skip", response_model=BillOccurrenceResponse)
async def skip_occurrence(
    occurrence_id: uuid.UUID,
    payload: SkipRequest,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(BillOccurrence).where(BillOccurrence.id == occurrence_id)
    )
    occ = result.scalar_one_or_none()
    if occ is None:
        raise HTTPException(status_code=404, detail="Ocurrencia no encontrada.")

    if occ.status in (
        BillOccurrenceStatus.PAID.value,
        BillOccurrenceStatus.CANCELLED.value,
    ):
        raise HTTPException(
            status_code=409,
            detail=f"No se puede saltar una ocurrencia en estado {occ.status}.",
        )

    occ.status = BillOccurrenceStatus.SKIPPED.value
    if payload.notes is not None:
        occ.notes = payload.notes
    await db.commit()
    await db.refresh(occ)
    return occ
