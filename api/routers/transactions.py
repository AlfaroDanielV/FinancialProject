import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..dependencies import current_user, current_user_via_token
from ..models.transaction import Transaction
from ..models.user import User
from ..schemas.transaction import (
    ShortcutTransactionCreate,
    TransactionCreate,
    TransactionListResponse,
    TransactionResponse,
)

router = APIRouter(prefix="/api/v1/transactions", tags=["transactions"])


# ── iPhone Shortcut endpoint ────────────────────────────────────────────────

@router.post("/shortcut", response_model=TransactionResponse, status_code=201)
async def shortcut_transaction(
    payload: ShortcutTransactionCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_via_token),
):
    """
    Webhook for the iPhone Shortcut.

    Headers:
        X-Shortcut-Token: <user.shortcut_token returned at registration>

    Body:
        {
          "amount": 5000,
          "merchant": "Supermercado Buen Precio",
          "category": "supermercado",
          "is_expense": true,
          "description": "compras semana",
          "transaction_date": "2026-04-12"
        }

    user_id is attached server-side from the resolved token; the Shortcut
    must NOT send it in the body.
    """
    signed_amount = -abs(payload.amount) if payload.is_expense else abs(payload.amount)

    txn = Transaction(
        user_id=user.id,
        amount=signed_amount,
        currency="CRC",
        merchant=payload.merchant,
        description=payload.description,
        category=payload.category,
        transaction_date=payload.transaction_date or date.today(),
        source="shortcut",
    )
    db.add(txn)
    await db.commit()
    await db.refresh(txn)
    return txn


# ── Standard CRUD ────────────────────────────────────────────────────────────

@router.post("", response_model=TransactionResponse, status_code=201)
async def create_transaction(
    payload: TransactionCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    txn = Transaction(
        user_id=user.id,
        account_id=payload.account_id,
        amount=payload.amount,
        currency=payload.currency,
        merchant=payload.merchant,
        description=payload.description,
        category=payload.category,
        subcategory=payload.subcategory,
        transaction_date=payload.transaction_date,
        source=payload.source,
    )
    db.add(txn)
    await db.commit()
    await db.refresh(txn)
    return txn


@router.get("", response_model=TransactionListResponse)
async def list_transactions(
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    count_result = await db.execute(
        select(func.count()).where(Transaction.user_id == user.id)
    )
    total = count_result.scalar_one()

    result = await db.execute(
        select(Transaction)
        .where(Transaction.user_id == user.id)
        .order_by(Transaction.transaction_date.desc(), Transaction.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    items = result.scalars().all()

    return TransactionListResponse(total=total, items=list(items))


@router.get("/{transaction_id}", response_model=TransactionResponse)
async def get_transaction(
    transaction_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    result = await db.execute(
        select(Transaction).where(
            Transaction.id == transaction_id,
            Transaction.user_id == user.id,
        )
    )
    txn = result.scalar_one_or_none()
    if not txn:
        raise HTTPException(status_code=404, detail="Transacción no encontrada.")
    return txn


@router.patch("/{transaction_id}/flag", response_model=TransactionResponse)
async def flag_transaction(
    transaction_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    result = await db.execute(
        select(Transaction).where(
            Transaction.id == transaction_id,
            Transaction.user_id == user.id,
        )
    )
    txn = result.scalar_one_or_none()
    if not txn:
        raise HTTPException(status_code=404, detail="Transacción no encontrada.")

    txn.parse_status = "flagged"
    await db.commit()
    await db.refresh(txn)
    return txn
