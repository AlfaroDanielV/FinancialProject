"""Job endpoints — run batch operations manually for the caller's user.

Auth: `X-Shortcut-Token` (strict; the dev `X-User-Id` shim is NOT honored
here so cron / iPhone Shortcut continue to work without dev tooling).

Each job processes only the calling user's data. A real scheduler (Celery /
arq on Redis) and admin-style fan-out across users will land in Phase 5+.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..dependencies import current_user_via_token
from ..models.user import User
from ..schemas.notifications import JobRunResult
from ..services import recurrence

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])


@router.post("/generate-occurrences", response_model=JobRunResult)
async def job_generate_occurrences(
    horizon_months: int = 6,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_via_token),
):
    created = await recurrence.generate_occurrences_all(
        db, user.id, horizon_months=horizon_months
    )
    await db.commit()
    return JobRunResult(
        job="generate_occurrences_all", processed=created, created=created
    )


@router.post("/mark-overdue", response_model=JobRunResult)
async def job_mark_overdue(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_via_token),
):
    flipped = await recurrence.mark_overdue(db, user.id)
    await db.commit()
    return JobRunResult(
        job="mark_overdue", processed=flipped, updated=flipped
    )


@router.post("/compute-notifications", response_model=JobRunResult)
async def job_compute_notifications(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_via_token),
):
    created = await recurrence.compute_pending_notifications(db, user.id)
    await db.commit()
    return JobRunResult(
        job="compute_pending_notifications",
        processed=created,
        created=created,
    )
