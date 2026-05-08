"""Lifecycle management for Phase 6c user insights.

B5 owns three deterministic maintenance paths:
- expiry and hard purge after the 30-day grace window;
- reinforcement when the same observation reappears; and
- derived `stated_observed_gap` detection.

The functions flush but do not commit. Callers own transaction boundaries.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.user_insight import UserInsight
from api.schemas.insights import (
    CONFIDENCE_CAP_BY_SOURCE,
    LLM_REINFORCEMENT_HARD_CAP,
    LLM_REINFORCEMENT_STEP,
    CashFlowStabilityContent,
    DebtLoadContent,
    EmergencyFundContent,
    InsightContent,
    RiskPostureContent,
    StatedGoalContent,
    StatedObservedGapContent,
    StatedPreferenceContent,
    validate_insight_content,
    valid_until_for,
)

from .persister import PersistStats, audit, persist_insights

_RAW_QUOTE_REDACTED = "[eliminado por privacidad]"
_RAW_QUOTE_RETENTION_DAYS = 30
_HARD_DELETE_GRACE_DAYS = 30


@dataclass
class ExpireStats:
    expired_active: int = 0
    raw_quotes_redacted: int = 0
    hard_deleted: int = 0


@dataclass
class ReinforceResult:
    insight_id: uuid.UUID
    reinforcement_count: int
    confidence_before: Decimal
    confidence_after: Decimal
    valid_until_before: datetime
    valid_until_after: datetime
    skipped_locked: bool = False


@dataclass
class GapDetectionStats:
    emitted: int = 0
    expired_unsupported: int = 0
    created: int = 0
    updated: int = 0
    reinforced: int = 0


async def expire_stale(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    hard_delete_after_days: int = _HARD_DELETE_GRACE_DAYS,
) -> ExpireStats:
    """Redact old raw quotes and purge expired unlocked insights.

    There is no `expired_at` column by design. Rows are considered
    expired when `valid_until < now`; read paths filter them out. This
    function hard-deletes unlocked rows after the grace window and leaves
    `user_locked` rows alone.
    """
    effective_now = _aware_utc(now)
    stats = ExpireStats()

    stats.raw_quotes_redacted = await _redact_old_raw_quotes(
        session, now=effective_now
    )

    expired_rows = list(
        (
            await session.execute(
                select(UserInsight).where(
                    UserInsight.valid_until < effective_now,
                    UserInsight.user_locked.is_(False),
                )
            )
        )
        .scalars()
        .all()
    )
    stats.expired_active = len(expired_rows)

    purge_cutoff = effective_now - timedelta(days=hard_delete_after_days)
    to_delete = [row for row in expired_rows if row.valid_until < purge_cutoff]
    for row in to_delete:
        await _audit_deleted(
            session,
            row=row,
            reason="expired_swept",
            created_at=effective_now,
        )
        await session.delete(row)
        stats.hard_deleted += 1

    await session.flush()
    return stats


async def reinforce(
    session: AsyncSession,
    insight_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> ReinforceResult | None:
    """Extend TTL and bump confidence for one existing insight.

    LLM-extracted rows use the B5 formula:
    `min(0.85 + 0.05 * reinforcement_count, 0.95)`, where
    `reinforcement_count` means repeated observations beyond the first.
    Computed rows move by +0.05 up to 1.00. User-locked rows are not
    modified.
    """
    effective_now = _aware_utc(now)
    row = await session.get(UserInsight, insight_id)
    if row is None:
        return None

    confidence_before = _as_decimal(row.confidence)
    valid_until_before = _aware_utc(row.valid_until)
    if row.user_locked:
        return ReinforceResult(
            insight_id=row.id,
            reinforcement_count=row.reinforcement_count,
            confidence_before=confidence_before,
            confidence_after=confidence_before,
            valid_until_before=valid_until_before,
            valid_until_after=valid_until_before,
            skipped_locked=True,
        )

    content = validate_insight_content(row.content)
    row.reinforcement_count += 1
    row.last_reinforced_at = effective_now
    row.confidence = _reinforced_confidence(
        source=row.source,
        previous_confidence=confidence_before,
        reinforcement_count=row.reinforcement_count,
    )
    row.valid_until = max(
        _aware_utc(row.valid_until),
        valid_until_for(content, now=effective_now),
    )
    row.updated_at = effective_now
    await session.flush()

    await audit(
        session,
        user_id=row.user_id,
        insight_id=row.id,
        actor=_actor_for_source(row.source),
        payload={
            "action": "reinforced",
            "insight_type": row.insight_type,
            "dedup_key": row.dedup_key,
            "reinforcement_count_after": row.reinforcement_count,
            "confidence_before": confidence_before,
            "confidence_after": row.confidence,
        },
        created_at=effective_now,
    )
    return ReinforceResult(
        insight_id=row.id,
        reinforcement_count=row.reinforcement_count,
        confidence_before=confidence_before,
        confidence_after=_as_decimal(row.confidence),
        valid_until_before=valid_until_before,
        valid_until_after=row.valid_until,
    )


async def detect_stated_observed_gaps(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> GapDetectionStats:
    """Emit or expire derived gaps between stated and observed signals."""
    effective_now = _aware_utc(now)
    active = await _active_insights(session, user_id=user_id, now=effective_now)
    desired = _desired_gaps(active)
    desired_keys = {gap.dedup_key() for gap in desired}

    existing_gaps = [
        row
        for row in active
        if row.insight_type == "stated_observed_gap"
    ]

    stats = GapDetectionStats(emitted=len(desired))
    for gap_row in existing_gaps:
        if gap_row.dedup_key not in desired_keys:
            await _mark_gap_unsupported(
                session,
                gap_row,
                now=effective_now,
                reason="gap_no_longer_supported",
            )
            stats.expired_unsupported += 1

    if desired:
        persisted = await persist_insights(
            session,
            user_id=user_id,
            insights=desired,
            source="computed",
            now=effective_now,
            actor="computed_worker",
        )
        stats.created = persisted.created
        stats.updated = persisted.updated
        stats.reinforced = persisted.reinforced

    return stats


async def run_lifecycle_for_user(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> tuple[GapDetectionStats, ExpireStats]:
    """Convenience function for jobs and tests."""
    effective_now = _aware_utc(now)
    gaps = await detect_stated_observed_gaps(
        session, user_id=user_id, now=effective_now
    )
    expiry = await expire_stale(session, now=effective_now)
    return gaps, expiry


async def _active_insights(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    now: datetime,
) -> list[UserInsight]:
    result = await session.execute(
        select(UserInsight).where(
            UserInsight.user_id == user_id,
            or_(
                UserInsight.valid_until > now,
                UserInsight.user_locked.is_(True),
            ),
        )
    )
    return list(result.scalars().all())


def _desired_gaps(rows: list[UserInsight]) -> list[StatedObservedGapContent]:
    parsed: list[tuple[UserInsight, InsightContent]] = []
    for row in rows:
        if row.insight_type == "stated_observed_gap":
            continue
        try:
            parsed.append((row, validate_insight_content(row.content)))
        except Exception:
            continue

    stated_preferences = [
        (row, content)
        for row, content in parsed
        if isinstance(content, StatedPreferenceContent)
    ]
    stated_goals = [
        (row, content)
        for row, content in parsed
        if isinstance(content, StatedGoalContent)
    ]
    risk_postures = [
        (row, content)
        for row, content in parsed
        if isinstance(content, RiskPostureContent)
    ]
    cash_flow = next(
        (
            (row, content)
            for row, content in parsed
            if isinstance(content, CashFlowStabilityContent)
        ),
        None,
    )
    debt_load = next(
        (
            (row, content)
            for row, content in parsed
            if isinstance(content, DebtLoadContent)
        ),
        None,
    )
    emergency = next(
        (
            (row, content)
            for row, content in parsed
            if isinstance(content, EmergencyFundContent)
        ),
        None,
    )

    gaps: list[StatedObservedGapContent] = []
    if cash_flow is not None:
        cash_row, cash = cash_flow
        for stated_row, pref in stated_preferences:
            if pref.topic == "savings" and cash.savings_rate_pct <= Decimal("5.00"):
                gaps.append(
                    StatedObservedGapContent(
                        stated_insight_id=stated_row.id,
                        observed_insight_id=cash_row.id,
                        description=(
                            "Decís priorizar el ahorro, pero la tasa de "
                            f"ahorro reciente está en {cash.savings_rate_pct}%."
                        ),
                    )
                )
            if (
                pref.topic == "debt_payoff"
                and cash.savings_rate_pct <= Decimal("0.00")
            ):
                gaps.append(
                    StatedObservedGapContent(
                        stated_insight_id=stated_row.id,
                        observed_insight_id=cash_row.id,
                        description=(
                            "Decís priorizar pagar deudas, pero el flujo "
                            "reciente no deja margen positivo."
                        ),
                    )
                )

        for stated_row, goal in stated_goals:
            text = goal.goal_text.lower()
            if (
                goal.status in {"mentioned", "committed"}
                and ("ahorr" in text or "fondo" in text)
                and cash.savings_rate_pct <= Decimal("0.00")
            ):
                gaps.append(
                    StatedObservedGapContent(
                        stated_insight_id=stated_row.id,
                        observed_insight_id=cash_row.id,
                        description=(
                            "Tenés una meta de ahorro activa, pero el flujo "
                            "reciente no muestra ahorro neto."
                        ),
                    )
                )

    if debt_load is not None:
        debt_row, debt = debt_load
        for stated_row, posture in risk_postures:
            if (
                posture.posture == "conservative"
                and debt.debt_to_income_ratio >= Decimal("0.40")
            ):
                gaps.append(
                    StatedObservedGapContent(
                        stated_insight_id=stated_row.id,
                        observed_insight_id=debt_row.id,
                        description=(
                            "Decís tener una postura conservadora, pero el "
                            "servicio de deuda reciente está alto frente al ingreso."
                        ),
                    )
                )

    if emergency is not None:
        emergency_row, fund = emergency
        for stated_row, posture in risk_postures:
            if posture.posture == "aggressive" and fund.sufficiency in {
                "insufficient",
                "borderline",
            }:
                gaps.append(
                    StatedObservedGapContent(
                        stated_insight_id=stated_row.id,
                        observed_insight_id=emergency_row.id,
                        description=(
                            "Decís tolerar más riesgo, pero la cobertura "
                            "líquida todavía está baja."
                        ),
                    )
                )

    return _dedupe_gaps(gaps)


def _dedupe_gaps(
    gaps: list[StatedObservedGapContent],
) -> list[StatedObservedGapContent]:
    by_key: dict[str, StatedObservedGapContent] = {}
    for gap in gaps:
        by_key.setdefault(gap.dedup_key(), gap)
    return list(by_key.values())


async def _mark_gap_unsupported(
    session: AsyncSession,
    row: UserInsight,
    *,
    now: datetime,
    reason: str,
) -> None:
    row.valid_until = min(
        _aware_utc(row.valid_until),
        now - timedelta(seconds=1),
    )
    row.updated_at = now
    await audit(
        session,
        user_id=row.user_id,
        insight_id=row.id,
        actor="computed_worker",
        payload={
            "action": "deleted",
            "insight_type": row.insight_type,
            "dedup_key": row.dedup_key,
            "content_at_deletion": _content_for_deletion_audit(row),
            "confidence_at_deletion": _as_decimal(row.confidence),
            "deletion_reason": reason,
        },
        created_at=now,
    )
    await session.flush()


async def _redact_old_raw_quotes(session: AsyncSession, *, now: datetime) -> int:
    cutoff = now - timedelta(days=_RAW_QUOTE_RETENTION_DAYS)
    rows = list(
        (
            await session.execute(
                select(UserInsight).where(
                    UserInsight.insight_type == "stated_preference",
                    UserInsight.created_at < cutoff,
                )
            )
        )
        .scalars()
        .all()
    )

    count = 0
    for row in rows:
        content = dict(row.content or {})
        raw_quote = content.get("raw_quote")
        if not raw_quote or raw_quote == _RAW_QUOTE_REDACTED:
            continue
        content["raw_quote"] = _RAW_QUOTE_REDACTED
        row.content = content
        row.updated_at = now
        count += 1
    if count:
        await session.flush()
    return count


async def _audit_deleted(
    session: AsyncSession,
    *,
    row: UserInsight,
    reason: str,
    created_at: datetime,
) -> None:
    await audit(
        session,
        user_id=row.user_id,
        insight_id=row.id,
        actor="computed_worker",
        payload={
            "action": "deleted",
            "insight_type": row.insight_type,
            "dedup_key": row.dedup_key,
            "content_at_deletion": _content_for_deletion_audit(row),
            "confidence_at_deletion": _as_decimal(row.confidence),
            "deletion_reason": reason,
        },
        created_at=created_at,
    )


def _content_for_deletion_audit(row: UserInsight) -> dict:
    content = dict(row.content or {})
    if row.insight_type == "stated_preference" and content.get("raw_quote"):
        content["raw_quote"] = _RAW_QUOTE_REDACTED
    return content


def _reinforced_confidence(
    *,
    source: str,
    previous_confidence: Decimal,
    reinforcement_count: int,
) -> Decimal:
    if source == "llm_extracted":
        repeated_observations = max(reinforcement_count - 1, 0)
        boosted = Decimal("0.85") + (
            LLM_REINFORCEMENT_STEP * Decimal(repeated_observations)
        )
        return min(
            max(previous_confidence, boosted),
            LLM_REINFORCEMENT_HARD_CAP,
        ).quantize(Decimal("0.01"))
    if source == "user_override":
        return Decimal("1.00")
    cap = CONFIDENCE_CAP_BY_SOURCE.get(source, Decimal("1.00"))
    return min(previous_confidence + Decimal("0.05"), cap).quantize(
        Decimal("0.01")
    )


def _actor_for_source(source: str) -> str:
    if source == "llm_extracted":
        return "llm_extractor"
    if source == "user_override":
        return "user"
    return "computed_worker"


def _aware_utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _as_decimal(value: object) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))
