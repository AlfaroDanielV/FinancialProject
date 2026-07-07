"""Unknown-command guard — deterministic, zero LLM.

A slash token no short-circuit matched must never reach the LLM extractor:
before this guard, "/advisor ..." silently degraded into the generic help menu
(dogfood report 2026-07-07). `llm_client=object()` proves the LLM is never
touched — any extractor call would blow up with AttributeError and surface as
EXTRACTOR_FAILED instead of the guard copy.
"""
from __future__ import annotations

import pytest

from bot import messages_es
from bot.clarification import (
    ClarificationState,
    load_clarification,
    save_clarification,
)
from bot.pipeline import process_message
from api.models.user import User
from api.redis_client import get_redis


async def _run(user, text, db):
    return await process_message(
        user=user,
        text=text,
        db=db,
        redis=get_redis(),
        llm_client=object(),  # guard replies before the LLM; see module docstring
        llm_model="x",
    )


@pytest.mark.asyncio
async def test_unknown_command_suggests_close_match(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)

    reply = await _run(user, "/advisor Dame un consejo para mis finanzas", session)

    assert "No reconozco el comando «/advisor»" in reply.text
    assert "¿Quisiste decir /asesor?" in reply.text
    assert "/menu" in reply.text


@pytest.mark.asyncio
async def test_unknown_command_without_close_match_has_no_suggestion(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)

    reply = await _run(user, "/zzzzzz", session)

    assert "No reconozco el comando «/zzzzzz»" in reply.text
    assert "¿Quisiste decir" not in reply.text


@pytest.mark.asyncio
async def test_known_command_with_trailing_text_points_at_bare_form(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)

    reply = await _run(user, "/help me con mis gastos", session)

    assert reply.text == messages_es.COMMAND_NO_ARGS_TPL.format(cmd="/help")


@pytest.mark.asyncio
async def test_telegram_only_command_points_at_telegram_and_app_equiv(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)

    reply = await _run(user, "/aprobar_shadow", session)

    assert "«/aprobar_shadow» funciona en el bot de Telegram" in reply.text
    assert "En la app te sirve /gmail" in reply.text


@pytest.mark.asyncio
async def test_telegram_only_command_without_app_equiv(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)

    reply = await _run(user, "/start", session)

    assert "«/start» funciona en el bot de Telegram" in reply.text
    assert "En la app te sirve" not in reply.text


@pytest.mark.asyncio
async def test_degenerate_slash_token_still_reaches_extractor(db_with_user):
    """"/5000" is not a command shape — it must fall through as a capture
    attempt. With llm_client=object() the extractor blows up and the B0.5 net
    answers with the understanding failure class, proving the guard let it by.
    """
    session, user_id = db_with_user
    user = await session.get(User, user_id)

    reply = await _run(user, "/5000", session)

    assert reply.error_class == "understanding"


@pytest.mark.asyncio
async def test_guard_preserves_pending_clarification_state(db_with_user):
    """A stray slash typo mid-flow must not abort the in-flight clarification."""
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    redis = get_redis()
    state = ClarificationState(
        partial={"intent": "log_expense", "dispatcher": "write", "confidence": 0.9},
        awaiting_field="amount",
        question_es="¿Cuánto fue?",
    )
    await save_clarification(user_id=user.id, state=state, redis=redis)

    reply = await _run(user, "/advisr", session)

    assert "No reconozco el comando" in reply.text
    survived = await load_clarification(user_id=user.id, redis=redis)
    assert survived is not None
    assert survived.awaiting_field == "amount"
