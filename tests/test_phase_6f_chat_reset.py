"""Native chat — POST /chat/reset clears durable conversational state.

"Nueva conversación" in the app calls this so a new conversation starts clean:
a stuck flow (e.g. a stale account-creation prompt, a pending write, or
clarification state) must not leak across. Mirrors the bot's /cancel + /clear.
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from api.database import get_db
from api.dependencies import current_user
from api.main import app
from bot.account_creation import (
    AccountCreationState,
    load_account_creation,
    save_account_creation,
)
from bot.clarification import ClarificationState, load_clarification, save_clarification


def _override(session, user_id):
    class _StubUser:
        def __init__(self) -> None:
            self.id = user_id
            self.status = "active"
            self.currency = "CRC"
            self.timezone = "America/Costa_Rica"

    async def _yield_session():
        yield session

    app.dependency_overrides[current_user] = lambda: _StubUser()
    app.dependency_overrides[get_db] = _yield_session


def _clear():
    app.dependency_overrides.pop(current_user, None)
    app.dependency_overrides.pop(get_db, None)


@pytest.mark.asyncio
async def test_reset_clears_account_and_clarification_state(db_with_user):
    from api.redis_client import get_redis

    session, user_id = db_with_user
    _override(session, user_id)
    redis = get_redis()
    try:
        # Plant a stuck account-creation flow + a clarification.
        await save_account_creation(
            user_id=user_id,
            state=AccountCreationState(step="awaiting_start", name="BAC"),
            redis=redis,
        )
        await save_clarification(
            user_id=user_id,
            state=ClarificationState(
                partial={}, awaiting_field="account", question_es="¿Cuál cuenta?"
            ),
            redis=redis,
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post("/api/v1/chat/reset")
            assert resp.status_code == 200, resp.text
            assert resp.json() == {"reset": True}

        assert await load_account_creation(user_id=user_id, redis=redis) is None
        assert await load_clarification(user_id=user_id, redis=redis) is None
    finally:
        _clear()
