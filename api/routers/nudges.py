"""REST endpoints for user_nudges.

Read + state transitions. Creation is NOT exposed — the orchestrator is
the only thing that creates nudges. Auth follows the same rule as other
domain routes: `current_user` (accepts the dev `X-User-Id` shim; magic-
link auth lands in Phase 6).
"""
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..dependencies import current_user
from ..models.enums import NudgeStatus
from ..models.user import User
from ..models.user_nudge import UserNudge
from ..schemas.nudges import (
    NudgeActionResponse,
    NudgeListResponse,
    UserNudgeResponse,
)
from ..services.nudges.actions import mark_acted_on, mark_dismissed

router = APIRouter(prefix="/api/v1/nudges", tags=["nudges"])


@router.get("", response_model=NudgeListResponse)
async def list_nudges(
    status_filter: Optional[NudgeStatus] = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
) -> NudgeListResponse:
    stmt = (
        select(UserNudge)
        .where(UserNudge.user_id == user.id)
        .order_by(UserNudge.created_at.desc())
        .limit(limit)
    )
    if status_filter is not None:
        stmt = stmt.where(UserNudge.status == status_filter.value)
    result = await db.execute(stmt)
    rows = list(result.scalars().all())
    return NudgeListResponse(
        items=[UserNudgeResponse.model_validate(r) for r in rows]
    )


@router.post(
    "/{nudge_id}/dismiss",
    response_model=NudgeActionResponse,
    status_code=status.HTTP_200_OK,
)
async def dismiss_nudge(
    nudge_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
) -> NudgeActionResponse:
    outcome = await mark_dismissed(db, user_id=user.id, nudge_id=nudge_id)
    await db.commit()
    return NudgeActionResponse(
        nudge=UserNudgeResponse.model_validate(outcome.nudge),
        silence_created=outcome.silence_created,
    )


@router.post(
    "/{nudge_id}/act",
    response_model=NudgeActionResponse,
    status_code=status.HTTP_200_OK,
)
async def act_on_nudge(
    nudge_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
) -> NudgeActionResponse:
    nudge = await mark_acted_on(db, user_id=user.id, nudge_id=nudge_id)
    await db.commit()
    return NudgeActionResponse(
        nudge=UserNudgeResponse.model_validate(nudge),
        silence_created=False,
    )
