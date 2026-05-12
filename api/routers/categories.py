import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..dependencies import current_user
from ..models.transaction import Transaction
from ..models.user import User
from ..models.user_category import UserCategory
from ..schemas.categories import CategoryCreate, CategoryResponse, CategoryUpdate
from ..services.categories import (
    ensure_default_categories,
    has_active_category_named,
)

router = APIRouter(prefix="/api/v1/categories", tags=["categories"])


def _response(category: UserCategory, transaction_count: int = 0) -> CategoryResponse:
    return CategoryResponse(
        id=category.id,
        user_id=category.user_id,
        name=category.name,
        kind=category.kind,
        color=category.color,
        icon=category.icon,
        is_default=category.is_default,
        archived=category.archived,
        transaction_count=transaction_count,
        created_at=category.created_at,
        updated_at=category.updated_at,
    )


@router.get("", response_model=list[CategoryResponse])
async def list_categories(
    include_archived: bool = Query(default=False),
    kind: Optional[str] = Query(default=None, pattern="^(income|expense|both)$"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    seeded = await ensure_default_categories(db, user.id)
    if seeded:
        await db.commit()

    stmt = (
        select(UserCategory, func.count(Transaction.id))
        .outerjoin(Transaction, Transaction.category_id == UserCategory.id)
        .where(UserCategory.user_id == user.id)
        .group_by(UserCategory.id)
        .order_by(UserCategory.archived.asc(), UserCategory.name.asc())
    )
    if not include_archived:
        stmt = stmt.where(UserCategory.archived.is_(False))
    if kind:
        stmt = stmt.where(UserCategory.kind.in_([kind, "both"]))

    result = await db.execute(stmt)
    return [
        _response(category, int(transaction_count or 0))
        for category, transaction_count in result.fetchall()
    ]


@router.post("", response_model=CategoryResponse, status_code=201)
async def create_category(
    payload: CategoryCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    if await has_active_category_named(db, user_id=user.id, name=payload.name):
        raise HTTPException(
            status_code=409,
            detail="Ya tenés una categoría activa con ese nombre.",
        )

    category = UserCategory(
        user_id=user.id,
        name=payload.name.strip(),
        kind=payload.kind,
        color=payload.color,
        icon=payload.icon,
        is_default=False,
    )
    db.add(category)
    await db.commit()
    await db.refresh(category)
    return _response(category)


@router.patch("/{category_id}", response_model=CategoryResponse)
async def update_category(
    category_id: uuid.UUID,
    payload: CategoryUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    result = await db.execute(
        select(UserCategory).where(
            UserCategory.id == category_id,
            UserCategory.user_id == user.id,
        )
    )
    category = result.scalar_one_or_none()
    if category is None:
        raise HTTPException(status_code=404, detail="Categoría no encontrada.")

    data = payload.model_dump(exclude_unset=True)
    if data.get("name") is not None:
        if await has_active_category_named(
            db,
            user_id=user.id,
            name=data["name"],
            exclude_id=category.id,
        ):
            raise HTTPException(
                status_code=409,
                detail="Ya tenés una categoría activa con ese nombre.",
            )
        category.name = data["name"].strip()
    if data.get("kind") is not None:
        category.kind = data["kind"]
    if data.get("color") is not None:
        category.color = data["color"]
    if "icon" in data:
        category.icon = data["icon"]
    if data.get("archived") is not None:
        if category.is_default and data["archived"]:
            raise HTTPException(
                status_code=400,
                detail="Las categorías default no se pueden archivar.",
            )
        category.archived = data["archived"]

    await db.commit()
    await db.refresh(category)

    count_result = await db.execute(
        select(func.count()).where(Transaction.category_id == category.id)
    )
    return _response(category, int(count_result.scalar_one() or 0))
