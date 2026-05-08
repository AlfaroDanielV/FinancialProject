"""Phase 6c B7 — /editar_memoria parser + handler tests.

Editor parser tests use FixtureLLMClient so they're offline. Handler
tests cover state lifecycle, retry budget, and the success path that
flips an insight to user_locked=true.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from api.models.user_insight import UserInsight, UserInsightAudit
from api.schemas.insights import (
    RiskPostureContent,
    StatedGoalContent,
)
from api.services.insights.editor_parser import (
    EditorParseError,
    parse_edit,
)
from api.services.insights.persister import persist_insights
from api.services.llm_extractor import FixtureLLMClient, RecordedLLMResponse
from bot import memory_handlers, messages_es


# ── editor parser ────────────────────────────────────────────────────────────


def _client(tool_input: dict, **token_kwargs):
    defaults = {
        "input_tokens": 80,
        "output_tokens": 30,
        "cache_read_input_tokens": 20,
        "cache_creation_input_tokens": 0,
    }
    defaults.update(token_kwargs)
    return FixtureLLMClient(
        default=RecordedLLMResponse(tool_input=tool_input, **defaults)
    )


@pytest.mark.asyncio
async def test_parse_edit_returns_validated_risk_posture():
    client = _client(
        {
            "type": "risk_posture",
            "content": {
                "posture": "conservative",
                "evidence_basis": "stated",
            },
        }
    )
    result = await parse_edit(
        target_type="risk_posture",
        user_text="soy bastante conservador con la plata",
        client=client,
        model="claude-haiku-4-5",
    )
    assert result.content.type == "risk_posture"
    assert result.content.posture == "conservative"
    assert result.run.input_tokens == 80


@pytest.mark.asyncio
async def test_parse_edit_rejects_type_mismatch():
    client = _client(
        {
            "type": "stated_goal",
            "content": {
                "goal_text": "Otra cosa",
                "target_amount": None,
                "target_date": None,
                "status": "mentioned",
            },
        }
    )
    with pytest.raises(EditorParseError):
        await parse_edit(
            target_type="risk_posture",
            user_text="soy conservador",
            client=client,
        )


@pytest.mark.asyncio
async def test_parse_edit_rejects_invalid_content_for_type():
    """Returned `type` is correct but `content` doesn't match the schema."""
    client = _client(
        {
            "type": "risk_posture",
            "content": {
                # Missing required `evidence_basis`; bogus posture value.
                "posture": "yolo",
            },
        }
    )
    with pytest.raises(EditorParseError):
        await parse_edit(
            target_type="risk_posture",
            user_text="???",
            client=client,
        )


@pytest.mark.asyncio
async def test_parse_edit_rejects_non_editable_target_type():
    """spending_pattern is computed-only, never editable. Caller should
    never pass it; parse_edit defends against it anyway."""
    client = _client({"type": "spending_pattern", "content": {}})
    with pytest.raises(EditorParseError):
        await parse_edit(
            target_type="spending_pattern",
            user_text="cualquier cosa",
            client=client,
        )


@pytest.mark.asyncio
async def test_parse_edit_rejects_empty_input():
    client = _client({"type": "risk_posture", "content": {}})
    with pytest.raises(EditorParseError):
        await parse_edit(
            target_type="risk_posture",
            user_text="   ",
            client=client,
        )


# ── handler-level: state + persistence ──────────────────────────────────────


class _SessionPassthrough:
    """Wraps a real AsyncSession so the handler can call methods on it
    without `await db.close()` actually closing the fixture's session.
    """

    def __init__(self, real) -> None:
        self._real = real

    def __getattr__(self, name):
        return getattr(self._real, name)

    async def close(self) -> None:
        return None


class _FakeFromUser:
    def __init__(self, tg_id: int) -> None:
        self.id = tg_id


class _FakeMessage:
    def __init__(self, from_user_id: int, text_: str = "") -> None:
        self.from_user = _FakeFromUser(from_user_id)
        self.text = text_
        self.answers: list[str] = []

    async def answer(self, text_: str, reply_markup=None) -> None:
        self.answers.append(text_)


@pytest.mark.asyncio
async def test_handler_reply_persists_user_override_and_writes_locked_audit(
    db_with_user,
):
    """End-to-end through the state-aware text handler.

    Seed an editable insight, stash an edit state in a fake redis,
    inject a FixtureLLMClient that returns a valid override, run the
    handler, assert: row updated, source=user_override, user_locked=true,
    locked audit row written, edit state cleared.
    """
    session, user_id = db_with_user

    # 1. Seed an editable risk_posture insight.
    seed = RiskPostureContent(posture="moderate", evidence_basis="observed")
    setattr(seed, "_llm_confidence", Decimal("0.80"))
    await persist_insights(
        session,
        user_id=user_id,
        insights=[seed],
        source="llm_extracted",
        now=datetime(2026, 5, 8, tzinfo=timezone.utc),
    )
    await session.commit()

    seeded_row = (
        await session.execute(
            select(UserInsight).where(UserInsight.user_id == user_id)
        )
    ).scalar_one()
    assert seeded_row.user_locked is False

    # 2. Build the fake state and redis.
    redis_store: dict[str, str] = {
        f"telegram:memory_edit:{user_id}": memory_handlers.MemoryEditState(
            insight_id=str(seeded_row.id),
            insight_type="risk_posture",
        ).to_json(),
    }

    class _FakeRedis:
        async def get(self, key):
            return redis_store.get(key)

        async def setex(self, key, ttl, value):
            redis_store[key] = value

        async def delete(self, key):
            redis_store.pop(key, None)

    fake_redis = _FakeRedis()

    # 3. Fake user lookup that returns a user with the same id.
    class _Stub:
        pass

    fake_user = _Stub()
    fake_user.id = user_id

    async def fake_user_lookup(*, telegram_user_id, db):
        return fake_user

    # 4. Fake AsyncSessionLocal context manager that yields the same
    #    `session` from the fixture so commits land in the same place
    #    as our setup data.
    session_factory = lambda: _SessionPassthrough(session)

    # 5. Inject a fixture LLM client that returns a valid override.
    fixture_client = _client(
        {
            "type": "risk_posture",
            "content": {
                "posture": "conservative",
                "evidence_basis": "stated",
            },
        }
    )

    msg = _FakeMessage(from_user_id=12345, text_="soy conservador")

    with (
        patch.object(memory_handlers, "AsyncSessionLocal", session_factory),
        patch.object(memory_handlers, "user_by_telegram_id", fake_user_lookup),
        patch.object(memory_handlers, "get_redis", lambda: fake_redis),
        patch.object(
            memory_handlers, "get_insight_llm_client", lambda: fixture_client
        ),
    ):
        await memory_handlers.on_editar_memoria_reply(msg)

    # State cleared.
    assert f"telegram:memory_edit:{user_id}" not in redis_store

    # Row updated to user_override + locked.
    refreshed = (
        await session.execute(
            select(UserInsight).where(UserInsight.id == seeded_row.id)
        )
    ).scalar_one()
    assert refreshed.source == "user_override"
    assert refreshed.user_locked is True
    assert refreshed.confidence == Decimal("1.00")
    assert refreshed.content["posture"] == "conservative"

    # Locked audit row was written.
    locked = (
        await session.execute(
            select(UserInsightAudit)
            .where(UserInsightAudit.user_id == user_id)
            .where(UserInsightAudit.action == "locked")
        )
    ).scalars().all()
    assert len(locked) == 1
    assert locked[0].payload["locked_via"] == "editar_memoria"

    # User-facing reply was sent.
    assert msg.answers
    assert "Listo" in msg.answers[0] or "lo voy a recordar" in msg.answers[0]


@pytest.mark.asyncio
async def test_handler_reply_increments_attempts_and_keeps_state_on_validation_fail(
    db_with_user,
):
    """A validation-failure reply should NOT clear the state on the
    first try; the user gets to retry."""
    session, user_id = db_with_user

    seed = RiskPostureContent(posture="moderate", evidence_basis="observed")
    setattr(seed, "_llm_confidence", Decimal("0.80"))
    await persist_insights(
        session,
        user_id=user_id,
        insights=[seed],
        source="llm_extracted",
        now=datetime(2026, 5, 8, tzinfo=timezone.utc),
    )
    await session.commit()
    seeded_row = (
        await session.execute(
            select(UserInsight).where(UserInsight.user_id == user_id)
        )
    ).scalar_one()

    redis_store: dict[str, str] = {
        f"telegram:memory_edit:{user_id}": memory_handlers.MemoryEditState(
            insight_id=str(seeded_row.id),
            insight_type="risk_posture",
        ).to_json(),
    }

    class _FakeRedis:
        async def get(self, key):
            return redis_store.get(key)

        async def setex(self, key, ttl, value):
            redis_store[key] = value

        async def delete(self, key):
            redis_store.pop(key, None)

    fake_redis = _FakeRedis()

    class _Stub:
        pass

    fake_user = _Stub()
    fake_user.id = user_id

    async def fake_user_lookup(*, telegram_user_id, db):
        return fake_user

    session_factory = lambda: _SessionPassthrough(session)

    # Tool returns wrong-type → parser raises ValidationError.
    bad_client = _client(
        {
            "type": "risk_posture",
            "content": {"posture": "yolo"},  # invalid Literal
        }
    )

    msg = _FakeMessage(from_user_id=12345, text_="soy lo que sea")

    with (
        patch.object(memory_handlers, "AsyncSessionLocal", session_factory),
        patch.object(memory_handlers, "user_by_telegram_id", fake_user_lookup),
        patch.object(memory_handlers, "get_redis", lambda: fake_redis),
        patch.object(
            memory_handlers, "get_insight_llm_client", lambda: bad_client
        ),
    ):
        await memory_handlers.on_editar_memoria_reply(msg)

    # State still present, attempts == 1.
    raw = redis_store.get(f"telegram:memory_edit:{user_id}")
    assert raw is not None
    state_after = memory_handlers.MemoryEditState.from_json(raw)
    assert state_after.attempts == 1

    # Insight not changed.
    refreshed = (
        await session.execute(
            select(UserInsight).where(UserInsight.id == seeded_row.id)
        )
    ).scalar_one()
    assert refreshed.user_locked is False

    # Reply was the retry-friendly message.
    assert msg.answers == [messages_es.EDITAR_MEMORIA_PARSE_FAIL]


@pytest.mark.asyncio
async def test_handler_reply_clears_state_after_max_attempts(db_with_user):
    """Three failed attempts → clear state, send give-up message."""
    session, user_id = db_with_user

    seed = RiskPostureContent(posture="moderate", evidence_basis="observed")
    setattr(seed, "_llm_confidence", Decimal("0.80"))
    await persist_insights(
        session,
        user_id=user_id,
        insights=[seed],
        source="llm_extracted",
        now=datetime(2026, 5, 8, tzinfo=timezone.utc),
    )
    await session.commit()
    seeded_row = (
        await session.execute(
            select(UserInsight).where(UserInsight.user_id == user_id)
        )
    ).scalar_one()

    # Pre-load attempts=2 so the next fail crosses the threshold.
    redis_store: dict[str, str] = {
        f"telegram:memory_edit:{user_id}": memory_handlers.MemoryEditState(
            insight_id=str(seeded_row.id),
            insight_type="risk_posture",
            attempts=2,
        ).to_json(),
    }

    class _FakeRedis:
        async def get(self, key):
            return redis_store.get(key)

        async def setex(self, key, ttl, value):
            redis_store[key] = value

        async def delete(self, key):
            redis_store.pop(key, None)

    class _Stub:
        pass

    fake_user = _Stub()
    fake_user.id = user_id

    async def fake_user_lookup(*, telegram_user_id, db):
        return fake_user

    session_factory = lambda: _SessionPassthrough(session)

    bad_client = _client(
        {
            "type": "risk_posture",
            "content": {"posture": "yolo"},
        }
    )

    msg = _FakeMessage(from_user_id=12345, text_="???")

    with (
        patch.object(memory_handlers, "AsyncSessionLocal", session_factory),
        patch.object(memory_handlers, "user_by_telegram_id", fake_user_lookup),
        patch.object(memory_handlers, "get_redis", lambda: _FakeRedis()),
        patch.object(
            memory_handlers, "get_insight_llm_client", lambda: bad_client
        ),
    ):
        await memory_handlers.on_editar_memoria_reply(msg)

    # State cleared.
    assert f"telegram:memory_edit:{user_id}" not in redis_store
    # Give-up reply.
    assert msg.answers == [messages_es.EDITAR_MEMORIA_PARSE_FAIL_GIVE_UP]


def test_clear_memory_edit_state_helper_is_idempotent():
    """The /cancel handler in bot/handlers.py calls this; it must not
    raise on a missing key."""
    import asyncio

    redis_store: dict[str, str] = {}

    class _FakeRedis:
        async def delete(self, key):
            redis_store.pop(key, None)

    async def go():
        await memory_handlers.clear_memory_edit_state(
            user_id=uuid.uuid4(), redis=_FakeRedis()
        )
        # Second call (no key present) still no-op.
        await memory_handlers.clear_memory_edit_state(
            user_id=uuid.uuid4(), redis=_FakeRedis()
        )

    asyncio.run(go())
