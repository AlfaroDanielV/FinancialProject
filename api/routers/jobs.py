"""Job endpoints — run batch operations manually.

Protected by the same X-Shortcut-Token header as the iPhone webhook. This is a
stop-gap until Phase 5+ wires up a real scheduler (Celery / RQ / arq).
"""
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..database import get_db
from ..schemas.notifications import JobRunResult
from ..services import recurrence

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])


def _check_token(x_shortcut_token: Optional[str]) -> None:
    if x_shortcut_token != settings.shortcut_token:
        raise HTTPException(status_code=401, detail="Token inválido.")


@router.post("/generate-occurrences", response_model=JobRunResult)
async def job_generate_occurrences(
    horizon_months: int = 6,
    x_shortcut_token: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db),
):
    _check_token(x_shortcut_token)
    created = await recurrence.generate_occurrences_all(
        db, horizon_months=horizon_months
    )
    await db.commit()
    return JobRunResult(
        job="generate_occurrences_all", processed=created, created=created
    )


@router.post("/mark-overdue", response_model=JobRunResult)
async def job_mark_overdue(
    x_shortcut_token: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db),
):
    _check_token(x_shortcut_token)
    flipped = await recurrence.mark_overdue(db)
    await db.commit()
    return JobRunResult(
        job="mark_overdue", processed=flipped, updated=flipped
    )


@router.post("/compute-notifications", response_model=JobRunResult)
async def job_compute_notifications(
    x_shortcut_token: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db),
):
    _check_token(x_shortcut_token)
    created = await recurrence.compute_pending_notifications(db)
    await db.commit()
    return JobRunResult(
        job="compute_pending_notifications",
        processed=created,
        created=created,
    )
