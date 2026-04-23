"""Evaluator interface.

An evaluator answers ONE question, deterministically: "who should get
nudged with MY specific nudge_type, right now?" It returns candidates;
the orchestrator handles dedup and silencing.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional, Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class NudgeCandidate:
    """One prospective row in user_nudges.

    The orchestrator turns candidates into INSERTs with ON CONFLICT DO NOTHING.
    `dedup_key` is the uniqueness guard — stable for the same logical
    situation so repeat evaluations don't duplicate.
    """

    user_id: uuid.UUID
    dedup_key: str
    payload: dict[str, Any] = field(default_factory=dict)
    priority: str = "normal"
    source_notification_event_id: Optional[uuid.UUID] = None


@runtime_checkable
class BaseNudgeEvaluator(Protocol):
    """Evaluators expose their nudge_type and one async method.

    Implementations MUST NOT insert, update, or commit — the orchestrator
    owns writes so it can batch + handle errors uniformly.

    `user_id` scopes the evaluation to a single user — that's what the
    `/jobs/evaluate-nudges` endpoint passes (matches the per-user Phase 4
    pattern). Passing `None` is reserved for a future multi-user sweep
    (cron / admin); today all callers scope.
    """

    nudge_type: str

    async def evaluate(
        self,
        session: AsyncSession,
        now: datetime,
        *,
        user_id: Optional[uuid.UUID] = None,
    ) -> list[NudgeCandidate]: ...
