"""Phase 6d B9 — conversational account creation in Redis."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import pytest
from sqlalchemy import select

from api.models.account import Account
from api.models.user import User
from api.services.llm_extractor import FixtureLLMClient
from bot import messages_es, pipeline
from bot.account_creation import (
    AccountCreationState,
    load_account_creation,
    save_account_creation,
)
from bot.pipeline import process_mock_extraction


@dataclass
class _FakeRedis:
    data: dict[str, str]
    ttls: dict[str, int]

    def __init__(self) -> None:
        self.data = {}
        self.ttls = {}

    async def get(self, key: str):
        return self.data.get(key)

    async def setex(self, key: str, ttl: int, value: str):
        self.data[key] = value
        self.ttls[key] = ttl

    async def delete(self, key: str):
        self.data.pop(key, None)
        self.ttls.pop(key, None)

    def expire_all(self) -> None:
        self.data.clear()
        self.ttls.clear()


async def _user(session, user_id):
    user = await session.get(User, user_id)
    assert user is not None
    user.currency = "CRC"
    user.timezone = "America/Costa_Rica"
    return user


@pytest.fixture
def allow_pipeline(monkeypatch: pytest.MonkeyPatch):
    async def _true(**kwargs):
        return True

    async def _none(**kwargs):
        return None

    def _noop_enqueue(**kwargs):
        return None

    monkeypatch.setattr(pipeline, "check_and_increment_rate", _true)
    monkeypatch.setattr(pipeline, "assert_within_budget", _none)
    monkeypatch.setattr(pipeline, "enqueue_insight_extraction", _noop_enqueue)


async def _send(user, text: str, session, redis):
    return await pipeline.process_message(
        user=user,
        text=text,
        db=session,
        redis=redis,
        llm_client=FixtureLLMClient(),
        llm_model="claude-haiku-4-5",
    )


async def _accounts(session, user_id):
    return list(
        (
            await session.execute(
                select(Account)
                .where(Account.user_id == user_id)
                .order_by(Account.created_at.asc())
            )
        )
        .scalars()
        .all()
    )


def _raw_expense_with_bac_hint():
    return {
        "intent": "log_expense",
        "dispatcher": "write",
        "amount": 5000,
        "currency": None,
        "merchant": "super",
        "category_hint": "supermercado",
        "account_hint": "BAC",
        "occurred_at_hint": None,
        "query_window": None,
        "confidence": 0.95,
        "raw_notes": None,
    }


@pytest.mark.asyncio
async def test_happy_path_creates_account_in_four_turns(db_with_user, allow_pipeline):
    session, user_id = db_with_user
    user = await _user(session, user_id)
    redis = _FakeRedis()

    reply = await _send(user, "crear cuenta BAC", session, redis)
    assert "¿Qué tipo es?" in reply.text

    reply = await _send(user, "ahorros", session, redis)
    assert "CRC o USD" in reply.text

    reply = await _send(user, "CRC", session, redis)
    assert "saldo inicial" in reply.text

    reply = await _send(user, "0", session, redis)
    assert "Voy a crear esta cuenta" in reply.text
    assert "BAC" in reply.text

    reply = await _send(user, "sí", session, redis)
    assert reply.text == "Listo, creé la cuenta BAC."

    accounts = await _accounts(session, user_id)
    assert len(accounts) == 1
    assert accounts[0].name == "BAC"
    assert accounts[0].account_type == "savings"
    assert accounts[0].currency == "CRC"
    assert accounts[0].initial_balance == Decimal("0.00")
    assert await load_account_creation(user_id=user_id, redis=redis) is None


@pytest.mark.asyncio
async def test_awaiting_start_abandoned_when_user_types_new_expense(
    db_with_user, allow_pipeline
):
    """Regression: a stale lazy 'create account?' prompt (awaiting_start) must
    NOT swallow a new capture. Typing a fresh expense abandons the prompt and
    proposes the expense (instead of the 'Decime crear…' trap fallback)."""
    from tests.fixtures.extractor_responses import BASIC_EXPENSE_CRC

    session, user_id = db_with_user
    user = await _user(session, user_id)
    redis = _FakeRedis()
    await save_account_creation(
        user_id=user_id,
        state=AccountCreationState(step="awaiting_start", name="BAC"),
        redis=redis,
    )

    reply = await pipeline.process_message(
        user=user,
        text="gasté 100000 en un restaurante",
        db=session,
        redis=redis,
        llm_client=FixtureLLMClient(default=BASIC_EXPENSE_CRC),
        llm_model="claude-haiku-4-5",
    )

    assert "Decime crear" not in reply.text
    assert reply.buttons  # an expense proposal → Sí / No / Editar
    assert await load_account_creation(user_id=user_id, redis=redis) is None


@pytest.mark.asyncio
async def test_awaiting_start_affirmative_still_proceeds(db_with_user, allow_pipeline):
    """An actual 'crear' answer to the prompt still advances the flow."""
    session, user_id = db_with_user
    user = await _user(session, user_id)
    redis = _FakeRedis()
    await save_account_creation(
        user_id=user_id,
        state=AccountCreationState(step="awaiting_start", name="BAC"),
        redis=redis,
    )

    reply = await _send(user, "crear", session, redis)
    assert "¿Qué tipo es?" in reply.text
    state = await load_account_creation(user_id=user_id, redis=redis)
    assert state is not None and state.step == "asking_type"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state",
    [
        AccountCreationState(step="awaiting_start", name="BAC"),
        AccountCreationState(step="asking_name"),
        AccountCreationState(step="asking_type", name="BAC"),
        AccountCreationState(
            step="asking_currency",
            name="BAC",
            account_type="savings",
        ),
        AccountCreationState(
            step="asking_balance",
            name="BAC",
            account_type="savings",
            currency="CRC",
        ),
        AccountCreationState(
            step="confirming",
            name="BAC",
            account_type="savings",
            currency="CRC",
            initial_balance="0",
        ),
    ],
)
async def test_cancel_clears_every_state_without_writing(
    db_with_user,
    allow_pipeline,
    state,
):
    session, user_id = db_with_user
    user = await _user(session, user_id)
    redis = _FakeRedis()
    await save_account_creation(user_id=user_id, state=state, redis=redis)

    reply = await _send(user, "/cancel", session, redis)

    assert reply.text == messages_es.CANCELLED
    assert await load_account_creation(user_id=user_id, redis=redis) is None
    assert await _accounts(session, user_id) == []


@pytest.mark.asyncio
async def test_expired_state_falls_back_to_idle_without_writing(
    db_with_user,
    allow_pipeline,
):
    session, user_id = db_with_user
    user = await _user(session, user_id)
    redis = _FakeRedis()
    await save_account_creation(
        user_id=user_id,
        state=AccountCreationState(
            step="confirming",
            name="BAC",
            account_type="savings",
            currency="CRC",
            initial_balance="0",
        ),
        redis=redis,
    )
    redis.expire_all()

    reply = await _send(user, "sí", session, redis)

    # The invariant under test — an EXPIRED account-creation state writes
    # nothing — still holds. The reply text changed with the 2026-06-13
    # query-robustness fix: a bare "sí" with no pending write no longer
    # dead-ends at "no tengo nada pendiente"; it falls through to the normal
    # extract → dispatch path (here the empty FixtureLLMClient can't parse
    # "sí", so it returns the handled EXTRACTOR_FAILED). Handled message, no
    # account created.
    assert reply.text == messages_es.EXTRACTOR_FAILED
    assert await _accounts(session, user_id) == []


@pytest.mark.asyncio
async def test_lazy_detection_handoff_prepops_name_and_replays_original_expense(
    db_with_user,
    allow_pipeline,
):
    session, user_id = db_with_user
    session.add(
        Account(
            user_id=user_id,
            name="Efectivo",
            account_type="checking",
            initial_balance=Decimal("0"),
        )
    )
    await session.commit()

    user = await _user(session, user_id)
    redis = _FakeRedis()

    reply = await process_mock_extraction(
        user=user,
        raw_extraction=_raw_expense_with_bac_hint(),
        db=session,
        redis=redis,
    )
    assert "No tengo registrada una cuenta llamada BAC" in reply.text

    state = await load_account_creation(user_id=user_id, redis=redis)
    assert state is not None
    assert state.step == "awaiting_start"
    assert state.name == "BAC"

    reply = await _send(user, "crear", session, redis)
    assert "La creo como BAC" in reply.text

    await _send(user, "ahorros", session, redis)
    await _send(user, "CRC", session, redis)
    await _send(user, "0", session, redis)
    reply = await _send(user, "sí", session, redis)

    assert "Listo, creé la cuenta BAC." in reply.text
    assert "Gasto de ₡5.000 en super" in reply.text
    assert "cuenta BAC" in reply.text
    assert reply.buttons

    accounts = await _accounts(session, user_id)
    assert [account.name for account in accounts] == ["Efectivo", "BAC"]
    assert await load_account_creation(user_id=user_id, redis=redis) is None
