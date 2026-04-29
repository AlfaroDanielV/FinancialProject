"""Phase 6a routing smoke tests.

These tests pin the split between the existing write/control dispatcher and
the new read-only query dispatcher stub.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest

from api.services.llm_extractor import FixtureLLMClient, RecordedLLMResponse
from api.services.telegram_dispatcher import ShowHelp
from bot.clarification import ClarificationState
from bot import pipeline


@dataclass
class _FakeUser:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    currency: str = "CRC"
    timezone: str = "America/Costa_Rica"
    telegram_user_id: int = 123456


class _StubSession:
    def __init__(self) -> None:
        self.added: list[Any] = []
        self.commits = 0

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.commits += 1


async def _allow_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _true(**kwargs):
        return True

    async def _none(**kwargs):
        return None

    async def _noop(**kwargs):
        return None

    monkeypatch.setattr(pipeline, "check_and_increment_rate", _true)
    # Budget gate moved to api.services.budget in bloque 8.5; stub it
    # to a no-op so routing tests don't need a real DB sum.
    monkeypatch.setattr(pipeline, "assert_within_budget", _noop)
    monkeypatch.setattr(pipeline, "load_clarification", _none)
    monkeypatch.setattr(pipeline, "clear_clarification", _noop)


@pytest.mark.asyncio
async def test_write_dispatcher_goes_to_existing_telegram_dispatcher(monkeypatch):
    await _allow_pipeline(monkeypatch)
    calls: list[dict[str, Any]] = []

    async def _dispatch(**kwargs):
        calls.append(kwargs)
        return ShowHelp()

    monkeypatch.setattr(pipeline, "dispatch", _dispatch)

    client = FixtureLLMClient(
        default=RecordedLLMResponse(
            tool_input={
                "intent": "log_expense",
                "dispatcher": "write",
                "amount": 5000,
                "currency": None,
                "merchant": "super",
                "category_hint": "supermercado",
                "account_hint": None,
                "occurred_at_hint": None,
                "query_window": None,
                "confidence": 0.95,
                "raw_notes": None,
            }
        )
    )

    await pipeline.process_message(
        user=_FakeUser(),
        text="gasté 5000 en el super",
        db=_StubSession(),
        redis=object(),
        llm_client=client,
        llm_model="claude-haiku-4-5",
    )

    assert len(calls) == 1
    assert calls[0]["extraction"].dispatcher == "write"


@pytest.mark.asyncio
async def test_query_dispatcher_goes_to_stub_not_telegram_dispatcher(monkeypatch):
    await _allow_pipeline(monkeypatch)

    async def _dispatch(**kwargs):  # pragma: no cover - should never run
        raise AssertionError("telegram dispatcher should not receive query traffic")

    async def _query_handle(user_id, message_text, telegram_chat_id):
        return f"query stub: {message_text}"

    monkeypatch.setattr(pipeline, "dispatch", _dispatch)
    monkeypatch.setattr(pipeline, "query_dispatcher_handle", _query_handle)

    client = FixtureLLMClient(
        default=RecordedLLMResponse(
            tool_input={
                "intent": "query",
                "dispatcher": "query",
                "amount": None,
                "currency": None,
                "merchant": None,
                "category_hint": None,
                "account_hint": None,
                "occurred_at_hint": None,
                "query_window": "this_week",
                "confidence": 0.95,
                "raw_notes": None,
            }
        )
    )

    reply = await pipeline.process_message(
        user=_FakeUser(),
        text="cuánto gasté esta semana",
        db=_StubSession(),
        redis=object(),
        llm_client=client,
        llm_model="claude-haiku-4-5",
    )

    assert reply.text == "query stub: cuánto gasté esta semana"


@pytest.mark.asyncio
async def test_undo_command_short_circuits_before_llm(monkeypatch):
    await _allow_pipeline(monkeypatch)
    undo_calls = 0

    async def _run_undo(**kwargs):
        nonlocal undo_calls
        undo_calls += 1
        return True, "undo ok"

    async def _dispatch(**kwargs):  # pragma: no cover - should never run
        raise AssertionError("dispatcher should not run for /undo")

    monkeypatch.setattr(pipeline, "run_undo", _run_undo)
    monkeypatch.setattr(pipeline, "dispatch", _dispatch)

    client = FixtureLLMClient(
        default=RecordedLLMResponse(
            tool_input={
                "intent": "unknown",
                "dispatcher": "control",
                "confidence": 0.1,
            }
        )
    )

    reply = await pipeline.process_message(
        user=_FakeUser(),
        text="/undo",
        db=_StubSession(),
        redis=object(),
        llm_client=client,
        llm_model="claude-haiku-4-5",
    )

    assert reply.text == "undo ok"
    assert undo_calls == 1


@pytest.mark.asyncio
async def test_low_confidence_query_bypasses_clarification(monkeypatch):
    await _allow_pipeline(monkeypatch)
    query_calls: list[dict[str, Any]] = []

    async def _dispatch(**kwargs):  # pragma: no cover - should never run
        raise AssertionError("telegram dispatcher should not receive query traffic")

    async def _save_clarification(**kwargs):  # pragma: no cover - should never run
        raise AssertionError("query traffic should not create clarification state")

    async def _query_handle(user_id, message_text, telegram_chat_id):
        query_calls.append(
            {
                "user_id": user_id,
                "message_text": message_text,
                "telegram_chat_id": telegram_chat_id,
            }
        )
        return "query stub"

    monkeypatch.setattr(pipeline, "dispatch", _dispatch)
    monkeypatch.setattr(pipeline, "save_clarification", _save_clarification)
    monkeypatch.setattr(pipeline, "query_dispatcher_handle", _query_handle)

    client = FixtureLLMClient(
        default=RecordedLLMResponse(
            tool_input={
                "intent": "query",
                "dispatcher": "query",
                "amount": None,
                "currency": None,
                "merchant": None,
                "category_hint": None,
                "account_hint": None,
                "occurred_at_hint": None,
                "query_window": None,
                "confidence": 0.4,
                "raw_notes": None,
            }
        )
    )

    reply = await pipeline.process_message(
        user=_FakeUser(),
        text="5000",
        db=_StubSession(),
        redis=object(),
        llm_client=client,
        llm_model="claude-haiku-4-5",
    )

    assert reply.text == "query stub"
    assert query_calls[0]["message_text"] == "5000"


@pytest.mark.asyncio
async def test_pending_clarification_query_reply_abandons_state(
    monkeypatch,
    caplog,
):
    await _allow_pipeline(monkeypatch)
    query_calls: list[dict[str, Any]] = []
    cleared: list[uuid.UUID] = []

    async def _load_clarification(**kwargs):
        return ClarificationState(
            partial={
                "intent": "unknown",
                "dispatcher": "control",
                "confidence": 0.4,
            },
            awaiting_field="intent",
            question_es="¿Es un gasto, un ingreso, o una consulta?",
        )

    async def _clear_clarification(*, user_id, redis):
        cleared.append(user_id)

    async def _dispatch(**kwargs):  # pragma: no cover - should never run
        raise AssertionError("telegram dispatcher should not receive query traffic")

    async def _query_handle(user_id, message_text, telegram_chat_id):
        query_calls.append(
            {
                "user_id": user_id,
                "message_text": message_text,
                "telegram_chat_id": telegram_chat_id,
            }
        )
        return "query stub"

    monkeypatch.setattr(pipeline, "load_clarification", _load_clarification)
    monkeypatch.setattr(pipeline, "clear_clarification", _clear_clarification)
    monkeypatch.setattr(pipeline, "dispatch", _dispatch)
    monkeypatch.setattr(pipeline, "query_dispatcher_handle", _query_handle)

    user = _FakeUser()
    with caplog.at_level("INFO", logger="bot.pipeline"):
        reply = await pipeline.process_message(
            user=user,
            text="consulta",
            db=_StubSession(),
            redis=object(),
            llm_client=FixtureLLMClient(),
            llm_model="claude-haiku-4-5",
        )

    assert reply.text == "query stub"
    assert cleared == [user.id]
    assert query_calls[0]["message_text"] == "consulta"
    assert "clarification_abandoned reason=query_dispatcher" in caplog.text


@pytest.mark.asyncio
async def test_pending_clarification_write_reply_uses_existing_dispatcher(
    monkeypatch,
):
    await _allow_pipeline(monkeypatch)
    dispatch_calls: list[dict[str, Any]] = []

    async def _load_clarification(**kwargs):
        return ClarificationState(
            partial={
                "intent": "unknown",
                "dispatcher": "control",
                "confidence": 0.4,
            },
            awaiting_field="intent",
            question_es="¿Es un gasto, un ingreso, o una consulta?",
        )

    async def _dispatch(**kwargs):
        dispatch_calls.append(kwargs)
        return ShowHelp()

    async def _query_handle(*args, **kwargs):  # pragma: no cover - should never run
        raise AssertionError("write clarification should not hit query dispatcher")

    monkeypatch.setattr(pipeline, "load_clarification", _load_clarification)
    monkeypatch.setattr(pipeline, "dispatch", _dispatch)
    monkeypatch.setattr(pipeline, "query_dispatcher_handle", _query_handle)

    reply = await pipeline.process_message(
        user=_FakeUser(),
        text="gasto",
        db=_StubSession(),
        redis=object(),
        llm_client=FixtureLLMClient(),
        llm_model="claude-haiku-4-5",
    )

    assert reply.text
    assert len(dispatch_calls) == 1
    assert dispatch_calls[0]["extraction"].dispatcher == "write"
