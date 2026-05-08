"""Phase 6c B11 — direct coverage for `_describe_insight` branches.

The B6 tool-level test exercises a few insight types via the actual
`get_user_context` call. This file unit-tests the renderer branches
for every type so the formatter coverage hits 80%+.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from app.queries.tools.user_context import _describe_insight
from api.schemas.insights import (
    ArchetypeContent,
    BehavioralFlagContent,
    CashFlowStabilityContent,
    CRSeasonalPatternContent,
    DebtLoadContent,
    DecisionStyleContent,
    EmergencyFundContent,
    FinancialLiteracyContent,
    RecurringDriftContent,
    RiskPostureContent,
    SpendingPatternContent,
    StatedGoalContent,
    StatedObservedGapContent,
    StatedPreferenceContent,
)


def _utc(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, tzinfo=timezone.utc)


def test_describe_spending_pattern_crc():
    out = _describe_insight(
        SpendingPatternContent(
            category="supermercado",
            monthly_avg_crc=Decimal("180000"),
            monthly_avg_usd=None,
            trend_pct_3mo=Decimal("8.0"),
            volatility_score=Decimal("0.32"),
        )
    )
    assert "supermercado" in out
    assert "₡180.000" in out
    assert "subiendo" in out


def test_describe_spending_pattern_usd():
    out = _describe_insight(
        SpendingPatternContent(
            category="streaming",
            monthly_avg_crc=None,
            monthly_avg_usd=Decimal("12"),
            trend_pct_3mo=Decimal("0"),
            volatility_score=Decimal("0.05"),
        )
    )
    assert "streaming" in out
    assert "$12" in out
    assert "estable" in out


def test_describe_spending_pattern_negative_trend():
    out = _describe_insight(
        SpendingPatternContent(
            category="comida",
            monthly_avg_crc=Decimal("75000"),
            monthly_avg_usd=None,
            trend_pct_3mo=Decimal("-12.5"),
            volatility_score=Decimal("0.20"),
        )
    )
    assert "bajando" in out


def test_describe_recurring_drift():
    out = _describe_insight(
        RecurringDriftContent(
            bill_id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
            baseline_amount=Decimal("100000"),
            current_amount=Decimal("120000"),
            drift_pct=Decimal("20.0"),
            detected_at=_utc(2026, 5, 8),
        )
    )
    assert "₡100.000" in out
    assert "₡120.000" in out
    assert "subiendo 20%" in out


def test_describe_recurring_drift_negative():
    out = _describe_insight(
        RecurringDriftContent(
            bill_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
            baseline_amount=Decimal("100000"),
            current_amount=Decimal("80000"),
            drift_pct=Decimal("-20.0"),
            detected_at=_utc(2026, 5, 8),
        )
    )
    assert "bajando 20%" in out


def test_describe_cash_flow_stability_with_last_income():
    out = _describe_insight(
        CashFlowStabilityContent(
            score_0_100=72,
            monthly_variance_pct=Decimal("18.0"),
            income_sources_count=2,
            savings_rate_pct=Decimal("12.5"),
            last_income_at=date(2026, 5, 1),
        )
    )
    assert "72/100" in out
    assert "12.5%" in out
    assert "1 de mayo" in out


def test_describe_cash_flow_stability_no_last_income():
    out = _describe_insight(
        CashFlowStabilityContent(
            score_0_100=40,
            monthly_variance_pct=Decimal("60.0"),
            income_sources_count=1,
            savings_rate_pct=Decimal("-3.0"),
            last_income_at=None,
        )
    )
    assert "40/100" in out
    assert "-3%" in out


def test_describe_debt_load():
    out = _describe_insight(
        DebtLoadContent(
            debt_to_income_ratio=Decimal("0.45"),
            total_monthly_debt_service_crc=Decimal("265000"),
            num_active_debts=3,
        )
    )
    assert "₡265.000" in out
    assert "3 deudas activas" in out


def test_describe_emergency_fund():
    out = _describe_insight(
        EmergencyFundContent(
            months_of_expenses_covered=Decimal("2.5"),
            liquid_balance_crc=Decimal("750000"),
            monthly_essential_expenses_crc=Decimal("300000"),
            sufficiency="borderline",
        )
    )
    assert "2.5" in out
    assert "₡750.000" in out
    assert "borderline" in out


def test_describe_cr_seasonal_pattern_with_history():
    out = _describe_insight(
        CRSeasonalPatternContent(
            event="aguinaldo",
            expected_month=12,
            historical_amount_crc=Decimal("400000"),
            last_observed_year=2025,
        )
    )
    assert "diciembre" in out
    assert "aguinaldo" in out
    assert "2025" in out


def test_describe_cr_seasonal_pattern_no_history():
    out = _describe_insight(
        CRSeasonalPatternContent(
            event="marchamo",
            expected_month=12,
            historical_amount_crc=None,
            last_observed_year=None,
        )
    )
    assert "marchamo" in out
    assert "sin año registrado" in out


def test_describe_stated_preference_topics():
    for topic, label in (
        ("debt_payoff", "pagar deudas"),
        ("savings", "ahorrar"),
        ("risk_tolerance", "tolerancia al riesgo"),
        ("lifestyle", "estilo de vida"),
    ):
        out = _describe_insight(
            StatedPreferenceContent(
                topic=topic,
                stance="prefers something",
                raw_quote=f"Quote about {topic}",
                extracted_at=_utc(2026, 5, 8),
            )
        )
        assert label in out


def test_describe_stated_goal_with_amount_and_date():
    out = _describe_insight(
        StatedGoalContent(
            goal_text="Ahorrar para el marchamo",
            target_amount=Decimal("500000"),
            target_date=date(2026, 12, 1),
            status="committed",
        )
    )
    assert "₡500.000" in out
    assert "1 de diciembre" in out
    assert "comprometida" in out


def test_describe_stated_goal_minimal():
    out = _describe_insight(
        StatedGoalContent(
            goal_text="Comprar carro",
            target_amount=None,
            target_date=None,
            status="mentioned",
        )
    )
    assert "Comprar carro" in out
    assert "mencionada" in out


def test_describe_stated_goal_abandoned():
    out = _describe_insight(
        StatedGoalContent(
            goal_text="Viajar a Asia",
            target_amount=None,
            target_date=None,
            status="abandoned",
        )
    )
    assert "abandonada" in out


def test_describe_behavioral_flag_each_type():
    for flag, label in (
        ("weekend_overspend", "fin de semana"),
        ("paycheck_cycle", "quincena"),
        ("impulse_pattern", "impulsivas"),
    ):
        out = _describe_insight(
            BehavioralFlagContent(
                flag_type=flag,
                evidence_window="últimas 8 semanas",
                strength_score=Decimal("0.65"),
            )
        )
        assert label in out


def test_describe_archetype_with_secondary():
    out = _describe_insight(
        ArchetypeContent(
            primary="saver_planner",
            secondary="aguinaldo_dependent",
            confidence=Decimal("0.80"),
            evidence_summary="planea sus gastos del mes",
        )
    )
    assert "saver planner" in out
    assert "aguinaldo dependent" in out
    assert "80%" in out


def test_describe_archetype_without_secondary():
    out = _describe_insight(
        ArchetypeContent(
            primary="impulsive_spender",
            secondary=None,
            confidence=Decimal("0.70"),
            evidence_summary="...",
        )
    )
    assert "impulsive spender" in out
    assert "70%" in out


def test_describe_risk_posture():
    out = _describe_insight(
        RiskPostureContent(posture="aggressive", evidence_basis="mixed")
    )
    assert "aggressive" in out
    assert "mixed" in out


def test_describe_decision_style():
    out = _describe_insight(
        DecisionStyleContent(
            style="analytical",
            evidence="pidió números y comparaciones",
        )
    )
    assert "analytical" in out
    assert "números" in out


def test_describe_financial_literacy():
    out = _describe_insight(
        FinancialLiteracyContent(
            level="advanced",
            evidence="maneja interés compuesto",
        )
    )
    assert "advanced" in out
    assert "interés compuesto" in out


def test_describe_stated_observed_gap():
    out = _describe_insight(
        StatedObservedGapContent(
            stated_insight_id=uuid.UUID(int=1),
            observed_insight_id=uuid.UUID(int=2),
            description="Decís ser ahorrador pero el savings rate está en 2%.",
        )
    )
    assert "savings rate" in out
