import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..database import get_db
from ..models.account import Account
from ..schemas.account import (
    VALID_ACCOUNT_TYPES,
    AccountCreate,
    AccountResponse,
    AccountUpdate,
)

router = APIRouter(prefix="/api/v1/accounts", tags=["accounts"])


def _get_default_user_id() -> uuid.UUID:
    if not settings.default_user_id:
        raise HTTPException(
            status_code=503,
            detail="DEFAULT_USER_ID not configured. Run scripts/create_user.py first.",
        )
    return uuid.UUID(settings.default_user_id)


@router.post("", response_model=AccountResponse, status_code=201)
async def create_account(
    payload: AccountCreate,
    db: AsyncSession = Depends(get_db),
):
    if payload.account_type not in VALID_ACCOUNT_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"account_type must be one of: {', '.join(sorted(VALID_ACCOUNT_TYPES))}",
        )

    user_id = _get_default_user_id()
    account = Account(
        user_id=user_id,
        name=payload.name,
        account_type=payload.account_type,
    )
    db.add(account)
    await db.commit()
    await db.refresh(account)
    return account


@router.get("", response_model=list[AccountResponse])
async def list_accounts(
    include_inactive: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
):
    user_id = _get_default_user_id()
    stmt = select(Account).where(Account.user_id == user_id)
    if not include_inactive:
        stmt = stmt.where(Account.is_active == True)  # noqa: E712
    stmt = stmt.order_by(Account.created_at.desc())

    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.get("/{account_id}", response_model=AccountResponse)
async def get_account(
    account_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    user_id = _get_default_user_id()
    result = await db.execute(
        select(Account).where(
            Account.id == account_id,
            Account.user_id == user_id,
        )
    )
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="Cuenta no encontrada.")
    return account


@router.patch("/{account_id}", response_model=AccountResponse)
async def update_account(
    account_id: uuid.UUID,
    payload: AccountUpdate,
    db: AsyncSession = Depends(get_db),
):
    user_id = _get_default_user_id()
    result = await db.execute(
        select(Account).where(
            Account.id == account_id,
            Account.user_id == user_id,
        )
    )
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="Cuenta no encontrada.")

    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(account, field, value)

    await db.commit()
    await db.refresh(account)
    return account


@router.delete("/{account_id}", response_model=AccountResponse)
async def delete_account(
    account_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    user_id = _get_default_user_id()
    result = await db.execute(
        select(Account).where(
            Account.id == account_id,
            Account.user_id == user_id,
        )
    )
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="Cuenta no encontrada.")

    account.is_active = False
    await db.commit()
    await db.refresh(account)
    return account
