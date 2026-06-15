"""REST endpoints for user_nudges.

Read + state transitions. Creation is NOT exposed — the orchestrator is
the only thing that creates nudges. Auth follows the same rule as other
domain routes: `current_user` (accepts the dev `X-User-Id` shim; magic-
link auth lands in Phase 6).
"""
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..dependencies import current_user
from ..models.enums import NudgeStatus
from ..models.user import User
from ..models.user_nudge import UserNudge
from ..schemas.nudges import (
    NudgeActionResponse,
    NudgeFeedButton,
    NudgeFeedItem,
    NudgeFeedResponse,
    NudgeListResponse,
    UserNudgeResponse,
)
from ..services.dedup import resolve_duplicate
from ..services.nudges.actions import mark_acted_on, mark_dismissed
from ..services.nudges.feed import build_feed
from ..services.transactions import TXN_DELETE_REASON_ES, TransactionDeleteError

router = APIRouter(prefix="/api/v1/nudges", tags=["nudges"])

NUDGE_TYPE_DUPLICATE = "duplicate_transaction"


async def _load_owned_nudge(
    db: AsyncSession, *, user_id: uuid.UUID, nudge_id: uuid.UUID
) -> UserNudge:
    nudge = (
        await db.execute(
            select(UserNudge).where(
                UserNudge.id == nudge_id, UserNudge.user_id == user_id
            )
        )
    ).scalar_one_or_none()
    if nudge is None:
        raise HTTPException(status_code=404, detail="Nudge no encontrado.")
    return nudge


@router.get("/feed", response_model=NudgeFeedResponse)
async def nudge_feed(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
) -> NudgeFeedResponse:
    """Native in-app alerts feed: pending nudges rendered to display text.

    Read-only — does NOT mark anything sent. Silenced types are filtered;
    push-pacing (rate limit / quiet hours) is intentionally not applied to a
    surface the user opened. Act/dismiss flow through the existing
    /nudges/{id}/act and /nudges/{id}/dismiss endpoints.
    """
    entries = await build_feed(db, user_id=user.id)
    return NudgeFeedResponse(
        items=[
            NudgeFeedItem(
                id=e.id,
                nudge_type=e.nudge_type,
                priority=e.priority,
                text=e.text,
                created_at=e.created_at,
                buttons=[
                    NudgeFeedButton(label=b.label, verb=b.verb) for b in e.buttons
                ],
            )
            for e in entries
        ]
    )


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
    nudge = await _load_owned_nudge(db, user_id=user.id, nudge_id=nudge_id)

    # "Conservar" on a duplicate warning: keep the row (clear the flag) and
    # resolve the nudge as acted_on — NEVER through mark_dismissed, whose
    # auto-silence would mute duplicate detection after two false positives.
    if nudge.nudge_type == NUDGE_TYPE_DUPLICATE:
        await resolve_duplicate(db, user=user, nudge=nudge, keep=True)
        await db.commit()
        await db.refresh(nudge)
        return NudgeActionResponse(
            nudge=UserNudgeResponse.model_validate(nudge),
            silence_created=False,
        )

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
    nudge = await _load_owned_nudge(db, user_id=user.id, nudge_id=nudge_id)

    # "Eliminar" on a duplicate warning: hard-delete the likely-duplicate row.
    # If the row can't be deleted (linked to a bill/debt) → 409; the nudge
    # stays pending so the user can choose "Conservar" instead.
    if nudge.nudge_type == NUDGE_TYPE_DUPLICATE:
        try:
            await resolve_duplicate(db, user=user, nudge=nudge, keep=False)
        except TransactionDeleteError as exc:
            await db.rollback()
            raise HTTPException(
                status_code=409,
                detail=TXN_DELETE_REASON_ES.get(
                    exc.reason_code, "No se puede eliminar este movimiento."
                ),
            ) from exc
        await db.commit()
        await db.refresh(nudge)
        return NudgeActionResponse(
            nudge=UserNudgeResponse.model_validate(nudge),
            silence_created=False,
        )

    nudge = await mark_acted_on(db, user_id=user.id, nudge_id=nudge_id)
    await db.commit()
    return NudgeActionResponse(
        nudge=UserNudgeResponse.model_validate(nudge),
        silence_created=False,
    )
