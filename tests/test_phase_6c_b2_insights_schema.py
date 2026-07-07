from __future__ import annotations

import importlib.util
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from api.schemas.insights import (
    ALL_INSIGHT_TYPES,
    LLM_EXTRACTABLE_TYPES,
    USER_EDITABLE_TYPES,
    DebtLoadContent,
    StatedGoalContent,
    validate_audit_payload,
    validate_insight_content,
    valid_until_for,
)


def _load_migration(name: str, alias: str):
    root = Path(__file__).resolve().parent.parent
    path = root / "migrations" / "versions" / name
    spec = importlib.util.spec_from_file_location(alias, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_migration_0013():
    return _load_migration("0013_phase6c_user_insights.py", "m0013_phase6c")


def test_insight_types_match_migration_check_constraint():
    # Original 14 from migration 0013 …
    migration = _load_migration_0013()
    assert set(migration._VALID_INSIGHT_TYPES).issubset(set(ALL_INSIGHT_TYPES))
    assert "emergency_fund" in ALL_INSIGHT_TYPES
    assert "stated_observed_gap" in ALL_INSIGHT_TYPES

    # … plus money_personality, added by migration 0046 (the live CHECK now).
    m0046 = _load_migration(
        "0046_money_personality_insight.py", "m0046_money_personality"
    )
    # The CHECK is an IN clause — order-independent; compare as sets.
    assert set(ALL_INSIGHT_TYPES) == set(m0046._NEW_TYPES)
    assert len(ALL_INSIGHT_TYPES) == 15
    assert "money_personality" in ALL_INSIGHT_TYPES


def test_llm_extractable_and_user_editable_types_exclude_computed_types():
    computed_only = {
        "spending_pattern",
        "recurring_drift",
        "cash_flow_stability",
        "debt_load",
        "emergency_fund",
        "cr_seasonal_pattern",
        "money_personality",
        "stated_observed_gap",
    }

    assert LLM_EXTRACTABLE_TYPES == USER_EDITABLE_TYPES
    assert LLM_EXTRACTABLE_TYPES.isdisjoint(computed_only)


def test_validate_insight_content_returns_concrete_model_and_dedup_key():
    content = validate_insight_content(
        {
            "type": "debt_load",
            "debt_to_income_ratio": Decimal("0.27"),
            "total_monthly_debt_service_crc": Decimal("180000"),
            "num_active_debts": 2,
        }
    )

    assert isinstance(content, DebtLoadContent)
    assert content.dedup_key() == "global"


def test_spending_pattern_requires_at_least_one_currency_average():
    with pytest.raises(ValidationError):
        validate_insight_content(
            {
                "type": "spending_pattern",
                "category": "supermercado",
                "trend_pct_3mo": Decimal("8.0"),
                "volatility_score": Decimal("0.2"),
            }
        )


def test_stated_goal_valid_until_uses_target_date_plus_30_days():
    content = StatedGoalContent(
        goal_text="Comprar carro",
        target_amount=Decimal("5000000"),
        target_date=date(2026, 8, 15),
        status="committed",
    )

    assert valid_until_for(content) == datetime(
        2026, 9, 14, tzinfo=timezone.utc
    )


def test_default_valid_until_uses_type_ttl_when_no_content_override():
    now = datetime(2026, 5, 7, 12, 0, tzinfo=timezone.utc)
    content = DebtLoadContent(
        debt_to_income_ratio=Decimal("0.27"),
        total_monthly_debt_service_crc=Decimal("180000"),
        num_active_debts=2,
    )

    assert valid_until_for(content, now=now) == datetime(
        2026, 5, 14, 12, 0, tzinfo=timezone.utc
    )


def test_stated_observed_gap_dedup_key_is_direction_independent():
    first = uuid.UUID("00000000-0000-0000-0000-000000000002")
    second = uuid.UUID("00000000-0000-0000-0000-000000000001")

    content = validate_insight_content(
        {
            "type": "stated_observed_gap",
            "stated_insight_id": first,
            "observed_insight_id": second,
            "description": "gap",
        }
    )

    assert content.dedup_key() == f"{second}::{first}"


def test_audit_payloads_are_discriminated_and_strict():
    payload = validate_audit_payload(
        {
            "action": "unlocked",
            "insight_type": "risk_posture",
            "dedup_key": "global",
            "content": {"type": "risk_posture", "posture": "conservative"},
            "unlocked_via": "editar_memoria",
        }
    )

    assert payload.action == "unlocked"

    with pytest.raises(ValidationError):
        validate_audit_payload(
            {
                "action": "exported",
                "count": 1,
                "format": "json",
                "request_id": "req-1",
                "unexpected": True,
            }
        )
