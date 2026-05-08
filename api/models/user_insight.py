import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TIMESTAMP

from .base import Base


_VALID_INSIGHT_TYPES = (
    "spending_pattern",
    "recurring_drift",
    "cash_flow_stability",
    "debt_load",
    "emergency_fund",
    "cr_seasonal_pattern",
    "stated_preference",
    "stated_goal",
    "behavioral_flag",
    "archetype",
    "risk_posture",
    "decision_style",
    "financial_literacy",
    "stated_observed_gap",
)
_VALID_SOURCES = ("computed", "llm_extracted", "user_override")
_VALID_AUDIT_ACTIONS = (
    "created",
    "updated",
    "deleted",
    "reinforced",
    "locked",
    "unlocked",
    "exported",
)
_VALID_AUDIT_ACTORS = ("computed_worker", "llm_extractor", "user", "admin")


def _quoted_in_clause(values: tuple[str, ...]) -> str:
    return "(" + ",".join(f"'{value}'" for value in values) + ")"


class UserInsight(Base):
    """Per-user typed insight (Phase 6c).

    `content` is JSONB, validated app-side against the corresponding
    Pydantic model in `api/schemas/insights.py` keyed by `insight_type`.
    The DB only enforces the type list and confidence range; payload
    correctness is the writer's responsibility.

    Three sources:
      - 'computed': nightly worker, deterministic from real data.
      - 'llm_extracted': post-query Haiku, capped 0.85 confidence base.
      - 'user_override': /editar_memoria. Always carries user_locked=true.

    `user_locked=true` rows are sacred: neither the computed worker nor
    the LLM extractor may overwrite them. Only the user can change or
    delete them via /olvidar / /editar_memoria.
    """

    __tablename__ = "user_insights"
    __table_args__ = (
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_user_insights_confidence_range",
        ),
        CheckConstraint(
            f"source IN {_quoted_in_clause(_VALID_SOURCES)}",
            name="ck_user_insights_source",
        ),
        CheckConstraint(
            f"insight_type IN {_quoted_in_clause(_VALID_INSIGHT_TYPES)}",
            name="ck_user_insights_type",
        ),
        UniqueConstraint(
            "user_id",
            "insight_type",
            "dedup_key",
            name="uq_user_insights_dedup",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    insight_type: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    confidence: Mapped[Decimal] = mapped_column(
        Numeric(3, 2), nullable=False
    )
    source: Mapped[str] = mapped_column(Text, nullable=False)
    valid_until: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False
    )
    user_locked: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    dedup_key: Mapped[str] = mapped_column(Text, nullable=False)
    reinforcement_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1
    )
    last_reinforced_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )


class UserInsightAudit(Base):
    """Immutable timeline of every memory-state change (Phase 6c).

    Written by `services/insights/persister.audit()` plus the privacy
    endpoints and Telegram commands. `payload` is JSONB validated
    app-side against per-action schemas in `api/schemas/insights.py`.

    `insight_id` is NULL for actions that don't reference a single row
    (e.g. 'exported' covers many) and stays a dangling UUID for
    'deleted' actions after the underlying row is hard-deleted —
    the audit timeline must outlive the data.
    """

    __tablename__ = "user_insights_audit"
    __table_args__ = (
        CheckConstraint(
            f"action IN {_quoted_in_clause(_VALID_AUDIT_ACTIONS)}",
            name="ck_user_insights_audit_action",
        ),
        CheckConstraint(
            f"actor IN {_quoted_in_clause(_VALID_AUDIT_ACTORS)}",
            name="ck_user_insights_audit_actor",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    insight_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    action: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSONB, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), default=datetime.utcnow
    )
