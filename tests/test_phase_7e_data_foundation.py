"""Phase 7e — data foundation: advice trace, snapshots, consent ledger.

Covers:
- record_advice_event persists an append-only row (own session) with full
  payloads and outcome_status='pending'.
- The assess_purchase query tool and the conversational goal-feasibility
  gate actually emit trace rows (the recorder swallows failures, so only a
  positive row assertion proves the wiring).
- capture_user_snapshots freezes envelope + cashflow figures per period,
  upserts idempotently, and recaptures the just-closed period early in a
  month.
- Consent ledger: GET shows never_set for undecided purposes; POST appends
  grant/revoke acts; history is append-only (two rows after grant+revoke).
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from api.database import get_db
from api.dependencies import current_user
from api.main import app
from api.models import AdviceEvent, CashflowSnapshot, EnvelopeSnapshot, UserConsent
from api.models.envelope import Envelope
from api.models.transaction import Transaction
from api.models.user import User
from api.services.advice_trace import record_advice_event, verdict_from
from api.services.snapshots import capture_user_snapshots


def _override(session, user_id):
    class _StubUser:
        def __init__(self) -> None:
            self.id = user_id
            self.status = "active"
            self.currency = "CRC"
            self.display_currency = "CRC"
            self.timezone = "America/Costa_Rica"

    async def _yield_session():
        yield session

    app.dependency_overrides[current_user] = lambda: _StubUser()
    app.dependency_overrides[get_db] = _yield_session


def _clear():
    app.dependency_overrides.pop(current_user, None)
    app.dependency_overrides.pop(get_db, None)


async def _advice_rows(session, user_id, kind=None):
    stmt = select(AdviceEvent).where(AdviceEvent.user_id == user_id)
    if kind:
        stmt = stmt.where(AdviceEvent.kind == kind)
    return (await session.execute(stmt)).scalars().all()


# ── advice trace ──────────────────────────────────────────────────────────────


def test_verdict_from_mapping():
    assert verdict_from(feasible=True, gate_reason=None) == "feasible"
    assert verdict_from(feasible=False, gate_reason=None) == "not_feasible"
    assert verdict_from(feasible=None, gate_reason="no_income") == "gated:no_income"
    assert verdict_from(feasible=None, gate_reason=None) == "unknown"


@pytest.mark.asyncio
async def test_record_advice_event_persists(db_with_user):
    session, user_id = db_with_user
    await record_advice_event(
        user_id=user_id,
        kind="assess_purchase",
        verdict="feasible",
        inputs={"amount": 100000, "timeline_months": 5},
        result={"feasible": True, "surplus": "250000.00"},
        surface="query_tool",
    )
    rows = await _advice_rows(session, user_id)
    assert len(rows) == 1
    row = rows[0]
    assert row.kind == "assess_purchase"
    assert row.verdict == "feasible"
    assert row.surface == "query_tool"
    assert row.inputs == {"amount": 100000, "timeline_months": 5}
    assert row.result["surplus"] == "250000.00"
    assert row.outcome_status == "pending"
    assert row.outcome is None


@pytest.mark.asyncio
async def test_assess_purchase_tool_emits_trace(db_with_user):
    from app.queries.tools.affordability import assess_purchase

    session, user_id = db_with_user
    payload = await assess_purchase(
        amount=200000, timeline_months=4, user_id=user_id
    )
    assert "feasible" in payload
    rows = await _advice_rows(session, user_id, kind="assess_purchase")
    assert len(rows) == 1
    # Fresh user: no income / no envelopes → a gated verdict, recorded as such.
    assert rows[0].verdict.startswith("gated:")
    assert rows[0].inputs == {"amount": 200000.0, "timeline_months": 4}
    assert rows[0].result["gate_reason"] == payload["gate_reason"]


@pytest.mark.asyncio
async def test_goal_feasibility_gate_emits_trace(db_with_user):
    from api.services.llm_extractor import ExtractionResult, Intent
    from api.services.telegram_dispatcher import ProposeAction, _dispatch_create_goal

    session, user_id = db_with_user
    user = await session.get(User, user_id)
    today = date.today()
    target_date = (today + timedelta(days=200)).isoformat()
    extraction = ExtractionResult(
        intent=Intent.CREATE_GOAL,
        dispatcher="write",
        goal_name="fondo de emergencia",
        goal_target_amount=Decimal("600000"),
        goal_target_date=target_date,
        confidence=0.9,
    )
    result = await _dispatch_create_goal(
        extraction=extraction, user=user, today=today, db=session
    )
    assert isinstance(result, ProposeAction)
    rows = await _advice_rows(session, user_id, kind="goal_feasibility")
    assert len(rows) == 1
    assert rows[0].surface == "write_dispatcher"
    assert rows[0].inputs["goal_name"] == "fondo de emergencia"
    assert rows[0].inputs["target_amount"] == "600000"


# ── snapshots ─────────────────────────────────────────────────────────────────


async def _seed_envelope_with_spend(session, user_id) -> uuid.UUID:
    env = Envelope(
        user_id=user_id,
        name="Súper",
        envelope_class="needs",
        limit_amount=Decimal("100000"),
        currency="CRC",
    )
    session.add(env)
    await session.flush()
    env_id = env.id
    session.add(
        Transaction(
            user_id=user_id,
            amount=-30000,
            currency="CRC",
            transaction_date=date.today(),
            source="manual",
            status="confirmed",
            envelope_id=env_id,
        )
    )
    await session.commit()
    return env_id


@pytest.mark.asyncio
async def test_capture_snapshots_freezes_and_upserts(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    env_id = await _seed_envelope_with_spend(session, user_id)

    captured = await capture_user_snapshots(session, user=user)
    await session.commit()
    assert captured["envelope_rows"] == 1
    period = captured["period"]

    snap = (
        await session.execute(
            select(EnvelopeSnapshot).where(
                EnvelopeSnapshot.envelope_id == env_id,
                EnvelopeSnapshot.period == period,
            )
        )
    ).scalar_one()
    assert snap.spent == Decimal("30000.00")
    assert snap.limit_amount == Decimal("100000.00")
    assert snap.available == Decimal("70000.00")
    assert snap.over_limit is False
    assert snap.name == "Súper"

    cash = (
        await session.execute(
            select(CashflowSnapshot).where(
                CashflowSnapshot.user_id == user_id,
                CashflowSnapshot.period == period,
            )
        )
    ).scalar_one()
    assert cash.envelope_allocations == Decimal("100000.00")
    assert cash.total_spent == Decimal("30000.00")
    # No income on file → honest NULL + the no_income gate.
    assert cash.monthly_income is None
    assert cash.gate_reason == "no_income"

    # Idempotent: a second capture upserts, never duplicates.
    session.add(
        Transaction(
            user_id=user_id,
            amount=-10000,
            currency="CRC",
            transaction_date=date.today(),
            source="manual",
            status="confirmed",
            envelope_id=env_id,
        )
    )
    await session.commit()
    await capture_user_snapshots(session, user=user)
    await session.commit()
    # The upsert is a core ON CONFLICT UPDATE; expire the identity map so the
    # re-select doesn't hand back the cached pre-update instance.
    session.expire_all()

    snaps = (
        await session.execute(
            select(EnvelopeSnapshot).where(
                EnvelopeSnapshot.envelope_id == env_id,
                EnvelopeSnapshot.period == period,
            )
        )
    ).scalars().all()
    assert len(snaps) == 1
    assert snaps[0].spent == Decimal("40000.00")


@pytest.mark.asyncio
async def test_capture_recaptures_previous_period_early_in_month(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    await _seed_envelope_with_spend(session, user_id)

    # Pretend it's day 2 of NEXT month: the just-closed period (= the real
    # current month, where the seeded spend lives) must also be captured.
    today = date.today()
    next_month_start = date(
        today.year + (1 if today.month == 12 else 0),
        1 if today.month == 12 else today.month + 1,
        1,
    )
    fake_today = next_month_start + timedelta(days=1)

    captured = await capture_user_snapshots(session, user=user, today=fake_today)
    await session.commit()

    assert captured["prev_period"] == f"{today.year:04d}-{today.month:02d}"
    periods = {
        p
        for (p,) in (
            await session.execute(
                select(EnvelopeSnapshot.period).where(
                    EnvelopeSnapshot.user_id == user_id
                )
            )
        ).all()
    }
    assert captured["period"] in periods  # the (fake) current month
    assert captured["prev_period"] in periods  # the closed month, full window

    prev_snap = (
        await session.execute(
            select(EnvelopeSnapshot).where(
                EnvelopeSnapshot.user_id == user_id,
                EnvelopeSnapshot.period == captured["prev_period"],
            )
        )
    ).scalar_one()
    assert prev_snap.spent == Decimal("30000.00")


# ── consent ledger ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_consent_grant_revoke_appends(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/api/v1/users/me/consents")
            assert resp.status_code == 200, resp.text
            states = {c["purpose"]: c["status"] for c in resp.json()["consents"]}
            assert states == {
                "core_service": "never_set",
                "behavioral_insights": "never_set",
                "product_research": "never_set",
                "aggregated_datasets": "never_set",
            }

            grant = await ac.post(
                "/api/v1/users/me/consents",
                json={
                    "purpose": "behavioral_insights",
                    "granted": True,
                    "version": "2026-06.v1",
                },
            )
            assert grant.status_code == 200, grant.text
            item = next(
                c
                for c in grant.json()["consents"]
                if c["purpose"] == "behavioral_insights"
            )
            assert item["status"] == "granted"
            assert item["version"] == "2026-06.v1"

            revoke = await ac.post(
                "/api/v1/users/me/consents",
                json={
                    "purpose": "behavioral_insights",
                    "granted": False,
                    "version": "2026-06.v1",
                },
            )
            assert revoke.status_code == 200
            item = next(
                c
                for c in revoke.json()["consents"]
                if c["purpose"] == "behavioral_insights"
            )
            assert item["status"] == "revoked"

            # Unknown purpose is rejected by the schema (Literal) → 422.
            bad = await ac.post(
                "/api/v1/users/me/consents",
                json={"purpose": "marketing", "granted": True, "version": "v1"},
            )
            assert bad.status_code == 422

        # Append-only: both acts persisted as separate rows.
        rows = (
            await session.execute(
                select(UserConsent).where(UserConsent.user_id == user_id)
            )
        ).scalars().all()
        assert len(rows) == 2
        assert {r.status for r in rows} == {"granted", "revoked"}
    finally:
        _clear()
