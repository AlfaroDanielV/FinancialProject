"""Tests for app.queries.history.

Real Redis (not fakeredis) to mirror the rest of the project: docker
compose already runs Redis 7 and the bot tests likewise hit a real
instance. Tests scope themselves to a unique uuid per case and clean up
on teardown.
"""
from __future__ import annotations

import json
import uuid

import pytest
import pytest_asyncio
from redis.asyncio import from_url as redis_from_url

from api.config import settings
from app.queries.history import (
    HISTORY_MAX_ENTRIES,
    HISTORY_TTL_S,
    ConversationTurn,
    append_turn,
    clear_history,
    history_key,
    load_history,
    to_anthropic_messages,
)


@pytest_asyncio.fixture
async def redis_client():
    client = redis_from_url(settings.redis_url, decode_responses=True)
    yield client
    await client.aclose()


@pytest_asyncio.fixture
async def user_id(redis_client):
    uid = uuid.uuid4()
    yield uid
    await redis_client.delete(history_key(uid))


# ── load_history ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_load_history_empty_for_unknown_user(redis_client, user_id):
    out = await load_history(user_id, redis=redis_client)
    assert out == []


# ── append_turn ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_append_turn_writes_user_then_assistant(redis_client, user_id):
    turns = await append_turn(
        user_id,
        user_msg="cuánto gasté esta semana",
        assistant_msg="Llevás ₡85.000.",
        redis=redis_client,
    )
    assert len(turns) == 2
    assert turns[0].role == "user"
    assert turns[0].content == "cuánto gasté esta semana"
    assert turns[1].role == "assistant"
    assert turns[1].content == "Llevás ₡85.000."


@pytest.mark.asyncio
async def test_append_turn_extends_existing_history(redis_client, user_id):
    await append_turn(
        user_id, user_msg="q1", assistant_msg="r1", redis=redis_client
    )
    turns = await append_turn(
        user_id, user_msg="q2", assistant_msg="r2", redis=redis_client
    )
    assert [t.content for t in turns] == ["q1", "r1", "q2", "r2"]


@pytest.mark.asyncio
async def test_append_turn_truncates_to_max_entries(redis_client, user_id):
    # 6 round-trips → 12 entries, but cap is 10. Oldest 2 must be dropped.
    for i in range(6):
        await append_turn(
            user_id,
            user_msg=f"q{i}",
            assistant_msg=f"r{i}",
            redis=redis_client,
        )
    turns = await load_history(user_id, redis=redis_client)
    assert len(turns) == HISTORY_MAX_ENTRIES == 10
    # First entry kept should be q1 (q0 + r0 dropped).
    assert turns[0].content == "q1"
    assert turns[-1].content == "r5"


@pytest.mark.asyncio
async def test_append_turn_renews_ttl_each_call(redis_client, user_id):
    await append_turn(
        user_id, user_msg="hola", assistant_msg="hola!", redis=redis_client
    )
    ttl1 = await redis_client.ttl(history_key(user_id))
    assert 0 < ttl1 <= HISTORY_TTL_S

    # Manually shorten to simulate aging.
    await redis_client.expire(history_key(user_id), 60)
    ttl_short = await redis_client.ttl(history_key(user_id))
    assert ttl_short <= 60

    await append_turn(
        user_id, user_msg="otra", assistant_msg="dale", redis=redis_client
    )
    ttl2 = await redis_client.ttl(history_key(user_id))
    assert ttl2 > ttl_short
    assert ttl2 > HISTORY_TTL_S - 10


# ── load_history serde + corruption tolerance ─────────────────────────────────


@pytest.mark.asyncio
async def test_load_history_round_trips_pydantic(redis_client, user_id):
    await append_turn(
        user_id, user_msg="hola", assistant_msg="qué tal", redis=redis_client
    )
    out = await load_history(user_id, redis=redis_client)
    assert all(isinstance(t, ConversationTurn) for t in out)
    # created_at is iso-format with timezone (Z or +00:00).
    assert "T" in out[0].created_at


@pytest.mark.asyncio
async def test_load_history_drops_corrupt_entries(redis_client, user_id):
    raw = json.dumps(
        [
            {
                "role": "user",
                "content": "ok",
                "created_at": "2026-04-28T12:00:00+00:00",
            },
            # Garbled — no role.
            {"content": "broken", "created_at": "2026-04-28T12:00:01+00:00"},
            {
                "role": "assistant",
                "content": "respuesta",
                "created_at": "2026-04-28T12:00:02+00:00",
            },
        ]
    )
    await redis_client.setex(history_key(user_id), HISTORY_TTL_S, raw)
    out = await load_history(user_id, redis=redis_client)
    assert [t.content for t in out] == ["ok", "respuesta"]


@pytest.mark.asyncio
async def test_load_history_returns_empty_on_invalid_json(
    redis_client, user_id
):
    await redis_client.setex(history_key(user_id), HISTORY_TTL_S, "not-json")
    out = await load_history(user_id, redis=redis_client)
    assert out == []


# ── clear_history ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_clear_history_removes_key(redis_client, user_id):
    await append_turn(
        user_id, user_msg="x", assistant_msg="y", redis=redis_client
    )
    assert await redis_client.exists(history_key(user_id))
    await clear_history(user_id, redis=redis_client)
    assert not await redis_client.exists(history_key(user_id))


# ── to_anthropic_messages ─────────────────────────────────────────────────────


def test_to_anthropic_messages_text_only_shape():
    now = "2026-04-28T12:00:00+00:00"
    turns = [
        ConversationTurn(role="user", content="hola", created_at=now),
        ConversationTurn(role="assistant", content="qué tal", created_at=now),
    ]
    out = to_anthropic_messages(turns)
    assert out == [
        {"role": "user", "content": "hola"},
        {"role": "assistant", "content": "qué tal"},
    ]


def test_to_anthropic_messages_drops_leading_assistant():
    now = "2026-04-28T12:00:00+00:00"
    turns = [
        ConversationTurn(role="assistant", content="orphan", created_at=now),
        ConversationTurn(role="user", content="real", created_at=now),
    ]
    out = to_anthropic_messages(turns)
    assert out == [{"role": "user", "content": "real"}]


def test_to_anthropic_messages_empty_in_empty_out():
    assert to_anthropic_messages([]) == []
