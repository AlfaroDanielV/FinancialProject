"""envelope_near_limit evaluator + nudge wiring.

Fires when a spending-cap envelope is nearly spent (stage "near", rolled-up
pct ≥ 90% and not over) and again once it goes over its limit (stage "over").
Reuses `compute_envelope_summary`, so the alert reads the same figure as the
on-screen bar. Two stages per envelope per month (distinct dedup keys); a
re-run at the same state is a no-op. Rules decide; the LLM (push) / the feed
renderer (pull) only phrase.

Prereqs: `docker compose up -d db && alembic upgrade head`.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from api.models.envelope import Envelope
from api.models.transaction import Transaction
from api.models.user_nudge import UserNudge
from api.services.nudges.delivery import buttons_for
from api.services.nudges.evaluators import EnvelopeNearLimitEvaluator
from api.services.nudges.feed import render_nudge_text
from api.services.nudges.orchestrator import evaluate_all
from api.services.nudges.phrasing import build_user_prompt


# The envelope spend window comes from compute_envelope_summary, which always
# uses the REAL current month (user-tz "now"); only the dedup-key month_tag uses
# the evaluator's `now`. Pin both to the real current month so they agree.
_TODAY = date.today()
_NOW = datetime(_TODAY.year, _TODAY.month, 15, 18, 0, tzinfo=timezone.utc)
_MONTH_TAG = f"{_TODAY.year:04d}-{_TODAY.month:02d}"


async def _seed_envelope(session, user_id, *, name="Súper", limit="100000"):
    env = Envelope(
        user_id=user_id,
        name=name,
        envelope_class="needs",
        limit_amount=Decimal(limit),
        currency="CRC",
    )
    session.add(env)
    await session.commit()
    await session.refresh(env)
    return env.id


async def _seed_expense(session, user_id, env_id, *, amount):
    """Tag a confirmed, current-month expense to the envelope (amount<0)."""
    session.add(
        Transaction(
            user_id=user_id,
            amount=-abs(amount),
            currency="CRC",
            transaction_date=date(_TODAY.year, _TODAY.month, 10),
            source="manual",
            status="confirmed",
            archived=False,
            envelope_id=env_id,
        )
    )
    await session.commit()


async def _envelope_nudges(session, user_id) -> list[UserNudge]:
    result = await session.execute(
        select(UserNudge).where(
            UserNudge.user_id == user_id,
            UserNudge.nudge_type == "envelope_near_limit",
        )
    )
    return list(result.scalars().all())


# ── evaluator ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fires_near_when_almost_spent(db_with_user):
    session, user_id = db_with_user
    env_id = await _seed_envelope(session, user_id, limit="100000")
    await _seed_expense(session, user_id, env_id, amount=50000)
    await _seed_expense(session, user_id, env_id, amount=42000)  # 92% total

    candidates = await EnvelopeNearLimitEvaluator().evaluate(
        session, _NOW, user_id=user_id
    )
    mine = [c for c in candidates if c.user_id == user_id]

    assert len(mine) == 1
    c = mine[0]
    assert c.dedup_key == f"envelope_near_limit:{env_id}:{_MONTH_TAG}:near"
    assert c.priority == "normal"
    assert c.payload["stage"] == "near"
    assert c.payload["pct"] == 92
    assert c.payload["name"] == "Súper"
    assert c.payload["currency"] == "CRC"
    assert c.payload["limit_amount"] == "100000.00"
    assert c.payload["spent"] == "92000.00"
    assert c.payload["available"] == "8000.00"


@pytest.mark.asyncio
async def test_fires_over_when_past_limit(db_with_user):
    session, user_id = db_with_user
    env_id = await _seed_envelope(session, user_id, limit="100000")
    await _seed_expense(session, user_id, env_id, amount=110000)  # 110%

    candidates = await EnvelopeNearLimitEvaluator().evaluate(
        session, _NOW, user_id=user_id
    )
    mine = [c for c in candidates if c.user_id == user_id]

    assert len(mine) == 1
    c = mine[0]
    assert c.dedup_key == f"envelope_near_limit:{env_id}:{_MONTH_TAG}:over"
    assert c.payload["stage"] == "over"
    assert c.payload["pct"] == 110


@pytest.mark.asyncio
async def test_does_not_fire_below_threshold(db_with_user):
    session, user_id = db_with_user
    env_id = await _seed_envelope(session, user_id, limit="100000")
    await _seed_expense(session, user_id, env_id, amount=50000)  # 50%

    candidates = await EnvelopeNearLimitEvaluator().evaluate(
        session, _NOW, user_id=user_id
    )
    assert [c for c in candidates if c.user_id == user_id] == []


@pytest.mark.asyncio
async def test_skips_shared_joined_envelope(db_with_user, monkeypatch):
    """A shared envelope the user only JOINED (is_shared) is display-only — it
    belongs to another owner and must not raise an alert for this user."""
    import api.services.nudges.evaluators.envelope_near_limit as mod
    from api.schemas.envelopes import (
        EnvelopeSummaryItem,
        EnvelopeSummaryResponse,
    )

    session, user_id = db_with_user

    owned = EnvelopeSummaryItem(
        id=uuid.uuid4(), name="Mío", envelope_class="needs", currency="CRC",
        limit_amount=100000, spent=95000, direct_spent=95000, available=5000,
        remaining=5000, pct=0.95, over_limit=False,
    )
    joined = EnvelopeSummaryItem(
        id=uuid.uuid4(), name="Compartido", envelope_class="needs",
        currency="CRC", limit_amount=100000, spent=99000, direct_spent=0,
        available=1000, remaining=1000, pct=0.99, over_limit=False,
        is_shared=True,
    )

    async def _fake_summary(_session, *, user, today=None):
        return EnvelopeSummaryResponse(
            period=_MONTH_TAG, currency="CRC",
            envelopes=[owned, joined], total_limit=100000,
        )

    monkeypatch.setattr(mod, "compute_envelope_summary", _fake_summary)

    candidates = await EnvelopeNearLimitEvaluator().evaluate(
        session, _NOW, user_id=user_id
    )
    mine = [c for c in candidates if c.user_id == user_id]
    assert len(mine) == 1
    assert mine[0].payload["name"] == "Mío"


# ── orchestrator: two-stage dedup ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_two_stage_dedup_near_then_over(db_with_user):
    session, user_id = db_with_user
    env_id = await _seed_envelope(session, user_id, limit="100000")
    await _seed_expense(session, user_id, env_id, amount=92000)  # near

    await evaluate_all(session, _NOW, user_id=user_id)
    await session.commit()
    nudges = await _envelope_nudges(session, user_id)
    assert len(nudges) == 1
    assert nudges[0].dedup_key.endswith(":near")

    # Same state → dedup, no second nudge.
    await evaluate_all(session, _NOW, user_id=user_id)
    await session.commit()
    assert len(await _envelope_nudges(session, user_id)) == 1

    # Push it over the limit → the "over" stage fires as a distinct nudge.
    await _seed_expense(session, user_id, env_id, amount=20000)  # 112% total
    await evaluate_all(session, _NOW, user_id=user_id)
    await session.commit()
    nudges = await _envelope_nudges(session, user_id)
    stages = sorted(n.dedup_key.rsplit(":", 1)[-1] for n in nudges)
    assert stages == ["near", "over"]


# ── phrasing / feed / buttons (pure) ─────────────────────────────────────────


def test_phrasing_feed_and_buttons_render():
    near = {
        "name": "Súper", "currency": "CRC", "limit_amount": "100000.00",
        "spent": "92000.00", "available": "8000.00", "pct": 92, "stage": "near",
    }
    over = {**near, "spent": "110000.00", "available": "0.00", "pct": 110,
            "stage": "over"}

    pn = build_user_prompt("envelope_near_limit", near)
    po = build_user_prompt("envelope_near_limit", over)
    assert "Súper" in pn and "casi" in pn.lower()
    assert "Súper" in po and "pas" in po.lower()  # "pasó" / "pasaste"

    fn = render_nudge_text("envelope_near_limit", near)
    fo = render_nudge_text("envelope_near_limit", over)
    assert "Súper" in fn and "92%" in fn
    assert "Súper" in fo and "110%" in fo

    assert [b.verb for b in buttons_for("envelope_near_limit")] == [
        "act", "later", "dismiss"
    ]
