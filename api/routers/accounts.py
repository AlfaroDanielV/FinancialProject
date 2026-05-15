import uuid
from typing import Iterable

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..dependencies import current_user
from ..models.account import Account
from ..models.user import User
from ..schemas.account import (
    VALID_ACCOUNT_TYPES,
    AccountCreate,
    AccountResponse,
    AccountUpdate,
)
from ..services.accounts import compute_account_balances

router = APIRouter(prefix="/api/v1/accounts", tags=["accounts"])


async def _accounts_with_balances(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    accounts: Iterable[Account],
) -> list[AccountResponse]:
    accounts_list = list(accounts)
    balances = await compute_account_balances(
        db,
        user_id=user_id,
        account_ids=[acc.id for acc in accounts_list],
    )
    responses: list[AccountResponse] = []
    for acc in accounts_list:
        balance = balances.get(acc.id)
        response = AccountResponse.model_validate(acc)
        if balance is not None:
            response = response.model_copy(
                update={
                    "current_balance": balance.current,
                    "month_start_balance": balance.month_start,
                }
            )
        responses.append(response)
    return responses


@router.post("", response_model=AccountResponse, status_code=201)
async def create_account(
    payload: AccountCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    if payload.account_type not in VALID_ACCOUNT_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"account_type must be one of: {', '.join(sorted(VALID_ACCOUNT_TYPES))}",
        )

    account = Account(
        user_id=user.id,
        name=payload.name,
        account_type=payload.account_type,
        currency=payload.currency,
        initial_balance=payload.initial_balance,
    )
    db.add(account)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Ya tenés una cuenta activa con ese nombre.",
        )
    await db.refresh(account)
    [response] = await _accounts_with_balances(
        db, user_id=user.id, accounts=[account]
    )
    return response


@router.get("", response_model=list[AccountResponse])
async def list_accounts(
    include_inactive: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    stmt = select(Account).where(Account.user_id == user.id)
    if not include_inactive:
        stmt = stmt.where(
            Account.is_active == True,  # noqa: E712
            Account.archived.is_(False),
        )
    stmt = stmt.order_by(Account.created_at.desc())

    result = await db.execute(stmt)
    accounts = list(result.scalars().all())
    return await _accounts_with_balances(db, user_id=user.id, accounts=accounts)


@router.get("/{account_id}", response_model=AccountResponse)
async def get_account(
    account_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    result = await db.execute(
        select(Account).where(
            Account.id == account_id,
            Account.user_id == user.id,
        )
    )
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="Cuenta no encontrada.")
    [response] = await _accounts_with_balances(
        db, user_id=user.id, accounts=[account]
    )
    return response


@router.patch("/{account_id}", response_model=AccountResponse)
async def update_account(
    account_id: uuid.UUID,
    payload: AccountUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    result = await db.execute(
        select(Account).where(
            Account.id == account_id,
            Account.user_id == user.id,
        )
    )
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="Cuenta no encontrada.")

    update_data = payload.model_dump(exclude_unset=True)
    if "account_type" in update_data:
        if update_data["account_type"] not in VALID_ACCOUNT_TYPES:
            raise HTTPException(
                status_code=400,
                detail=(
                    "account_type must be one of: "
                    f"{', '.join(sorted(VALID_ACCOUNT_TYPES))}"
                ),
            )

    for field, value in update_data.items():
        setattr(account, field, value)

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Ya tenés una cuenta activa con ese nombre.",
        )
    await db.refresh(account)
    [response] = await _accounts_with_balances(
        db, user_id=user.id, accounts=[account]
    )
    return response


@router.delete("/{account_id}", response_model=AccountResponse)
async def delete_account(
    account_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    result = await db.execute(
        select(Account).where(
            Account.id == account_id,
            Account.user_id == user.id,
        )
    )
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="Cuenta no encontrada.")

    account.is_active = False
    account.archived = True
    await db.commit()
    await db.refresh(account)
    [response] = await _accounts_with_balances(
        db, user_id=user.id, accounts=[account]
    )
    return response
