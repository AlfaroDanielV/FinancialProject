"""SMART goal creation — Specific / Measurable / Achievable / Relevant / Time-bound.

The 2026-07 dogfood found conversational goal creation "failing awfully":
- F1 an inescapable amount-clarification loop (digit-free replies re-ask forever);
- F2 "2 millones para diciembre" silently created a ₡2 goal (fullmatch parser);
- F3 an unresolvable date was silently dropped (no deadline, no feasibility);
- infeasible goals just proposed with no actionable choice.

This suite locks the fixes. The deterministic path (parser, dispatch, clarify)
is exercised without the real LLM — the LLM only ever supplied the fields.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from api.models.user import User
from api.redis_client import get_redis
from api.services.llm_extractor import GOAL_NO_DATE_SENTINEL, ExtractionResult, Intent
from api.services.telegram_dispatcher import (
    AskClarification,
    ProposeAction,
    _resolve_goal_target_date,
    dispatch,
)
from bot import messages_es
from bot.clarification import ClarificationState, _parse_amount_es, merge_reply
from bot.pipeline import process_message


TODAY = date(2026, 5, 31)


class _StubUser:
    currency = "CRC"


def _goal(**overrides) -> ExtractionResult:
    base = dict(
        intent=Intent.CREATE_GOAL,
        dispatcher="write",
        goal_name="vacaciones",
        goal_target_amount=Decimal("2000000"),
        confidence=0.9,
    )
    base.update(overrides)
    return ExtractionResult(**base)


# ── F2: the ₡2 goal — multiplier survives a trailing phrase ───────────────────


@pytest.mark.parametrize(
    "reply,expected",
    [
        ("2 millones para diciembre", Decimal("2000000")),
        ("quiero 2 millones", Decimal("2000000")),
        ("500 mil para el carro", Decimal("500000")),
        ("1,5 millones", Decimal("1500000")),
        ("15k", Decimal("15000")),
        ("2 millones", Decimal("2000000")),  # bare still works
        ("500000", Decimal("500000")),
    ],
)
def test_amount_parser_keeps_multiplier_with_trailing_words(reply, expected):
    assert _parse_amount_es(reply) == expected


def test_mil_does_not_trigger_inside_millones():
    # "mil" is a substring of "millones"; the ×1000 branch must not fire on it.
    assert _parse_amount_es("3 millones") == Decimal("3000000")


# ── F3 + date resolver hardening ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "hint,expected",
    [
        ("en 6 meses", date(2026, 11, 30)),
        ("dentro de tres meses", date(2026, 8, 31)),
        ("en un año", date(2027, 5, 31)),
        ("el próximo mes", date(2026, 6, 30)),
        ("el próximo año", date(2027, 5, 31)),
        ("fin de año", date(2026, 12, 1)),
        ("15 de diciembre", date(2026, 12, 15)),
        ("diciembre 2027", date(2027, 12, 1)),
        ("para el 2027", date(2027, 12, 1)),
        ("2026-08-15", date(2026, 8, 15)),
    ],
)
def test_resolve_date_hardened(hint, expected):
    assert _resolve_goal_target_date(hint, TODAY) == expected


def test_resolve_date_unparseable_returns_none():
    assert _resolve_goal_target_date("cuando pueda", TODAY) is None


@pytest.mark.asyncio
async def test_missing_date_asks_with_chips(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    decision = await dispatch(extraction=_goal(), user=user, today=TODAY, db=session)
    assert isinstance(decision, AskClarification)
    assert decision.awaiting_field == "goal_target_date"
    assert "Sin fecha" in decision.options


@pytest.mark.asyncio
async def test_unparseable_date_reasks_not_drops(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    decision = await dispatch(
        extraction=_goal(goal_target_date="cuando pueda"),
        user=user,
        today=TODAY,
        db=session,
    )
    assert isinstance(decision, AskClarification)
    assert decision.awaiting_field == "goal_target_date"


@pytest.mark.asyncio
async def test_past_date_rejected(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    decision = await dispatch(
        extraction=_goal(goal_target_date="2020-01-01"),
        user=user,
        today=TODAY,
        db=session,
    )
    assert isinstance(decision, AskClarification)
    assert decision.awaiting_field == "goal_target_date"


@pytest.mark.asyncio
async def test_sin_fecha_sentinel_proceeds_dateless(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    decision = await dispatch(
        extraction=_goal(goal_target_date=GOAL_NO_DATE_SENTINEL),
        user=user,
        today=TODAY,
        db=session,
    )
    assert isinstance(decision, ProposeAction)
    assert decision.payload["target_date"] is None


def test_merge_reply_sin_fecha_maps_to_sentinel():
    state = ClarificationState(
        partial=_goal().model_dump(mode="json"),
        awaiting_field="goal_target_date",
        question_es="¿Para cuándo?",
    )
    merged = merge_reply(state, "Sin fecha", _StubUser())
    assert merged is not None
    assert merged.goal_target_date == GOAL_NO_DATE_SENTINEL


def test_merge_reply_date_hint_passes_through():
    state = ClarificationState(
        partial=_goal().model_dump(mode="json"),
        awaiting_field="goal_target_date",
        question_es="¿Para cuándo?",
    )
    merged = merge_reply(state, "en 8 meses", _StubUser())
    assert merged.goal_target_date == "en 8 meses"


# ── SMART-M plausibility ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_implausibly_small_target_reasks_amount(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    decision = await dispatch(
        extraction=_goal(goal_target_amount=Decimal("2")),
        user=user,
        today=TODAY,
        db=session,
    )
    assert isinstance(decision, AskClarification)
    assert decision.awaiting_field == "goal_target_amount"


# ── SMART-S junk name ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_amount_as_name_reasks(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    decision = await dispatch(
        extraction=_goal(goal_name="2 millones"),
        user=user,
        today=TODAY,
        db=session,
    )
    assert isinstance(decision, AskClarification)
    assert decision.awaiting_field == "goal_name"


# ── F1 loop escape + attempts cap (through the real pipeline) ─────────────────


async def _chat(user, text, db):
    return await process_message(
        user=user,
        text=text,
        db=db,
        redis=get_redis(),
        llm_client=object(),  # amount clarification is deterministic, no LLM
        llm_model="x",
    )


@pytest.mark.asyncio
async def test_amount_clarification_cancel_word_escapes(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    redis = get_redis()
    from bot.clarification import save_clarification

    await save_clarification(
        user_id=user.id,
        state=ClarificationState(
            partial=_goal(goal_target_amount=None, goal_name=None).model_dump(
                mode="json"
            ),
            awaiting_field="goal_target_amount",
            question_es="¿Cuánto querés ahorrar?",
        ),
        redis=redis,
    )
    reply = await _chat(user, "cancelar", session)
    assert reply.text == messages_es.CANCELLED


@pytest.mark.asyncio
async def test_amount_clarification_bails_after_cap(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    redis = get_redis()
    from bot.clarification import load_clarification, save_clarification

    await save_clarification(
        user_id=user.id,
        state=ClarificationState(
            partial=_goal(goal_target_amount=None, goal_name=None).model_dump(
                mode="json"
            ),
            awaiting_field="goal_target_amount",
            question_es="¿Cuánto querés ahorrar?",
        ),
        redis=redis,
    )
    # First uninterpretable reply → re-ask (attempts→1).
    first = await _chat(user, "no sé", session)
    assert "¿Cuánto" in first.text
    assert await load_clarification(user_id=user.id, redis=redis) is not None
    # Second → bail + clear.
    second = await _chat(user, "ni idea", session)
    assert second.text == messages_es.CLARIFY_GAVE_UP
    assert await load_clarification(user_id=user.id, redis=redis) is None


# ── SMART-A infeasible decision-point ─────────────────────────────────────────


def test_merge_reply_infeasible_extend_rewrites_date():
    state = ClarificationState(
        partial=_goal(goal_target_date="en 2 meses").model_dump(mode="json"),
        awaiting_field="goal_infeasible",
        question_es="¿Qué querés hacer?",
        options=["Extender el plazo (~18 meses)", "Bajar la meta a ₡500.000", "Crear así"],
        goal_alt_date="2027-11-30",
        goal_alt_amount="500000",
    )
    merged = merge_reply(state, "Extender el plazo (~18 meses)", _StubUser())
    assert merged.goal_target_date == "2027-11-30"


def test_merge_reply_infeasible_reduce_rewrites_amount():
    state = ClarificationState(
        partial=_goal(goal_target_date="en 2 meses").model_dump(mode="json"),
        awaiting_field="goal_infeasible",
        question_es="¿Qué querés hacer?",
        options=["Extender el plazo", "Bajar la meta a ₡500.000", "Crear así"],
        goal_alt_date="2027-11-30",
        goal_alt_amount="500000",
    )
    merged = merge_reply(state, "Bajar la meta a ₡500.000", _StubUser())
    assert merged.goal_target_amount == Decimal("500000")


def test_merge_reply_infeasible_force_sets_flag():
    state = ClarificationState(
        partial=_goal(goal_target_date="en 2 meses").model_dump(mode="json"),
        awaiting_field="goal_infeasible",
        question_es="¿Qué querés hacer?",
        options=["Extender el plazo", "Bajar la meta", "Crear así"],
        goal_alt_date="2027-11-30",
        goal_alt_amount="500000",
    )
    merged = merge_reply(state, "Crear así", _StubUser())
    assert merged.goal_force_create is True
