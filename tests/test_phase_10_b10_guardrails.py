"""P10 B10 — safety guardrails: the deterministic two-tier distress net.

Locks (D7/O3): a genuine crisis signal short-circuits PRE-LLM to the warm
línea-1322 hand-off (never assessed by an LLM, never diagnosed, no
confidentiality promise); financial despair keeps its financial answer and
gets a warm real-person note appended once per day; ordinary money questions
never trigger anything; the CR «matar de risa» idiom never false-positives.
Also locks the toxic-positivity ban living in the advisory persona (D6).

Prereqs (pipeline tests): `docker compose up -d db redis` + migrations.
"""
from __future__ import annotations

import uuid

import pytest

from api.services.advisory.guardrails import detect_distress
from bot import messages_es, pipeline
from bot.redis_keys import distress_note_key


# ── the detector table ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "ya no quiero seguir viviendo con estas deudas",
        "a veces pienso en quitarme la vida",
        "me quiero matar, no doy más",
        "he pensado en suicidarme por la plata",
        "no vale la pena vivir así",
        "quiero acabar con todo",
    ],
)
def test_crisis_signals_detected(text):
    assert detect_distress(text) == "crisis"


@pytest.mark.parametrize(
    "text",
    [
        "no puedo más con las deudas",
        "estoy ahogado en deudas, ¿qué hago?",
        "me estoy ahogando con los pagos",
        "no veo salida con el préstamo",
        "estoy desesperada, me van a embargar",
        "siento que lo voy a perder todo",
    ],
)
def test_financial_despair_detected(text):
    assert detect_distress(text) == "financial"


@pytest.mark.parametrize(
    "text",
    [
        "¿me alcanza para un carro?",
        "gasté 5000 en el super",
        "¿cuánto debo de la tarjeta?",
        "me quiero matar de risa con este chunche",  # CR idiom — not a signal
        "quiero salir de deudas este año",
        "/asesor",
        "",
    ],
)
def test_ordinary_messages_are_quiet(text):
    assert detect_distress(text) is None


def test_crisis_wins_over_financial():
    assert (
        detect_distress("estoy ahogado en deudas y ya no quiero vivir") == "crisis"
    )


# ── the copy contract (O3) ────────────────────────────────────────────────────


def test_crisis_copy_points_to_1322_and_911_without_diagnosis():
    copy = messages_es.DISTRESS_CRISIS
    assert "1322" in copy
    assert "9-1-1" in copy
    # Never assess/diagnose/promise confidentiality.
    for forbidden in ("diagnos", "confidencial", "depres", "trastorno"):
        assert forbidden not in copy.lower()


def test_financial_suffix_points_to_real_people_not_crisis_line():
    suffix = messages_es.DISTRESS_FINANCIAL_SUFFIX
    assert "1322" not in suffix  # money-stress is never escalated (O3)
    assert "asesor" in suffix
    assert "solidarista" in suffix


# ── pipeline wiring ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_crisis_short_circuits_pre_llm(db_with_user):
    from api.models.user import User
    from api.redis_client import get_redis

    session, user_id = db_with_user
    user = await session.get(User, user_id)
    # llm_client=object(): if anything touched the LLM this would explode —
    # the crisis path must run PRE-LLM.
    reply = await pipeline.process_message(
        user=user,
        text="ya no quiero seguir viviendo, las deudas me comieron",
        db=session,
        redis=get_redis(),
        llm_client=object(),
        llm_model="x",
    )
    assert reply.text == messages_es.DISTRESS_CRISIS


@pytest.mark.asyncio
async def test_financial_despair_keeps_answer_and_appends_note_once(
    db_with_user, monkeypatch
):
    from api.models.user import User
    from api.redis_client import get_redis
    from api.services.llm_extractor import ExtractionResult, Intent
    from app.queries.dispatcher import DispatchOutcome

    session, user_id = db_with_user
    user = await session.get(User, user_id)
    redis = get_redis()
    await redis.delete(distress_note_key(user_id))
    await redis.delete(f"telegram:pending:{user_id}")

    async def _fake_extract(*a, **k):
        return ExtractionResult(intent=Intent.QUERY, dispatcher="query", confidence=0.9)

    async def _fake_query(*a, **k):
        return DispatchOutcome(text="Tu plan para salir de deudas: …")

    monkeypatch.setattr(pipeline, "extract_finance_intent", _fake_extract)
    monkeypatch.setattr(pipeline, "run_dispatch", _fake_query)

    reply = await pipeline.process_message(
        user=user, text="estoy ahogado en deudas, ¿qué hago?",
        db=session, redis=redis, llm_client=object(), llm_model="x",
    )
    # The financial answer survives AND the warm note rides along.
    assert reply.text.startswith("Tu plan para salir de deudas")
    assert messages_es.DISTRESS_FINANCIAL_SUFFIX in reply.text

    # Second vent the same day: answer only, no nagging.
    reply2 = await pipeline.process_message(
        user=user, text="sigo ahogado en deudas",
        db=session, redis=redis, llm_client=object(), llm_model="x",
    )
    assert messages_es.DISTRESS_FINANCIAL_SUFFIX not in reply2.text
    await redis.delete(distress_note_key(user_id))


# ── toxic-positivity ban (D6) lives in the persona ────────────────────────────


def test_advisory_persona_bans_manufactured_optimism():
    from app.queries.prompts.system import _ADVISORY_PERSONA

    lowered = _ADVISORY_PERSONA.lower()
    assert "optimismo fabricado" in lowered
    assert "todo va a estar bien" in lowered
    assert "gate_reason" in _ADVISORY_PERSONA
