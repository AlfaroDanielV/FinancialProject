import uuid
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..dependencies import current_user
from ..models.enums import NotificationChannel, NotificationStatus
from ..models.notification_event import NotificationEvent
from ..models.user import User
from ..schemas.notifications import NotificationEventResponse
from ..services.recurrence import today_cr

router = APIRouter(prefix="/api/v1/notifications", tags=["notifications"])

CR_TZ = ZoneInfo("America/Costa_Rica")


@router.get("/pending", response_model=list[NotificationEventResponse])
async def list_pending(
    channel: Optional[NotificationChannel] = Query(default=None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    today = today_cr()
    stmt = (
        select(NotificationEvent)
        .where(
            NotificationEvent.user_id == user.id,
            NotificationEvent.status == NotificationStatus.PENDING.value,
            NotificationEvent.trigger_date <= today,
        )
        .order_by(NotificationEvent.trigger_date.asc())
    )
    if channel is not None:
        stmt = stmt.where(NotificationEvent.channel == channel.value)
    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.post("/{notification_id}/acknowledge", response_model=NotificationEventResponse)
async def acknowledge_notification(
    notification_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    result = await db.execute(
        select(NotificationEvent).where(
            NotificationEvent.id == notification_id,
            NotificationEvent.user_id == user.id,
        )
    )
    event = result.scalar_one_or_none()
    if event is None:
        raise HTTPException(status_code=404, detail="Notificación no encontrada.")

    event.status = NotificationStatus.ACKNOWLEDGED.value
    event.acknowledged_at = datetime.now(CR_TZ)
    await db.commit()
    await db.refresh(event)
    return event


@router.post("/{notification_id}/dismiss", response_model=NotificationEventResponse)
async def dismiss_notification(
    notification_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    result = await db.execute(
        select(NotificationEvent).where(
            NotificationEvent.id == notification_id,
            NotificationEvent.user_id == user.id,
        )
    )
    event = result.scalar_one_or_none()
    if event is None:
        raise HTTPException(status_code=404, detail="Notificación no encontrada.")

    event.status = NotificationStatus.DISMISSED.value
    await db.commit()
    await db.refresh(event)
    return event
