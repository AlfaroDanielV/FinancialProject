"""Persistence layer for Phase 6c insights.

Computed and LLM-extracted writers both pass validated `InsightContent`
objects here. This module owns `user_insights` mutation, deduplication,
`user_locked` protection, TTL assignment, and audit rows.

The caller owns transaction boundaries. Functions flush changes so IDs
are available for audit rows, but they do not commit.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.user_insight import UserInsight, UserInsightAudit
from api.schemas.insights import (
    CONFIDENCE_CAP_BY_SOURCE,
    LLM_REINFORCEMENT_HARD_CAP,
    LLM_REINFORCEMENT_STEP,
    InsightContent,
    validate_audit_payload,
    validate_insight_content,
    valid_until_for,
)

from .computed import computed_confidence

InsightSource = Literal["computed", "llm_extracted", "user_override"]
AuditActor = Literal["computed_worker", "llm_extractor", "user", "admin"]

_SOURCE_ACTOR: dict[str, AuditActor] = {
    "computed": "computed_worker",
    "llm_extracted": "llm_extractor",
    "user_override": "user",
}


@dataclass
class PersistStats:
    created: int = 0
    updated: int = 0
    reinforced: int = 0
    skipped_locked: int = 0
    skipped_user_override: int = 0


async def persist_insights(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    insights: list[InsightContent],
    source: InsightSource,
    now: datetime | None = None,
    actor: AuditActor | None = None,
) -> PersistStats:
    """Upsert a batch of validated insights for one user.

    Existing locked or user-overridden rows are protected from automatic
    writers. Skips are counted but not audited because the audit table is
    reserved for actual state transitions.
    """
    effective_now = now or datetime.now(timezone.utc)
    effective_actor = actor or _SOURCE_ACTOR[source]
    stats = PersistStats()

    for content in insights:
        dedup_key = content.dedup_key()
        result = await session.execute(
            select(UserInsight).where(
                UserInsight.user_id == user_id,
                UserInsight.insight_type == content.type,
                UserInsight.dedup_key == dedup_key,
            )
        )
        existing = result.scalar_one_or_none()

        if existing is not None and existing.user_locked:
            stats.skipped_locked += 1
            continue
        if (
            existing is not None
            and existing.source == "user_override"
            and source != "user_override"
        ):
            stats.skipped_user_override += 1
            continue

        confidence = _confidence_for_source(content, source)
        valid_until = valid_until_for(content, now=effective_now)
        content_json = _content_json(content)

        if existing is None:
            insight = UserInsight(
                user_id=user_id,
                insight_type=content.type,
                content=content_json,
                confidence=confidence,
                source=source,
                valid_until=valid_until,
                user_locked=source == "user_override",
                dedup_key=dedup_key,
                reinforcement_count=1,
                created_at=effective_now,
                updated_at=effective_now,
            )
            session.add(insight)
            await session.flush()
            await audit(
                session,
                user_id=user_id,
                insight_id=insight.id,
                actor=effective_actor,
                payload={
                    "action": "created",
                    "insight_type": content.type,
                    "dedup_key": dedup_key,
                    "content": content_json,
                    "confidence": confidence,
                    "source": source,
                    "valid_until": valid_until,
                },
                created_at=effective_now,
            )
            stats.created += 1
            continue

        previous_content = existing.content
        previous_confidence = _as_decimal(existing.confidence)
        if _content_equivalent(previous_content, content_json):
            confidence_after = _reinforced_confidence(
                previous_confidence=previous_confidence,
                new_confidence=confidence,
                source=source,
                reinforcement_count=existing.reinforcement_count + 1,
            )
            existing.reinforcement_count += 1
            existing.last_reinforced_at = effective_now
            existing.confidence = confidence_after
            existing.valid_until = max(existing.valid_until, valid_until)
            existing.updated_at = effective_now
            await session.flush()
            await audit(
                session,
                user_id=user_id,
                insight_id=existing.id,
                actor=effective_actor,
                payload={
                    "action": "reinforced",
                    "insight_type": content.type,
                    "dedup_key": dedup_key,
                    "reinforcement_count_after": existing.reinforcement_count,
                    "confidence_before": previous_confidence,
                    "confidence_after": confidence_after,
                },
                created_at=effective_now,
            )
            stats.reinforced += 1
            continue

        existing.content = content_json
        existing.confidence = confidence
        existing.source = source
        existing.valid_until = valid_until
        existing.user_locked = source == "user_override"
        existing.reinforcement_count = 1
        existing.last_reinforced_at = None
        existing.updated_at = effective_now
        await session.flush()
        await audit(
            session,
            user_id=user_id,
            insight_id=existing.id,
            actor=effective_actor,
            payload={
                "action": "updated",
                "insight_type": content.type,
                "dedup_key": dedup_key,
                "previous_content": previous_content,
                "new_content": content_json,
                "previous_confidence": previous_confidence,
                "new_confidence": confidence,
            },
            created_at=effective_now,
        )
        stats.updated += 1

    return stats


async def audit(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    insight_id: uuid.UUID | None,
    actor: AuditActor,
    payload: dict,
    created_at: datetime | None = None,
) -> UserInsightAudit:
    """Validate and insert a typed audit payload."""
    validated = validate_audit_payload(payload)
    row = UserInsightAudit(
        user_id=user_id,
        insight_id=insight_id,
        action=validated.action,
        actor=actor,
        payload=validated.model_dump(mode="json"),
        created_at=created_at or datetime.now(timezone.utc),
    )
    session.add(row)
    await session.flush()
    return row


def _confidence_for_source(content: InsightContent, source: InsightSource) -> Decimal:
    if source == "computed":
        return _cap_confidence(computed_confidence(content), source)
    if source == "user_override":
        return Decimal("1.00")

    raw = getattr(content, "_llm_confidence", None)
    if raw is None:
        raw = getattr(content, "confidence", Decimal("0.85"))
    return _cap_confidence(_as_decimal(raw), source)


def _reinforced_confidence(
    *,
    previous_confidence: Decimal,
    new_confidence: Decimal,
    source: InsightSource,
    reinforcement_count: int,
) -> Decimal:
    if source == "llm_extracted":
        boosted = Decimal("0.85") + (
            LLM_REINFORCEMENT_STEP * Decimal(max(reinforcement_count - 1, 0))
        )
        return min(
            max(previous_confidence, new_confidence, boosted),
            LLM_REINFORCEMENT_HARD_CAP,
        )
    return _cap_confidence(max(previous_confidence, new_confidence), source)


def _cap_confidence(value: Decimal, source: InsightSource) -> Decimal:
    cap = CONFIDENCE_CAP_BY_SOURCE[source]
    return max(Decimal("0.00"), min(value, cap)).quantize(Decimal("0.01"))


def _content_json(content: InsightContent) -> dict:
    return content.model_dump(mode="json")


def _content_equivalent(existing: dict, new: dict) -> bool:
    try:
        existing_model = validate_insight_content(existing)
        new_model = validate_insight_content(new)
    except Exception:
        return existing == new
    return existing_model.model_dump(mode="json") == new_model.model_dump(mode="json")


def _as_decimal(value: object) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))
