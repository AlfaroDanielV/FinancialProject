"""Evaluator orchestrator.

Runs each registered evaluator, filters out candidates whose user has an
active silence for that nudge_type, and inserts the survivors with
ON CONFLICT DO NOTHING. Does NOT commit — the caller does, matching the
Phase 4 `/jobs/*` pattern.

Returned per-type counts tell you what happened:
    candidates    — what the evaluator produced
    created       — rows actually inserted (fresh)
    deduplicated  — rows that hit the UNIQUE constraint (already exist)
    silenced      — candidates skipped because the user is silenced

A re-run with no state change produces created=0 for every type. That's
the idempotency contract; it's what lets us run the job from cron without
thinking about overlap.
"""
from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.user_nudge import UserNudge, UserNudgeSilence
from ...schemas.nudges import NudgeEvaluateCounts, NudgeEvaluateResult
from .evaluators import ALL_EVALUATORS, BaseNudgeEvaluator, NudgeCandidate


log = logging.getLogger("nudges.orchestrator")


async def _active_silence_user_ids(
    session: AsyncSession,
    *,
    nudge_type: str,
    now: datetime,
    user_ids: set[uuid.UUID],
) -> set[uuid.UUID]:
    """Subset of user_ids whose silence for `nudge_type` is still active
    at `now`. Bulk lookup — one query per nudge_type."""
    if not user_ids:
        return set()
    stmt = select(UserNudgeSilence.user_id).where(
        UserNudgeSilence.nudge_type == nudge_type,
        UserNudgeSilence.silenced_until > now,
        UserNudgeSilence.user_id.in_(user_ids),
    )
    result = await session.execute(stmt)
    return set(result.scalars().all())


async def _insert_candidate(
    session: AsyncSession, *, nudge_type: str, candidate: NudgeCandidate
) -> bool:
    """INSERT ... ON CONFLICT DO NOTHING. Returns True when a new row
    was written, False when the (user_id, dedup_key) pair already exists."""
    stmt = (
        pg_insert(UserNudge)
        .values(
            user_id=candidate.user_id,
            nudge_type=nudge_type,
            priority=candidate.priority,
            dedup_key=candidate.dedup_key,
            payload=candidate.payload,
            source_notification_event_id=candidate.source_notification_event_id,
        )
        .on_conflict_do_nothing(
            index_elements=["user_id", "dedup_key"]
        )
        .returning(UserNudge.id)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none() is not None


async def evaluate_all(
    session: AsyncSession,
    now: Optional[datetime] = None,
    *,
    user_id: Optional[uuid.UUID] = None,
    evaluators: Optional[list[BaseNudgeEvaluator]] = None,
) -> NudgeEvaluateResult:
    """Run every evaluator, apply silence filter, persist survivors.

    The caller commits. Passing `user_id` scopes every evaluator to that
    user — that's what the /jobs/evaluate-nudges endpoint does.

    `evaluators` override is for tests; production uses ALL_EVALUATORS.
    """
    effective_now = now or datetime.now(timezone.utc)
    registry = evaluators if evaluators is not None else ALL_EVALUATORS

    per_type: dict[str, NudgeEvaluateCounts] = {}
    totals: dict[str, int] = defaultdict(int)

    for evaluator in registry:
        counts = NudgeEvaluateCounts(nudge_type=evaluator.nudge_type)
        candidates = await evaluator.evaluate(
            session, effective_now, user_id=user_id
        )
        counts.candidates = len(candidates)

        if candidates:
            user_ids = {c.user_id for c in candidates}
            silenced = await _active_silence_user_ids(
                session,
                nudge_type=evaluator.nudge_type,
                now=effective_now,
                user_ids=user_ids,
            )

            for candidate in candidates:
                if candidate.user_id in silenced:
                    counts.silenced += 1
                    continue
                inserted = await _insert_candidate(
                    session,
                    nudge_type=evaluator.nudge_type,
                    candidate=candidate,
                )
                if inserted:
                    counts.created += 1
                else:
                    counts.deduplicated += 1

        per_type[evaluator.nudge_type] = counts
        totals["created"] += counts.created
        totals["deduplicated"] += counts.deduplicated
        totals["silenced"] += counts.silenced

    log.info(
        "evaluate_all user=%s created=%d dedup=%d silenced=%d per_type=%s",
        user_id,
        totals["created"],
        totals["deduplicated"],
        totals["silenced"],
        {k: (v.created, v.deduplicated, v.silenced) for k, v in per_type.items()},
    )

    return NudgeEvaluateResult(
        evaluated_at=effective_now,
        per_type=list(per_type.values()),
        created=totals["created"],
        deduplicated=totals["deduplicated"],
        silenced=totals["silenced"],
    )
