import uuid
import math
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..dependencies import current_user
from ..models.goal import Goal
from ..models.user import User
from ..schemas.goals import (
    ContributeRequest,
    GoalCreate,
    GoalProgress,
    GoalResponse,
    GoalUpdate,
)

router = APIRouter(prefix="/api/v1/goals", tags=["goals"])


@router.post("", response_model=GoalResponse, status_code=201)
async def create_goal(
    payload: GoalCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    goal = Goal(
        user_id=user.id,
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
    user: User = Depends(current_user),
):
    stmt = select(Goal).where(Goal.user_id == user.id)
    if status:
        stmt = stmt.where(Goal.status == status)
    stmt = stmt.order_by(Goal.priority.asc(), Goal.created_at.desc())

    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.get("/progress", response_model=list[GoalProgress])
async def goals_progress(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    result = await db.execute(
        select(Goal).where(Goal.user_id == user.id, Goal.status == "active")
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
    user: User = Depends(current_user),
):
    result = await db.execute(
        select(Goal).where(Goal.id == goal_id, Goal.user_id == user.id)
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
    user: User = Depends(current_user),
):
    result = await db.execute(
        select(Goal).where(Goal.id == goal_id, Goal.user_id == user.id)
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
    user: User = Depends(current_user),
):
    result = await db.execute(
        select(Goal).where(Goal.id == goal_id, Goal.user_id == user.id)
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
    user: User = Depends(current_user),
):
    result = await db.execute(
        select(Goal).where(Goal.id == goal_id, Goal.user_id == user.id)
    )
    goal = result.scalar_one_or_none()
    if not goal:
        raise HTTPException(status_code=404, detail="Meta no encontrada.")

    goal.status = "abandoned"
    await db.commit()
    await db.refresh(goal)
    return goal
