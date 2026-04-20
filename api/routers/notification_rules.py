import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models.enums import NotificationScope
from ..models.notification_rule import NotificationRule
from ..schemas.notifications import (
    NotificationRuleCreate,
    NotificationRuleResponse,
    NotificationRuleUpdate,
)

router = APIRouter(prefix="/api/v1/notification-rules", tags=["notification-rules"])


@router.post("", response_model=NotificationRuleResponse, status_code=201)
async def create_notification_rule(
    payload: NotificationRuleCreate,
    db: AsyncSession = Depends(get_db),
):
    rule = NotificationRule(
        scope=payload.scope.value,
        recurring_bill_id=payload.recurring_bill_id,
        custom_event_id=payload.custom_event_id,
        category=payload.category.value if payload.category else None,
        advance_days=payload.advance_days,
    )
    db.add(rule)
    try:
        await db.commit()
    except Exception as e:  # CheckConstraint or FK violation
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(e.__cause__ or e)) from e
    await db.refresh(rule)
    return rule


@router.get("", response_model=list[NotificationRuleResponse])
async def list_notification_rules(
    scope: Optional[NotificationScope] = Query(default=None),
    is_active: Optional[bool] = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(NotificationRule)
    if scope is not None:
        stmt = stmt.where(NotificationRule.scope == scope.value)
    if is_active is not None:
        stmt = stmt.where(NotificationRule.is_active.is_(is_active))
    stmt = stmt.order_by(NotificationRule.created_at.desc())
    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.get("/{rule_id}", response_model=NotificationRuleResponse)
async def get_notification_rule(
    rule_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(NotificationRule).where(NotificationRule.id == rule_id)
    )
    rule = result.scalar_one_or_none()
    if rule is None:
        raise HTTPException(status_code=404, detail="Regla no encontrada.")
    return rule


@router.patch("/{rule_id}", response_model=NotificationRuleResponse)
async def update_notification_rule(
    rule_id: uuid.UUID,
    payload: NotificationRuleUpdate,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(NotificationRule).where(NotificationRule.id == rule_id)
    )
    rule = result.scalar_one_or_none()
    if rule is None:
        raise HTTPException(status_code=404, detail="Regla no encontrada.")

    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(rule, field, value)

    await db.commit()
    await db.refresh(rule)
    return rule


@router.delete("/{rule_id}", response_model=NotificationRuleResponse)
async def soft_delete_notification_rule(
    rule_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(NotificationRule).where(NotificationRule.id == rule_id)
    )
    rule = result.scalar_one_or_none()
    if rule is None:
        raise HTTPException(status_code=404, detail="Regla no encontrada.")

    rule.is_active = False
    await db.commit()
    await db.refresh(rule)
    return rule
