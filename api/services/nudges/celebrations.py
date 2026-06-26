"""Event-driven celebration trigger (Phase 8 B5).

When the user hits an earned moment — a goal flips to achieved, a debt is paid
off — we want the celebration to fire IMMEDIATELY (peak-end rule), not wait for
the next scheduler/nightly pass. This wraps the orchestrator's ``evaluate_all``
in a fully isolated, best-effort call so a goal/debt write is NEVER broken by a
celebration evaluation.

Same discipline as the snapshot capture in ``workers/insights_nightly.py``:
its OWN session and its OWN try/except. A failure here is logged loudly and
swallowed — a missed celebration is recoverable (the nightly job + the 4×/day
scheduler re-run the same idempotent evaluators), a broken goal/debt write is
not.

``evaluate_all`` is idempotent (ON CONFLICT on the dedup key), so triggering on
every relevant write is safe — a re-trigger of an already-celebrated milestone
creates nothing.
"""
from __future__ import annotations

import logging
import uuid
from typing import Optional, Sequence

from .evaluators import (
    BaseNudgeEvaluator,
    DebtPaidOffEvaluator,
    FirstFullMonthEvaluator,
    GoalAchievedEvaluator,
    UnderBudgetMonthEvaluator,
)
from .orchestrator import evaluate_all


log = logging.getLogger("nudges.celebrations")


# The four positive-milestone evaluators (the snapshot-dependent two ride the
# nightly job; the goal/debt two ride the event triggers below + nightly safety
# net). Reused by workers/insights_nightly.py.
CELEBRATION_EVALUATORS: list[BaseNudgeEvaluator] = [
    GoalAchievedEvaluator(),
    DebtPaidOffEvaluator(),
    FirstFullMonthEvaluator(),
    UnderBudgetMonthEvaluator(),
]


async def trigger_celebration_nudges(
    *,
    user_id: uuid.UUID,
    evaluators: Optional[Sequence[BaseNudgeEvaluator]] = None,
) -> None:
    """Run the celebration evaluators for one user in an isolated session.

    Best-effort: never raises. Pass a narrowed ``evaluators`` list (e.g. just
    ``[GoalAchievedEvaluator()]``) to scope the event trigger to the milestone
    that actually changed; omit it to run all four.
    """
    registry = list(evaluators) if evaluators is not None else CELEBRATION_EVALUATORS
    try:
        # Imported lazily so importing this module never drags in the DB engine
        # (keeps the router import graph light + tests fast).
        from api.database import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            await evaluate_all(session, user_id=user_id, evaluators=registry)
            await session.commit()
    except Exception:
        log.exception("celebration_trigger_failed user=%s", user_id)
