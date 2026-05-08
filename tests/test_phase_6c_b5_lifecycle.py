from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select

from api.models.user_insight import UserInsight, UserInsightAudit
from api.schemas.insights import (
    CashFlowStabilityContent,
    StatedPreferenceContent,
)
from api.services.insights.lifecycle import (
    expire_stale,
    detect_stated_observed_gaps,
    reinforce,
)
from api.services.insights.persister import persist_insights


def _utc(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, tzinfo=timezone.utc)


async def _add_insight(
    session,
    user_id: uuid.UUID,
    content,
    *,
    source: str,
    confidence: Decimal,
    valid_until: datetime,
    created_at: datetime,
    user_locked: bool = False,
) -> UserInsight:
    row = UserInsight(
        user_id=user_id,
        insight_type=content.type,
        content=content.model_dump(mode="json"),
        confidence=confidence,
        source=source,
        valid_until=valid_until,
        user_locked=user_locked,
        dedup_key=content.dedup_key(),
        reinforcement_count=1,
        created_at=created_at,
        updated_at=created_at,
    )
    session.add(row)
    await session.flush()
    return row


async def test_reinforce_llm_extends_ttl_and_bumps_confidence(db_with_user):
    session, user_id = db_with_user
    now = _utc(2026, 5, 7)
    content = StatedPreferenceContent(
        topic="savings",
        stance="quiere ahorrar más cada mes",
        raw_quote="Quiero ahorrar más.",
        extracted_at=now,
    )
    setattr(content, "_llm_confidence", Decimal("0.72"))
    stats = await persist_insights(
        session,
        user_id=user_id,
        insights=[content],
        source="llm_extracted",
        now=now - timedelta(days=170),
    )
    await session.commit()
    assert stats.created == 1

    row = (
        await session.execute(
            select(UserInsight).where(UserInsight.user_id == user_id)
        )
    ).scalar_one()
    before_valid_until = row.valid_until

    result = await reinforce(session, row.id, now=now)
    await session.commit()
    await session.refresh(row)

    assert result is not None
    assert result.reinforcement_count == 2
    assert row.reinforcement_count == 2
    assert row.confidence == Decimal("0.90")
    assert row.valid_until > before_valid_until
    audit = (
        await session.execute(
            select(UserInsightAudit)
            .where(UserInsightAudit.insight_id == row.id)
            .where(UserInsightAudit.action == "reinforced")
        )
    ).scalar_one()
    assert audit.actor == "llm_extractor"
    assert audit.payload["confidence_after"] == "0.90"


async def test_reinforce_skips_user_locked_rows(db_with_user):
    session, user_id = db_with_user
    now = _utc(2026, 5, 7)
    content = StatedPreferenceContent(
        topic="risk_tolerance",
        stance="se considera conservador",
        raw_quote="Soy conservador.",
        extracted_at=now,
    )
    row = await _add_insight(
        session,
        user_id,
        content,
        source="user_override",
        confidence=Decimal("1.00"),
        valid_until=now - timedelta(days=1),
        created_at=now - timedelta(days=30),
        user_locked=True,
    )
    await session.commit()

    result = await reinforce(session, row.id, now=now)
    await session.commit()
    await session.refresh(row)

    assert result is not None
    assert result.skipped_locked is True
    assert row.reinforcement_count == 1
    assert row.valid_until == now - timedelta(days=1)


async def test_expire_stale_redacts_raw_quote_and_purges_old_unlocked(
    db_with_user,
):
    session, user_id = db_with_user
    now = _utc(2026, 5, 7)
    old_pref = StatedPreferenceContent(
        topic="savings",
        stance="quiere ahorrar más",
        raw_quote="Esta cita no debe vivir para siempre.",
        extracted_at=now - timedelta(days=40),
    )
    active_old_quote = await _add_insight(
        session,
        user_id,
        old_pref,
        source="llm_extracted",
        confidence=Decimal("0.80"),
        valid_until=now + timedelta(days=100),
        created_at=now - timedelta(days=40),
    )
    expired_content = CashFlowStabilityContent(
        score_0_100=60,
        monthly_variance_pct=Decimal("20.00"),
        income_sources_count=1,
        savings_rate_pct=Decimal("2.00"),
        last_income_at=None,
    )
    expired = await _add_insight(
        session,
        user_id,
        expired_content,
        source="computed",
        confidence=Decimal("0.70"),
        valid_until=now - timedelta(days=31),
        created_at=now - timedelta(days=90),
    )
    locked_expired = await _add_insight(
        session,
        user_id,
        StatedPreferenceContent(
            topic="debt_payoff",
            stance="prioriza deuda",
            raw_quote="No borrar automático.",
            extracted_at=now - timedelta(days=40),
        ),
        source="user_override",
        confidence=Decimal("1.00"),
        valid_until=now - timedelta(days=31),
        created_at=now - timedelta(days=90),
        user_locked=True,
    )
    await session.commit()

    stats = await expire_stale(session, now=now)
    await session.commit()

    redacted = await session.get(UserInsight, active_old_quote.id)
    deleted = await session.get(UserInsight, expired.id)
    locked = await session.get(UserInsight, locked_expired.id)
    delete_audit = (
        await session.execute(
            select(UserInsightAudit).where(
                UserInsightAudit.insight_id == expired.id,
                UserInsightAudit.action == "deleted",
            )
        )
    ).scalar_one()

    assert stats.raw_quotes_redacted == 2
    assert stats.hard_deleted == 1
    assert redacted.content["raw_quote"] == "[eliminado por privacidad]"
    assert deleted is None
    assert locked is not None
    assert delete_audit.payload["deletion_reason"] == "expired_swept"


async def test_detect_stated_observed_gap_emits_then_expires_when_resolved(
    db_with_user,
):
    session, user_id = db_with_user
    now = _utc(2026, 5, 7)
    stated = await _add_insight(
        session,
        user_id,
        StatedPreferenceContent(
            topic="savings",
            stance="prioriza ahorrar",
            raw_quote="Quiero ahorrar más.",
            extracted_at=now,
        ),
        source="llm_extracted",
        confidence=Decimal("0.82"),
        valid_until=now + timedelta(days=100),
        created_at=now,
    )
    observed = await _add_insight(
        session,
        user_id,
        CashFlowStabilityContent(
            score_0_100=50,
            monthly_variance_pct=Decimal("30.00"),
            income_sources_count=1,
            savings_rate_pct=Decimal("2.00"),
            last_income_at=None,
        ),
        source="computed",
        confidence=Decimal("0.80"),
        valid_until=now + timedelta(days=20),
        created_at=now,
    )
    await session.commit()

    stats = await detect_stated_observed_gaps(session, user_id, now=now)
    await session.commit()
    gap = (
        await session.execute(
            select(UserInsight).where(
                UserInsight.user_id == user_id,
                UserInsight.insight_type == "stated_observed_gap",
            )
        )
    ).scalar_one()

    assert stats.emitted == 1
    assert stats.created == 1
    assert gap.content["stated_insight_id"] == str(stated.id)
    assert gap.content["observed_insight_id"] == str(observed.id)
    assert "tasa de ahorro" in gap.content["description"]

    observed.content = CashFlowStabilityContent(
        score_0_100=85,
        monthly_variance_pct=Decimal("5.00"),
        income_sources_count=1,
        savings_rate_pct=Decimal("20.00"),
        last_income_at=None,
    ).model_dump(mode="json")
    await session.commit()

    resolved_stats = await detect_stated_observed_gaps(
        session,
        user_id,
        now=now + timedelta(days=1),
    )
    await session.commit()
    await session.refresh(gap)

    assert resolved_stats.emitted == 0
    assert resolved_stats.expired_unsupported == 1
    assert gap.valid_until < now + timedelta(days=1)
    audit = (
        await session.execute(
            select(UserInsightAudit).where(
                UserInsightAudit.insight_id == gap.id,
                UserInsightAudit.action == "deleted",
            )
        )
    ).scalar_one()
    assert audit.payload["deletion_reason"] == "gap_no_longer_supported"
