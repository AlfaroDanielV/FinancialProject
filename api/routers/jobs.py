"""Job endpoints — run batch operations manually for the caller's user.

Auth: `X-Shortcut-Token` (strict; the dev `X-User-Id` shim is NOT honored
here so cron / iPhone Shortcut continue to work without dev tooling).

Each job processes only the calling user's data. A real scheduler (Celery /
arq on Redis) and admin-style fan-out across users will land in Phase 5+.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..database import get_db
from ..dependencies import current_user_via_token
from ..models.user import User
from ..schemas.notifications import JobRunResult
from ..schemas.nudges import NudgeDeliveryResult, NudgeEvaluateResult
from ..services import recurrence
from ..services.nudges.delivery import deliver_all as deliver_all_nudges
from ..services.nudges.orchestrator import evaluate_all as evaluate_all_nudges
from ..services.nudges.phrasing import AnthropicPhrasingClient

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


@router.post("/evaluate-nudges", response_model=NudgeEvaluateResult)
async def job_evaluate_nudges(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_via_token),
):
    """Run every Phase 5d evaluator against the caller's data, persist
    fresh candidates. Idempotent — re-runs produce created=0."""
    result = await evaluate_all_nudges(db, user_id=user.id)
    await db.commit()
    return result


@router.post("/deliver-nudges", response_model=NudgeDeliveryResult)
async def job_deliver_nudges(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_via_token),
):
    """Process the caller's pending nudges: phrase via LLM and send via
    Telegram, subject to quiet hours / silence / rate-limit filters.
    Idempotent: re-running right after does nothing because everything
    just sent is status='sent' now."""
    from bot.nudges_send import telegram_send_fn

    phrasing = AnthropicPhrasingClient(api_key=settings.anthropic_api_key)
    result = await deliver_all_nudges(
        db,
        user_id=user.id,
        phrasing_client=phrasing,
        send_fn=telegram_send_fn,
        model=settings.llm_extraction_model,
    )
    await db.commit()
    return result
