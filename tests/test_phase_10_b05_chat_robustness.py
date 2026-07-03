"""P10 B0.5 — compound-intent + failure-class transparency (checks C1–C5).

Locks the fixes for the 2026-07-02 compound-message bug: "Ganó 2000000 al mes
analizas mis expenses deudas y saldos de crédito dime si me alcanza con mi
salario" produced the mobile non-2xx banner. Remediations under test:

- R1: serialization inside the endpoint guard + chat-route validation handlers
      (request + response validation degrade to a handled 200 chat body).
- R2: the write/control branch of _route_extraction is netted symmetrically
      with the query branch.
- R3: Intent.QUERY on the write dispatcher re-routes to the query dispatcher
      (RerouteToQuery) instead of raising RuntimeError.
- R4: ExtractionResult couples intent↔dispatcher via a model_validator.
- R5: BotReply/ChatMessageResponse carry a machine-readable error_class.

Prereqs: `docker compose up -d db redis` + `alembic upgrade head`.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pytest

from api.services.llm_extractor import ExtractionResult, Intent
from api.services.llm_extractor.client import FixtureLLMClient, RecordedLLMResponse
from app.queries import dispatcher
from app.queries.llm_client import AnthropicQueryClient
from bot import messages_es, pipeline

COMPOUND_MESSAGE = (
    "Ganó 2000000 al mes analizas mis expenses deudas y saldos de crédito "
    "dime si me alcanza con mi salario"
)

# The mis-tagged extraction the bug produced: analytical intent, write routing.
MISTAGGED_COMPOUND = RecordedLLMResponse(
    tool_input={
        "intent": "query",
        "dispatcher": "write",  # ← the mis-tag; the model_validator must fix it
        "confidence": 0.85,
        "raw_notes": "mensaje compuesto",
    }
)


# ── fake query-LLM plumbing (mirrors test_query_robustness.py) ────────────────


@dataclass
class _Usage:
    input_tokens: int = 5
    output_tokens: int = 4


@dataclass
class _Text:
    text: str
    type: str = "text"


@dataclass
class _Response:
    content: list[Any]
    usage: _Usage = field(default_factory=_Usage)
    stop_reason: str = "end_turn"


class _FakeMessages:
    async def create(self, **kwargs):
        return _Response(content=[_Text(text="Con tu salario y tus deudas: …")])


class _FakeAnthropic:
    def __init__(self) -> None:
        self.messages = _FakeMessages()


@pytest.fixture(autouse=True)
def _fake_query_llm():
    dispatcher.set_query_llm_client(
        AnthropicQueryClient(api_key="", anthropic_client=_FakeAnthropic())
    )
    yield
    dispatcher.set_query_llm_client(None)


class _U:
    """Minimal user stand-in for pipeline unit paths."""

    def __init__(self) -> None:
        self.id = uuid.uuid4()
        self.telegram_user_id = 42
        self.timezone = "America/Costa_Rica"


# ── C2 — intent↔dispatcher consistency (R4) ──────────────────────────────────


def test_c2_query_intent_forces_query_dispatcher():
    r = ExtractionResult(intent=Intent.QUERY, dispatcher="write", confidence=0.9)
    assert r.dispatcher == "query"


def test_c2_write_intents_force_write_dispatcher():
    for intent in (
        Intent.LOG_EXPENSE,
        Intent.LOG_INCOME,
        Intent.LOG_TRANSFER,
        Intent.CREATE_GOAL,
        Intent.MARK_BILL_PAID,
        Intent.RECORD_DEBT_PAYMENT,
        Intent.SET_BALANCE,
    ):
        r = ExtractionResult(intent=intent, dispatcher="query", confidence=0.9)
        assert r.dispatcher == "write", intent


def test_c2_control_intents_force_control_dispatcher():
    for intent in (
        Intent.CONFIRM_YES,
        Intent.CONFIRM_NO,
        Intent.UNDO,
        Intent.HELP,
        Intent.UNKNOWN,
    ):
        r = ExtractionResult(intent=intent, dispatcher="write", confidence=0.9)
        assert r.dispatcher == "control", intent


def test_c2_consistent_pairs_pass_through_unchanged():
    r = ExtractionResult(
        intent=Intent.LOG_EXPENSE, dispatcher="write", amount=5000, confidence=0.95
    )
    assert r.dispatcher == "write"
    q = ExtractionResult(intent=Intent.QUERY, dispatcher="query", confidence=0.95)
    assert q.dispatcher == "query"


# ── R3 — Intent.QUERY on the write dispatcher re-routes, never raises ────────


@pytest.mark.asyncio
async def test_r3_apply_decision_reroutes_to_query(monkeypatch):
    from api.redis_client import get_redis
    from api.services.telegram_dispatcher import RerouteToQuery

    async def _fake_query(*a, **k):
        return dispatcher.DispatchOutcome(text="Respuesta analítica.")

    monkeypatch.setattr(pipeline, "run_dispatch", _fake_query)
    reply = await pipeline._apply_decision(
        user=_U(),
        decision=RerouteToQuery(),
        db=None,
        redis=get_redis(),
        source_text=COMPOUND_MESSAGE,
    )
    assert reply.text == "Respuesta analítica."
    assert reply.error_class is None


@pytest.mark.asyncio
async def test_r3_reroute_failure_still_handled(monkeypatch):
    from api.redis_client import get_redis
    from api.services.telegram_dispatcher import RerouteToQuery

    async def _boom(*a, **k):
        raise RuntimeError("query loop exploded")

    monkeypatch.setattr(pipeline, "run_dispatch", _boom)
    reply = await pipeline._apply_decision(
        user=_U(),
        decision=RerouteToQuery(),
        db=None,
        redis=get_redis(),
        source_text=COMPOUND_MESSAGE,
    )
    assert isinstance(reply.text, str) and reply.text
    assert reply.error_class == "system"


# ── C3 — symmetric write/control net (R2) ─────────────────────────────────────


@pytest.mark.asyncio
async def test_c3_write_branch_crash_is_netted(monkeypatch):
    async def _crash(*a, **k):
        raise RuntimeError("dispatcher exploded")

    monkeypatch.setattr(pipeline, "dispatch", _crash)
    extraction = ExtractionResult(
        intent=Intent.LOG_EXPENSE, dispatcher="write", amount=5000, confidence=0.9
    )
    reply = await pipeline._route_extraction(
        user=_U(),
        text="gasté 5000 en el super",
        extraction=extraction,
        today=date.today(),
        db=None,
        redis=None,
    )
    assert reply.text == messages_es.CHAT_UNEXPECTED_ERROR
    assert reply.error_class == "system"


# ── C4 — response-build safety ────────────────────────────────────────────────


def test_c4_fully_populated_botreply_serializes():
    from api.routers.chat import _build_response

    reply = pipeline.BotReply(
        text="ok",
        buttons=[pipeline.ConfirmButton(label="Sí ✅", callback_data="pending:x:yes")],
        url_buttons=[pipeline.UrlButton(label="Abrir", url="https://example.com")],
        open_screen=pipeline.OpenScreen(
            screen="debt_create", prefill={"debt_name": "carro", "amount": 5_000_000}
        ),
        error_class=None,
    )
    resp = _build_response(reply)
    dumped = resp.model_dump()
    assert dumped["reply_text"] == "ok"
    assert dumped["buttons"][0]["label"] == "Sí ✅"
    assert dumped["url_buttons"][0]["url"] == "https://example.com"
    assert dumped["open_screen"]["screen"] == "debt_create"
    assert dumped["error_class"] is None


@pytest.mark.asyncio
async def test_c4_build_response_failure_degrades_to_handled_200(
    db_with_user, monkeypatch
):
    """R1: serialization now lives INSIDE the endpoint guard."""
    from httpx import ASGITransport, AsyncClient

    import api.routers.chat as chat_router
    from api.database import get_db
    from api.dependencies import current_user
    from api.main import app

    session, user_id = db_with_user

    class _StubUser:
        id = user_id
        status = "active"

    async def _yield_session():
        yield session

    async def _ok(*a, **k):
        return pipeline.BotReply(text="fine")

    def _explode(reply):
        raise TypeError("unserializable reply")

    app.dependency_overrides[current_user] = lambda: _StubUser()
    app.dependency_overrides[get_db] = _yield_session
    monkeypatch.setattr(chat_router, "process_message", _ok)
    monkeypatch.setattr(chat_router, "_build_response", _explode)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post("/api/v1/chat/message", json={"text": "hola"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["reply_text"] == messages_es.CHAT_UNEXPECTED_ERROR
        assert body["error_class"] == "system"
    finally:
        app.dependency_overrides.pop(current_user, None)
        app.dependency_overrides.pop(get_db, None)


@pytest.mark.asyncio
async def test_c4_request_validation_error_returns_handled_200(db_with_user):
    """A malformed chat body (empty text) degrades to the handled 200 chat
    body on /api/v1/chat/* — never a bare 422 the client shows as a banner."""
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
            resp = await ac.post("/api/v1/chat/message", json={"text": ""})
        assert resp.status_code == 200, resp.text
        assert resp.json()["error_class"] == "system"
    finally:
        app.dependency_overrides.pop(current_user, None)
        app.dependency_overrides.pop(get_db, None)


@pytest.mark.asyncio
async def test_c4_non_chat_request_validation_stays_422(db_with_user):
    """The chat-route handler must NOT relax validation anywhere else."""
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
            # accounts POST with a garbage body → normal 422 contract intact
            resp = await ac.post("/api/v1/accounts", json={"name": 123456})
        assert resp.status_code == 422, resp.text
    finally:
        app.dependency_overrides.pop(current_user, None)
        app.dependency_overrides.pop(get_db, None)


# ── C1 — the verbatim compound message end-to-end ─────────────────────────────


@pytest.mark.asyncio
async def test_c1_compound_message_returns_answer_not_error(db_with_user):
    """The verbatim bug message, mis-tagged by the extractor exactly as on
    2026-07-02, must come back HTTP 200 with the ANALYTICAL answer."""
    from httpx import ASGITransport, AsyncClient

    from api.database import get_db
    from api.dependencies import current_user
    from api.main import app
    from api.models.user import User
    from api.redis_client import get_redis
    from bot.app import set_llm_client

    session, user_id = db_with_user
    user = await session.get(User, user_id)
    assert user is not None
    await get_redis().delete(f"telegram:pending:{user_id}")

    async def _yield_session():
        yield session

    app.dependency_overrides[current_user] = lambda: user
    app.dependency_overrides[get_db] = _yield_session
    set_llm_client(FixtureLLMClient(default=MISTAGGED_COMPOUND))
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/chat/message", json={"text": COMPOUND_MESSAGE}
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # The analytical answer from the (fake) query dispatcher — not the
        # RuntimeError, not the generic error copy.
        assert body["reply_text"] == "Con tu salario y tus deudas: …"
        assert body["error_class"] is None
    finally:
        set_llm_client(None)
        app.dependency_overrides.pop(current_user, None)
        app.dependency_overrides.pop(get_db, None)


@pytest.mark.asyncio
async def test_c1_compound_message_with_query_crash_still_200(
    db_with_user, monkeypatch
):
    """Even if the query dispatcher then fails hard, the compound message
    yields a handled 200 with a distinguishable system class — never a 5xx."""
    from httpx import ASGITransport, AsyncClient

    from api.database import get_db
    from api.dependencies import current_user
    from api.main import app
    from api.models.user import User
    from api.redis_client import get_redis
    from bot.app import set_llm_client

    session, user_id = db_with_user
    user = await session.get(User, user_id)
    await get_redis().delete(f"telegram:pending:{user_id}")

    async def _yield_session():
        yield session

    async def _boom(*a, **k):
        raise RuntimeError("query dispatcher exploded")

    app.dependency_overrides[current_user] = lambda: user
    app.dependency_overrides[get_db] = _yield_session
    set_llm_client(FixtureLLMClient(default=MISTAGGED_COMPOUND))
    monkeypatch.setattr(pipeline, "run_dispatch", _boom)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/chat/message", json={"text": COMPOUND_MESSAGE}
            )
        assert resp.status_code == 200, resp.text
        assert resp.json()["error_class"] == "system"
    finally:
        set_llm_client(None)
        app.dependency_overrides.pop(current_user, None)
        app.dependency_overrides.pop(get_db, None)


# ── C5 — failure-class distinctness ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_c5_failure_classes_are_distinct(db_with_user, monkeypatch):
    """understanding / budget / transient / system carry four distinct
    user-facing strings AND four distinct machine-readable classes."""
    from api.models.user import User
    from api.redis_client import get_redis
    from app.queries.delivery import BudgetExceeded

    session, user_id = db_with_user
    user = await session.get(User, user_id)
    redis = get_redis()
    await redis.delete(f"telegram:pending:{user_id}")

    replies: dict[str, pipeline.BotReply] = {}

    # understanding — extractor blows up
    async def _extract_boom(*a, **k):
        raise RuntimeError("anthropic overloaded")

    monkeypatch.setattr(pipeline, "extract_finance_intent", _extract_boom)
    replies["understanding"] = await pipeline.process_message(
        user=user, text="mensaje raro", db=session, redis=redis,
        llm_client=object(), llm_model="x",
    )

    # budget — daily token budget exhausted
    async def _budget_boom(*a, **k):
        raise BudgetExceeded("daily budget")

    monkeypatch.setattr(pipeline, "assert_within_budget", _budget_boom)
    replies["budget"] = await pipeline.process_message(
        user=user, text="cuánto gasté", db=session, redis=redis,
        llm_client=object(), llm_model="x",
    )
    monkeypatch.setattr(
        pipeline, "assert_within_budget", lambda **k: _noop_budget()
    )

    # system — write dispatcher crash
    async def _extract_write(*a, **k):
        return ExtractionResult(
            intent=Intent.LOG_EXPENSE, dispatcher="write", amount=1, confidence=0.9
        )

    async def _dispatch_boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(pipeline, "extract_finance_intent", _extract_write)
    monkeypatch.setattr(pipeline, "dispatch", _dispatch_boom)
    replies["system"] = await pipeline.process_message(
        user=user, text="gasté 1", db=session, redis=redis,
        llm_client=object(), llm_model="x",
    )

    # transient — rate limit
    async def _rate_denied(*a, **k):
        return False

    monkeypatch.setattr(pipeline, "check_and_increment_rate", _rate_denied)
    replies["transient"] = await pipeline.process_message(
        user=user, text="hola", db=session, redis=redis,
        llm_client=object(), llm_model="x",
    )

    classes = {k: r.error_class for k, r in replies.items()}
    assert classes == {
        "understanding": "understanding",
        "budget": "budget",
        "system": "system",
        "transient": "transient",
    }
    texts = [r.text for r in replies.values()]
    assert len(set(texts)) == 4, texts


async def _noop_budget():
    return 0
