"""Phase 6f B2 — bearer-token auth + POST /api/v1/chat/message tests.

Covers the contract the native app depends on:

1. Exchange returns `token` + `expires_at` in the body alongside the cookie.
2. `Authorization: Bearer <token>` authenticates `/chat/message`.
3. The cookie path still works (SPA backwards-compat during cutover).
4. Invalid / malformed / missing auth fails cleanly with 401/422.
5. The chat surface reuses `bot/pipeline.py::process_message()` — slash
   commands like `/help` and `/cancel` route through the same
   short-circuit path the Telegram handler does and return identical text.
6. Pipeline state mutations (e.g. `/cancel` clearing Redis keys) are
   visible from the chat endpoint.

We intentionally don't exercise the LLM extractor here: the goal is
parity with the bot pipeline, not extractor coverage. Slash commands
short-circuit before the LLM is called, which is the cleanest deterministic
seam to verify the chat surface plumbs into the same code path.
"""
from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from api.database import get_db
from api.main import app
from api.models.user import User
from api.services.auth.magic_link import generate_link
from api.services.auth.session import decode_session_jwt
from bot import messages_es
from bot.pending import load_pending
from bot.redis_keys import pending_key


# ── Helpers ──────────────────────────────────────────────────────────────────


def _override_db(session):
    """Override the get_db dependency so the endpoint uses the test session."""

    async def _yield():
        yield session

    app.dependency_overrides[get_db] = _yield


def _clear_db_override():
    app.dependency_overrides.pop(get_db, None)


async def _exchange(client: AsyncClient, raw_token: str):
    return await client.post(
        "/api/v1/auth/magic-link/exchange",
        json={"token": raw_token},
    )


async def _chat(client: AsyncClient, text: str, *, headers=None, cookies=None):
    return await client.post(
        "/api/v1/chat/message",
        json={"text": text},
        headers=headers,
        cookies=cookies,
    )


# ── 1. Exchange returns bearer token + cookie ────────────────────────────────


@pytest.mark.asyncio
async def test_exchange_returns_bearer_token_and_cookie(db_with_user):
    session, user_id = db_with_user
    link = await generate_link(session, user_id=user_id, purpose="onboarding")

    _override_db(session)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await _exchange(ac, link.raw_token)

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["user_id"] == str(user_id)
        assert "token" in body and len(body["token"]) > 20
        assert "expires_at" in body
        assert body["expires_at"] > 0

        # The body token decodes to the same user.
        claims = decode_session_jwt(body["token"])
        assert claims is not None
        assert claims["sub"] == str(user_id)
        assert claims["exp"] == body["expires_at"]

        # Phase 6f B16: no cookie is set — the JWT body is the only credential.
        assert "set-cookie" not in {k.lower() for k in resp.headers.keys()}
    finally:
        _clear_db_override()


# ── 2. Bearer header authenticates /chat/message ─────────────────────────────


@pytest.mark.asyncio
async def test_bearer_token_authenticates_chat_message(db_with_user):
    session, user_id = db_with_user
    link = await generate_link(session, user_id=user_id, purpose="onboarding")

    _override_db(session)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            exchange = await _exchange(ac, link.raw_token)
            assert exchange.status_code == 200
            token = exchange.json()["token"]

            chat = await _chat(
                ac,
                "/help",
                headers={"Authorization": f"Bearer {token}"},
                # Drop any cookies that the previous response set, so we're
                # genuinely testing bearer auth in isolation.
                cookies={},
            )

        assert chat.status_code == 200, chat.text
        body = chat.json()
        assert isinstance(body["reply_text"], str) and body["reply_text"]
        assert isinstance(body["buttons"], list)
        assert isinstance(body["url_buttons"], list)
    finally:
        _clear_db_override()


# ── 3. No auth → 401, malformed body → 422 ───────────────────────────────────


@pytest.mark.asyncio
async def test_chat_message_requires_auth(db_with_user):
    session, _user_id = db_with_user
    _override_db(session)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await _chat(ac, "/help")
        assert resp.status_code == 401
    finally:
        _clear_db_override()


@pytest.mark.asyncio
async def test_chat_message_rejects_malformed_body(db_with_user):
    session, user_id = db_with_user
    link = await generate_link(session, user_id=user_id, purpose="onboarding")

    _override_db(session)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            exchange = await _exchange(ac, link.raw_token)
            token = exchange.json()["token"]

            # Empty text → 422 (min_length=1).
            empty = await ac.post(
                "/api/v1/chat/message",
                json={"text": ""},
                headers={"Authorization": f"Bearer {token}"},
                cookies={},
            )
            assert empty.status_code == 422

            # Missing field → 422.
            missing = await ac.post(
                "/api/v1/chat/message",
                json={},
                headers={"Authorization": f"Bearer {token}"},
                cookies={},
            )
            assert missing.status_code == 422
    finally:
        _clear_db_override()


# ── 5. Invalid bearer → 401 ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_invalid_bearer_token_rejected(db_with_user):
    session, _user_id = db_with_user
    _override_db(session)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await _chat(
                ac,
                "/help",
                headers={"Authorization": "Bearer not-a-real-jwt"},
            )
        assert resp.status_code == 401
    finally:
        _clear_db_override()


# ── 6. Bearer pointing at a vanished user → 401 ──────────────────────────────


@pytest.mark.asyncio
async def test_bearer_token_for_unknown_user_rejected(db_with_user):
    """A well-formed JWT signed with the right key but for a user that
    no longer exists must 401 — not 200, not 500. Prevents stale tokens
    from authenticating after account deletion."""
    from api.services.auth.session import issue_session_jwt

    session, _user_id = db_with_user
    bogus_id = uuid.uuid4()
    bogus_jti = uuid.uuid4()
    token = issue_session_jwt(bogus_id, bogus_jti)

    _override_db(session)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await _chat(
                ac, "/help", headers={"Authorization": f"Bearer {token}"}
            )
        assert resp.status_code == 401
    finally:
        _clear_db_override()


# ── 7. Suspended user via bearer → 403 ───────────────────────────────────────


@pytest.mark.asyncio
async def test_suspended_user_bearer_returns_403(db_with_user):
    session, user_id = db_with_user
    link = await generate_link(session, user_id=user_id, purpose="onboarding")

    _override_db(session)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            exchange = await _exchange(ac, link.raw_token)
            token = exchange.json()["token"]

            # Suspend the user out-of-band.
            from sqlalchemy import update

            await session.execute(
                update(User).where(User.id == user_id).values(status="suspended")
            )
            await session.commit()

            resp = await _chat(
                ac,
                "/help",
                headers={"Authorization": f"Bearer {token}"},
                cookies={},
            )
        assert resp.status_code == 403
    finally:
        _clear_db_override()


# ── 8. Chat write-proposal returns confirm buttons (dispatcher parity) ──────


@pytest.mark.asyncio
async def test_chat_write_proposal_returns_confirm_buttons(db_with_user):
    """End-to-end: text → fixture LLM → write dispatcher → ProposeAction
    → BotReply with Sí/No/Editar buttons. Proves the chat endpoint is
    plumbed into the same dispatcher that the Telegram handler runs."""
    from api.services.llm_extractor import FixtureLLMClient, RecordedLLMResponse
    from bot.app import set_llm_client

    from tests.fixtures.extractor_responses import BASIC_EXPENSE_CRC

    session, user_id = db_with_user

    # Plant an account so the dispatcher has somewhere to assign the
    # expense; without one, the proposal would lazy-trigger account
    # creation instead.
    from api.models.account import Account

    acct = Account(
        user_id=user_id,
        name="BAC",
        account_type="checking",
        currency="CRC",
        initial_balance=0,
        is_active=True,
    )
    session.add(acct)
    await session.commit()

    fixture = FixtureLLMClient(default=BASIC_EXPENSE_CRC)
    set_llm_client(fixture)

    link = await generate_link(session, user_id=user_id, purpose="onboarding")
    _override_db(session)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            exchange = await _exchange(ac, link.raw_token)
            token = exchange.json()["token"]

            chat = await _chat(
                ac,
                "gasté 5000 colones en el super",
                headers={"Authorization": f"Bearer {token}"},
                cookies={},
            )

        assert chat.status_code == 200, chat.text
        body = chat.json()
        # ProposeAction emits 3 buttons: Sí / No / Editar
        assert len(body["buttons"]) == 3
        labels = {b["label"] for b in body["buttons"]}
        assert labels == {
            messages_es.CONFIRM_BUTTONS_YES,
            messages_es.CONFIRM_BUTTONS_NO,
            messages_es.CONFIRM_BUTTONS_EDIT,
        }
        # And every callback_data starts with the canonical pending: prefix.
        for b in body["buttons"]:
            assert b["callback_data"].startswith("pending:")
    finally:
        _clear_db_override()


# ── 9. Chat /cancel clears Redis pending state (pipeline parity) ─────────────


@pytest.mark.asyncio
async def test_chat_cancel_clears_pending_pipeline_state(db_with_user):
    """Hitting /chat/message with `/cancel` must run the same Redis
    state-cleaning path that the Telegram bot's `/cancel` does. This
    proves the chat endpoint is wired to `process_message()` and the
    durable state machine, not a parallel handler."""
    from api.redis_client import get_redis
    from bot.pending import PendingAction, save_pending

    session, user_id = db_with_user

    # Plant a pending action directly in Redis as if the user had just
    # produced a transaction proposal. confirmation_id is None so the
    # /cancel handler skips the DB resolve step (pre-5d-style row).
    redis = get_redis()
    pending = PendingAction(
        short_id="abc12345",
        action_type="log_expense",
        payload={"merchant": "Auto Mercado", "amount": -5000.0},
        summary_es="Gasto de ₡5.000 en Auto Mercado",
    )
    await save_pending(user_id=user_id, pending=pending, redis=redis)

    # Confirm the key exists before we call /cancel.
    raw_before = await redis.get(pending_key(user_id))
    assert raw_before is not None

    link = await generate_link(session, user_id=user_id, purpose="onboarding")

    _override_db(session)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            exchange = await _exchange(ac, link.raw_token)
            token = exchange.json()["token"]

            resp = await _chat(
                ac,
                "/cancel",
                headers={"Authorization": f"Bearer {token}"},
                cookies={},
            )
        assert resp.status_code == 200, resp.text
        assert resp.json()["reply_text"] == messages_es.CANCELLED

        # The pipeline must have cleared the pending Redis key.
        loaded = await load_pending(user_id=user_id, redis=redis)
        assert loaded is None
    finally:
        _clear_db_override()
