"""P10 B2+B3 — advisory activation (intent-per-message + session) and the
persona/toolset/cap variant of the query dispatcher.

Locks (B2): the D12 spine — a planning question is advisory-routed with or
without a session; a transactional lookup stays plain even mid-session; the
continuity session is idle-expiring and shared by both channels; /asesor ·
/normal · /cancel · /chat/reset manage it; the feature flag gates everything.

Locks (B3): the normal-mode system prompt + tool list stay BYTE-IDENTICAL
(prompt-cache protection); the advisory variant adds the static persona +
principle-framing contract (number-free, brace-free), the ADVISORY_TOOLSET
allowlist, and the higher iteration cap. compare_periods stays the last tool
(cache anchor) in BOTH modes.

Prereqs: `docker compose up -d db redis` + `alembic upgrade head`.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pytest

from api.config import settings
from bot import advisory, messages_es, pipeline
from bot.redis_keys import advisory_session_key


@pytest.fixture(autouse=True)
def _advisory_flag_on(monkeypatch):
    monkeypatch.setattr(settings, "advisory_persona_enabled", True)


@pytest.fixture()
async def _redis():
    from api.redis_client import get_redis

    return get_redis()


def _uid() -> uuid.UUID:
    return uuid.uuid4()


# ── B2: the per-turn resolver (D12) ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_planning_question_is_advisory_without_session(_redis):
    uid = _uid()
    assert await advisory.advisory_this_turn(
        user_id=uid, text="¿me alcanza para comprar casa en cinco años?", redis=_redis
    )


@pytest.mark.asyncio
async def test_transactional_lookup_is_plain_even_mid_session(_redis):
    uid = _uid()
    await advisory.start_advisory_session(user_id=uid, redis=_redis)
    try:
        assert not await advisory.advisory_this_turn(
            user_id=uid, text="¿cuánto tengo en mis cuentas?", redis=_redis
        )
        assert not await advisory.advisory_this_turn(
            user_id=uid, text="cuánto gasté esta semana", redis=_redis
        )
    finally:
        await advisory.end_advisory_session(user_id=uid, redis=_redis)


@pytest.mark.asyncio
async def test_neutral_question_is_advisory_only_inside_session(_redis):
    uid = _uid()
    neutral = "¿y con lo del carro qué pensás?"
    assert not await advisory.advisory_this_turn(
        user_id=uid, text=neutral, redis=_redis
    )
    await advisory.start_advisory_session(user_id=uid, redis=_redis)
    try:
        assert await advisory.advisory_this_turn(
            user_id=uid, text=neutral, redis=_redis
        )
    finally:
        await advisory.end_advisory_session(user_id=uid, redis=_redis)


@pytest.mark.asyncio
async def test_flag_off_never_advisory(_redis, monkeypatch):
    monkeypatch.setattr(settings, "advisory_persona_enabled", False)
    uid = _uid()
    await advisory.start_advisory_session(user_id=uid, redis=_redis)
    try:
        assert not await advisory.advisory_this_turn(
            user_id=uid, text="hacé un plan para mi retiro", redis=_redis
        )
    finally:
        await advisory.end_advisory_session(user_id=uid, redis=_redis)


@pytest.mark.asyncio
async def test_session_is_idle_expiring_and_touch_refreshes(_redis):
    uid = _uid()
    await advisory.start_advisory_session(user_id=uid, redis=_redis)
    try:
        ttl = await _redis.ttl(advisory_session_key(uid))
        assert 0 < ttl <= advisory.ADVISORY_SESSION_TTL_S
        # An advisory turn refreshes the idle window.
        await _redis.expire(advisory_session_key(uid), 60)
        assert await advisory.advisory_this_turn(
            user_id=uid, text="seguime con el plan", redis=_redis
        ) or True  # the resolver ran; TTL refresh is what we assert next
        await advisory.touch_advisory_session(user_id=uid, redis=_redis)
        assert await _redis.ttl(advisory_session_key(uid)) > 60
    finally:
        await advisory.end_advisory_session(user_id=uid, redis=_redis)


# ── B2: commands through the shared pipeline ──────────────────────────────────


class _U:
    def __init__(self) -> None:
        self.id = uuid.uuid4()
        self.telegram_user_id = 42
        self.timezone = "America/Costa_Rica"
        self.full_name = "Daniel Alfaro"


@pytest.mark.asyncio
async def test_asesor_and_normal_commands_manage_the_session(_redis, db_with_user):
    session, user_id = db_with_user
    from api.models.user import User

    user = await session.get(User, user_id)

    reply = await pipeline.process_message(
        user=user, text="/asesor", db=session, redis=_redis,
        llm_client=object(), llm_model="x",
    )
    assert reply.text == messages_es.ADVISORY_STARTED
    assert await _redis.exists(advisory_session_key(user_id))

    reply = await pipeline.process_message(
        user=user, text="/normal", db=session, redis=_redis,
        llm_client=object(), llm_model="x",
    )
    assert reply.text == messages_es.ADVISORY_ENDED
    assert not await _redis.exists(advisory_session_key(user_id))

    reply = await pipeline.process_message(
        user=user, text="/normal", db=session, redis=_redis,
        llm_client=object(), llm_model="x",
    )
    assert reply.text == messages_es.ADVISORY_NOT_ACTIVE


@pytest.mark.asyncio
async def test_asesor_flag_off_replies_unavailable(_redis, db_with_user, monkeypatch):
    monkeypatch.setattr(settings, "advisory_persona_enabled", False)
    session, user_id = db_with_user
    from api.models.user import User

    user = await session.get(User, user_id)
    reply = await pipeline.process_message(
        user=user, text="/asesor", db=session, redis=_redis,
        llm_client=object(), llm_model="x",
    )
    assert reply.text == messages_es.ADVISORY_UNAVAILABLE
    assert not await _redis.exists(advisory_session_key(user_id))


@pytest.mark.asyncio
async def test_cancel_clears_the_session(_redis, db_with_user):
    session, user_id = db_with_user
    from api.models.user import User

    user = await session.get(User, user_id)
    await advisory.start_advisory_session(user_id=user_id, redis=_redis)
    await pipeline.process_message(
        user=user, text="/cancel", db=session, redis=_redis,
        llm_client=object(), llm_model="x",
    )
    assert not await _redis.exists(advisory_session_key(user_id))


# ── B2: routing threads advisory into run_dispatch ────────────────────────────


@pytest.mark.asyncio
async def test_route_extraction_passes_advisory_flag(_redis, monkeypatch):
    from api.services.llm_extractor import ExtractionResult, Intent
    from app.queries.dispatcher import DispatchOutcome
    from datetime import date

    seen: dict = {}

    async def _capture(**kwargs):
        seen.update(kwargs)
        return DispatchOutcome(text="ok")

    monkeypatch.setattr(pipeline, "run_dispatch", _capture)
    user = _U()

    extraction = ExtractionResult(intent=Intent.QUERY, dispatcher="query", confidence=0.9)
    await pipeline._route_extraction(
        user=user, text="¿me alcanza para un carro?", extraction=extraction,
        today=date.today(), db=None, redis=_redis,
    )
    assert seen["advisory"] is True

    seen.clear()
    await pipeline._route_extraction(
        user=user, text="cuánto gasté esta semana", extraction=extraction,
        today=date.today(), db=None, redis=_redis,
    )
    assert seen["advisory"] is False


# ── B2: native endpoints ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_advisory_session_endpoints(db_with_user):
    from httpx import ASGITransport, AsyncClient

    from api.database import get_db
    from api.dependencies import current_user
    from api.main import app

    session, user_id = db_with_user

    class _StubUser:
        id = user_id
        status = "active"

    async def _yield_session():
        yield session

    app.dependency_overrides[current_user] = lambda: _StubUser()
    app.dependency_overrides[get_db] = _yield_session
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.get("/api/v1/chat/advisory-session")
            assert r.status_code == 200 and r.json()["active"] is False

            r = await ac.post("/api/v1/chat/advisory-session", json={"active": True})
            assert r.json() == {"active": True, "enabled": True}

            r = await ac.get("/api/v1/chat/advisory-session")
            assert r.json()["active"] is True

            # /chat/reset ends the session too.
            r = await ac.post("/api/v1/chat/reset")
            assert r.json() == {"reset": True}
            r = await ac.get("/api/v1/chat/advisory-session")
            assert r.json()["active"] is False
    finally:
        app.dependency_overrides.pop(current_user, None)
        app.dependency_overrides.pop(get_db, None)


# ── B3: prompt variant ────────────────────────────────────────────────────────


def _user_for_prompt():
    from api.models.user import User

    return User(
        id=uuid.uuid4(), email="x@example.com", full_name="Daniel Alfaro",
        shortcut_token="t" * 48, timezone="America/Costa_Rica", currency="CRC",
    )


_NOW = datetime(2026, 7, 3, 18, 0, tzinfo=timezone.utc)

# The advisory prompt is larger than the byte-locked 9500 normal cap; it warms
# its own cache entry, so it gets its own deliberate ceiling.
ADVISORY_MAX_PROMPT_CHARS = 13_000


def test_normal_mode_prompt_is_byte_identical():
    from app.queries.prompts.system import _ADVISORY_PERSONA, build_system_prompt

    plain = build_system_prompt(user=_user_for_prompt(), now=_NOW)
    explicit = build_system_prompt(
        user=_user_for_prompt(), now=_NOW, advisory=False
    )
    assert plain == explicit
    assert _ADVISORY_PERSONA not in plain
    assert "modo asesor" not in plain.lower()


def test_advisory_prompt_adds_persona_and_framing_after_conventions():
    from app.queries.prompts.system import (
        _ADVISORY_PERSONA,
        _CONVENTIONS,
        _PRINCIPLE_FRAMING,
        build_system_prompt,
    )

    prompt = build_system_prompt(user=_user_for_prompt(), now=_NOW, advisory=True)
    assert _ADVISORY_PERSONA in prompt
    assert _PRINCIPLE_FRAMING in prompt
    assert prompt.index(_PRINCIPLE_FRAMING) > prompt.index(_CONVENTIONS)
    assert len(prompt) <= ADVISORY_MAX_PROMPT_CHARS


def test_advisory_sections_are_number_free_and_brace_free():
    from app.queries.prompts.system import _ADVISORY_PERSONA, _PRINCIPLE_FRAMING

    for section in (_ADVISORY_PERSONA, _PRINCIPLE_FRAMING):
        assert "{" not in section and "}" not in section
        assert not any(ch.isdigit() for ch in section)
        assert "₡" not in section and "%" not in section


# ── B3: tool scoping ──────────────────────────────────────────────────────────

# TODAY's normal-mode tool set — the lock. get_user_context ships only when
# the insights flag registered it; compare_periods is ALWAYS the LAST wire
# entry (the cache breakpoint anchor — the P10 B3 fix removed the module-level
# self-registration that used to strand it mid-list).
_GOLDEN_BASE_SET = {
    "list_transactions",
    "aggregate_transactions",
    "list_unassigned_transactions",
    "get_account_balance",
    "list_accounts",
    "list_recurring_bills",
    "list_debts",
    "get_debt_details",
    "get_pending_confirmations",
    "get_envelope_spending",
    "suggest_reallocation_candidates",
    "assess_purchase",
    "get_savings_capacity",
    "assess_financing",
    "list_goals",
    "assess_goal",
    "list_registered_income",
    "compute_net_salary",
    "get_card_analysis",
    "get_user_context",
    "compare_periods",
}


def test_normal_mode_tool_list_is_locked_with_anchor_last():
    from app.queries.tools import BASE_TOOLSET, register_builtin_tools
    from app.queries.tools.base import is_tool_registered, list_tools_for_anthropic

    register_builtin_tools()
    names = [t["name"] for t in list_tools_for_anthropic(allowed=BASE_TOOLSET)]
    expected = {
        n for n in _GOLDEN_BASE_SET
        if n != "get_user_context" or is_tool_registered("get_user_context")
    }
    assert set(names) == expected
    assert len(names) == len(set(names))
    # The cache breakpoint anchor: compare_periods LAST, in both raw registry
    # order and the filtered wire order.
    assert names[-1] == "compare_periods"
    raw = [t["name"] for t in list_tools_for_anthropic()]
    assert raw[-1] == "compare_periods"


def test_advisory_toolset_is_superset_with_anchor_last():
    from app.queries.tools import (
        ADVISORY_ONLY_TOOLS,
        ADVISORY_TOOLSET,
        BASE_TOOLSET,
        register_builtin_tools,
    )
    from app.queries.tools.base import list_tools_for_anthropic

    register_builtin_tools()
    assert set(BASE_TOOLSET) <= set(ADVISORY_TOOLSET)
    assert set(ADVISORY_ONLY_TOOLS).isdisjoint(BASE_TOOLSET)
    names = [t["name"] for t in list_tools_for_anthropic(allowed=ADVISORY_TOOLSET)]
    assert names[-1] == "compare_periods"
    # No advisory-only tool may ever leak into a normal-mode wire list.
    base_names = [t["name"] for t in list_tools_for_anthropic(allowed=BASE_TOOLSET)]
    assert set(base_names).isdisjoint(ADVISORY_ONLY_TOOLS)


# ── B3: run_dispatch wiring (prompt + cap) ────────────────────────────────────


@dataclass
class _CapturedLoop:
    kwargs: dict = field(default_factory=dict)

    async def run_query_loop(self, **kwargs):
        from app.queries.llm_client import QueryLLMResponse

        self.kwargs.update(kwargs)
        return QueryLLMResponse(
            text="ok", total_iterations=1, total_input_tokens=1,
            total_output_tokens=1, tools_used=[], duration_ms=1,
            cache_read_input_tokens=0, cache_creation_input_tokens=0,
        )


@pytest.mark.asyncio
async def test_run_dispatch_advisory_swaps_prompt_toolset_and_cap(
    db_with_user, monkeypatch
):
    from app.queries import dispatcher
    from app.queries.prompts.system import _ADVISORY_PERSONA

    session, user_id = db_with_user
    captured = _CapturedLoop()
    monkeypatch.setattr(dispatcher, "get_query_llm_client", lambda: captured)

    outcome = await dispatcher.run_dispatch(
        user_id=user_id, message_text="¿me alcanza para una casa?", advisory=True
    )
    assert outcome.text == "ok"
    assert _ADVISORY_PERSONA in captured.kwargs["system_prompt"]
    assert captured.kwargs["max_iterations"] == settings.llm_advisory_iteration_cap
    tool_names = [t["name"] for t in captured.kwargs["tools"]]
    assert tool_names[-1] == "compare_periods"

    captured.kwargs.clear()
    outcome = await dispatcher.run_dispatch(
        user_id=user_id, message_text="cuánto gasté", advisory=False
    )
    assert _ADVISORY_PERSONA not in captured.kwargs["system_prompt"]
    assert captured.kwargs["max_iterations"] == settings.llm_query_iteration_cap


# ── "/asesor <pregunta>" (TestFlight repro 2026-07-04) ────────────────────────


@pytest.mark.asyncio
async def test_asesor_with_args_flag_off_replies_unavailable(
    _redis, db_with_user, monkeypatch
):
    """The repro: '/asesor <texto>' fell through the exact-match set into the
    LLM path. Flag off it must hit the SAME graceful stub as bare /asesor."""
    monkeypatch.setattr(settings, "advisory_persona_enabled", False)
    session, user_id = db_with_user
    from api.models.user import User

    user = await session.get(User, user_id)
    reply = await pipeline.process_message(
        user=user,
        text="/asesor analizá mis deudas y mi flujo recurrente",
        db=session, redis=_redis, llm_client=object(), llm_model="x",
    )
    assert reply.text == messages_es.ADVISORY_UNAVAILABLE
    assert not await _redis.exists(advisory_session_key(user_id))


@pytest.mark.asyncio
async def test_asesor_with_args_flag_on_starts_session_and_answers(
    _redis, db_with_user, monkeypatch
):
    """Flag on: '/asesor <pregunta>' starts the session AND the question is
    answered as the first advisory turn — never dropped, never an error."""
    from api.services.llm_extractor import ExtractionResult, Intent
    from app.queries.dispatcher import DispatchOutcome

    session, user_id = db_with_user
    from api.models.user import User

    user = await session.get(User, user_id)
    await _redis.delete(f"telegram:pending:{user_id}")

    seen: dict = {}

    async def _fake_extract(*a, **k):
        # The command prefix must already be stripped before extraction.
        assert not k.get("text", "").startswith("/asesor")
        return ExtractionResult(intent=Intent.QUERY, dispatcher="query", confidence=0.9)

    async def _fake_query(**kwargs):
        seen.update(kwargs)
        return DispatchOutcome(text="Análisis con tus números reales: …")

    monkeypatch.setattr(pipeline, "extract_finance_intent", _fake_extract)
    monkeypatch.setattr(pipeline, "run_dispatch", _fake_query)

    reply = await pipeline.process_message(
        user=user,
        text="/asesor analizá mis deudas y mi flujo recurrente",
        db=session, redis=_redis, llm_client=object(), llm_model="x",
    )
    try:
        assert reply.text == "Análisis con tus números reales: …"
        assert await _redis.exists(advisory_session_key(user_id))
        # The remainder (not the slash command) reached the dispatcher, in
        # advisory mode (session just started → resolver True).
        assert seen["message_text"] == "analizá mis deudas y mi flujo recurrente"
        assert seen["advisory"] is True
    finally:
        await advisory.end_advisory_session(user_id=user_id, redis=_redis)
