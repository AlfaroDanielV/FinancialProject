import uuid
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Header, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..database import get_db
from ..models.transaction import Transaction
from ..schemas.transaction import (
    ShortcutTransactionCreate,
    TransactionCreate,
    TransactionListResponse,
    TransactionResponse,
)

router = APIRouter(prefix="/api/v1/transactions", tags=["transactions"])


def _get_default_user_id() -> uuid.UUID:
    if not settings.default_user_id:
        raise HTTPException(
            status_code=503,
            detail="DEFAULT_USER_ID not configured. Run scripts/create_user.py first.",
        )
    return uuid.UUID(settings.default_user_id)


# ── iPhone Shortcut endpoint ────────────────────────────────────────────────

@router.post("/shortcut", response_model=TransactionResponse, status_code=201)
async def shortcut_transaction(
    payload: ShortcutTransactionCreate,
    x_shortcut_token: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db),
):
    """
    Webhook for the iPhone Shortcut.

    Headers:
        X-Shortcut-Token: <value of SHORTCUT_TOKEN in .env>

    Body:
        {
          "amount": 5000,
          "merchant": "Supermercado Buen Precio",
          "category": "supermercado",
          "is_expense": true,         // optional, defaults true
          "description": "compras semana",  // optional
          "transaction_date": "2026-04-12"  // optional, defaults today
        }
    """
    if x_shortcut_token != settings.shortcut_token:
        raise HTTPException(status_code=401, detail="Token inválido.")

    user_id = _get_default_user_id()
    signed_amount = -abs(payload.amount) if payload.is_expense else abs(payload.amount)

    txn = Transaction(
        user_id=user_id,
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
):
    user_id = _get_default_user_id()
    txn = Transaction(
        user_id=user_id,
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
):
    user_id = _get_default_user_id()

    count_result = await db.execute(
        select(func.count()).where(Transaction.user_id == user_id)
    )
    total = count_result.scalar_one()

    result = await db.execute(
        select(Transaction)
        .where(Transaction.user_id == user_id)
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
):
    user_id = _get_default_user_id()
    result = await db.execute(
        select(Transaction).where(
            Transaction.id == transaction_id,
            Transaction.user_id == user_id,
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
):
    user_id = _get_default_user_id()
    result = await db.execute(
        select(Transaction).where(
            Transaction.id == transaction_id,
            Transaction.user_id == user_id,
        )
    )
    txn = result.scalar_one_or_none()
    if not txn:
        raise HTTPException(status_code=404, detail="Transacción no encontrada.")

    txn.parse_status = "flagged"
    await db.commit()
    await db.refresh(txn)
    return txn
