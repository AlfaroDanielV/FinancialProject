"""Pydantic schemas for Phase 5d nudges.

Response-oriented: the API exposes reads, dismiss, and act. Creation is
internal (evaluators write directly to the ORM with ON CONFLICT DO NOTHING),
so no Create schema is public.
"""
import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field

from ..models.enums import NudgePriority, NudgeStatus, NudgeType


class UserNudgeResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    nudge_type: str
    priority: str
    dedup_key: str
    payload: dict[str, Any]
    source_notification_event_id: Optional[uuid.UUID]
    status: str
    delivery_channel: Optional[str]
    created_at: datetime
    sent_at: Optional[datetime]
    dismissed_at: Optional[datetime]
    acted_on_at: Optional[datetime]
    expired_at: Optional[datetime]

    model_config = {"from_attributes": True}


class NudgeListResponse(BaseModel):
    items: list[UserNudgeResponse]


# ── per-type candidate counts from evaluator orchestrator ────────────────────


class NudgeEvaluateCounts(BaseModel):
    """Per-type breakdown of what the orchestrator did. Mirrors the
    dataclass the orchestrator returns; kept as a BaseModel so the
    /jobs/evaluate-nudges endpoint can serialize it directly.
    """

    nudge_type: str
    candidates: int = 0
    created: int = 0
    deduplicated: int = 0
    silenced: int = 0


class NudgeEvaluateResult(BaseModel):
    evaluated_at: datetime
    per_type: list[NudgeEvaluateCounts]
    created: int = 0
    deduplicated: int = 0
    silenced: int = 0


# ── delivery job result ──────────────────────────────────────────────────────


class NudgeDeliveryResult(BaseModel):
    processed: int = 0
    sent: int = 0
    throttled_rate_limit: int = 0
    throttled_quiet_hours: int = 0
    throttled_silenced: int = 0
    failed: int = 0


# ── action endpoints ─────────────────────────────────────────────────────────


class NudgeActionResponse(BaseModel):
    """Returned by /dismiss and /act — the updated nudge plus, in the case
    of dismiss, whether a silence was auto-inserted.
    """

    nudge: UserNudgeResponse
    silence_created: bool = False


# ── query params helper (kept as a model so FastAPI can inject it) ───────────


class NudgeListQuery(BaseModel):
    status: Optional[NudgeStatus] = None
    limit: int = Field(default=50, ge=1, le=200)
