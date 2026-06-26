"""Phase 8 B5 — earned-celebration nudge layer.

The four positive-milestone evaluators (goal_achieved, debt_paid_off,
first_full_month, under_budget_month) are read-only: they turn a real milestone
into a NudgeCandidate; the orchestrator inserts + dedups. The *decision* to
celebrate is deterministic — no LLM, no fabricated numbers. These tests prove
each fires ONLY on its milestone, dedups, reads the FROZEN snapshot (never live),
and that the feed/buttons/phrasing/state-transitions all wire up.

Prereqs: `docker compose up -d db && alembic upgrade head` (needs migration 0042
for the widened nudge_type CHECK).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from api.models.debt import Debt
from api.models.goal import Goal
from api.models.snapshot import CashflowSnapshot, EnvelopeSnapshot
from api.models.user_nudge import UserNudge, UserNudgeSilence
from api.services.nudges.actions import mark_acted_on, mark_dismissed
from api.services.nudges.delivery import buttons_for
from api.services.nudges.evaluators import (
    ALL_EVALUATORS,
    DebtPaidOffEvaluator,
    FirstFullMonthEvaluator,
    GoalAchievedEvaluator,
    UnderBudgetMonthEvaluator,
)
from api.services.nudges.feed import render_nudge_text
from api.services.nudges.orchestrator import evaluate_all
from api.services.nudges.phrasing import build_user_prompt


_NOW = datetime(2026, 6, 15, 15, 0, tzinfo=timezone.utc)
_CLOSED = "2026-05"   # a month strictly before _NOW
_CURRENT = "2026-06"  # _NOW's month


# ── seed helpers ──────────────────────────────────────────────────────────────


async def _add_goal(session, user_id, *, name, target, current, status="active"):
    goal = Goal(
        user_id=user_id,
        name=name,
        target_amount=Decimal(target),
        target_currency="CRC",
        current_amount=Decimal(current),
        status=status,
    )
    session.add(goal)
    await session.commit()
    await session.refresh(goal)
    return goal


async def _add_debt(session, user_id, *, name, balance, archived=False):
    debt = Debt(
        user_id=user_id,
        name=name,
        debt_type="loan",
        original_amount=Decimal("5000000"),
        current_balance=Decimal(balance),
        interest_rate=Decimal("0.20"),
        minimum_payment=Decimal("100000"),
        payment_due_day=1,
        archived=archived,
        is_active=not archived,
    )
    session.add(debt)
    await session.commit()
    await session.refresh(debt)
    return debt


async def _add_cashflow_snapshot(session, user_id, *, period):
    snap = CashflowSnapshot(
        user_id=user_id,
        period=period,
        currency="CRC",
        debt_payments=Decimal("0"),
        recurring_bills=Decimal("0"),
        envelope_allocations=Decimal("0"),
        committed_outflows=Decimal("0"),
        surplus=Decimal("0"),
        savings_allocations=Decimal("0"),
        total_limit=Decimal("0"),
        total_spent=Decimal("0"),
        total_reserved=Decimal("0"),
        total_available=Decimal("0"),
        payload={},
    )
    session.add(snap)
    await session.commit()
    await session.refresh(snap)
    return snap


async def _add_envelope_snapshot(
    session, user_id, *, name, period, over_limit, spent, limit="100000"
):
    snap = EnvelopeSnapshot(
        user_id=user_id,
        period=period,
        envelope_id=None,  # FK SET NULL allows NULL; dedup falls back to snap.id
        name=name,
        envelope_class="needs",
        currency="CRC",
        depth=1,
        limit_amount=Decimal(limit),
        spent=Decimal(spent),
        direct_spent=Decimal(spent),
        reserved=Decimal("0"),
        available=Decimal(limit) - Decimal(spent),
        over_limit=over_limit,
    )
    session.add(snap)
    await session.commit()
    await session.refresh(snap)
    return snap


async def _nudges_of_type(session, user_id, nudge_type):
    result = await session.execute(
        select(UserNudge).where(
            UserNudge.user_id == user_id, UserNudge.nudge_type == nudge_type
        )
    )
    return list(result.scalars().all())


# ── goal_achieved ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_goal_achieved_fires_on_status_and_on_target(db_with_user):
    session, user_id = db_with_user
    achieved = await _add_goal(
        session, user_id, name="Viaje", target="500000", current="200000",
        status="achieved",
    )
    at_target = await _add_goal(
        session, user_id, name="Emergencia", target="300000", current="300000",
    )
    await _add_goal(  # under target, active → not a milestone
        session, user_id, name="Carro", target="9000000", current="100000",
    )
    await _add_goal(  # over target but abandoned → not a win
        session, user_id, name="Vieja", target="100", current="500",
        status="abandoned",
    )

    cands = await GoalAchievedEvaluator().evaluate(session, _NOW, user_id=user_id)
    keys = {c.dedup_key for c in cands}
    assert keys == {
        f"goal_achieved:{achieved.id}",
        f"goal_achieved:{at_target.id}",
    }
    c = next(c for c in cands if c.dedup_key == f"goal_achieved:{achieved.id}")
    assert c.priority == "normal"
    assert c.payload["name"] == "Viaje"
    assert c.payload["target_amount"] == "500000.00"
    assert c.payload["currency"] == "CRC"


@pytest.mark.asyncio
async def test_goal_achieved_dedups(db_with_user):
    session, user_id = db_with_user
    await _add_goal(
        session, user_id, name="Viaje", target="500000", current="500000",
        status="achieved",
    )
    evs = [GoalAchievedEvaluator()]

    first = await evaluate_all(session, now=_NOW, user_id=user_id, evaluators=evs)
    await session.commit()
    second = await evaluate_all(
        session, now=_NOW + timedelta(hours=1), user_id=user_id, evaluators=evs
    )
    await session.commit()

    pt1 = {c.nudge_type: c for c in first.per_type}
    pt2 = {c.nudge_type: c for c in second.per_type}
    assert pt1["goal_achieved"].created == 1
    assert pt2["goal_achieved"].created == 0
    assert pt2["goal_achieved"].deduplicated == 1
    assert len(await _nudges_of_type(session, user_id, "goal_achieved")) == 1


# ── debt_paid_off ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_debt_paid_off_fires_only_at_zero(db_with_user):
    session, user_id = db_with_user
    paid = await _add_debt(session, user_id, name="Préstamo", balance="0")
    await _add_debt(session, user_id, name="Tarjeta", balance="250000")

    cands = await DebtPaidOffEvaluator().evaluate(session, _NOW, user_id=user_id)
    assert {c.dedup_key for c in cands} == {f"debt_paid:{paid.id}"}
    assert cands[0].payload["name"] == "Préstamo"
    assert cands[0].priority == "normal"


@pytest.mark.asyncio
async def test_debt_double_signal_collapses_to_one(db_with_user):
    """payment-to-0 then delete (archive) must celebrate exactly once."""
    session, user_id = db_with_user
    debt = await _add_debt(session, user_id, name="Préstamo", balance="0")
    evs = [DebtPaidOffEvaluator()]

    # Signal 1: payment brought it to zero (still active).
    first = await evaluate_all(session, now=_NOW, user_id=user_id, evaluators=evs)
    await session.commit()
    # Signal 2: the user later deletes it (archived=True, balance unchanged).
    debt.archived = True
    debt.is_active = False
    await session.commit()
    second = await evaluate_all(
        session, now=_NOW + timedelta(hours=2), user_id=user_id, evaluators=evs
    )
    await session.commit()

    assert {c.nudge_type: c for c in first.per_type}["debt_paid_off"].created == 1
    assert {c.nudge_type: c for c in second.per_type}["debt_paid_off"].created == 0
    assert len(await _nudges_of_type(session, user_id, "debt_paid_off")) == 1


# ── first_full_month ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_first_full_month_once_only(db_with_user):
    session, user_id = db_with_user
    await _add_cashflow_snapshot(session, user_id, period=_CLOSED)
    await _add_cashflow_snapshot(session, user_id, period=_CURRENT)

    cands = await FirstFullMonthEvaluator().evaluate(session, _NOW, user_id=user_id)
    # Exactly one per user, carrying the EARLIEST period.
    assert len(cands) == 1
    assert cands[0].dedup_key == f"first_full_month:{user_id}"
    assert cands[0].payload["period"] == _CLOSED

    evs = [FirstFullMonthEvaluator()]
    await evaluate_all(session, now=_NOW, user_id=user_id, evaluators=evs)
    await session.commit()
    second = await evaluate_all(
        session, now=_NOW + timedelta(days=1), user_id=user_id, evaluators=evs
    )
    await session.commit()
    assert {c.nudge_type: c for c in second.per_type}["first_full_month"].created == 0
    assert len(await _nudges_of_type(session, user_id, "first_full_month")) == 1


@pytest.mark.asyncio
async def test_first_full_month_no_snapshot_no_fire(db_with_user):
    session, user_id = db_with_user
    cands = await FirstFullMonthEvaluator().evaluate(session, _NOW, user_id=user_id)
    assert cands == []


# ── under_budget_month ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_under_budget_only_closed_clean_spent_months(db_with_user):
    session, user_id = db_with_user
    clean = await _add_envelope_snapshot(
        session, user_id, name="Comida", period=_CLOSED, over_limit=False,
        spent="80000",
    )
    await _add_envelope_snapshot(  # over the limit → not a win
        session, user_id, name="Salidas", period=_CLOSED, over_limit=True,
        spent="120000",
    )
    await _add_envelope_snapshot(  # current (open) month → too early
        session, user_id, name="Comida", period=_CURRENT, over_limit=False,
        spent="50000",
    )
    await _add_envelope_snapshot(  # untouched envelope → not earned
        session, user_id, name="Regalos", period=_CLOSED, over_limit=False,
        spent="0",
    )

    cands = await UnderBudgetMonthEvaluator().evaluate(
        session, _NOW, user_id=user_id
    )
    assert {c.dedup_key for c in cands} == {f"under_budget:{clean.id}:{_CLOSED}"}
    assert cands[0].payload["name"] == "Comida"
    assert cands[0].payload["period"] == _CLOSED
    assert cands[0].payload["spent"] == "80000.00"


@pytest.mark.asyncio
async def test_under_budget_reads_frozen_snapshot_no_refire(db_with_user):
    """A later limit edit must NOT re-fire a celebration already made."""
    session, user_id = db_with_user
    snap = await _add_envelope_snapshot(
        session, user_id, name="Comida", period=_CLOSED, over_limit=False,
        spent="80000", limit="100000",
    )
    evs = [UnderBudgetMonthEvaluator()]

    first = await evaluate_all(session, now=_NOW, user_id=user_id, evaluators=evs)
    await session.commit()
    assert {c.nudge_type: c for c in first.per_type}["under_budget_month"].created == 1

    # Simulate a limit edit landing on the frozen row. The dedup key (envelope/
    # period) is unchanged, so the celebration must not duplicate.
    snap.limit_amount = Decimal("50000")
    snap.available = Decimal("-30000")
    await session.commit()

    second = await evaluate_all(
        session, now=_NOW + timedelta(hours=6), user_id=user_id, evaluators=evs
    )
    await session.commit()
    assert {c.nudge_type: c for c in second.per_type}["under_budget_month"].created == 0
    assert len(await _nudges_of_type(session, user_id, "under_budget_month")) == 1


# ── feed render + buttons + phrasing ──────────────────────────────────────────


def test_feed_render_and_buttons_present_for_each_type():
    cases = {
        "goal_achieved": (
            {"name": "Viaje", "currency": "CRC", "target_amount": "500000.00"},
            "Viaje",
        ),
        "debt_paid_off": ({"name": "Préstamo", "currency": "CRC"}, "Préstamo"),
        "first_full_month": ({"period": "2026-05"}, "mayo"),
        "under_budget_month": (
            {
                "name": "Comida", "currency": "CRC", "period": "2026-05",
                "spent": "80000.00", "limit_amount": "100000.00",
            },
            "Comida",
        ),
    }
    for nudge_type, (payload, token) in cases.items():
        text = render_nudge_text(nudge_type, payload)
        assert text and text != "Tenés una notificación pendiente."
        assert token in text
        buttons = buttons_for(nudge_type)
        assert buttons, f"{nudge_type} must have at least one button"
        # No problem-focused "No mostrar más" guilt on a celebration.
        assert all("No mostrar" not in b.label for b in buttons)


def test_phrasing_prompts_carry_real_numbers():
    goal_prompt = build_user_prompt(
        "goal_achieved",
        {"name": "Viaje", "currency": "CRC", "target_amount": "500000.00"},
    )
    assert "Viaje" in goal_prompt
    assert "CRC 500,000" in goal_prompt

    budget_prompt = build_user_prompt(
        "under_budget_month",
        {
            "name": "Comida", "currency": "CRC", "period": "2026-05",
            "spent": "80000.00", "limit_amount": "100000.00",
        },
    )
    assert "Comida" in budget_prompt
    assert "CRC 80,000" in budget_prompt
    assert "CRC 100,000" in budget_prompt


# ── state transitions ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mark_acted_and_dismiss_work(db_with_user):
    session, user_id = db_with_user
    await _add_goal(
        session, user_id, name="Viaje", target="500000", current="500000",
        status="achieved",
    )
    await _add_debt(session, user_id, name="Préstamo", balance="0")
    await evaluate_all(
        session, now=_NOW, user_id=user_id,
        evaluators=[GoalAchievedEvaluator(), DebtPaidOffEvaluator()],
    )
    await session.commit()

    goal_nudge = (await _nudges_of_type(session, user_id, "goal_achieved"))[0]
    debt_nudge = (await _nudges_of_type(session, user_id, "debt_paid_off"))[0]

    acted = await mark_acted_on(session, user_id=user_id, nudge_id=goal_nudge.id)
    dismissed = await mark_dismissed(
        session, user_id=user_id, nudge_id=debt_nudge.id
    )
    await session.commit()

    assert acted.status == "acted_on"
    assert dismissed.nudge.status == "dismissed"


# ── registry + CHECK constraint ───────────────────────────────────────────────


def test_all_evaluators_count_is_eleven():
    assert len(ALL_EVALUATORS) == 11
    types = {e.nudge_type for e in ALL_EVALUATORS}
    assert {
        "goal_achieved",
        "debt_paid_off",
        "first_full_month",
        "under_budget_month",
    } <= types


@pytest.mark.asyncio
async def test_check_constraint_accepts_new_types(db_with_user):
    """Proves migration 0042 widened the CHECK on both tables."""
    session, user_id = db_with_user
    for t in ("goal_achieved", "debt_paid_off", "first_full_month", "under_budget_month"):
        session.add(
            UserNudge(
                user_id=user_id,
                nudge_type=t,
                priority="normal",
                dedup_key=f"{t}:{uuid.uuid4()}",
                payload={},
            )
        )
        session.add(
            UserNudgeSilence(
                user_id=user_id,
                nudge_type=t,
                silenced_until=_NOW + timedelta(days=1),
                reason="manual_user_request",
            )
        )
    await session.commit()  # would raise IntegrityError if the CHECK rejected them

    assert len(await _nudges_of_type(session, user_id, "goal_achieved")) == 1
