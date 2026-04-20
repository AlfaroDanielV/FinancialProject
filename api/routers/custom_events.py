import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models.custom_event import CustomEvent
from ..models.enums import CustomEventType
from ..schemas.custom_events import (
    CustomEventCreate,
    CustomEventResponse,
    CustomEventUpdate,
)

router = APIRouter(prefix="/api/v1/custom-events", tags=["custom-events"])


@router.post("", response_model=CustomEventResponse, status_code=201)
async def create_custom_event(
    payload: CustomEventCreate,
    db: AsyncSession = Depends(get_db),
):
    event = CustomEvent(
        title=payload.title,
        description=payload.description,
        event_type=payload.event_type.value,
        event_date=payload.event_date,
        is_all_day=payload.is_all_day,
        event_time=payload.event_time,
        amount=payload.amount,
        currency=payload.currency,
        recurrence_rule=payload.recurrence_rule,
    )
    db.add(event)
    await db.commit()
    await db.refresh(event)
    return event


@router.get("", response_model=list[CustomEventResponse])
async def list_custom_events(
    event_type: Optional[CustomEventType] = Query(default=None),
    is_active: Optional[bool] = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(CustomEvent)
    if event_type is not None:
        stmt = stmt.where(CustomEvent.event_type == event_type.value)
    if is_active is not None:
        stmt = stmt.where(CustomEvent.is_active.is_(is_active))
    stmt = stmt.order_by(CustomEvent.event_date.asc())
    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.get("/{event_id}", response_model=CustomEventResponse)
async def get_custom_event(
    event_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(CustomEvent).where(CustomEvent.id == event_id)
    )
    event = result.scalar_one_or_none()
    if event is None:
        raise HTTPException(status_code=404, detail="Evento no encontrado.")
    return event


@router.patch("/{event_id}", response_model=CustomEventResponse)
async def update_custom_event(
    event_id: uuid.UUID,
    payload: CustomEventUpdate,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(CustomEvent).where(CustomEvent.id == event_id)
    )
    event = result.scalar_one_or_none()
    if event is None:
        raise HTTPException(status_code=404, detail="Evento no encontrado.")

    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        if field == "event_type" and value is not None:
            setattr(event, field, value.value if hasattr(value, "value") else value)
        else:
            setattr(event, field, value)

    await db.commit()
    await db.refresh(event)
    return event


@router.delete("/{event_id}", response_model=CustomEventResponse)
async def soft_delete_custom_event(
    event_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(CustomEvent).where(CustomEvent.id == event_id)
    )
    event = result.scalar_one_or_none()
    if event is None:
        raise HTTPException(status_code=404, detail="Evento no encontrado.")

    event.is_active = False
    await db.commit()
    await db.refresh(event)
    return event
