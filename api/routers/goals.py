import math
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..dependencies import current_user
from ..models.account import Account
from ..models.goal import Goal
from ..models.goal_contribution import GoalContribution
from ..models.user import User
from ..schemas.goals import (
    ContributeRequest,
    GoalCancelPreview,
    GoalCancelResult,
    GoalContributionCreate,
    GoalContributionResponse,
    GoalContributionResult,
    GoalForecastResponse,
    GoalProgress,
    GoalResponse,
    GoalCreate,
    GoalUpdate,
)
from ..services.clock import user_today
from ..services.goals import (
    cancel_goal_with_refunds,
    compute_cancel_preview,
    create_funded_contribution,
    has_unrefunded_sourced_contributions,
)

router = APIRouter(prefix="/api/v1/goals", tags=["goals"])


def _stored_status(status: str | None) -> str | None:
    if status == "completed":
        return "achieved"
    return status


async def _get_goal(
    db: AsyncSession, *, user_id: uuid.UUID, goal_id: uuid.UUID
) -> Goal:
    result = await db.execute(
        select(Goal).where(Goal.id == goal_id, Goal.user_id == user_id)
    )
    goal = result.scalar_one_or_none()
    if goal is None:
        raise HTTPException(status_code=404, detail="Meta no encontrada.")
    return goal


async def _validate_linked_account(
    db: AsyncSession, *, user_id: uuid.UUID, account_id: uuid.UUID | None
) -> None:
    if account_id is None:
        return
    result = await db.execute(
        select(Account.id).where(
            Account.id == account_id,
            Account.user_id == user_id,
            Account.archived.is_(False),
        )
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=400, detail="Cuenta vinculada inválida.")


@router.post("", response_model=GoalResponse, status_code=201)
async def create_goal(
    payload: GoalCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    await _validate_linked_account(
        db, user_id=user.id, account_id=payload.linked_account_id
    )
    goal = Goal(
        user_id=user.id,
        name=payload.name,
        target_amount=payload.target_amount,
        target_currency=payload.target_currency,
        current_amount=payload.current_amount,
        target_date=payload.target_date,
        priority=payload.priority,
        monthly_contribution=payload.monthly_contribution,
        linked_account_id=payload.linked_account_id,
    )
    # Phase 7d: NO auto-achieve — with refunds at stake, "cumplida" is always
    # an explicit, confirmed user action (the app prompts at 100%).
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
        stmt = stmt.where(Goal.status == _stored_status(status))
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
    today = user_today(user)

    progress_list: list[GoalProgress] = []
    for goal in goals:
        target = Decimal(goal.target_amount)
        current = Decimal(goal.current_amount or 0)
        remaining = max(target - current, Decimal("0"))
        percent = (float(current / target) * 100) if target > 0 else 0

        months_remaining = None
        monthly_needed = None
        on_track = None

        if goal.target_date:
            days_left = (goal.target_date - today).days
            months_remaining = max(math.ceil(days_left / 30.44), 0)
            if months_remaining > 0:
                monthly_needed = (remaining / Decimal(months_remaining)).quantize(
                    Decimal("0.01")
                )
                if goal.monthly_contribution:
                    on_track = monthly_needed <= Decimal(goal.monthly_contribution)
            elif remaining > 0:
                monthly_needed = remaining
                on_track = False

        progress_list.append(
            GoalProgress(
                id=goal.id,
                name=goal.name,
                target_amount=target,
                target_currency=goal.target_currency,
                current_amount=current,
                remaining=remaining,
                progress_percent=round(percent, 2),
                months_remaining=months_remaining,
                monthly_needed=monthly_needed,
                on_track=on_track,
                status=goal.status,
            )
        )

    return progress_list


@router.get("/{goal_id}", response_model=GoalResponse)
async def get_goal(
    goal_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    return await _get_goal(db, user_id=user.id, goal_id=goal_id)


@router.patch("/{goal_id}", response_model=GoalResponse)
async def update_goal(
    goal_id: uuid.UUID,
    payload: GoalUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    goal = await _get_goal(db, user_id=user.id, goal_id=goal_id)

    update_data = payload.model_dump(exclude_unset=True)
    if "deadline" in update_data and "target_date" not in update_data:
        update_data["target_date"] = update_data["deadline"]
    update_data.pop("deadline", None)
    if "status" in update_data:
        update_data["status"] = _stored_status(update_data["status"])
        # Phase 7d: abandoning via PATCH would skip the refund — close the
        # side door and point at the cancel endpoint.
        if update_data["status"] == "abandoned":
            raise HTTPException(
                status_code=400,
                detail=(
                    "Para cancelar la meta usá «Cancelar meta», así te "
                    "devolvemos los aportes a tus cuentas."
                ),
            )
    if "linked_account_id" in update_data:
        await _validate_linked_account(
            db, user_id=user.id, account_id=update_data["linked_account_id"]
        )

    for field, value in update_data.items():
        setattr(goal, field, value)

    await db.commit()
    await db.refresh(goal)
    return goal


async def _create_goal_contribution(
    goal: Goal,
    payload: GoalContributionCreate,
    db: AsyncSession,
    user: User,
) -> GoalContribution:
    """Phase 7d — funded contribution: requires a source account, validates
    live funds, creates a goal-marked negative transaction. No auto-achieve
    (cumplida is always explicit; refunds are at stake)."""
    if goal.status in ("abandoned", "achieved"):
        raise HTTPException(
            status_code=400,
            detail="Esta meta no acepta más contribuciones.",
        )
    contribution, _txn = await create_funded_contribution(
        db, user_id=user.id, goal=goal, payload=payload
    )
    return contribution


@router.post("/{goal_id}/contributions", response_model=GoalContributionResult)
async def add_goal_contribution(
    goal_id: uuid.UUID,
    payload: GoalContributionCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    goal = await _get_goal(db, user_id=user.id, goal_id=goal_id)
    contribution = await _create_goal_contribution(goal, payload, db, user)
    await db.commit()
    await db.refresh(goal)
    await db.refresh(contribution)
    return GoalContributionResult(goal=goal, contribution=contribution)


@router.post("/{goal_id}/contribute", response_model=GoalResponse)
async def contribute_to_goal(
    goal_id: uuid.UUID,
    payload: ContributeRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    goal = await _get_goal(db, user_id=user.id, goal_id=goal_id)
    await _create_goal_contribution(goal, payload, db, user)
    await db.commit()
    await db.refresh(goal)
    return goal


@router.get("/{goal_id}/cancel-preview", response_model=GoalCancelPreview)
async def goal_cancel_preview(
    goal_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    """Phase 7d — what a cancel would refund, per source account, plus the
    unrefundable bucket. Shown in the app BEFORE confirming (auditability)."""
    goal = await _get_goal(db, user_id=user.id, goal_id=goal_id)
    return await compute_cancel_preview(db, user_id=user.id, goal=goal)


@router.post("/{goal_id}/cancel", response_model=GoalCancelResult)
async def cancel_goal(
    goal_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    """Phase 7d — cancel the goal and refund the money to the accounts it
    came from. Idempotent. An achieved goal never refunds (in either
    direction) — 400."""
    goal = await _get_goal(db, user_id=user.id, goal_id=goal_id)
    if goal.status == "achieved":
        raise HTTPException(
            status_code=400,
            detail="Esta meta ya está cumplida — los aportes no se devuelven.",
        )
    refunds, unrefundable = await cancel_goal_with_refunds(
        db, user_id=user.id, goal=goal, today=user_today(user)
    )
    await db.commit()
    await db.refresh(goal)
    return GoalCancelResult(
        goal=goal,
        refunded_total=sum((r.amount for r in refunds), Decimal("0")),
        unrefundable_total=unrefundable,
        refunds=refunds,
    )


@router.delete("/{goal_id}", status_code=204)
async def delete_goal(
    goal_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    """Phase 7d — TRUE delete. Blocked while the goal still holds money a
    cancel would return; afterwards the goal + its contribution history go,
    but the account movements survive (transactions.goal_id SET NULL)."""
    goal = await _get_goal(db, user_id=user.id, goal_id=goal_id)
    if await has_unrefunded_sourced_contributions(db, goal_id=goal.id):
        raise HTTPException(
            status_code=409,
            detail=(
                "Esta meta tiene aportes sin devolver. Cancelá la meta "
                "primero para recuperar la plata."
            ),
        )
    await db.delete(goal)
    await db.commit()


@router.get(
    "/{goal_id}/contributions",
    response_model=list[GoalContributionResponse],
)
async def list_goal_contributions(
    goal_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    """Phase 6e B9 — list all contributions for a goal, most recent first."""
    await _get_goal(db, user_id=user.id, goal_id=goal_id)
    result = await db.execute(
        select(GoalContribution)
        .where(GoalContribution.goal_id == goal_id)
        .order_by(GoalContribution.occurred_at.desc())
    )
    return list(result.scalars().all())


def _month_start(d: date) -> date:
    return date(d.year, d.month, 1)


def _add_months(d: date, months: int) -> date:
    year = d.year + (d.month - 1 + months) // 12
    month = (d.month - 1 + months) % 12 + 1
    return date(year, month, 1)


@router.get("/{goal_id}/forecast", response_model=GoalForecastResponse)
async def goal_forecast(
    goal_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    """Phase 6e B9 — months-to-target at the last 3 months' contribution pace.

    Avg pace is computed over the last 3 complete calendar months. Goals
    with no contributions in that window return `has_enough_data=false`
    (we don't pretend a forecast we can't back). Already-achieved goals
    return zeros.
    """
    goal = await _get_goal(db, user_id=user.id, goal_id=goal_id)
    target = Decimal(goal.target_amount)
    current = Decimal(goal.current_amount or 0)
    remaining = max(target - current, Decimal("0"))

    if remaining == 0:
        return GoalForecastResponse(
            goal_id=goal.id,
            target_amount=target,
            current_amount=current,
            remaining=Decimal("0"),
            avg_monthly_contribution=Decimal("0"),
            months_to_target=0,
            projected_completion_date=None,
            has_enough_data=True,
            lookback_months=3,
        )

    today = user_today(user)
    window_start = _add_months(_month_start(today), -3)
    window_end = _month_start(today)

    result = await db.execute(
        select(GoalContribution.amount).where(
            GoalContribution.goal_id == goal_id,
            GoalContribution.occurred_at
            >= datetime.combine(window_start, datetime.min.time(), tzinfo=timezone.utc),
            GoalContribution.occurred_at
            < datetime.combine(window_end, datetime.min.time(), tzinfo=timezone.utc),
        )
    )
    amounts = [Decimal(row) for row in result.scalars().all()]
    total = sum(amounts, Decimal("0"))
    avg_monthly = (total / Decimal("3")).quantize(Decimal("0.01"))

    if avg_monthly <= 0:
        return GoalForecastResponse(
            goal_id=goal.id,
            target_amount=target,
            current_amount=current,
            remaining=remaining,
            avg_monthly_contribution=Decimal("0"),
            months_to_target=None,
            projected_completion_date=None,
            has_enough_data=False,
            lookback_months=3,
        )

    months = int(math.ceil(float(remaining / avg_monthly)))
    projected = _add_months(_month_start(today), months)
    return GoalForecastResponse(
        goal_id=goal.id,
        target_amount=target,
        current_amount=current,
        remaining=remaining,
        avg_monthly_contribution=avg_monthly,
        months_to_target=months,
        projected_completion_date=projected,
        has_enough_data=True,
        lookback_months=3,
    )
