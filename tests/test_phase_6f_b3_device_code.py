"""Phase 6f B3 — device-code login tests.

The Telegram `/login` command issues a 6-char alphanumeric code, stored
in Redis with TTL 5 min. The native app posts the code to
`POST /api/v1/auth/device-code/exchange` and receives a session JWT.

Tests cover:
1. Happy path — code → exchange → JWT authenticates `/chat/message`.
2. Single-use — second exchange of the same code → 401.
3. Unknown code → 401 (with generic Spanish message; no probing).
4. Expired code (TTL elapsed) → 401.
5. Suspended user → 401 (status-leak guard).
6. Malformed body (empty / too long) → 422.
7. Case-insensitive input — lower-case code normalizes and works.

The tests bypass the Telegram bot entirely; they call
`request_device_code()` directly to mint codes, then exchange via the
HTTP endpoint. Bot-handler coverage is exercised by the regression
slice (`test_phase_6d_b10_welcome.py` and friends) that loads
`bot.handlers` at import time.
"""
from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update

from api.database import get_db
from api.main import app
from api.models.user import User
from api.redis_client import get_redis
from api.services.auth.device_code import request_device_code
from bot.redis_keys import device_code_key


def _override_db(session):
    async def _yield():
        yield session

    app.dependency_overrides[get_db] = _yield


def _clear_db_override():
    app.dependency_overrides.pop(get_db, None)


async def _exchange(client: AsyncClient, code: str):
    return await client.post(
        "/api/v1/auth/device-code/exchange",
        json={"code": code},
    )


# ── 1. Happy path: code → JWT → chat works ───────────────────────────────────


@pytest.mark.asyncio
async def test_device_code_exchange_returns_jwt_and_authenticates_chat(db_with_user):
    session, user_id = db_with_user
    # Re-fetch the User from the session so we can pass a real instance
    # into request_device_code (it just reads `.id`).
    user = await session.get(User, user_id)
    assert user is not None

    redis = get_redis()
    code = await request_device_code(user=user, redis=redis)
    assert len(code) == 6
    assert code.isupper()

    _override_db(session)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await _exchange(ac, code)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["user_id"] == str(user_id)
            assert body["email"] == user.email
            assert "token" in body and len(body["token"]) > 20
            assert body["expires_at"] > 0
            assert "set-cookie" not in {k.lower() for k in resp.headers.keys()}

            # JWT must authenticate /chat/message via the bearer branch.
            chat = await ac.post(
                "/api/v1/chat/message",
                json={"text": "/help"},
                headers={"Authorization": f"Bearer {body['token']}"},
            )
            assert chat.status_code == 200, chat.text
            assert chat.json()["reply_text"]
    finally:
        _clear_db_override()


# ── 2. Single-use: second exchange of same code → 401 ───────────────────────


@pytest.mark.asyncio
async def test_device_code_is_single_use(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    redis = get_redis()
    code = await request_device_code(user=user, redis=redis)

    _override_db(session)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            first = await _exchange(ac, code)
            assert first.status_code == 200

            second = await _exchange(ac, code)
            assert second.status_code == 401
    finally:
        _clear_db_override()


# ── 3. Unknown code → 401 ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unknown_device_code_rejected(db_with_user):
    session, _user_id = db_with_user
    _override_db(session)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await _exchange(ac, "ZZZZZZ")
        assert resp.status_code == 401
    finally:
        _clear_db_override()


# ── 4. Expired code → 401 ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_expired_device_code_rejected(db_with_user):
    """Forcibly delete the Redis key to simulate TTL expiry — equivalent
    behaviour from the endpoint's perspective."""
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    redis = get_redis()
    code = await request_device_code(user=user, redis=redis)

    # Manually expire the code.
    await redis.delete(device_code_key(code))

    _override_db(session)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await _exchange(ac, code)
        assert resp.status_code == 401
    finally:
        _clear_db_override()


# ── 5. Suspended user → 401 ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_suspended_user_device_code_rejected(db_with_user):
    """Suspension MUST 401 (not 200, not 403). The device-code path is
    pre-auth: we don't even hit `current_user` yet, so the suspension
    check has to live in the endpoint itself. A 403 here would leak
    that the code was otherwise valid; we return the same generic 401
    the bad-code path uses."""
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    redis = get_redis()
    code = await request_device_code(user=user, redis=redis)

    await session.execute(
        update(User).where(User.id == user_id).values(status="suspended")
    )
    await session.commit()

    _override_db(session)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await _exchange(ac, code)
        assert resp.status_code == 401
    finally:
        _clear_db_override()


# ── 6. Malformed body → 422 ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_device_code_malformed_body_rejected(db_with_user):
    session, _user_id = db_with_user
    _override_db(session)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            empty = await _exchange(ac, "")
            assert empty.status_code == 422

            too_long = await ac.post(
                "/api/v1/auth/device-code/exchange",
                json={"code": "X" * 33},
            )
            assert too_long.status_code == 422

            missing = await ac.post(
                "/api/v1/auth/device-code/exchange",
                json={},
            )
            assert missing.status_code == 422
    finally:
        _clear_db_override()


# ── 7. Lower-case input normalizes ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_device_code_normalizes_case_and_whitespace(db_with_user):
    """Operators paste codes from Telegram; clipboard may carry trailing
    newlines or the keyboard may emit lower-case if autoshift fails.
    normalize_code() handles both."""
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    redis = get_redis()
    code = await request_device_code(user=user, redis=redis)

    _override_db(session)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            messy = f"  {code.lower()}\n"
            resp = await _exchange(ac, messy)
        assert resp.status_code == 200, resp.text
    finally:
        _clear_db_override()


# ── 8. Parallel exchanges of one code: exactly one wins ──────────────────────


@pytest.mark.asyncio
async def test_device_code_concurrent_exchanges_one_wins(db_with_user):
    """Two parallel exchanges of the same code: exactly one returns 200,
    the other returns 401. Guards against a tab-double-click race that
    could otherwise mint two JWTs for the same code."""
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    redis = get_redis()
    code = await request_device_code(user=user, redis=redis)

    _override_db(session)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            a, b = await asyncio.gather(_exchange(ac, code), _exchange(ac, code))
        statuses = sorted([a.status_code, b.status_code])
        assert statuses == [200, 401], (a.status_code, b.status_code)
    finally:
        _clear_db_override()
