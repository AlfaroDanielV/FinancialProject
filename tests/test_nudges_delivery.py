"""Tests for the delivery worker.

The delivery worker is where the anti-saturation rules live at runtime.
Every branch has a test:

    - quiet hours defer
    - silence (live) defers
    - rate limit: prior-run and same-run variants
    - high-priority bypasses the rate limit
    - happy path: pending → sent
    - LLM failure: counted as failed, nudge stays pending
    - unpaired user: counted as failed, nothing sent

No real Anthropic / Telegram calls — `FixturePhrasingClient` and a
local fake send function replace them.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select, text

from api.models.user import User
from api.models.user_nudge import UserNudge, UserNudgeSilence
from api.services.nudges.delivery import NudgeMessage, deliver_all
from api.services.nudges.phrasing import (
    FixturePhrasingClient,
    PhrasingClientError,
)
from api.services.nudges.policy import (
    RATE_LIMIT_WINDOW_HOURS,
    REASON_AUTO_DISMISSED_2X,
)


_TEST_MODEL = "claude-haiku-4-5"


@dataclass
class _FakeSend:
    """Records every NudgeMessage passed in; `ok_response` chooses whether
    the fake pretends the channel succeeded."""

    calls: list[NudgeMessage] = field(default_factory=list)
    ok_response: bool = True
    raise_on_call: bool = False

    async def __call__(self, message: NudgeMessage) -> bool:
        self.calls.append(message)
        if self.raise_on_call:
            raise RuntimeError("simulated telegram error")
        return self.ok_response


class _RaisingPhrasingClient:
    async def phrase(
        self, *, system_prompt, user_prompt, model, timeout_s=12.0
    ) -> str:
        raise PhrasingClientError("simulated LLM failure")


async def _pair_user(session, user_id: uuid.UUID, tg_id: int = 12345) -> None:
    user = await session.get(User, user_id)
    user.telegram_user_id = tg_id
    await session.commit()


async def _insert_nudge(
    session,
    user_id: uuid.UUID,
    *,
    nudge_type: str = "missing_income",
    priority: str = "normal",
    status: str = "pending",
    dedup_key: str | None = None,
    payload: dict | None = None,
    sent_at: datetime | None = None,
) -> uuid.UUID:
    nid = uuid.uuid4()
    session.add(
        UserNudge(
            id=nid,
            user_id=user_id,
            nudge_type=nudge_type,
            priority=priority,
            status=status,
            dedup_key=dedup_key or f"{nudge_type}:{uuid.uuid4()}",
            payload=payload or {"txn_count_last_7d": 5, "window_days": 7, "lookback_days": 30},
            sent_at=sent_at,
        )
    )
    await session.commit()
    return nid


async def _refresh_nudge(session, nid: uuid.UUID) -> UserNudge:
    result = await session.execute(select(UserNudge).where(UserNudge.id == nid))
    n = result.scalar_one()
    await session.refresh(n)
    return n


# ─────────────────────────────────────────────────────────────────────────────
# happy path
# ─────────────────────────────────────────────────────────────────────────────


async def test_delivery_sends_pending_nudge(db_with_user):
    session, user_id = db_with_user
    await _pair_user(session, user_id)
    nid = await _insert_nudge(session, user_id)

    now = datetime(2026, 4, 22, 15, 0, tzinfo=timezone.utc)  # 09:00 CR — awake
    phrasing = FixturePhrasingClient(canned_text="¡Hola! ¿Querés agregar tu ingreso?")
    sender = _FakeSend()

    result = await deliver_all(
        session, user_id=user_id,
        phrasing_client=phrasing, send_fn=sender,
        model=_TEST_MODEL, now=now,
    )
    await session.commit()

    assert result.processed == 1
    assert result.sent == 1
    assert result.throttled_quiet_hours == 0
    assert result.throttled_silenced == 0
    assert result.throttled_rate_limit == 0
    assert result.failed == 0
    assert len(sender.calls) == 1
    assert sender.calls[0].chat_id == 12345
    assert sender.calls[0].text == "¡Hola! ¿Querés agregar tu ingreso?"
    assert [b.verb for b in sender.calls[0].buttons] == ["act", "later", "dismiss"]

    n = await _refresh_nudge(session, nid)
    assert n.status == "sent"
    assert n.sent_at is not None
    assert n.delivery_channel == "telegram"


# ─────────────────────────────────────────────────────────────────────────────
# quiet hours
# ─────────────────────────────────────────────────────────────────────────────


async def test_delivery_defers_in_quiet_hours(db_with_user):
    session, user_id = db_with_user
    await _pair_user(session, user_id)
    await _insert_nudge(session, user_id)

    # 04:00 UTC = 22:00 CR → quiet window
    now = datetime(2026, 4, 22, 4, 0, tzinfo=timezone.utc)
    phrasing = FixturePhrasingClient()
    sender = _FakeSend()

    result = await deliver_all(
        session, user_id=user_id,
        phrasing_client=phrasing, send_fn=sender,
        model=_TEST_MODEL, now=now,
    )
    await session.commit()

    assert result.sent == 0
    assert result.throttled_quiet_hours == 1
    assert sender.calls == []
    assert phrasing.calls == []  # LLM never called in quiet hours


# ─────────────────────────────────────────────────────────────────────────────
# silence
# ─────────────────────────────────────────────────────────────────────────────


async def test_delivery_respects_live_silence(db_with_user):
    session, user_id = db_with_user
    await _pair_user(session, user_id)
    await _insert_nudge(session, user_id, nudge_type="missing_income")
    now = datetime(2026, 4, 22, 15, 0, tzinfo=timezone.utc)
    # Silence the type AFTER the nudge was created — delivery re-checks.
    session.add(
        UserNudgeSilence(
            user_id=user_id,
            nudge_type="missing_income",
            silenced_until=now + timedelta(days=14),
            reason=REASON_AUTO_DISMISSED_2X,
        )
    )
    await session.commit()

    phrasing = FixturePhrasingClient()
    sender = _FakeSend()
    result = await deliver_all(
        session, user_id=user_id,
        phrasing_client=phrasing, send_fn=sender,
        model=_TEST_MODEL, now=now,
    )
    await session.commit()

    assert result.throttled_silenced == 1
    assert result.sent == 0
    assert sender.calls == []


# ─────────────────────────────────────────────────────────────────────────────
# rate limit
# ─────────────────────────────────────────────────────────────────────────────


async def test_delivery_rate_limit_blocks_normal_after_prior_send(db_with_user):
    session, user_id = db_with_user
    await _pair_user(session, user_id)
    now = datetime(2026, 4, 22, 15, 0, tzinfo=timezone.utc)
    # Prior sent normal nudge 2h ago → inside 48h window
    await _insert_nudge(
        session, user_id, status="sent", priority="normal",
        sent_at=now - timedelta(hours=2),
    )
    # Fresh pending normal nudge
    await _insert_nudge(session, user_id, priority="normal")

    phrasing = FixturePhrasingClient()
    sender = _FakeSend()
    result = await deliver_all(
        session, user_id=user_id,
        phrasing_client=phrasing, send_fn=sender,
        model=_TEST_MODEL, now=now,
    )
    await session.commit()

    assert result.throttled_rate_limit == 1
    assert result.sent == 0


async def test_delivery_rate_limit_blocks_second_normal_in_same_run(db_with_user):
    session, user_id = db_with_user
    await _pair_user(session, user_id)
    now = datetime(2026, 4, 22, 15, 0, tzinfo=timezone.utc)
    await _insert_nudge(session, user_id, priority="normal")
    await _insert_nudge(session, user_id, priority="normal")

    phrasing = FixturePhrasingClient()
    sender = _FakeSend()
    result = await deliver_all(
        session, user_id=user_id,
        phrasing_client=phrasing, send_fn=sender,
        model=_TEST_MODEL, now=now,
    )
    await session.commit()

    assert result.sent == 1
    assert result.throttled_rate_limit == 1


async def test_delivery_high_priority_bypasses_rate_limit(db_with_user):
    session, user_id = db_with_user
    await _pair_user(session, user_id)
    now = datetime(2026, 4, 22, 15, 0, tzinfo=timezone.utc)
    # Prior normal send in window → would block a normal nudge
    await _insert_nudge(
        session, user_id, status="sent", priority="normal",
        sent_at=now - timedelta(hours=1),
    )
    # Plus a pending normal (should throttle) and a pending high (should send)
    await _insert_nudge(session, user_id, priority="normal")
    await _insert_nudge(session, user_id, priority="high",
                        nudge_type="upcoming_bill",
                        payload={
                            "due_date": "2026-04-23",
                            "snapshot": {"bill_name": "ICE", "amount_expected": 35000, "currency": "CRC"},
                        })

    phrasing = FixturePhrasingClient()
    sender = _FakeSend()
    result = await deliver_all(
        session, user_id=user_id,
        phrasing_client=phrasing, send_fn=sender,
        model=_TEST_MODEL, now=now,
    )
    await session.commit()

    assert result.sent == 1
    assert result.throttled_rate_limit == 1
    # HIGH went first (ordering guarantees this); its payload drove the call
    assert sender.calls[0].chat_id == 12345
    assert any("ICE" in c["user"] for c in phrasing.calls)


# ─────────────────────────────────────────────────────────────────────────────
# failure modes
# ─────────────────────────────────────────────────────────────────────────────


async def test_delivery_llm_failure_keeps_nudge_pending(db_with_user):
    session, user_id = db_with_user
    await _pair_user(session, user_id)
    nid = await _insert_nudge(session, user_id)

    now = datetime(2026, 4, 22, 15, 0, tzinfo=timezone.utc)
    sender = _FakeSend()
    result = await deliver_all(
        session, user_id=user_id,
        phrasing_client=_RaisingPhrasingClient(), send_fn=sender,
        model=_TEST_MODEL, now=now,
    )
    await session.commit()

    assert result.failed == 1
    assert result.sent == 0
    assert sender.calls == []  # we never reached the send

    n = await _refresh_nudge(session, nid)
    assert n.status == "pending"  # next run retries


async def test_delivery_unpaired_user_counts_as_failed(db_with_user):
    session, user_id = db_with_user
    # Do NOT call _pair_user — user has no telegram_user_id.
    nid = await _insert_nudge(session, user_id)

    now = datetime(2026, 4, 22, 15, 0, tzinfo=timezone.utc)
    phrasing = FixturePhrasingClient()
    sender = _FakeSend()
    result = await deliver_all(
        session, user_id=user_id,
        phrasing_client=phrasing, send_fn=sender,
        model=_TEST_MODEL, now=now,
    )
    await session.commit()

    assert result.failed == 1
    assert result.sent == 0
    assert phrasing.calls == []
    n = await _refresh_nudge(session, nid)
    assert n.status == "pending"


async def test_delivery_send_error_counts_as_failed(db_with_user):
    session, user_id = db_with_user
    await _pair_user(session, user_id)
    nid = await _insert_nudge(session, user_id)

    now = datetime(2026, 4, 22, 15, 0, tzinfo=timezone.utc)
    phrasing = FixturePhrasingClient()
    sender = _FakeSend(ok_response=False)

    result = await deliver_all(
        session, user_id=user_id,
        phrasing_client=phrasing, send_fn=sender,
        model=_TEST_MODEL, now=now,
    )
    await session.commit()

    assert result.failed == 1
    assert result.sent == 0
    assert len(sender.calls) == 1
    n = await _refresh_nudge(session, nid)
    assert n.status == "pending"
