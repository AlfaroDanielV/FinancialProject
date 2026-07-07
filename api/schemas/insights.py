"""Pydantic schemas for Phase 6c user_insights (B2).

Two families:
    InsightContent — the discriminated union of the 14 insight types.
                     Persisted in user_insights.content JSONB.
                     Each variant declares its own dedup_key().

    AuditPayload   — per-action schemas for user_insights_audit.payload.
                     Validated by persister before insert; ensures the
                     audit timeline is reconstructable.

Decisions locked in docs/phase-6c-decisions.md (entries 2026-05-06,
plus the B1-review entries 2026-05-07).

Why discriminated union with `type` field: the module-level
`validate_insight_content(...)` helper uses Pydantic's TypeAdapter to
dispatch to the right class based on the `type` literal. The persister
doesn't have to know the type set at import time; new types extend the
Annotated[Union] declaration at the bottom of this file and everything
else stays.
"""
from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Annotated, Any, Literal, Self, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    TypeAdapter,
    model_validator,
)


# ── shared utility ──────────────────────────────────────────────────────────


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


# Per-type TTL in days. Single source of truth for valid_until math —
# read both by the persister at write time and by the lifecycle's
# reinforcement bump. Keep aligned with the table in
# docs/phase-6c-decisions.md (Decision #5).
TTL_DAYS_BY_TYPE: dict[str, int] = {
    "spending_pattern": 60,
    "recurring_drift": 30,
    "cash_flow_stability": 30,
    "debt_load": 7,
    "emergency_fund": 7,
    "cr_seasonal_pattern": 18 * 30,  # 18 months ≈ 540 days
    "stated_preference": 180,
    "stated_goal": 365,  # default; computed override when target_date set
    "behavioral_flag": 90,
    "archetype": 90,
    "risk_posture": 90,
    "decision_style": 90,
    "financial_literacy": 90,
    "money_personality": 90,
    "stated_observed_gap": 30,
}


def default_valid_until(
    insight_type: str, *, now: datetime | None = None
) -> datetime:
    days = TTL_DAYS_BY_TYPE.get(insight_type)
    if days is None:
        raise ValueError(f"Unknown insight_type: {insight_type!r}")
    return (now or _now_utc()) + timedelta(days=days)


# Confidence-cap policy. The persister and the lifecycle's reinforcement
# helper both consult this map.
CONFIDENCE_CAP_BY_SOURCE: dict[str, Decimal] = {
    "computed": Decimal("1.00"),
    "llm_extracted": Decimal("0.85"),
    "user_override": Decimal("1.00"),
}
LLM_REINFORCEMENT_STEP = Decimal("0.05")
LLM_REINFORCEMENT_HARD_CAP = Decimal("0.95")


_WHITESPACE_RE = re.compile(r"\s+")


def _norm_text(s: str, *, max_len: int = 80) -> str:
    """Whitespace-collapse + lowercase + truncate. Used by dedup_key for
    free-text-keyed insight types (stated_goal)."""
    return _WHITESPACE_RE.sub(" ", s.strip().lower())[:max_len]


# ── insight content base ────────────────────────────────────────────────────


class _InsightBase(BaseModel):
    """Common shape for every InsightContent variant.

    Subclasses declare a `type: Literal["..."]` field for the
    discriminated union and override `dedup_key()`. The base has a
    sensible default for types that are singletons per user
    (cash_flow_stability, debt_load, archetype, etc.).
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    _computed_confidence: Decimal | None = PrivateAttr(default=None)
    _llm_confidence: Decimal | None = PrivateAttr(default=None)

    def dedup_key(self) -> str:
        return "global"


# ── computed insight types ──────────────────────────────────────────────────


class SpendingPatternContent(_InsightBase):
    type: Literal["spending_pattern"] = "spending_pattern"
    category: str = Field(..., min_length=1, max_length=100)
    monthly_avg_crc: Decimal | None = None
    monthly_avg_usd: Decimal | None = None
    trend_pct_3mo: Decimal       # signed, e.g. +8.0 = up 8%
    volatility_score: Decimal = Field(..., ge=0, le=1)

    @model_validator(mode="after")
    def _requires_one_currency_average(self) -> Self:
        if self.monthly_avg_crc is None and self.monthly_avg_usd is None:
            raise ValueError(
                "spending_pattern needs monthly_avg_crc or monthly_avg_usd"
            )
        return self

    def dedup_key(self) -> str:
        return self.category.strip().lower()


class RecurringDriftContent(_InsightBase):
    type: Literal["recurring_drift"] = "recurring_drift"
    bill_id: uuid.UUID
    baseline_amount: Decimal
    current_amount: Decimal
    drift_pct: Decimal           # signed
    detected_at: datetime

    def dedup_key(self) -> str:
        return str(self.bill_id)


class CashFlowStabilityContent(_InsightBase):
    type: Literal["cash_flow_stability"] = "cash_flow_stability"
    score_0_100: int = Field(..., ge=0, le=100)
    monthly_variance_pct: Decimal = Field(..., ge=0)
    income_sources_count: int = Field(..., ge=0)
    # Enriched in 2026-05-07 review: P7 affordability needs both.
    savings_rate_pct: Decimal      # signed; can be negative when overspending
    last_income_at: date | None = None


class DebtLoadContent(_InsightBase):
    type: Literal["debt_load"] = "debt_load"
    debt_to_income_ratio: Decimal = Field(..., ge=0)
    total_monthly_debt_service_crc: Decimal = Field(..., ge=0)
    num_active_debts: int = Field(..., ge=0)


class EmergencyFundContent(_InsightBase):
    """Liquid reserve coverage. Critical for P7 affordability checks
    ("¿podés absorber un gasto de X sin endeudarte?")."""

    type: Literal["emergency_fund"] = "emergency_fund"
    months_of_expenses_covered: Decimal = Field(..., ge=0)
    liquid_balance_crc: Decimal = Field(..., ge=0)
    monthly_essential_expenses_crc: Decimal = Field(..., ge=0)
    sufficiency: Literal[
        "insufficient", "borderline", "adequate", "abundant"
    ]


class CRSeasonalPatternContent(_InsightBase):
    type: Literal["cr_seasonal_pattern"] = "cr_seasonal_pattern"
    event: Literal["aguinaldo", "salario_escolar", "marchamo"]
    expected_month: int = Field(..., ge=1, le=12)
    historical_amount_crc: Decimal | None = None
    last_observed_year: int | None = None

    def dedup_key(self) -> str:
        return self.event


# ── llm-extracted insight types ─────────────────────────────────────────────


class StatedPreferenceContent(_InsightBase):
    type: Literal["stated_preference"] = "stated_preference"
    topic: Literal[
        "debt_payoff", "savings", "risk_tolerance", "lifestyle"
    ]
    stance: str = Field(..., min_length=1, max_length=200)
    # raw_quote is wiped at age 30d by lifecycle (see Decision #7).
    raw_quote: str = Field(..., min_length=1, max_length=280)
    extracted_at: datetime

    def dedup_key(self) -> str:
        return self.topic


class StatedGoalContent(_InsightBase):
    type: Literal["stated_goal"] = "stated_goal"
    goal_text: str = Field(..., min_length=1, max_length=200)
    target_amount: Decimal | None = None
    target_date: date | None = None
    status: Literal["mentioned", "committed", "abandoned"]

    def dedup_key(self) -> str:
        # Normalised goal text — first 80 chars after whitespace collapse.
        return _norm_text(self.goal_text)


class BehavioralFlagContent(_InsightBase):
    type: Literal["behavioral_flag"] = "behavioral_flag"
    flag_type: Literal[
        "weekend_overspend", "paycheck_cycle", "impulse_pattern"
    ]
    evidence_window: str = Field(..., min_length=1, max_length=80)
    strength_score: Decimal = Field(..., ge=0, le=1)

    def dedup_key(self) -> str:
        return self.flag_type


class ArchetypeContent(_InsightBase):
    type: Literal["archetype"] = "archetype"
    primary: str = Field(..., min_length=1, max_length=64)
    secondary: str | None = Field(default=None, max_length=64)
    confidence: Decimal = Field(..., ge=0, le=1)
    evidence_summary: str = Field(..., min_length=1, max_length=300)


class RiskPostureContent(_InsightBase):
    type: Literal["risk_posture"] = "risk_posture"
    posture: Literal["conservative", "moderate", "aggressive"]
    evidence_basis: Literal["stated", "observed", "mixed"]


class DecisionStyleContent(_InsightBase):
    type: Literal["decision_style"] = "decision_style"
    style: Literal["analytical", "intuitive", "avoidant"]
    evidence: str = Field(..., min_length=1, max_length=200)


class FinancialLiteracyContent(_InsightBase):
    type: Literal["financial_literacy"] = "financial_literacy"
    level: Literal["beginner", "intermediate", "advanced"]
    evidence: str = Field(..., min_length=1, max_length=200)


class MoneyPersonalityContent(_InsightBase):
    """Deterministic money archetype (Workstream E, 2026-07). COMPUTED only —
    the nightly worker classifies the user's OWN ledger into one of four labels
    via `api/services/finance/money_personality.py`. NOT LLM-extractable (the
    two-writers rule: only the computed writer produces it) and NOT user-editable
    (a data-derived label; `/olvidar` deletes it and it recomputes nightly).
    Singleton per user (dedup_key='global'). Confidence is attached by the
    computed writer, not carried in content."""

    type: Literal["money_personality"] = "money_personality"
    personality: Literal["spender", "avoider", "saver", "investor"]
    scores: dict[str, Decimal] = Field(default_factory=dict)
    evidence_summary: str = Field(..., min_length=1, max_length=300)


# ── derived ────────────────────────────────────────────────────────────────


class StatedObservedGapContent(_InsightBase):
    """Emitted by lifecycle.detect_stated_observed_gaps when a stated_*
    insight contradicts a computed one. Recomputed nightly; expires
    in 30d if not regenerated.
    """

    type: Literal["stated_observed_gap"] = "stated_observed_gap"
    stated_insight_id: uuid.UUID
    observed_insight_id: uuid.UUID
    description: str = Field(..., min_length=1, max_length=300)

    def dedup_key(self) -> str:
        # Sorted to make the key direction-independent.
        a, b = sorted(
            (str(self.stated_insight_id), str(self.observed_insight_id))
        )
        return f"{a}::{b}"


# ── discriminated union ─────────────────────────────────────────────────────


InsightContent = Annotated[
    Union[
        SpendingPatternContent,
        RecurringDriftContent,
        CashFlowStabilityContent,
        DebtLoadContent,
        EmergencyFundContent,
        CRSeasonalPatternContent,
        StatedPreferenceContent,
        StatedGoalContent,
        BehavioralFlagContent,
        ArchetypeContent,
        RiskPostureContent,
        DecisionStyleContent,
        FinancialLiteracyContent,
        MoneyPersonalityContent,
        StatedObservedGapContent,
    ],
    Field(discriminator="type"),
]

InsightContentAdapter = TypeAdapter(InsightContent)


def validate_insight_content(payload: Any) -> InsightContent:
    """Validate a raw JSON-like payload into a concrete insight model."""
    return InsightContentAdapter.validate_python(payload)


def valid_until_for(
    content: InsightContent, *, now: datetime | None = None
) -> datetime:
    """Return the write-time expiry timestamp for a validated insight.

    `stated_goal` has a content-dependent TTL: target_date + 30 days
    when present, otherwise the default 365-day TTL.
    """
    if isinstance(content, StatedGoalContent) and content.target_date:
        expires_on = content.target_date + timedelta(days=30)
        return datetime.combine(
            expires_on, datetime.min.time(), tzinfo=timezone.utc
        )
    return default_valid_until(content.type, now=now)


# Source-of-truth tuple. Compare against the migration's CHECK constraint.
ALL_INSIGHT_TYPES: tuple[str, ...] = (
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
    "money_personality",
    "stated_observed_gap",
)

# Types the LLM extractor is allowed to produce. Anything else is
# computed-only or derived-only. Decision #2 in the doc.
LLM_EXTRACTABLE_TYPES: frozenset[str] = frozenset(
    {
        "stated_preference",
        "stated_goal",
        "archetype",
        "risk_posture",
        "decision_style",
        "financial_literacy",
    }
)

# Types the user can edit via /editar_memoria. Computed types are
# off-limits — the user can /olvidar them but cannot rewrite them
# (a manual override would lie about reality).
USER_EDITABLE_TYPES: frozenset[str] = frozenset(
    {
        "stated_preference",
        "stated_goal",
        "archetype",
        "risk_posture",
        "decision_style",
        "financial_literacy",
    }
)


# ── audit payloads (per-action schemas) ─────────────────────────────────────


class _AuditPayloadBase(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AuditPayloadCreated(_AuditPayloadBase):
    """Snapshot for replay — could be used by a future undo flow."""

    action: Literal["created"] = "created"
    insight_type: str
    dedup_key: str
    content: dict[str, Any]
    confidence: Decimal
    source: str
    valid_until: datetime


class AuditPayloadUpdated(_AuditPayloadBase):
    """Diff for forensics."""

    action: Literal["updated"] = "updated"
    insight_type: str
    dedup_key: str
    previous_content: dict[str, Any]
    new_content: dict[str, Any]
    previous_confidence: Decimal
    new_confidence: Decimal


class AuditPayloadDeleted(_AuditPayloadBase):
    """Captures content at deletion time so a future undo within the
    audit retention window can restore the row."""

    action: Literal["deleted"] = "deleted"
    insight_type: str
    dedup_key: str
    content_at_deletion: dict[str, Any]
    confidence_at_deletion: Decimal
    deletion_reason: str


class AuditPayloadReinforced(_AuditPayloadBase):
    action: Literal["reinforced"] = "reinforced"
    insight_type: str
    dedup_key: str
    reinforcement_count_after: int
    confidence_before: Decimal
    confidence_after: Decimal


class AuditPayloadLocked(_AuditPayloadBase):
    action: Literal["locked"] = "locked"
    insight_type: str
    dedup_key: str
    content: dict[str, Any]
    locked_via: Literal["editar_memoria", "admin"]


class AuditPayloadUnlocked(_AuditPayloadBase):
    action: Literal["unlocked"] = "unlocked"
    insight_type: str
    dedup_key: str
    content: dict[str, Any]
    unlocked_via: Literal["editar_memoria", "admin"]


class AuditPayloadExported(_AuditPayloadBase):
    """No content snapshot — would balloon the audit table for a
    user-driven action that's expected to be frequent. Cross-reference
    via request_id with the API access logs."""

    action: Literal["exported"] = "exported"
    count: int = Field(..., ge=0)
    format: Literal["json"] = "json"
    request_id: str = Field(..., min_length=1, max_length=64)


AuditPayload = Annotated[
    Union[
        AuditPayloadCreated,
        AuditPayloadUpdated,
        AuditPayloadDeleted,
        AuditPayloadReinforced,
        AuditPayloadLocked,
        AuditPayloadUnlocked,
        AuditPayloadExported,
    ],
    Field(discriminator="action"),
]

AuditPayloadAdapter = TypeAdapter(AuditPayload)


def validate_audit_payload(payload: Any) -> AuditPayload:
    """Validate a raw JSON-like audit payload into its action schema."""
    return AuditPayloadAdapter.validate_python(payload)
