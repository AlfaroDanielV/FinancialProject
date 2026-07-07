"""Money personality classifier (Workstream E, 2026-07).

Deterministically labels a user Spender / Avoider / Saver / Investor from their
OWN ledger (never an LLM). Persisted as a Phase 6c COMPUTED insight
(`money_personality`) and consumed as a P10 B7 ranking modifier (mapped to a
Klontz money-script — modifier, never a selector).

The precedence table is pure, so most of this is DB-free.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from api.models.user_insight import UserInsight
from api.schemas.insights import (
    LLM_EXTRACTABLE_TYPES,
    MoneyPersonalityContent,
    validate_insight_content,
)
from api.services.finance.money_personality import (
    MoneyPersonalityInputs,
    classify_money_personality,
    describe_personality_evidence,
    stored_personality,
)
from app.queries.tools.framing import _optional_signals
from app.queries.tools.user_context import _describe_insight


def _mk(**overrides) -> MoneyPersonalityInputs:
    """Neutral baseline (enough data; falls to the moderate-savings saver)."""
    base = dict(
        confirmed_txn_count_90d=20,
        income_3m=Decimal("1000000"),
        expenses_3m=Decimal("900000"),  # savings_rate 0.10
        has_budget=True,
        over_limit_count=0,
        wants_share=Decimal("0.30"),
        investing_allocation_share=Decimal("0"),
        has_investment_balance=False,
        goal_contribution_count_90d=0,
        days_since_last_capture=2,
        shadow_backlog_count=0,
    )
    base.update(overrides)
    return MoneyPersonalityInputs(**base)


# ── precedence table (pure) ───────────────────────────────────────────────────


def test_insufficient_data_returns_none():
    assert classify_money_personality(_mk(confirmed_txn_count_90d=5)).personality is None


def test_avoider_when_disengaged():
    # 2 of 3 signals: no budget + stale capture.
    r = classify_money_personality(
        _mk(has_budget=False, days_since_last_capture=30)
    )
    assert r.personality == "avoider"


def test_investor_by_investment_balance():
    assert classify_money_personality(_mk(has_investment_balance=True)).personality == "investor"


def test_investor_by_allocation_share():
    r = classify_money_personality(_mk(investing_allocation_share=Decimal("0.20")))
    assert r.personality == "investor"


def test_saver_high_rate_and_restraint():
    r = classify_money_personality(
        _mk(expenses_3m=Decimal("700000"), wants_share=Decimal("0.30"))  # rate 0.30
    )
    assert r.personality == "saver"


def test_spender_low_savings_rate():
    r = classify_money_personality(_mk(expenses_3m=Decimal("990000")))  # rate 0.01
    assert r.personality == "spender"


def test_spender_wants_heavy():
    r = classify_money_personality(_mk(wants_share=Decimal("0.50")))
    assert r.personality == "spender"


def test_spender_over_limit():
    r = classify_money_personality(_mk(over_limit_count=2))
    assert r.personality == "spender"


def test_fallback_saver_moderate_rate():
    assert classify_money_personality(_mk()).personality == "saver"  # rate 0.10


def test_fallback_spender_thin_rate():
    r = classify_money_personality(_mk(expenses_3m=Decimal("930000")))  # rate 0.07
    assert r.personality == "spender"


def test_income_unknown_is_indeterminate_not_spender():
    r = classify_money_personality(
        _mk(income_3m=Decimal("0"), wants_share=Decimal("0.30"), over_limit_count=0)
    )
    assert r.personality is None
    assert "indeterminate" in r.reasons[0]


def test_avoider_precedes_investor():
    # A disengaged user with an investment balance is still an avoider (a wrong
    # ledger makes the rest moot).
    r = classify_money_personality(
        _mk(has_budget=False, days_since_last_capture=30, has_investment_balance=True)
    )
    assert r.personality == "avoider"


def test_scores_are_bounded_0_1():
    r = classify_money_personality(_mk(wants_share=Decimal("0.90"), over_limit_count=9))
    for label, score in r.scores.items():
        assert Decimal("0") <= score <= Decimal("1"), (label, score)


# ── schema + writer separation ────────────────────────────────────────────────


def test_money_personality_not_llm_extractable():
    # Two-writers rule: only the computed writer produces it.
    assert "money_personality" not in LLM_EXTRACTABLE_TYPES


def test_content_validates_through_the_union():
    content = validate_insight_content(
        {
            "type": "money_personality",
            "personality": "saver",
            "scores": {"saver": "0.80"},
            "evidence_summary": "Ahorrás parejo.",
        }
    )
    assert isinstance(content, MoneyPersonalityContent)
    assert content.personality == "saver"


# ── rendering ──────────────────────────────────────────────────────────────────


def test_describe_insight_renders_spanish_label():
    out = _describe_insight(
        MoneyPersonalityContent(
            personality="investor",
            scores={"investor": Decimal("1.00")},
            evidence_summary="Tenés plata trabajando en una cuenta de inversión.",
        )
    )
    assert "Inversionista" in out
    assert "inversión" in out


def test_evidence_copy_has_no_llm_and_fits_limit():
    r = classify_money_personality(_mk(wants_share=Decimal("0.60")))
    copy = describe_personality_evidence(r, _mk(wants_share=Decimal("0.60")))
    assert 0 < len(copy) <= 300


# ── DB: stored_personality + framing prefers the computed label ───────────────


async def _insert_insight(
    session, user_id, insight_type, content, *, confidence, source="computed"
):
    row = UserInsight(
        user_id=user_id,
        insight_type=insight_type,
        content=content,
        confidence=Decimal(str(confidence)),
        source=source,
        valid_until=datetime.now(timezone.utc) + timedelta(days=90),
        dedup_key="global",
    )
    session.add(row)
    await session.flush()
    return row


@pytest.mark.asyncio
async def test_stored_personality_reads_latest_valid(db_with_user):
    session, user_id = db_with_user
    await _insert_insight(
        session,
        user_id,
        "money_personality",
        {"type": "money_personality", "personality": "saver", "scores": {},
         "evidence_summary": "x"},
        confidence="1.00",
    )
    assert await stored_personality(session, user_id) == "saver"


@pytest.mark.asyncio
async def test_stored_personality_none_when_absent(db_with_user):
    session, user_id = db_with_user
    assert await stored_personality(session, user_id) is None


@pytest.mark.asyncio
async def test_framing_maps_personality_to_klontz(db_with_user):
    session, user_id = db_with_user
    await _insert_insight(
        session,
        user_id,
        "money_personality",
        {"type": "money_personality", "personality": "spender", "scores": {},
         "evidence_summary": "x"},
        confidence="1.00",
    )
    archetype, _risk = await _optional_signals(session, user_id)
    assert archetype == "money_status"


@pytest.mark.asyncio
async def test_framing_prefers_computed_over_llm_archetype(db_with_user):
    session, user_id = db_with_user
    # LLM archetype (lower confidence) says money_vigilance; computed says avoider
    # (→ money_avoidance) and must win.
    await _insert_insight(
        session,
        user_id,
        "archetype",
        {"type": "archetype", "primary": "money_vigilance", "confidence": "0.80",
         "evidence_summary": "x"},
        confidence="0.80",
        source="llm_extracted",
    )
    await _insert_insight(
        session,
        user_id,
        "money_personality",
        {"type": "money_personality", "personality": "avoider", "scores": {},
         "evidence_summary": "x"},
        confidence="1.00",
    )
    archetype, _risk = await _optional_signals(session, user_id)
    assert archetype == "money_avoidance"


@pytest.mark.asyncio
async def test_goal_proposal_includes_personality_note(db_with_user):
    """SMART-R (D7): the goal proposal carries a deterministic, personality-matched
    line — here the spender flourish — read from the computed insight."""
    from datetime import date as _date
    from api.models.user import User
    from api.services.llm_extractor import (
        GOAL_NO_DATE_SENTINEL,
        ExtractionResult,
        Intent,
    )
    from api.services.telegram_dispatcher import ProposeAction, dispatch

    session, user_id = db_with_user
    user = await session.get(User, user_id)
    await _insert_insight(
        session,
        user_id,
        "money_personality",
        {"type": "money_personality", "personality": "spender", "scores": {},
         "evidence_summary": "x"},
        confidence="1.00",
    )
    ext = ExtractionResult(
        intent=Intent.CREATE_GOAL,
        dispatcher="write",
        goal_name="vacaciones",
        goal_target_amount=Decimal("2000000"),
        goal_target_date=GOAL_NO_DATE_SENTINEL,
        confidence=0.9,
    )
    decision = await dispatch(
        extraction=ext, user=user, today=_date(2026, 5, 31), db=session
    )
    assert isinstance(decision, ProposeAction)
    assert "sin culpa" in decision.summary_es


@pytest.mark.asyncio
async def test_framing_investor_falls_back_to_llm_archetype(db_with_user):
    session, user_id = db_with_user
    # investor has no Klontz target → the LLM archetype (if any) fills in.
    await _insert_insight(
        session,
        user_id,
        "archetype",
        {"type": "archetype", "primary": "money_worship", "confidence": "0.80",
         "evidence_summary": "x"},
        confidence="0.80",
        source="llm_extracted",
    )
    await _insert_insight(
        session,
        user_id,
        "money_personality",
        {"type": "money_personality", "personality": "investor", "scores": {},
         "evidence_summary": "x"},
        confidence="1.00",
    )
    archetype, _risk = await _optional_signals(session, user_id)
    assert archetype == "money_worship"
