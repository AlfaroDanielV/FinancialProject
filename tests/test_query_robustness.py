"""Query-path robustness — the agent must NEVER surface a raw 500.

Root cause (2026-06-13 operator report: "Hubo un error. Intentá de nuevo."
on "¿cómo salgo de deudas?"): the query dispatcher mapped *known* LLM
failures to Spanish copy, but anything else — a post-success side-effect
hiccup (history/Redis, the attach nudge) or `build_system_prompt` — escaped
`run_dispatch`, then `_route_extraction` (no try), then `process_message`,
landing as a 500 the native chat shows as "Hubo un error".

These lock the safety net: a thrown side effect still returns the LLM's
answer; an unanticipated failure returns a handled Spanish message; and the
pipeline's `_route_extraction` wrap is the last line of defense.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from app.queries import dispatcher
from app.queries.llm_client import AnthropicQueryClient


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
    """Always returns a direct text answer (no tool use) on the first call."""

    async def create(self, **kwargs):
        return _Response(content=[_Text(text="Tu plan para salir de deudas: …")])


class _FakeAnthropic:
    def __init__(self) -> None:
        self.messages = _FakeMessages()


class _SessionContext:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return None


@pytest.fixture(autouse=True)
def _fake_llm():
    dispatcher.set_query_llm_client(
        AnthropicQueryClient(api_key="", anthropic_client=_FakeAnthropic())
    )
    yield
    dispatcher.set_query_llm_client(None)


@pytest.mark.asyncio
async def test_post_success_side_effect_failure_still_returns_answer(
    db_with_user, monkeypatch
):
    """If the attach-nudge (or history/telemetry) raises AFTER the LLM
    answered, the user still gets the answer — not an error."""
    session, user_id = db_with_user
    monkeypatch.setattr(
        dispatcher, "AsyncSessionLocal", lambda: _SessionContext(session)
    )

    async def _boom(*a, **k):
        raise RuntimeError("redis hiccup in attach suggestion")

    monkeypatch.setattr(dispatcher, "_maybe_append_attach_suggestion", _boom)

    text = await dispatcher.handle(
        user_id=user_id, message_text="cómo salgo de deudas", telegram_chat_id=1
    )
    assert text == "Tu plan para salir de deudas: …"


@pytest.mark.asyncio
async def test_build_system_prompt_failure_returns_handled_message(
    db_with_user, monkeypatch
):
    """An unanticipated failure before the LLM call returns a handled
    Spanish message (the generic _MSG), never a propagated exception."""
    session, user_id = db_with_user
    monkeypatch.setattr(
        dispatcher, "AsyncSessionLocal", lambda: _SessionContext(session)
    )

    def _boom(*a, **k):
        raise ValueError("insights blew up building the prompt")

    monkeypatch.setattr(dispatcher, "build_system_prompt", _boom)

    # Must NOT raise — must return a string the user can read.
    outcome = await dispatcher.run_dispatch(
        user_id=user_id, message_text="cómo salgo de deudas", telegram_chat_id=1
    )
    assert isinstance(outcome.text, str) and outcome.text
    assert outcome.error_category == "unhandled"
    # The generic copy, not a stack trace.
    assert "rompió" in outcome.text.lower() or "admin" in outcome.text.lower()


@pytest.mark.asyncio
async def test_route_extraction_wraps_dispatcher_crash(monkeypatch):
    """Last line of defense: if the dispatcher itself raises, the pipeline
    returns a handled BotReply, not a 500."""
    from bot import pipeline
    from api.services.llm_extractor import ExtractionResult, Intent

    async def _crash(*a, **k):
        raise RuntimeError("totally unexpected")

    monkeypatch.setattr(pipeline, "query_dispatcher_handle", _crash)

    class _U:
        id = __import__("uuid").uuid4()
        telegram_user_id = 42

    extraction = ExtractionResult(intent=Intent.QUERY, dispatcher="query", confidence=0.9)
    reply = await pipeline._route_extraction(
        user=_U(), text="cómo salgo de deudas", extraction=extraction,
        today=__import__("datetime").date.today(), db=None, redis=None,
    )
    assert isinstance(reply.text, str) and reply.text  # handled, no raise


@pytest.mark.asyncio
async def test_bare_yes_without_pending_falls_through_to_query(
    db_with_user, monkeypatch
):
    """The operator's second symptom: answering "Sí" to a question the query
    dispatcher asked must NOT dead-end at "No tengo nada pendiente por
    confirmar" — with no pending write, it flows to extract → dispatch so the
    LLM answers with its history."""
    from bot import pipeline, messages_es
    from api.redis_client import get_redis
    from api.services.llm_extractor import ExtractionResult, Intent

    session, user_id = db_with_user
    from api.models.user import User

    user = await session.get(User, user_id)
    await get_redis().delete(  # ensure no stale pending for this user
        f"telegram:pending:{user_id}"
    )

    async def _fake_extract(*a, **k):
        return ExtractionResult(intent=Intent.QUERY, dispatcher="query", confidence=0.9)

    async def _fake_query(*a, **k):
        return "Tu capacidad de ahorro es ₡X."

    monkeypatch.setattr(pipeline, "extract_finance_intent", _fake_extract)
    monkeypatch.setattr(pipeline, "query_dispatcher_handle", _fake_query)

    reply = await pipeline.process_message(
        user=user, text="Sí", db=session, redis=get_redis(),
        llm_client=object(), llm_model="x",
    )
    assert reply.text == "Tu capacidad de ahorro es ₡X."
    assert reply.text != messages_es.PENDING_NONE_TO_CONFIRM
