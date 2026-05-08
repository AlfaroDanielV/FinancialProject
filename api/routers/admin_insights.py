"""Phase 6c B10 — admin trigger for the nightly insights recompute.

    POST /api/v1/admin/insights/run-nightly[?user_id=<uuid>]

Auth: real `X-Shortcut-Token` (current_user_via_token, no dev shim).
The optional `?user_id=` is accepted for API stability with the gmail
admin pattern, but until P9 it must equal the caller's id — there is
no cross-tenant admin role yet.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..dependencies import current_user_via_token
from ..models.user import User
from ..services.insights.nightly_runner import (
    NightlyRunStats,
    run_nightly_for_user,
)


router = APIRouter(
    prefix="/api/v1/admin/insights",
    tags=["insights-admin"],
)


def _stats_to_dict(stats: NightlyRunStats) -> dict:
    return {
        "user_id": str(stats.user_id),
        "insights_proposed": stats.insights_proposed,
        "persisted": {
            "created": stats.persisted_created,
            "updated": stats.persisted_updated,
            "reinforced": stats.persisted_reinforced,
            "skipped_locked": stats.persisted_skipped_locked,
            "skipped_user_override": stats.persisted_skipped_user_override,
        },
        "gaps": {
            "emitted": stats.gaps_emitted,
            "created": stats.gaps_created,
            "updated": stats.gaps_updated,
            "reinforced": stats.gaps_reinforced,
            "expired_unsupported": stats.gaps_expired_unsupported,
        },
    }


@router.post("/run-nightly", status_code=200)
async def run_nightly(
    user_id: uuid.UUID | None = Query(
        default=None,
        description=(
            "Optional. When omitted, runs for the authenticated caller. "
            "When provided, must equal the caller's id (cross-tenant "
            "admin lands in P9)."
        ),
    ),
    user: User = Depends(current_user_via_token),
    db: AsyncSession = Depends(get_db),
) -> dict:
    target = user_id or user.id
    if target != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Solo podés disparar tu propio recálculo. "
                "Acceso multi-tenant llega en P9."
            ),
        )
    stats = await run_nightly_for_user(db, user_id=target)
    await db.commit()
    return _stats_to_dict(stats)
