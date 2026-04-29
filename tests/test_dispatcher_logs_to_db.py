from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.queries import dispatcher
from app.queries.llm_client import QueryLLMResponse
from app.queries.tools.base import _reset_registry_for_tests
from api.models.llm_query_dispatch import LLMQueryDispatch


class _FakeQueryClient:
    async def run_query_loop(self, **kwargs):
        return QueryLLMResponse(
            text="Aún estoy aprendiendo a responder consultas financieras.",
            total_iterations=0,
            total_input_tokens=11,
            total_output_tokens=7,
            tools_used=[],
            duration_ms=12,
        )


class _SessionContext:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return None


@pytest.fixture(autouse=True)
def _clean_state():
    dispatcher.set_query_llm_client(_FakeQueryClient())
    _reset_registry_for_tests()
    yield
    dispatcher.set_query_llm_client(None)
    _reset_registry_for_tests()


@pytest.mark.asyncio
async def test_dispatcher_creates_llm_query_dispatch_row(db_with_user, monkeypatch):
    session, user_id = db_with_user
    monkeypatch.setattr(
        dispatcher,
        "AsyncSessionLocal",
        lambda: _SessionContext(session),
    )

    response = await dispatcher.handle(
        user_id=user_id,
        message_text="cuánto gasté",
        telegram_chat_id=123,
    )

    rows = await session.execute(
        select(LLMQueryDispatch).where(LLMQueryDispatch.user_id == user_id)
    )
    row = rows.scalar_one()

    assert response == "Aún estoy aprendiendo a responder consultas financieras."
    assert row.message_hash
    assert row.total_iterations == 0
    assert row.total_input_tokens == 11
    assert row.total_output_tokens == 7
    assert row.tools_used == []
    assert row.final_response_chars == len(response)
    assert row.error is None
    assert row.duration_ms == 12
