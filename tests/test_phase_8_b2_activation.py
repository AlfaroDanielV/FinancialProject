"""Phase 8 B2 — chat-led activation + redefine "activated".

Covers the four moving parts:

1. `GET /onboarding/status` activation signals — `has_balance` (initial_balance
   > 0 or an anchor), `has_expense` (a real expense, mirroring the dashboard
   filter: confirmed, non-archived, not a transfer leg / goal flow / ajuste),
   and `is_activated = has_accounts AND has_balance AND has_expense`.
2. `_dispatch_set_balance` cold start — a user with ZERO accounts who states a
   balance gets `StartAccountCreation` (the LLM never names the account); a user
   WITH an account behaves as before.
3. The seeded account-creation flow skips the currency + balance questions and
   creates the account with the stated real balance.
4. `build_onboarding_reply` — chat-led empty welcome, progress next-steps, and
   the activated capabilities reminder.
"""
from __future__ import annotations

import socket
from datetime import date, datetime
from decimal import Decimal
from urllib.parse import urlparse

import pytest

from api.config import settings
from api.models.account import Account
from api.models.goal import Goal
from api.models.transaction import Transaction
from api.models.transfer import Transfer
from api.models.user import User
from api.redis_client import get_redis
from api.routers.onboarding import onboarding_status
from api.services.anchors import AJUSTE_CATEGORY
from api.services.llm_extractor import ExtractionResult, Intent
from api.services.telegram_dispatcher import (
    ProposeAction,
    StartAccountCreation,
    dispatch,
)
from bot.account_creation import (
    clear_account_creation,
    handle_account_creation_reply,
    load_account_creation,
    start_account_creation,
)
from bot.onboarding_welcome import build_onboarding_reply


def _db_reachable() -> bool:
    try:
        url = urlparse(settings.database_url.replace("+asyncpg", ""))
        with socket.create_connection(
            (url.hostname or "localhost", url.port or 5432), timeout=0.5
        ):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    not _db_reachable(), reason="Postgres not reachable"
)


# ── helpers ────────────────────────────────────────────────────────────────────


async def _account(
    session, user_id, name, *, account_type="checking", initial="0"
) -> Account:
    a = Account(
        user_id=user_id,
        name=name,
        account_type=account_type,
        currency="CRC",
        initial_balance=Decimal(initial),
    )
    session.add(a)
    await session.commit()
    await session.refresh(a)
    return a


async def _expense(session, user_id, account_id, *, amount="-5000", **extra) -> None:
    session.add(
        Transaction(
            user_id=user_id,
            account_id=account_id,
            amount=Decimal(amount),
            currency="CRC",
            merchant="Super",
            transaction_date=date.today(),
            source="manual",
            status="confirmed",
            **extra,
        )
    )
    await session.commit()


async def _user(session, user_id) -> User:
    user = await session.get(User, user_id)
    assert user is not None
    user.currency = "CRC"
    user.timezone = "America/Costa_Rica"
    return user


def _set_balance_ext(**overrides) -> ExtractionResult:
    base = dict(
        intent=Intent.SET_BALANCE,
        dispatcher="write",
        amount=Decimal("200000"),
        account_hint=None,
        confidence=0.95,
    )
    base.update(overrides)
    return ExtractionResult(**base)


# ── 1. onboarding_status activation signals ────────────────────────────────────


async def test_status_zero_accounts_not_activated(db_with_user):
    session, user_id = db_with_user
    user = await _user(session, user_id)
    status = await onboarding_status(db=session, user=user)
    assert status.has_accounts is False
    assert status.has_balance is False
    assert status.has_expense is False
    assert status.is_activated is False


async def test_status_balance_from_initial_balance(db_with_user):
    session, user_id = db_with_user
    user = await _user(session, user_id)
    await _account(session, user_id, "BAC", initial="200000")
    status = await onboarding_status(db=session, user=user)
    assert status.has_accounts is True
    assert status.has_balance is True
    # No expense yet → not activated.
    assert status.has_expense is False
    assert status.is_activated is False


async def test_status_account_balance_and_expense_activates(db_with_user):
    session, user_id = db_with_user
    user = await _user(session, user_id)
    a = await _account(session, user_id, "BAC", initial="200000")
    await _expense(session, user_id, a.id, amount="-5000")
    status = await onboarding_status(db=session, user=user)
    assert status.has_balance is True
    assert status.has_expense is True
    assert status.is_activated is True


async def test_status_non_expense_rows_dont_count(db_with_user):
    session, user_id = db_with_user
    user = await _user(session, user_id)
    a = await _account(session, user_id, "BAC", initial="200000")
    b = await _account(session, user_id, "BCR", initial="0")

    # A transfer leg (from A to B): excluded.
    transfer = Transfer(
        user_id=user_id,
        from_account_id=a.id,
        to_account_id=b.id,
        amount=Decimal("10000"),
        currency="CRC",
        occurred_at=datetime.utcnow(),
    )
    session.add(transfer)
    await session.commit()
    await session.refresh(transfer)
    await _expense(session, user_id, a.id, amount="-10000", transfer_id=transfer.id)

    # A goal aporte (savings flow, not spending): excluded.
    goal = Goal(user_id=user_id, name="Vacaciones", target_amount=Decimal("500000"))
    session.add(goal)
    await session.commit()
    await session.refresh(goal)
    await _expense(session, user_id, a.id, amount="-20000", goal_id=goal.id)

    # A reconciliation ajuste (balance correction, not P&L): excluded.
    await _expense(
        session, user_id, a.id, amount="-3000", category=AJUSTE_CATEGORY
    )

    status = await onboarding_status(db=session, user=user)
    assert status.has_balance is True  # the account still has a real balance
    assert status.has_expense is False  # none of the three count
    assert status.is_activated is False

    # A plain expense flips it.
    await _expense(session, user_id, a.id, amount="-5000")
    status2 = await onboarding_status(db=session, user=user)
    assert status2.has_expense is True
    assert status2.is_activated is True


# ── 2. _dispatch_set_balance cold start ─────────────────────────────────────────


async def test_set_balance_cold_start_returns_start_account_creation(db_with_user):
    session, user_id = db_with_user
    user = await _user(session, user_id)
    res = await dispatch(
        extraction=_set_balance_ext(amount=Decimal("200000")),
        user=user,
        today=date.today(),
        db=session,
    )
    assert isinstance(res, StartAccountCreation)
    assert res.initial_balance == "200000"
    assert res.currency == "CRC"


async def test_set_balance_with_account_still_proposes(db_with_user):
    session, user_id = db_with_user
    user = await _user(session, user_id)
    await _account(session, user_id, "BAC", initial="0")
    res = await dispatch(
        extraction=_set_balance_ext(amount=Decimal("200000")),
        user=user,
        today=date.today(),
        db=session,
    )
    # A user WITH an account is unchanged: one account resolves → propose.
    assert isinstance(res, ProposeAction)
    assert res.action_type == "set_balance"


# ── 3. seeded account-creation flow ─────────────────────────────────────────────


async def test_seeded_account_creation_skips_currency_and_balance(db_with_user):
    session, user_id = db_with_user
    user = await _user(session, user_id)
    redis = get_redis()
    try:
        prompt = await start_account_creation(
            user_id=user.id,
            redis=redis,
            currency="CRC",
            initial_balance="200000",
        )
        # Cold start asks the NAME first (balance-aware prompt).
        assert "se llama" in prompt.lower()
        state = await load_account_creation(user_id=user.id, redis=redis)
        assert state is not None and state.step == "asking_name"

        # name → asks type
        state = await load_account_creation(user_id=user.id, redis=redis)
        out_name = await handle_account_creation_reply(
            user=user, text="BAC ahorros", state=state, db=session, redis=redis
        )
        assert "tipo" in out_name.text.lower()

        # type → confirmation directly (currency + balance questions skipped)
        state = await load_account_creation(user_id=user.id, redis=redis)
        assert state.step == "asking_type"
        out_type = await handle_account_creation_reply(
            user=user, text="ahorros", state=state, db=session, redis=redis
        )
        assert "¿Seguimos?" in out_type.text
        assert "200000" in out_type.text
        assert "¿En qué moneda" not in out_type.text
        assert "saldo inicial arranca" not in out_type.text.lower()

        # confirm → account created WITH the seeded balance
        state = await load_account_creation(user_id=user.id, redis=redis)
        assert state.step == "confirming"
        out_yes = await handle_account_creation_reply(
            user=user, text="sí", state=state, db=session, redis=redis
        )
        assert out_yes.account is not None
        assert out_yes.account.initial_balance == Decimal("200000")
        assert out_yes.account.currency == "CRC"
    finally:
        await clear_account_creation(user_id=user.id, redis=redis)


# ── 4. build_onboarding_reply copy ──────────────────────────────────────────────


async def test_onboarding_reply_empty_is_chat_led(db_with_user):
    session, user_id = db_with_user
    reply = await build_onboarding_reply(
        user=await _user(session, user_id), db=session, first_name="Dani"
    )
    assert "decime cuánto tenés ahora en la cuenta" in reply.text
    assert "Te falta registrar" not in reply.text


async def test_onboarding_reply_no_balance_asks_for_saldo(db_with_user):
    session, user_id = db_with_user
    await _account(session, user_id, "BAC", initial="0")
    reply = await build_onboarding_reply(
        user=await _user(session, user_id), db=session
    )
    assert "Ya tenés tu cuenta" in reply.text
    assert "decime el saldo real" in reply.text


async def test_onboarding_reply_balance_no_expense_asks_for_gasto(db_with_user):
    session, user_id = db_with_user
    await _account(session, user_id, "BAC", initial="200000")
    reply = await build_onboarding_reply(
        user=await _user(session, user_id), db=session
    )
    assert "Registrá tu primer gasto" in reply.text


async def test_onboarding_reply_activated_is_capabilities_help(db_with_user):
    session, user_id = db_with_user
    a = await _account(session, user_id, "BAC", initial="200000")
    await _expense(session, user_id, a.id, amount="-5000")
    reply = await build_onboarding_reply(
        user=await _user(session, user_id), db=session
    )
    assert "Ya tenés lo básico registrado." in reply.text
