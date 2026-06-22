import json
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..dependencies import current_user
from ..models.bill_occurrence import BillOccurrence
from ..models.enums import BillOccurrenceStatus
from ..models.recurring_bill import RecurringBill
from ..models.transaction import Transaction
from ..models.user import User
from ..redis_client import get_redis
from ..schemas.recurring_bills import (
    BillMarkPaidRequest,
    BillMarkPaidResponse,
    BillOccurrenceResponse,
    RecurringBillCreate,
    RecurringBillResponse,
    RecurringBillUpdate,
)
from ..services import recurrence
from ..services.clock import user_today
from ..services.envelopes import is_valid_envelope_target

router = APIRouter(prefix="/api/v1/recurring-bills", tags=["recurring-bills"])

MARK_PAID_IDEMPOTENCY_TTL_S = 600  # 10 minutes; matches B9 account-creation TTL.

ACTIONABLE_OCCURRENCE_STATUSES = (
    BillOccurrenceStatus.PENDING.value,
    BillOccurrenceStatus.OVERDUE.value,
    BillOccurrenceStatus.PARTIALLY_PAID.value,
)


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
    user: User = Depends(current_user),
):
    bill = RecurringBill(
        user_id=user.id,
        name=payload.name,
        provider=payload.provider,
        category=payload.category,
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
    category: Optional[str] = Query(default=None),
    is_active: Optional[bool] = Query(default=None),
    account_id: Optional[uuid.UUID] = Query(default=None),
    provider: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    stmt = select(RecurringBill).where(RecurringBill.user_id == user.id)
    if category is not None:
        stmt = stmt.where(RecurringBill.category == category)
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
    user: User = Depends(current_user),
):
    bill = await recurrence.fetch_bill(bill_id, user.id, db)
    if bill is None:
        raise HTTPException(status_code=404, detail="Factura recurrente no encontrada.")
    return bill


@router.patch("/{bill_id}", response_model=RecurringBillResponse)
async def update_recurring_bill(
    bill_id: uuid.UUID,
    payload: RecurringBillUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    bill = await recurrence.fetch_bill(bill_id, user.id, db)
    if bill is None:
        raise HTTPException(status_code=404, detail="Factura recurrente no encontrada.")

    update_data = payload.model_dump(exclude_unset=True)
    env_id = update_data.get("envelope_id")
    if env_id is not None and not await is_valid_envelope_target(
        db, user_id=user.id, envelope_id=env_id
    ):
        raise HTTPException(status_code=400, detail="Sobre inválido.")
    schedule_changed = bool(_SCHEDULE_FIELDS & update_data.keys())
    was_active_before = bool(bill.is_active)

    for field, value in update_data.items():
        if field == "frequency" and value is not None:
            setattr(bill, field, value.value if hasattr(value, "value") else value)
        else:
            setattr(bill, field, value)

    await db.flush()

    resumed = (
        "is_active" in update_data
        and update_data["is_active"] is True
        and not was_active_before
    )

    if schedule_changed:
        await recurrence.delete_future_pending(bill.id, db)
        await recurrence.generate_occurrences(bill, db)
    elif resumed:
        # Resume after pause: bill was deactivated, future occurrences may
        # have been left in place (pause does not cancel) but we still
        # backfill any missing future months up to the standard horizon.
        await recurrence.generate_occurrences(bill, db)

    await db.commit()
    await db.refresh(bill)
    return bill


@router.delete("/{bill_id}", response_model=RecurringBillResponse)
async def soft_delete_recurring_bill(
    bill_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    bill = await recurrence.fetch_bill(bill_id, user.id, db)
    if bill is None:
        raise HTTPException(status_code=404, detail="Factura recurrente no encontrada.")

    bill.is_active = False
    await recurrence.cancel_future_pending(bill.id, db)
    await db.commit()
    await db.refresh(bill)
    return bill


@router.post(
    "/{bill_id}/mark-paid",
    response_model=BillMarkPaidResponse,
)
async def mark_bill_paid(
    bill_id: uuid.UUID,
    payload: BillMarkPaidRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    """Phase 6e B6 — convenience: mark the next pending occurrence paid.

    Auto-resolves the smallest-due_date pending/overdue/partially_paid
    occurrence for this bill, creates a manual transaction, and links it.
    Idempotency: same `idempotency_key` within 10 min returns the original
    response without duplicating the transaction.
    """
    redis = get_redis()
    cache_key = (
        f"bill_mark_paid:{user.id}:{bill_id}:{payload.idempotency_key}"
    )
    cached = await redis.get(cache_key)
    if cached:
        body = json.loads(cached)
        body["idempotent_replay"] = True
        return BillMarkPaidResponse.model_validate(body)

    bill = await recurrence.fetch_bill(bill_id, user.id, db)
    if bill is None:
        raise HTTPException(status_code=404, detail="Factura recurrente no encontrada.")

    occurrence_result = await db.execute(
        select(BillOccurrence)
        .where(
            BillOccurrence.recurring_bill_id == bill_id,
            BillOccurrence.user_id == user.id,
            BillOccurrence.status.in_(ACTIONABLE_OCCURRENCE_STATUSES),
        )
        .order_by(BillOccurrence.due_date.asc())
        .limit(1)
    )
    occurrence = occurrence_result.scalar_one_or_none()
    if occurrence is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "No hay ocurrencias pendientes. Generá nuevas con "
                "POST /api/v1/jobs/generate-occurrences."
            ),
        )

    if payload.amount_paid is not None:
        amount_paid = float(payload.amount_paid)
    elif occurrence.amount_expected is not None:
        amount_paid = float(occurrence.amount_expected)
    elif bill.amount_expected is not None:
        amount_paid = float(bill.amount_expected)
    else:
        raise HTTPException(
            status_code=400,
            detail="Esta factura es de monto variable; mandá amount_paid.",
        )

    target_account = payload.account_id or bill.account_id
    transaction_date = payload.paid_at or occurrence.due_date or user_today(user)

    txn = Transaction(
        user_id=user.id,
        account_id=target_account,
        category_id=payload.category_id,
        amount=Decimal(str(-abs(amount_paid))),
        currency=bill.currency,
        merchant=bill.provider or bill.name,
        description=payload.description or f"Pago: {bill.name}",
        category=bill.category,
        transaction_date=transaction_date,
        source="manual",
        # Fixed-expense attachment (B2): tag the payment to the bill's envelope so
        # the actual amount counts as spend there and the reservation releases the
        # same cycle (never both).
        envelope_id=bill.envelope_id,
    )
    db.add(txn)
    await db.flush()

    try:
        result = await recurrence.link_transaction_to_occurrence(
            occurrence.id,
            txn.id,
            db,
            user.id,
            amount_paid=amount_paid,
            paid_at=datetime.combine(transaction_date, datetime.min.time()),
            notes=payload.description,
        )
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    await db.commit()
    await db.refresh(result.occurrence)

    response = BillMarkPaidResponse(
        occurrence=BillOccurrenceResponse.model_validate(result.occurrence),
        transaction_id=txn.id,
        amount_delta_pct=result.amount_delta_pct,
        warning=result.warning,
        idempotent_replay=False,
    )
    await redis.setex(
        cache_key,
        MARK_PAID_IDEMPOTENCY_TTL_S,
        response.model_dump_json(),
    )
    return response
