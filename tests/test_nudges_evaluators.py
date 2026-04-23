"""Unit-ish tests for the three Phase 5d nudge evaluators.

We call them "unit tests" even though they hit a real Postgres, because
the behavior under test is the SQL — stubbing the session would reduce
coverage to reading our own fixtures back. Each test provisions its own
user via db_with_user, seeds domain data, runs the evaluator, asserts
candidates / dedup_keys / priority, and relies on the fixture to clean up.

Prereqs: `docker compose up -d db && alembic upgrade head`.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import text

from sqlalchemy import select

from api.models.notification_event import NotificationEvent
from api.models.pending_confirmation import PendingConfirmation
from api.models.transaction import Transaction
from api.models.user_nudge import UserNudge, UserNudgeSilence
from api.services.nudges.evaluators import (
    MissingIncomeEvaluator,
    StalePendingEvaluator,
    UpcomingBillEvaluator,
)
from api.services.nudges.orchestrator import evaluate_all
from api.services.nudges.policy import REASON_AUTO_DISMISSED_2X


# ─────────────────────────────────────────────────────────────────────────────
# missing_income
# ─────────────────────────────────────────────────────────────────────────────


async def _insert_expenses(session, user_id: uuid.UUID, n: int, ref_date: date):
    for i in range(n):
        session.add(
            Transaction(
                user_id=user_id,
                amount=Decimal("-1000"),
                currency="CRC",
                merchant=f"Merchant {i}",
                transaction_date=ref_date - timedelta(days=i % 6),
                source="manual",
            )
        )
    await session.commit()


async def test_missing_income_positive(db_with_user):
    session, user_id = db_with_user
    now = datetime(2026, 4, 22, 15, 0, tzinfo=timezone.utc)
    await _insert_expenses(session, user_id, 5, now.date())

    candidates = await MissingIncomeEvaluator().evaluate(session, now)
    mine = [c for c in candidates if c.user_id == user_id]

    assert len(mine) == 1
    c = mine[0]
    assert c.dedup_key == f"missing_income:{user_id}:2026-04"
    assert c.priority == "normal"
    assert c.payload["txn_count_last_7d"] == 5
    assert c.payload["month_tag"] == "2026-04"


async def test_missing_income_negative_not_enough_txns(db_with_user):
    session, user_id = db_with_user
    now = datetime(2026, 4, 22, 15, 0, tzinfo=timezone.utc)
    await _insert_expenses(session, user_id, 4, now.date())  # below threshold

    candidates = await MissingIncomeEvaluator().evaluate(session, now)
    mine = [c for c in candidates if c.user_id == user_id]

    assert mine == []


async def test_missing_income_negative_income_exists(db_with_user):
    session, user_id = db_with_user
    now = datetime(2026, 4, 22, 15, 0, tzinfo=timezone.utc)
    await _insert_expenses(session, user_id, 6, now.date())
    # Any income in the last 30 days disqualifies.
    session.add(
        Transaction(
            user_id=user_id,
            amount=Decimal("500000"),
            currency="CRC",
            merchant="Employer",
            transaction_date=now.date() - timedelta(days=10),
            source="manual",
        )
    )
    await session.commit()

    candidates = await MissingIncomeEvaluator().evaluate(session, now)
    mine = [c for c in candidates if c.user_id == user_id]

    assert mine == []


async def test_missing_income_dedup_key_stable(db_with_user):
    session, user_id = db_with_user
    now = datetime(2026, 4, 22, 15, 0, tzinfo=timezone.utc)
    await _insert_expenses(session, user_id, 7, now.date())

    run1 = await MissingIncomeEvaluator().evaluate(session, now)
    run2 = await MissingIncomeEvaluator().evaluate(
        session, now + timedelta(hours=3)
    )
    keys_1 = {c.dedup_key for c in run1 if c.user_id == user_id}
    keys_2 = {c.dedup_key for c in run2 if c.user_id == user_id}
    assert keys_1 == keys_2


# ─────────────────────────────────────────────────────────────────────────────
# stale_pending_confirmation
# ─────────────────────────────────────────────────────────────────────────────


async def _insert_pending(
    session, user_id: uuid.UUID, created_at: datetime, resolved: bool = False
) -> uuid.UUID:
    pid = uuid.uuid4()
    payload = {
        "action_type": "log_expense",
        "summary_es": "Registrar gasto de ₡5,000 en Más x Menos.",
        "payload": {
            "amount": "-5000",
            "currency": "CRC",
            "merchant": "Más x Menos",
        },
    }
    session.add(
        PendingConfirmation(
            id=pid,
            user_id=user_id,
            short_id="abc12345",
            channel="telegram",
            action_type="log_expense",
            proposed_action=payload,
            created_at=created_at,
            resolved_at=datetime.now(timezone.utc) if resolved else None,
            resolution="confirmed" if resolved else None,
        )
    )
    await session.commit()
    return pid


async def test_stale_pending_positive(db_with_user):
    session, user_id = db_with_user
    now = datetime(2026, 4, 22, 15, 0, tzinfo=timezone.utc)
    pid = await _insert_pending(session, user_id, now - timedelta(hours=50))

    candidates = await StalePendingEvaluator().evaluate(session, now)
    mine = [c for c in candidates if c.user_id == user_id]

    assert len(mine) == 1
    c = mine[0]
    assert c.dedup_key == f"stale_pending:{pid}"
    assert c.priority == "normal"
    assert c.payload["pending_confirmation_id"] == str(pid)
    assert c.payload["action_type"] == "log_expense"
    assert c.payload["proposed_action"]["payload"]["merchant"] == "Más x Menos"


async def test_stale_pending_negative_too_recent(db_with_user):
    session, user_id = db_with_user
    now = datetime(2026, 4, 22, 15, 0, tzinfo=timezone.utc)
    await _insert_pending(session, user_id, now - timedelta(hours=10))

    candidates = await StalePendingEvaluator().evaluate(session, now)
    mine = [c for c in candidates if c.user_id == user_id]

    assert mine == []


async def test_stale_pending_negative_resolved(db_with_user):
    session, user_id = db_with_user
    now = datetime(2026, 4, 22, 15, 0, tzinfo=timezone.utc)
    await _insert_pending(
        session, user_id, now - timedelta(hours=72), resolved=True
    )

    candidates = await StalePendingEvaluator().evaluate(session, now)
    mine = [c for c in candidates if c.user_id == user_id]

    assert mine == []


async def test_stale_pending_dedup_key_stable(db_with_user):
    session, user_id = db_with_user
    now = datetime(2026, 4, 22, 15, 0, tzinfo=timezone.utc)
    pid = await _insert_pending(session, user_id, now - timedelta(hours=60))

    run1 = await StalePendingEvaluator().evaluate(session, now)
    run2 = await StalePendingEvaluator().evaluate(
        session, now + timedelta(hours=2)
    )
    keys_1 = [c.dedup_key for c in run1 if c.user_id == user_id]
    keys_2 = [c.dedup_key for c in run2 if c.user_id == user_id]
    assert keys_1 == keys_2 == [f"stale_pending:{pid}"]


# ─────────────────────────────────────────────────────────────────────────────
# upcoming_bill
# ─────────────────────────────────────────────────────────────────────────────


async def _insert_notification_event(
    session,
    user_id: uuid.UUID,
    *,
    due_date: date,
    trigger_date: date,
    snapshot_kind: str = "bill",
    status: str = "pending",
) -> uuid.UUID:
    nid = uuid.uuid4()
    if snapshot_kind == "bill":
        snapshot = {
            "kind": "bill",
            "bill_name": "ICE",
            "bill_provider": "ICE",
            "bill_category": "utility_electricity",
            "amount_expected": 35000.0,
            "currency": "CRC",
            "due_date": due_date.isoformat(),
        }
    else:
        snapshot = {
            "kind": "event",
            "title": "Marchamo",
            "event_type": "tax_deadline",
            "event_date": due_date.isoformat(),
            "amount": 120000.0,
            "currency": "CRC",
        }
    # Per Phase 4 CHECK, notification_events requires exactly one of
    # bill_occurrence_id / custom_event_id. For this evaluator we don't
    # care which side it points at — it reads payload_snapshot. We seed
    # a throwaway bill_occurrence so the CHECK passes.
    if snapshot_kind == "bill":
        await _seed_bill_chain(
            session, user_id, due_date=due_date, notif_id=nid, snapshot=snapshot,
            trigger_date=trigger_date, status=status,
        )
    else:
        await _seed_event_chain(
            session, user_id, due_date=due_date, notif_id=nid, snapshot=snapshot,
            trigger_date=trigger_date, status=status,
        )
    return nid


async def _seed_bill_chain(
    session, user_id, *, due_date, notif_id, snapshot, trigger_date, status
):
    bill_id = uuid.uuid4()
    occ_id = uuid.uuid4()
    await session.execute(
        text(
            """
            INSERT INTO recurring_bills
              (id, user_id, name, category, currency, frequency, start_date,
               lead_time_days, is_active, is_variable_amount, created_at, updated_at)
            VALUES
              (:id, :uid, 'ICE', 'utility_electricity', 'CRC', 'monthly',
               :start, 0, true, false, now(), now())
            """
        ),
        {"id": bill_id, "uid": user_id, "start": due_date - timedelta(days=30)},
    )
    await session.execute(
        text(
            """
            INSERT INTO bill_occurrences
              (id, user_id, recurring_bill_id, due_date, amount_expected,
               status, created_at, updated_at)
            VALUES
              (:id, :uid, :bid, :dd, 35000, 'pending', now(), now())
            """
        ),
        {"id": occ_id, "uid": user_id, "bid": bill_id, "dd": due_date},
    )
    session.add(
        NotificationEvent(
            id=notif_id,
            user_id=user_id,
            bill_occurrence_id=occ_id,
            trigger_date=trigger_date,
            advance_days=(due_date - trigger_date).days,
            channel="in_app",
            status=status,
            payload_snapshot=snapshot,
        )
    )
    await session.commit()


async def _seed_event_chain(
    session, user_id, *, due_date, notif_id, snapshot, trigger_date, status
):
    ev_id = uuid.uuid4()
    await session.execute(
        text(
            """
            INSERT INTO custom_events
              (id, user_id, title, event_type, event_date, is_all_day,
               currency, is_active, created_at, updated_at)
            VALUES
              (:id, :uid, 'Marchamo', 'tax_deadline', :dd, true,
               'CRC', true, now(), now())
            """
        ),
        {"id": ev_id, "uid": user_id, "dd": due_date},
    )
    session.add(
        NotificationEvent(
            id=notif_id,
            user_id=user_id,
            custom_event_id=ev_id,
            trigger_date=trigger_date,
            advance_days=(due_date - trigger_date).days,
            channel="in_app",
            status=status,
            payload_snapshot=snapshot,
        )
    )
    await session.commit()


async def test_upcoming_bill_positive_normal_priority(db_with_user):
    session, user_id = db_with_user
    now = datetime(2026, 4, 22, 15, 0, tzinfo=timezone.utc)
    # due in 3 days → inside 72h window, outside 24h high window
    nid = await _insert_notification_event(
        session, user_id,
        due_date=now.date() + timedelta(days=3),
        trigger_date=now.date(),
    )

    candidates = await UpcomingBillEvaluator().evaluate(session, now)
    mine = [c for c in candidates if c.user_id == user_id]

    assert len(mine) == 1
    c = mine[0]
    assert c.dedup_key == f"upcoming_bill:{nid}"
    assert c.priority == "normal"
    assert c.source_notification_event_id == nid
    assert c.payload["snapshot"]["bill_name"] == "ICE"


async def test_upcoming_bill_positive_high_priority(db_with_user):
    session, user_id = db_with_user
    now = datetime(2026, 4, 22, 15, 0, tzinfo=timezone.utc)
    # due tomorrow → high priority
    nid = await _insert_notification_event(
        session, user_id,
        due_date=now.date() + timedelta(days=1),
        trigger_date=now.date(),
    )

    candidates = await UpcomingBillEvaluator().evaluate(session, now)
    mine = [c for c in candidates if c.user_id == user_id]

    assert len(mine) == 1
    assert mine[0].priority == "high"
    assert mine[0].dedup_key == f"upcoming_bill:{nid}"


async def test_upcoming_bill_negative_outside_window(db_with_user):
    session, user_id = db_with_user
    now = datetime(2026, 4, 22, 15, 0, tzinfo=timezone.utc)
    # due in 10 days → outside 72h window
    await _insert_notification_event(
        session, user_id,
        due_date=now.date() + timedelta(days=10),
        trigger_date=now.date(),
    )

    candidates = await UpcomingBillEvaluator().evaluate(session, now)
    mine = [c for c in candidates if c.user_id == user_id]
    assert mine == []


async def test_upcoming_bill_negative_not_pending(db_with_user):
    session, user_id = db_with_user
    now = datetime(2026, 4, 22, 15, 0, tzinfo=timezone.utc)
    await _insert_notification_event(
        session, user_id,
        due_date=now.date() + timedelta(days=2),
        trigger_date=now.date(),
        status="delivered",
    )

    candidates = await UpcomingBillEvaluator().evaluate(session, now)
    mine = [c for c in candidates if c.user_id == user_id]
    assert mine == []


async def test_upcoming_bill_custom_event_uses_event_date(db_with_user):
    session, user_id = db_with_user
    now = datetime(2026, 4, 22, 15, 0, tzinfo=timezone.utc)
    nid = await _insert_notification_event(
        session, user_id,
        due_date=now.date() + timedelta(days=2),
        trigger_date=now.date(),
        snapshot_kind="event",
    )

    candidates = await UpcomingBillEvaluator().evaluate(session, now)
    mine = [c for c in candidates if c.user_id == user_id]

    assert len(mine) == 1
    assert mine[0].dedup_key == f"upcoming_bill:{nid}"
    assert mine[0].payload["snapshot"]["title"] == "Marchamo"


# ─────────────────────────────────────────────────────────────────────────────
# orchestrator
# ─────────────────────────────────────────────────────────────────────────────


async def _nudges_for(session, user_id: uuid.UUID) -> list[UserNudge]:
    result = await session.execute(
        select(UserNudge).where(UserNudge.user_id == user_id)
    )
    return list(result.scalars().all())


async def test_orchestrator_creates_missing_income(db_with_user):
    session, user_id = db_with_user
    now = datetime(2026, 4, 22, 15, 0, tzinfo=timezone.utc)
    await _insert_expenses(session, user_id, 5, now.date())

    result = await evaluate_all(session, now=now, user_id=user_id)
    await session.commit()

    assert result.created == 1
    assert result.deduplicated == 0
    assert result.silenced == 0
    per_type = {c.nudge_type: c for c in result.per_type}
    assert per_type["missing_income"].created == 1
    assert per_type["stale_pending_confirmation"].created == 0
    assert per_type["upcoming_bill"].created == 0

    nudges = await _nudges_for(session, user_id)
    assert len(nudges) == 1
    n = nudges[0]
    assert n.nudge_type == "missing_income"
    assert n.status == "pending"
    assert n.priority == "normal"
    assert n.dedup_key.startswith(f"missing_income:{user_id}:")


async def test_orchestrator_second_run_is_noop_dedup(db_with_user):
    session, user_id = db_with_user
    now = datetime(2026, 4, 22, 15, 0, tzinfo=timezone.utc)
    await _insert_expenses(session, user_id, 6, now.date())

    first = await evaluate_all(session, now=now, user_id=user_id)
    await session.commit()
    second = await evaluate_all(
        session, now=now + timedelta(hours=1), user_id=user_id
    )
    await session.commit()

    assert first.created == 1
    assert second.created == 0
    assert second.deduplicated == 1
    nudges = await _nudges_for(session, user_id)
    assert len(nudges) == 1  # no duplicate


async def test_orchestrator_respects_silence(db_with_user):
    session, user_id = db_with_user
    now = datetime(2026, 4, 22, 15, 0, tzinfo=timezone.utc)
    await _insert_expenses(session, user_id, 7, now.date())

    # Silence missing_income for this user for 14 days.
    session.add(
        UserNudgeSilence(
            user_id=user_id,
            nudge_type="missing_income",
            silenced_until=now + timedelta(days=14),
            reason=REASON_AUTO_DISMISSED_2X,
        )
    )
    await session.commit()

    result = await evaluate_all(session, now=now, user_id=user_id)
    await session.commit()

    assert result.created == 0
    assert result.silenced == 1
    nudges = await _nudges_for(session, user_id)
    assert nudges == []


async def test_orchestrator_scoping_ignores_other_users(db_with_user):
    """Orchestrator run with user_id=A must not touch user B's data."""
    session, user_a = db_with_user
    now = datetime(2026, 4, 22, 15, 0, tzinfo=timezone.utc)
    # Seed candidate data for user A
    await _insert_expenses(session, user_a, 5, now.date())
    # Create a second user + seed data that would also qualify
    from api.models.user import User as UserModel
    import secrets as _secrets

    user_b_obj = UserModel(
        email=f"other-{uuid.uuid4().hex}@example.com",
        full_name="Other",
        shortcut_token=_secrets.token_urlsafe(48),
    )
    session.add(user_b_obj)
    await session.commit()
    await session.refresh(user_b_obj)
    user_b = user_b_obj.id
    try:
        await _insert_expenses(session, user_b, 6, now.date())

        # Run scoped to A only
        result = await evaluate_all(session, now=now, user_id=user_a)
        await session.commit()

        assert result.created == 1
        a_nudges = await _nudges_for(session, user_a)
        b_nudges = await _nudges_for(session, user_b)
        assert len(a_nudges) == 1
        assert b_nudges == []  # B was not touched
    finally:
        # Explicit cleanup for user B (db_with_user only tracks user A)
        await session.execute(
            text("DELETE FROM user_nudges WHERE user_id = :u"), {"u": user_b}
        )
        await session.execute(
            text("DELETE FROM transactions WHERE user_id = :u"), {"u": user_b}
        )
        await session.execute(
            text("DELETE FROM users WHERE id = :u"), {"u": user_b}
        )
        await session.commit()


async def test_upcoming_bill_dedup_key_stable(db_with_user):
    session, user_id = db_with_user
    now = datetime(2026, 4, 22, 15, 0, tzinfo=timezone.utc)
    nid = await _insert_notification_event(
        session, user_id,
        due_date=now.date() + timedelta(days=2),
        trigger_date=now.date(),
    )

    run1 = await UpcomingBillEvaluator().evaluate(session, now)
    run2 = await UpcomingBillEvaluator().evaluate(
        session, now + timedelta(hours=2)
    )
    keys_1 = [c.dedup_key for c in run1 if c.user_id == user_id]
    keys_2 = [c.dedup_key for c in run2 if c.user_id == user_id]
    assert keys_1 == keys_2 == [f"upcoming_bill:{nid}"]
