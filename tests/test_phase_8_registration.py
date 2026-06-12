"""Phase 8 B1 — Telegram cold-start registration.

Covers the deterministic Redis mini-flow (bot/registration.py):
- begin → email → nombre → confirm "sí" creates the user through the shared
  api/services/users.py path with telegram_user_id bound in the same insert,
  seeds the default notification rule + categories, and records the
  `core_service` consent grant (Phase 7e ledger, source 'telegram').
- Validation: bad email re-prompts; taken email points at the pairing path;
  short name re-prompts; "no" at confirm cancels without writing.
- begin is re-entrant (an in-flight flow re-prompts its step, never resets).
"""
from __future__ import annotations

import secrets
import uuid

import pytest
from sqlalchemy import select, text

from api.models.consent import UserConsent
from api.models.notification_rule import NotificationRule
from api.models.user import User
from api.models.user_category import UserCategory
from api.redis_client import get_redis
from bot import messages_es
from bot.registration import (
    TERMS_VERSION,
    begin_registration,
    clear_registration,
    handle_registration_text,
    load_registration,
)


def _tg_id() -> int:
    return int.from_bytes(secrets.token_bytes(5), "big")


def _email() -> str:
    return f"reg-{uuid.uuid4().hex[:10]}@example.com"


async def _purge_user(session, email: str) -> None:
    uid = (
        await session.execute(select(User.id).where(User.email == email))
    ).scalar_one_or_none()
    if uid is None:
        return
    for tbl in ("user_consents", "notification_rules", "user_categories"):
        await session.execute(
            text(f"DELETE FROM {tbl} WHERE user_id = :u"), {"u": str(uid)}
        )
    await session.execute(
        text("DELETE FROM users WHERE id = :u"), {"u": str(uid)}
    )
    await session.commit()


@pytest.mark.asyncio
async def test_full_flow_creates_user_with_consent(db_with_user):
    session, _existing_uid = db_with_user
    redis = get_redis()
    tg = _tg_id()
    email = _email()
    try:
        start = await begin_registration(telegram_user_id=tg, redis=redis)
        assert start == messages_es.REGISTER_START

        r1 = await handle_registration_text(
            telegram_user_id=tg, text=f"  {email.upper()} ", db=session, redis=redis
        )
        assert r1 == messages_es.REGISTER_ASK_NAME

        r2 = await handle_registration_text(
            telegram_user_id=tg, text="María Pérez", db=session, redis=redis
        )
        assert "María Pérez" in r2 and email in r2  # confirm summary

        r3 = await handle_registration_text(
            telegram_user_id=tg, text="Sí ✅", db=session, redis=redis
        )
        assert "Cuenta creada" in r3

        user = (
            await session.execute(select(User).where(User.email == email))
        ).scalar_one()
        assert user.telegram_user_id == tg
        assert user.currency == "CRC"
        assert len(user.shortcut_token) >= 60
        assert user.shortcut_token in r3  # delivered in-chat

        consent = (
            await session.execute(
                select(UserConsent).where(UserConsent.user_id == user.id)
            )
        ).scalar_one()
        assert consent.purpose == "core_service"
        assert consent.status == "granted"
        assert consent.version == TERMS_VERSION
        assert consent.source == "telegram"

        rule_count = (
            await session.execute(
                select(NotificationRule).where(
                    NotificationRule.user_id == user.id
                )
            )
        ).scalars().all()
        assert len(rule_count) == 1
        categories = (
            await session.execute(
                select(UserCategory).where(UserCategory.user_id == user.id)
            )
        ).scalars().all()
        assert len(categories) > 0

        assert (
            await load_registration(telegram_user_id=tg, redis=redis)
        ) is None  # state cleared
    finally:
        await clear_registration(telegram_user_id=tg, redis=redis)
        await _purge_user(session, email)


@pytest.mark.asyncio
async def test_invalid_email_reprompts(db_with_user):
    session, _uid = db_with_user
    redis = get_redis()
    tg = _tg_id()
    try:
        await begin_registration(telegram_user_id=tg, redis=redis)
        reply = await handle_registration_text(
            telegram_user_id=tg, text="no es un email", db=session, redis=redis
        )
        assert reply == messages_es.REGISTER_EMAIL_INVALID
        state = await load_registration(telegram_user_id=tg, redis=redis)
        assert state["step"] == "email"
    finally:
        await clear_registration(telegram_user_id=tg, redis=redis)


@pytest.mark.asyncio
async def test_taken_email_points_to_pairing(db_with_user):
    session, existing_uid = db_with_user
    redis = get_redis()
    tg = _tg_id()
    existing_email = (
        await session.execute(
            select(User.email).where(User.id == existing_uid)
        )
    ).scalar_one()
    try:
        await begin_registration(telegram_user_id=tg, redis=redis)
        reply = await handle_registration_text(
            telegram_user_id=tg, text=existing_email, db=session, redis=redis
        )
        assert reply == messages_es.REGISTER_EMAIL_TAKEN
        state = await load_registration(telegram_user_id=tg, redis=redis)
        assert state["step"] == "email"  # flow not burned
    finally:
        await clear_registration(telegram_user_id=tg, redis=redis)


@pytest.mark.asyncio
async def test_decline_at_confirm_writes_nothing(db_with_user):
    session, _uid = db_with_user
    redis = get_redis()
    tg = _tg_id()
    email = _email()
    try:
        await begin_registration(telegram_user_id=tg, redis=redis)
        await handle_registration_text(
            telegram_user_id=tg, text=email, db=session, redis=redis
        )
        await handle_registration_text(
            telegram_user_id=tg, text="Juan Soto", db=session, redis=redis
        )
        reply = await handle_registration_text(
            telegram_user_id=tg, text="No ❌", db=session, redis=redis
        )
        assert reply == messages_es.REGISTER_CANCELLED
        created = (
            await session.execute(select(User.id).where(User.email == email))
        ).scalar_one_or_none()
        assert created is None
        assert (
            await load_registration(telegram_user_id=tg, redis=redis)
        ) is None
    finally:
        await clear_registration(telegram_user_id=tg, redis=redis)


@pytest.mark.asyncio
async def test_begin_is_reentrant(db_with_user):
    session, _uid = db_with_user
    redis = get_redis()
    tg = _tg_id()
    email = _email()
    try:
        await begin_registration(telegram_user_id=tg, redis=redis)
        await handle_registration_text(
            telegram_user_id=tg, text=email, db=session, redis=redis
        )
        # A second /start mid-flow re-prompts the CURRENT step (name), it
        # does not restart from email.
        again = await begin_registration(telegram_user_id=tg, redis=redis)
        assert again == messages_es.REGISTER_ASK_NAME
        state = await load_registration(telegram_user_id=tg, redis=redis)
        assert state["step"] == "name"
        assert state["email"] == email
    finally:
        await clear_registration(telegram_user_id=tg, redis=redis)


@pytest.mark.asyncio
async def test_no_state_returns_none(db_with_user):
    session, _uid = db_with_user
    redis = get_redis()
    reply = await handle_registration_text(
        telegram_user_id=_tg_id(), text="hola", db=session, redis=redis
    )
    assert reply is None  # caller falls back to PAIR_PROMPT
