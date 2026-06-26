"""Phase 6d B10 — onboarding-aware /start, /help, and /setup.

Phase 6f B16: the SPA was retired. The setup link is now a native deep link
(`ledgercr://exchange?token=...`) embedded in the message text, not an
inline-button https SPA URL.
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import select

from api.models.account import Account
from api.models.debt import Debt
from api.models.magic_link_token import MagicLinkToken
from api.models.recurring_bill import RecurringBill
from api.models.recurring_income import RecurringIncome
from api.models.transaction import Transaction
from api.models.user import User
from bot import handlers, onboarding_handlers
from bot.onboarding_welcome import build_onboarding_reply, build_setup_reply


class _SessionContext:
    def __init__(self, session) -> None:
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return None


class _FakeFromUser:
    def __init__(self, tg_id: int, first_name: str = "Dani") -> None:
        self.id = tg_id
        self.first_name = first_name


class _FakeMessage:
    def __init__(self, from_user_id: int = 1234, first_name: str = "Dani") -> None:
        self.from_user = _FakeFromUser(from_user_id, first_name)
        self.answers: list[tuple[str, object]] = []

    async def answer(self, text: str, reply_markup=None, **kwargs) -> None:
        self.answers.append((text, reply_markup))


async def _user(session, user_id):
    user = await session.get(User, user_id)
    assert user is not None
    user.currency = "CRC"
    user.timezone = "America/Costa_Rica"
    return user


async def _magic_links(session, user_id):
    return list(
        (
            await session.execute(
                select(MagicLinkToken)
                .where(MagicLinkToken.user_id == user_id)
                .order_by(MagicLinkToken.created_at.asc())
            )
        )
        .scalars()
        .all()
    )


async def _seed_complete_onboarding(session, user_id):
    # Phase 8 B2: "complete" now means ACTIVATED (1 account + a real balance +
    # 1 expense), so the account carries a positive balance and we add an
    # expense alongside the income/debt/bill.
    today = date(2026, 5, 12)
    account = Account(
        user_id=user_id,
        name="BAC",
        account_type="savings",
        currency="CRC",
        initial_balance=Decimal("500000"),
    )
    session.add(account)
    await session.flush()
    session.add_all(
        [
            Transaction(
                user_id=user_id,
                account_id=account.id,
                amount=Decimal("-5000"),
                currency="CRC",
                merchant="Super",
                transaction_date=today,
                source="manual",
                status="confirmed",
            ),
            RecurringIncome(
                user_id=user_id,
                name="Salario",
                income_type="salary",
                amount=Decimal("1000000"),
                currency="CRC",
                frequency="monthly",
                next_payment_date=today,
            ),
            Debt(
                user_id=user_id,
                account_id=account.id,
                name="Hipoteca BAC",
                debt_type="mortgage",
                lender="BAC",
                original_amount=Decimal("10000000"),
                current_balance=Decimal("9000000"),
                interest_rate=Decimal("0.0850"),
                minimum_payment=Decimal("100000"),
                payment_due_day=15,
                term_months=120,
                start_date=today,
                currency="CRC",
            ),
            RecurringBill(
                user_id=user_id,
                name="ICE",
                provider="ICE",
                category="utilities",
                amount_expected=Decimal("25000"),
                currency="CRC",
                frequency="monthly",
                day_of_month=15,
                start_date=today,
            ),
        ]
    )
    await session.commit()


_DEEP_LINK_RE = re.compile(r"ledgercr://\S+")


def _extract_deep_link(text: str) -> str:
    """The native deep link is embedded in the message text (Phase 6f B16),
    not an inline button."""
    m = _DEEP_LINK_RE.search(text)
    assert m is not None, f"no deep link in text: {text!r}"
    return m.group(0)


@pytest.mark.asyncio
async def test_empty_start_reply_generates_setup_link(db_with_user):
    session, user_id = db_with_user
    reply = await build_onboarding_reply(
        user=await _user(session, user_id),
        db=session,
        first_name="Dani",
        include_setup_link=True,
    )

    assert "Hola, Dani." in reply.text
    # Phase 8 B2: chat-led empty welcome — ask one balance, no setup checklist.
    assert "decime cuánto tenés ahora en la cuenta" in reply.text
    assert "ledgercr://exchange?token=" in reply.text
    links = await _magic_links(session, user_id)
    assert len(links) == 1
    assert links[0].purpose == "onboarding"


@pytest.mark.asyncio
async def test_partial_reply_shows_progress_next_step_without_link_by_default(
    db_with_user,
):
    session, user_id = db_with_user
    # An account with no real balance yet → activation's next step is the saldo.
    session.add(
        Account(
            user_id=user_id,
            name="BAC",
            account_type="savings",
            currency="CRC",
            initial_balance=Decimal("0"),
        )
    )
    await session.commit()

    reply = await build_onboarding_reply(
        user=await _user(session, user_id),
        db=session,
    )

    # Phase 8 B2: progress framing — no "te falta" checklist; one next step.
    assert "Te falta registrar" not in reply.text
    assert "Ya tenés tu cuenta" in reply.text
    assert "decime el saldo real" in reply.text
    assert "ledgercr://" not in reply.text
    assert await _magic_links(session, user_id) == []


@pytest.mark.asyncio
async def test_complete_reply_is_short_command_reminder(db_with_user):
    session, user_id = db_with_user
    await _seed_complete_onboarding(session, user_id)

    reply = await build_onboarding_reply(
        user=await _user(session, user_id),
        db=session,
        include_setup_link=True,
    )

    assert "Ya tenés lo básico registrado." in reply.text
    assert "/setup" in reply.text
    assert "/cancel" in reply.text
    assert "ledgercr://" not in reply.text


@pytest.mark.asyncio
async def test_setup_reply_always_mints_a_fresh_single_use_link(db_with_user):
    session, user_id = db_with_user
    user = await _user(session, user_id)

    first = await build_setup_reply(user=user, db=session)
    second = await build_setup_reply(user=user, db=session)

    assert "ledgercr://exchange?token=" in first.text
    assert "ledgercr://exchange?token=" in second.text
    assert _extract_deep_link(first.text) != _extract_deep_link(second.text)
    links = await _magic_links(session, user_id)
    assert len(links) == 2
    assert {link.purpose for link in links} == {"onboarding"}


@pytest.mark.asyncio
async def test_on_start_empty_user_sends_setup_link(db_with_user):
    session, user_id = db_with_user
    user = await _user(session, user_id)
    msg = _FakeMessage()

    async def fake_user_lookup(*, telegram_user_id, db):
        assert telegram_user_id == msg.from_user.id
        return user

    with (
        patch.object(handlers, "AsyncSessionLocal", lambda: _SessionContext(session)),
        patch.object(handlers, "user_by_telegram_id", fake_user_lookup),
    ):
        await handlers.on_start(msg, SimpleNamespace(args=None))

    assert len(msg.answers) == 1
    text, reply_markup = msg.answers[0]
    assert "decime cuánto tenés ahora en la cuenta" in text
    assert reply_markup is None  # deep link is in text, not an inline button
    assert "ledgercr://exchange?token=" in text


@pytest.mark.asyncio
async def test_on_help_existing_user_uses_onboarding_status(db_with_user):
    session, user_id = db_with_user
    await _seed_complete_onboarding(session, user_id)
    user = await _user(session, user_id)
    msg = _FakeMessage()

    async def fake_user_lookup(*, telegram_user_id, db):
        return user

    with (
        patch.object(handlers, "AsyncSessionLocal", lambda: _SessionContext(session)),
        patch.object(handlers, "user_by_telegram_id", fake_user_lookup),
    ):
        await handlers.on_help(msg)

    assert len(msg.answers) == 1
    text, reply_markup = msg.answers[0]
    assert "Ya tenés lo básico registrado." in text
    assert "/setup" in text
    assert reply_markup is None


@pytest.mark.asyncio
async def test_on_setup_handler_mints_new_link_each_time(db_with_user):
    session, user_id = db_with_user
    user = await _user(session, user_id)
    msg = _FakeMessage()

    async def fake_user_lookup(*, telegram_user_id, db):
        return user

    with (
        patch.object(
            onboarding_handlers,
            "AsyncSessionLocal",
            lambda: _SessionContext(session),
        ),
        patch.object(onboarding_handlers, "user_by_telegram_id", fake_user_lookup),
    ):
        await onboarding_handlers.on_setup(msg)
        await onboarding_handlers.on_setup(msg)

    urls = [_extract_deep_link(text) for text, _rm in msg.answers]
    assert len(urls) == 2
    assert urls[0] != urls[1]
    assert all("ledgercr://exchange?token=" in url for url in urls)
