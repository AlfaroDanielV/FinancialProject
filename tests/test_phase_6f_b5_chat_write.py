"""Phase 6f B5 — write transaction capture parity through the chat API.

Verifies the full round-trip that the native app's Chat screen relies on:

1. Sending a write-intent message returns a proposal + confirm/cancel chips.
2. Posting the button label (including emoji, e.g. 'Sí ✅') is recognized
   as a confirmation — the emoji-stripping fix in _text_is_confirmation.
3. After confirmation the transaction is committed to the DB.
4. The cancel label ('No ❌') is recognised as a rejection and the
   pending proposal is cleared without committing anything.

Uses the FixtureLLMClient so the LLM is never called; the confirm/cancel
step goes through the deterministic pending-action path, same as Telegram.
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from api.database import get_db
from api.main import app
from api.models.account import Account
from api.models.transaction import Transaction
from api.services.auth.magic_link import generate_link
from bot import messages_es
from bot.pending import load_pending
from bot.redis_keys import pending_key


# ── helpers ───────────────────────────────────────────────────────────────────


def _override_db(session):
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


async def _chat(client: AsyncClient, text: str, bearer: str):
    return await client.post(
        "/api/v1/chat/message",
        json={"text": text},
        headers={"Authorization": f"Bearer {bearer}"},
        cookies={},
    )


async def _setup(session, user_id):
    """Plant an account and return a bearer token for the test user."""
    from api.services.llm_extractor import FixtureLLMClient
    from bot.app import set_llm_client
    from tests.fixtures.extractor_responses import BASIC_EXPENSE_CRC

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

    set_llm_client(FixtureLLMClient(default=BASIC_EXPENSE_CRC))

    link = await generate_link(session, user_id=user_id, purpose="onboarding")
    _override_db(session)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        exchange = await _exchange(ac, link.raw_token)
    assert exchange.status_code == 200
    return exchange.json()["token"]


# ── 1. Confirm button label (with emoji) commits the transaction ──────────────


@pytest.mark.asyncio
async def test_confirm_label_with_emoji_commits_transaction(db_with_user):
    """'Sí ✅' (the exact button label) must route through _text_is_confirmation
    and commit the pending proposal. Before the B5 emoji-strip fix, the ✅
    prevented a match and the pipeline fell through to the LLM."""
    session, user_id = db_with_user

    token = await _setup(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Step 1: send a write message → get proposal + buttons
            propose = await _chat(ac, "gasté 5000 colones en el super", token)
            assert propose.status_code == 200, propose.text
            body = propose.json()
            assert len(body["buttons"]) == 3
            yes_label = messages_es.CONFIRM_BUTTONS_YES  # "Sí ✅"

            # Step 2: post the exact button label (emoji included)
            confirm = await _chat(ac, yes_label, token)
            assert confirm.status_code == 200, confirm.text
            reply = confirm.json()["reply_text"]

            # The confirmation reply must NOT be the "nothing pending" error.
            assert messages_es.PENDING_NONE_TO_CONFIRM not in reply
            assert messages_es.PENDING_EXPIRED not in reply

        # Step 3: a transaction row must exist in the DB.
        rows = (await session.execute(select(Transaction).where(
            Transaction.user_id == user_id
        ))).scalars().all()
        assert len(rows) == 1
        tx = rows[0]
        assert tx.amount == -5000
        assert tx.status == "confirmed"
    finally:
        _clear_db_override()


# ── 2. Cancel label (with emoji) rejects without committing ──────────────────


@pytest.mark.asyncio
async def test_cancel_label_with_emoji_rejects_proposal(db_with_user):
    """'No ❌' (button label) must reject the pending proposal. The pending
    Redis key is cleared and no transaction is written."""
    session, user_id = db_with_user

    token = await _setup(session, user_id)
    try:
        from api.redis_client import get_redis

        redis = get_redis()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Propose
            propose = await _chat(ac, "gasté 5000 colones en el super", token)
            assert propose.status_code == 200
            assert len(propose.json()["buttons"]) == 3

            # Confirm pending key exists
            raw = await redis.get(pending_key(user_id))
            assert raw is not None

            # Cancel with the emoji label
            no_label = messages_es.CONFIRM_BUTTONS_NO  # e.g. "No ❌"
            cancel = await _chat(ac, no_label, token)
            assert cancel.status_code == 200

        # Pending state cleared
        loaded = await load_pending(user_id=user_id, redis=redis)
        assert loaded is None

        # No transaction committed
        rows = (await session.execute(select(Transaction).where(
            Transaction.user_id == user_id
        ))).scalars().all()
        assert len(rows) == 0
    finally:
        _clear_db_override()


# ── 3. Plain 'sí' (lowercase, no emoji) still works ─────────────────────────


@pytest.mark.asyncio
async def test_plain_si_still_confirms(db_with_user):
    """The emoji-strip change must not break the existing plain-text path."""
    session, user_id = db_with_user

    token = await _setup(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            propose = await _chat(ac, "gasté 5000 colones en el super", token)
            assert propose.status_code == 200
            assert len(propose.json()["buttons"]) == 3

            confirm = await _chat(ac, "sí", token)
            assert confirm.status_code == 200
            assert messages_es.PENDING_NONE_TO_CONFIRM not in confirm.json()["reply_text"]

        rows = (await session.execute(select(Transaction).where(
            Transaction.user_id == user_id
        ))).scalars().all()
        assert len(rows) == 1
    finally:
        _clear_db_override()
