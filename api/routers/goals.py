import uuid
import math
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..database import get_db
from ..models.goal import Goal
from ..schemas.goals import (
    ContributeRequest,
    GoalCreate,
    GoalProgress,
    GoalResponse,
    GoalUpdate,
)

router = APIRouter(prefix="/api/v1/goals", tags=["goals"])


def _get_default_user_id() -> uuid.UUID:
    if not settings.default_user_id:
        raise HTTPException(
            status_code=503,
            detail="DEFAULT_USER_ID not configured. Run scripts/create_user.py first.",
        )
    return uuid.UUID(settings.default_user_id)


@router.post("", response_model=GoalResponse, status_code=201)
async def create_goal(
    payload: GoalCreate,
    db: AsyncSession = Depends(get_db),
):
    user_id = _get_default_user_id()
    goal = Goal(
        user_id=user_id,
        name=payload.name,
        target_amount=payload.target_amount,
        deadline=payload.deadline,
        priority=payload.priority,
        monthly_contribution=payload.monthly_contribution,
    )
    db.add(goal)
    await db.commit()
    await db.refresh(goal)
    return goal


@router.get("", response_model=list[GoalResponse])
async def list_goals(
    status: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    user_id = _get_default_user_id()
    stmt = select(Goal).where(Goal.user_id == user_id)
    if status:
        stmt = stmt.where(Goal.status == status)
    stmt = stmt.order_by(Goal.priority.asc(), Goal.created_at.desc())

    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.get("/progress", response_model=list[GoalProgress])
async def goals_progress(
    db: AsyncSession = Depends(get_db),
):
    user_id = _get_default_user_id()
    result = await db.execute(
        select(Goal).where(Goal.user_id == user_id, Goal.status == "active")
    )
    goals = list(result.scalars().all())
    today = date.today()

    progress_list = []
    for g in goals:
        target = float(g.target_amount)
        current = float(g.current_amount)
        remaining = max(target - current, 0)
        percent = (current / target * 100) if target > 0 else 0

        months_remaining = None
        monthly_needed = None
        on_track = None

        if g.deadline:
            days_left = (g.deadline - today).days
            months_remaining = max(math.ceil(days_left / 30.44), 0)
            if months_remaining > 0:
                monthly_needed = round(remaining / months_remaining, 2)
                if g.monthly_contribution:
                    on_track = monthly_needed <= float(g.monthly_contribution)
            elif remaining > 0:
                monthly_needed = remaining
                on_track = False

        progress_list.append(GoalProgress(
            id=g.id,
            name=g.name,
            target_amount=target,
            current_amount=current,
            remaining=round(remaining, 2),
            progress_percent=round(percent, 2),
            months_remaining=months_remaining,
            monthly_needed=monthly_needed,
            on_track=on_track,
            status=g.status,
        ))

    return progress_list


@router.get("/{goal_id}", response_model=GoalResponse)
async def get_goal(
    goal_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    user_id = _get_default_user_id()
    result = await db.execute(
        select(Goal).where(Goal.id == goal_id, Goal.user_id == user_id)
    )
    goal = result.scalar_one_or_none()
    if not goal:
        raise HTTPException(status_code=404, detail="Meta no encontrada.")
    return goal


@router.patch("/{goal_id}", response_model=GoalResponse)
async def update_goal(
    goal_id: uuid.UUID,
    payload: GoalUpdate,
    db: AsyncSession = Depends(get_db),
):
    user_id = _get_default_user_id()
    result = await db.execute(
        select(Goal).where(Goal.id == goal_id, Goal.user_id == user_id)
    )
    goal = result.scalar_one_or_none()
    if not goal:
        raise HTTPException(status_code=404, detail="Meta no encontrada.")

    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(goal, field, value)

    await db.commit()
    await db.refresh(goal)
    return goal


@router.post("/{goal_id}/contribute", response_model=GoalResponse)
async def contribute_to_goal(
    goal_id: uuid.UUID,
    payload: ContributeRequest,
    db: AsyncSession = Depends(get_db),
):
    user_id = _get_default_user_id()
    result = await db.execute(
        select(Goal).where(Goal.id == goal_id, Goal.user_id == user_id)
    )
    goal = result.scalar_one_or_none()
    if not goal:
        raise HTTPException(status_code=404, detail="Meta no encontrada.")

    if goal.status != "active":
        raise HTTPException(status_code=400, detail="Solo se puede contribuir a metas activas.")

    goal.current_amount = float(goal.current_amount) + payload.amount

    if float(goal.current_amount) >= float(goal.target_amount):
        goal.status = "completed"

    await db.commit()
    await db.refresh(goal)
    return goal


@router.delete("/{goal_id}", response_model=GoalResponse)
async def delete_goal(
    goal_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    user_id = _get_default_user_id()
    result = await db.execute(
        select(Goal).where(Goal.id == goal_id, Goal.user_id == user_id)
    )
    goal = result.scalar_one_or_none()
    if not goal:
        raise HTTPException(status_code=404, detail="Meta no encontrada.")

    goal.status = "abandoned"
    await db.commit()
    await db.refresh(goal)
    return goal
