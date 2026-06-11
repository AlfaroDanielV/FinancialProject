import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..dependencies import current_user
from ..models.transfer import Transfer
from ..models.user import User
from ..schemas.transfers import TransferCreate, TransferResponse
from ..services.transfers import create_transfer_with_transactions

router = APIRouter(prefix="/api/v1/transfers", tags=["transfers"])


@router.post("", response_model=TransferResponse, status_code=201)
async def create_transfer(
    payload: TransferCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    result = await create_transfer_with_transactions(
        db, user_id=user.id, payload=payload
    )
    await db.commit()
    await db.refresh(result.transfer)
    response = TransferResponse.model_validate(result.transfer)
    return response.model_copy(
        update={
            "debit_transaction_id": result.debit_transaction_id,
            "credit_transaction_id": result.credit_transaction_id,
        }
    )


@router.get("", response_model=list[TransferResponse])
async def list_transfers(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    account_id: uuid.UUID | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    stmt = select(Transfer).where(Transfer.user_id == user.id)
    if account_id is not None:
        # Either side — the native account detail lists every transfer that
        # touches the account (Phase 7b).
        stmt = stmt.where(
            or_(
                Transfer.from_account_id == account_id,
                Transfer.to_account_id == account_id,
            )
        )
    result = await db.execute(
        stmt.order_by(Transfer.occurred_at.desc(), Transfer.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return list(result.scalars().all())
