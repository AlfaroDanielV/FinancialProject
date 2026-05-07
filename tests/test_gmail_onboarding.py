"""Tests for the onboarding state machine (post-addenda).

Covers transitions, TTL renewal, pending_senders idempotency, and the
forward-compat tolerance of from_json (so old in-flight JSON survives
the deploy that lands the addenda).
"""
from __future__ import annotations

import time
import uuid

import pytest

from bot import gmail_onboarding


class StubRedis:
    def __init__(self) -> None:
        self.store: dict[str, tuple[str, float | None]] = {}

    async def set(self, key, value, ex=None):
        exp = (time.time() + ex) if ex else None
        self.store[key] = (value, exp)
        return True

    async def get(self, key):
        entry = self.store.get(key)
        if entry is None:
            return None
        value, exp = entry
        if exp is not None and exp < time.time():
            self.store.pop(key, None)
            return None
        return value

    async def delete(self, *keys):
        removed = 0
        for k in keys:
            if self.store.pop(k, None) is not None:
                removed += 1
        return removed


# ── basic begin / get / clear ────────────────────────────────────────────────


async def test_begin_writes_state_with_ttl():
    redis = StubRedis()
    user_id = uuid.uuid4()

    state = await gmail_onboarding.begin(
        user_id=user_id, telegram_chat_id=123456, redis=redis
    )
    assert state.state == "awaiting_oauth"
    assert state.telegram_chat_id == 123456
    assert state.pending_senders == []

    key = gmail_onboarding.gmail_onboarding_key(user_id)
    assert key in redis.store
    _, exp = redis.store[key]
    assert exp is not None
    assert time.time() + 1700 <= exp <= time.time() + 1820


async def test_get_returns_none_when_absent():
    redis = StubRedis()
    assert await gmail_onboarding.get(uuid.uuid4(), redis) is None


async def test_clear_drops_state():
    redis = StubRedis()
    user_id = uuid.uuid4()
    await gmail_onboarding.begin(
        user_id=user_id, telegram_chat_id=1, redis=redis
    )
    await gmail_onboarding.clear(user_id, redis)
    assert await gmail_onboarding.get(user_id, redis) is None


# ── transitions (post-addenda graph) ─────────────────────────────────────────


async def test_transition_oauth_to_selecting_banks():
    redis = StubRedis()
    user_id = uuid.uuid4()
    await gmail_onboarding.begin(
        user_id=user_id, telegram_chat_id=1, redis=redis
    )
    new = await gmail_onboarding.transition(
        user_id=user_id, to="selecting_banks", redis=redis
    )
    assert new.state == "selecting_banks"


async def test_transition_selecting_to_confirming():
    redis = StubRedis()
    user_id = uuid.uuid4()
    await gmail_onboarding.begin(
        user_id=user_id, telegram_chat_id=1, redis=redis
    )
    await gmail_onboarding.transition(
        user_id=user_id, to="selecting_banks", redis=redis
    )
    new = await gmail_onboarding.transition(
        user_id=user_id, to="confirming", redis=redis
    )
    assert new.state == "confirming"


async def test_transition_confirming_back_to_selecting_banks():
    """User taps 'Editar lista' — confirming → selecting_banks must be
    allowed so the user can keep adding/removing senders."""
    redis = StubRedis()
    user_id = uuid.uuid4()
    await gmail_onboarding.begin(
        user_id=user_id, telegram_chat_id=1, redis=redis
    )
    await gmail_onboarding.transition(
        user_id=user_id, to="selecting_banks", redis=redis
    )
    await gmail_onboarding.transition(
        user_id=user_id, to="confirming", redis=redis
    )
    back = await gmail_onboarding.transition(
        user_id=user_id, to="selecting_banks", redis=redis
    )
    assert back.state == "selecting_banks"


async def test_transition_rejects_old_awaiting_sample_target():
    """The pre-addenda awaiting_sample state is gone. Trying to jump
    there must raise — guards us against half-migrated callers."""
    redis = StubRedis()
    user_id = uuid.uuid4()
    await gmail_onboarding.begin(
        user_id=user_id, telegram_chat_id=1, redis=redis
    )
    with pytest.raises(gmail_onboarding.InvalidTransition):
        await gmail_onboarding.transition(
            user_id=user_id, to="awaiting_sample", redis=redis
        )


async def test_transition_rejects_jump_to_active():
    redis = StubRedis()
    user_id = uuid.uuid4()
    await gmail_onboarding.begin(
        user_id=user_id, telegram_chat_id=1, redis=redis
    )
    with pytest.raises(gmail_onboarding.InvalidTransition):
        await gmail_onboarding.transition(
            user_id=user_id, to="active", redis=redis
        )


async def test_transition_without_session_raises():
    redis = StubRedis()
    with pytest.raises(RuntimeError, match="no onboarding session"):
        await gmail_onboarding.transition(
            user_id=uuid.uuid4(), to="selecting_banks", redis=redis
        )


async def test_transition_renews_ttl():
    redis = StubRedis()
    user_id = uuid.uuid4()
    await gmail_onboarding.begin(
        user_id=user_id, telegram_chat_id=1, redis=redis
    )
    key = gmail_onboarding.gmail_onboarding_key(user_id)
    value, _ = redis.store[key]
    redis.store[key] = (value, time.time() + 5)

    await gmail_onboarding.transition(
        user_id=user_id, to="selecting_banks", redis=redis
    )
    _, new_exp = redis.store[key]
    assert new_exp is not None
    assert new_exp > time.time() + 1500


# ── pending_senders ──────────────────────────────────────────────────────────


async def _begin_and_select(redis, user_id):
    await gmail_onboarding.begin(
        user_id=user_id, telegram_chat_id=1, redis=redis
    )
    await gmail_onboarding.transition(
        user_id=user_id, to="selecting_banks", redis=redis
    )


async def test_add_pending_sender_appends():
    redis = StubRedis()
    user_id = uuid.uuid4()
    await _begin_and_select(redis, user_id)

    state, was_new = await gmail_onboarding.add_pending_sender(
        user_id=user_id,
        email="notificaciones@bac.cr",
        bank_name="BAC",
        source="preset_tap",
        redis=redis,
    )
    assert was_new is True
    assert state.pending_senders == [
        {
            "email": "notificaciones@bac.cr",
            "bank_name": "BAC",
            "source": "preset_tap",
        }
    ]


async def test_add_pending_sender_is_idempotent_on_email():
    redis = StubRedis()
    user_id = uuid.uuid4()
    await _begin_and_select(redis, user_id)

    await gmail_onboarding.add_pending_sender(
        user_id=user_id,
        email="x@bac.cr",
        bank_name="BAC",
        source="preset_tap",
        redis=redis,
    )
    state, was_new = await gmail_onboarding.add_pending_sender(
        user_id=user_id,
        email="X@BAC.cr",  # same email, different case
        bank_name=None,    # different metadata — should be ignored
        source="custom_typed",
        redis=redis,
    )
    assert was_new is False
    assert len(state.pending_senders) == 1
    # First-write metadata wins (we don't overwrite).
    assert state.pending_senders[0]["bank_name"] == "BAC"
    assert state.pending_senders[0]["source"] == "preset_tap"


async def test_add_pending_sender_rejects_outside_selecting_banks():
    redis = StubRedis()
    user_id = uuid.uuid4()
    await gmail_onboarding.begin(
        user_id=user_id, telegram_chat_id=1, redis=redis
    )
    with pytest.raises(gmail_onboarding.InvalidTransition):
        await gmail_onboarding.add_pending_sender(
            user_id=user_id,
            email="x@y",
            bank_name=None,
            source="custom_typed",
            redis=redis,
        )


async def test_remove_pending_sender():
    redis = StubRedis()
    user_id = uuid.uuid4()
    await _begin_and_select(redis, user_id)
    await gmail_onboarding.add_pending_sender(
        user_id=user_id,
        email="a@bac.cr",
        bank_name="BAC",
        source="preset_tap",
        redis=redis,
    )
    await gmail_onboarding.add_pending_sender(
        user_id=user_id,
        email="b@promerica.fi.cr",
        bank_name="Promerica",
        source="preset_tap",
        redis=redis,
    )

    state = await gmail_onboarding.remove_pending_sender(
        user_id=user_id, email="A@BAC.cr", redis=redis
    )
    assert [e["email"] for e in state.pending_senders] == ["b@promerica.fi.cr"]


async def test_remove_pending_sender_noop_when_absent():
    redis = StubRedis()
    user_id = uuid.uuid4()
    await _begin_and_select(redis, user_id)

    state = await gmail_onboarding.remove_pending_sender(
        user_id=user_id, email="never-added@x.com", redis=redis
    )
    assert state.pending_senders == []


# ── awaiting_bank (preset → ask email flow) ─────────────────────────────────


async def test_set_awaiting_bank_marks_pending():
    redis = StubRedis()
    user_id = uuid.uuid4()
    await _begin_and_select(redis, user_id)

    state = await gmail_onboarding.set_awaiting_bank(
        user_id=user_id, bank_name="BAC", redis=redis
    )
    assert state.awaiting_bank == "BAC"


async def test_set_awaiting_bank_overwrites_previous():
    """User taps BAC, changes mind, taps Promerica before typing.
    Both work — last tap wins."""
    redis = StubRedis()
    user_id = uuid.uuid4()
    await _begin_and_select(redis, user_id)

    await gmail_onboarding.set_awaiting_bank(
        user_id=user_id, bank_name="BAC", redis=redis
    )
    state = await gmail_onboarding.set_awaiting_bank(
        user_id=user_id, bank_name="Promerica", redis=redis
    )
    assert state.awaiting_bank == "Promerica"


async def test_set_awaiting_bank_clear_with_none():
    redis = StubRedis()
    user_id = uuid.uuid4()
    await _begin_and_select(redis, user_id)
    await gmail_onboarding.set_awaiting_bank(
        user_id=user_id, bank_name="BAC", redis=redis
    )
    state = await gmail_onboarding.set_awaiting_bank(
        user_id=user_id, bank_name=None, redis=redis
    )
    assert state.awaiting_bank is None


async def test_set_awaiting_bank_rejects_outside_selecting_banks():
    redis = StubRedis()
    user_id = uuid.uuid4()
    await gmail_onboarding.begin(
        user_id=user_id, telegram_chat_id=1, redis=redis
    )
    with pytest.raises(gmail_onboarding.InvalidTransition):
        await gmail_onboarding.set_awaiting_bank(
            user_id=user_id, bank_name="BAC", redis=redis
        )


# ── selection_message_id ─────────────────────────────────────────────────────


async def test_set_selection_message_id():
    redis = StubRedis()
    user_id = uuid.uuid4()
    await _begin_and_select(redis, user_id)
    await gmail_onboarding.set_selection_message_id(
        user_id=user_id, message_id=42, redis=redis
    )
    state = await gmail_onboarding.get(user_id, redis)
    assert state is not None
    assert state.selection_message_id == 42


# ── round-trip + forward-compat ──────────────────────────────────────────────


async def test_state_round_trips_through_redis():
    redis = StubRedis()
    user_id = uuid.uuid4()
    await _begin_and_select(redis, user_id)
    await gmail_onboarding.add_pending_sender(
        user_id=user_id,
        email="a@bac.cr",
        bank_name="BAC",
        source="preset_tap",
        redis=redis,
    )
    fetched = await gmail_onboarding.get(user_id, redis)
    assert fetched is not None
    assert fetched.state == "selecting_banks"
    assert fetched.pending_senders == [
        {"email": "a@bac.cr", "bank_name": "BAC", "source": "preset_tap"}
    ]


async def test_from_json_tolerates_unknown_fields():
    """When we deploy the addenda, in-flight Redis state from the
    pre-addenda code may have keys we no longer use. from_json must
    drop them silently instead of raising TypeError."""
    raw = (
        '{"state": "selecting_banks", "telegram_chat_id": 1, '
        '"started_at": "x", "pending_senders": [], '
        '"unknown_legacy_field": "boop"}'
    )
    state = gmail_onboarding.OnboardingState.from_json(raw)
    assert state.state == "selecting_banks"
