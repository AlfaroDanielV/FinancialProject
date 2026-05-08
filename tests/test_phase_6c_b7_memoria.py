"""Phase 6c B7 — /memoria render + grouping.

Tests the pure helpers in `api.services.insights.memory_view`: grouping,
ordering, low-confidence disclaimer, empty state, and footer.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from api.models.user_insight import UserInsight
from api.schemas.insights import (
    ArchetypeContent,
    CashFlowStabilityContent,
    SpendingPatternContent,
    StatedGoalContent,
    StatedPreferenceContent,
)
from api.services.insights.memory_view import (
    EMPTY_MEMORIA,
    GROUP_BANDERAS,
    GROUP_CONOZCO,
    GROUP_LABELS_ES,
    GROUP_METAS,
    GROUP_PATRONES,
    LOW_CONFIDENCE_THRESHOLD,
    group_for,
    render_group_summary_lines,
    render_memoria_text,
    render_view,
)


def _row(content, *, confidence: Decimal, updated_at: datetime) -> UserInsight:
    user_id = uuid.uuid4()
    return UserInsight(
        id=uuid.uuid4(),
        user_id=user_id,
        insight_type=content.type,
        content=content.model_dump(mode="json"),
        confidence=confidence,
        source="computed",
        valid_until=updated_at + timedelta(days=30),
        user_locked=False,
        dedup_key=content.dedup_key(),
        reinforcement_count=1,
        created_at=updated_at,
        updated_at=updated_at,
    )


def test_group_for_maps_every_type():
    assert group_for("spending_pattern") == GROUP_PATRONES
    assert group_for("stated_goal") == GROUP_METAS
    assert group_for("archetype") == GROUP_CONOZCO
    assert group_for("stated_observed_gap") == GROUP_BANDERAS


def test_render_view_groups_and_renders_bullets():
    now = datetime.now(timezone.utc)
    rows = [
        _row(
            SpendingPatternContent(
                category="supermercado",
                monthly_avg_crc=Decimal("180000"),
                monthly_avg_usd=None,
                trend_pct_3mo=Decimal("8.0"),
                volatility_score=Decimal("0.32"),
            ),
            confidence=Decimal("0.85"),
            updated_at=now - timedelta(days=1),
        ),
        _row(
            StatedGoalContent(
                goal_text="Ahorrar para el marchamo",
                target_amount=Decimal("500000"),
                target_date=None,
                status="committed",
            ),
            confidence=Decimal("0.95"),
            updated_at=now,
        ),
        _row(
            ArchetypeContent(
                primary="saver_planner",
                secondary=None,
                confidence=Decimal("0.85"),
                evidence_summary="planea sus gastos del mes",
            ),
            confidence=Decimal("0.85"),
            updated_at=now,
        ),
    ]
    view = render_view(rows)
    assert view.total == 3
    assert len(view.groups[GROUP_METAS]) == 1
    assert len(view.groups[GROUP_PATRONES]) == 1
    assert len(view.groups[GROUP_CONOZCO]) == 1
    # Bullet uses the user_context renderer; we don't assert exact prose.
    bullet = view.groups[GROUP_PATRONES][0].bullet_html
    assert bullet.startswith("• ")
    assert "supermercado" in bullet


def test_low_confidence_inserts_disclaimer():
    now = datetime.now(timezone.utc)
    threshold = LOW_CONFIDENCE_THRESHOLD - Decimal("0.01")
    row = _row(
        StatedPreferenceContent(
            topic="savings",
            stance="quiere ahorrar pero no es seguro",
            raw_quote="Tal vez ahorre.",
            extracted_at=now,
        ),
        confidence=threshold,
        updated_at=now,
    )
    view = render_view([row])
    bullet = view.groups[GROUP_CONOZCO][0].bullet_html
    assert "⚠️" in bullet


def test_no_disclaimer_at_or_above_threshold():
    now = datetime.now(timezone.utc)
    row = _row(
        StatedPreferenceContent(
            topic="savings",
            stance="quiere ahorrar",
            raw_quote="Quiero ahorrar.",
            extracted_at=now,
        ),
        confidence=LOW_CONFIDENCE_THRESHOLD,
        updated_at=now,
    )
    view = render_view([row])
    bullet = view.groups[GROUP_CONOZCO][0].bullet_html
    assert "⚠️" not in bullet


def test_render_memoria_text_empty_state_has_footer():
    text = render_memoria_text(render_view([]), footer="FOOTER_X")
    assert EMPTY_MEMORIA in text
    assert text.endswith("FOOTER_X")


def test_render_memoria_text_orders_groups_metas_first():
    now = datetime.now(timezone.utc)
    rows = [
        _row(
            SpendingPatternContent(
                category="supermercado",
                monthly_avg_crc=Decimal("180000"),
                monthly_avg_usd=None,
                trend_pct_3mo=Decimal("0"),
                volatility_score=Decimal("0.10"),
            ),
            confidence=Decimal("0.80"),
            updated_at=now,
        ),
        _row(
            StatedGoalContent(
                goal_text="Comprar carro",
                target_amount=None,
                target_date=None,
                status="mentioned",
            ),
            confidence=Decimal("0.85"),
            updated_at=now,
        ),
    ]
    text = render_memoria_text(render_view(rows), footer="FOOTER")
    metas_idx = text.find(GROUP_LABELS_ES[GROUP_METAS])
    patrones_idx = text.find(GROUP_LABELS_ES[GROUP_PATRONES])
    assert 0 <= metas_idx < patrones_idx, text


def test_group_summary_lines_count_per_group():
    now = datetime.now(timezone.utc)
    rows = [
        _row(
            CashFlowStabilityContent(
                score_0_100=70,
                monthly_variance_pct=Decimal("12.0"),
                income_sources_count=1,
                savings_rate_pct=Decimal("8.0"),
                last_income_at=None,
            ),
            confidence=Decimal("0.80"),
            updated_at=now,
        ),
        _row(
            SpendingPatternContent(
                category="comida",
                monthly_avg_crc=Decimal("100000"),
                monthly_avg_usd=None,
                trend_pct_3mo=Decimal("0"),
                volatility_score=Decimal("0.10"),
            ),
            confidence=Decimal("0.80"),
            updated_at=now,
        ),
        _row(
            StatedGoalContent(
                goal_text="Ahorro",
                target_amount=None,
                target_date=None,
                status="mentioned",
            ),
            confidence=Decimal("0.85"),
            updated_at=now,
        ),
    ]
    lines = render_group_summary_lines(render_view(rows))
    joined = "\n".join(lines)
    assert "Tus metas: 1" in joined
    assert "Patrones: 2" in joined


def test_short_label_capped_at_45_chars():
    now = datetime.now(timezone.utc)
    row = _row(
        StatedGoalContent(
            goal_text=(
                "Ahorrar para una casa enorme con piscina y mucho terreno "
                "alrededor"
            ),
            target_amount=None,
            target_date=None,
            status="mentioned",
        ),
        confidence=Decimal("0.85"),
        updated_at=now,
    )
    view = render_view([row])
    item = view.groups[GROUP_METAS][0]
    assert len(item.short_label) <= 45
