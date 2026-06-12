import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TIMESTAMP

from .base import Base


class AdviceEvent(Base):
    """Append-only trace of every deterministic verdict surfaced to the user
    (Phase 7e). One row per advice emission — assess_purchase, savings
    capacity, the goal feasibility gate, card never-payoff analysis, the
    over-commitment nudge. `inputs` + `result` hold the full engine payloads
    so every verdict stays reproducible; `outcome_*` is reserved for a future
    labeling worker that links advice to what the user actually did.

    This is telemetry (like `llm_query_dispatches`), NOT financial state —
    recording must never block or alter the advice path. Rows are never
    updated by the emitting surfaces."""

    __tablename__ = "advice_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    verdict: Mapped[str] = mapped_column(String(30), nullable=False)
    surface: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    subject_type: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    subject_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    inputs: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    outcome_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
    )
    outcome: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    outcome_observed_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), default=datetime.utcnow
    )
