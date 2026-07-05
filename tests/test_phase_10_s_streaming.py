"""P10.S — token-streaming chat (SSE) tests.

Three concerns:

1. `AnthropicQueryClient` streaming branch (no DB) — `on_event=None` is
   byte-identical to the blocking path (`.create`, never `.stream`); with a
   callback it emits `Delta`s in order and a `ToolBatch` between tool turns,
   and the request BODY is identical to the blocking call (prompt-cache lock).
2. The SSE route (`POST /chat/message/stream`, httpx ASGI) — flag-gated 404,
   event order `connected → started → delta* → final` for a query turn, and a
   lone `final` (no deltas) for a deterministic write turn.
3. Contract — `process_message(on_event=...)` defaults to None and the Telegram
   call sites never pass it (so Telegram stays byte-identical, un-streamed).
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from api.config import settings
from app.queries import dispatcher
from app.queries.llm_client import AnthropicQueryClient
from app.queries.stream_events import Delta, ToolBatch
from app.queries.tools.base import _reset_registry_for_tests


# ── Fake Anthropic SDK shapes (mirror the 0.39.0 surface used by the code) ───


@dataclass
class _Usage:
    input_tokens: int = 100
    output_tokens: int = 30
    cache_read_input_tokens: int = 7
    cache_creation_input_tokens: int = 11


@dataclass
class _Text:
    text: str
    type: str = "text"


@dataclass
class _ToolUse:
    name: str = "get_user_context"
    id: str = "tu_1"
    input: dict = field(default_factory=dict)
    type: str = "tool_use"


@dataclass
class _Response:
    content: list[Any]
    usage: _Usage = field(default_factory=_Usage)
    stop_reason: str = "end_turn"


def _turn_text(resp: _Response) -> str:
    return "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")


def _chunk(full: str) -> list[str]:
    # Preserve content exactly; "".join(chunks) == full.
    if not full:
        return []
    return re.findall(r"\S+\s*|\s+", full) or [full]


class _FakeStream:
    def __init__(self, resp: _Response):
        self._resp = resp

    @property
    def text_stream(self):
        async def _gen():
            for c in _chunk(_turn_text(self._resp)):
                yield c

        return _gen()

    async def get_final_message(self):
        return self._resp


class _FakeStreamCM:
    def __init__(self, resp: _Response):
        self._resp = resp

    async def __aenter__(self):
        return _FakeStream(self._resp)

    async def __aexit__(self, *exc):
        return False


class _FakeMessages:
    """Scripted; one `_Response` consumed per model turn, via create() OR
    stream() (both advance the same cursor)."""

    def __init__(self, script: list[_Response]):
        self._script = script
        self._i = 0
        self.create_calls: list[dict] = []
        self.stream_calls: list[dict] = []

    def _next(self) -> _Response:
        resp = self._script[self._i]
        self._i += 1
        return resp

    async def create(self, **kwargs):
        self.create_calls.append(kwargs)
        return self._next()

    def stream(self, **kwargs):  # plain def, like the real SDK (returns a CM)
        self.stream_calls.append(kwargs)
        return _FakeStreamCM(self._next())


class _FakeAnthropic:
    def __init__(self, script: list[_Response]):
        self.messages = _FakeMessages(script)


def _client(script: list[_Response]) -> AnthropicQueryClient:
    return AnthropicQueryClient(api_key="", anthropic_client=_FakeAnthropic(script))


async def _noop_tool(name: str, args: dict, user_id: uuid.UUID) -> str:
    return "ok"


def _collector():
    """Returns (events_list, async_callback) — on_event must be awaitable."""
    events: list[Any] = []

    async def _cb(ev):
        events.append(ev)

    return events, _cb


async def _anoop(ev):  # async no-op on_event
    return None


_LOOP_KW = dict(
    system_prompt="SYS",
    user_message="¿cuánto gasté?",
    user_id=uuid.uuid4(),
    tools=[{"name": "get_user_context", "description": "d", "input_schema": {}}],
    tool_executor=_noop_tool,
    model="claude-x",
)


# ── 1. Streaming client behavior (no DB) ─────────────────────────────────────


@pytest.mark.asyncio
async def test_on_event_none_uses_create_not_stream():
    """on_event=None → the blocking create() path, byte-identical old behavior."""
    client = _client([_Response(content=[_Text("Gastaste ₡5.000.")])])
    resp = await client.run_query_loop(**_LOOP_KW)  # no on_event
    assert resp.text == "Gastaste ₡5.000."
    assert len(client._client.messages.create_calls) == 1
    assert client._client.messages.stream_calls == []


@pytest.mark.asyncio
async def test_streaming_emits_deltas_in_order_and_returns_final():
    client = _client([_Response(content=[_Text("Gastaste ₡5.000 esta semana.")])])
    events, cb = _collector()
    resp = await client.run_query_loop(**_LOOP_KW, on_event=cb)
    # Streamed, not blocking.
    assert len(client._client.messages.stream_calls) == 1
    assert client._client.messages.create_calls == []
    deltas = [e for e in events if isinstance(e, Delta)]
    assert deltas, "expected Delta events"
    assert "".join(d.text for d in deltas) == "Gastaste ₡5.000 esta semana."
    # The returned (canonical) text is authoritative.
    assert resp.text == "Gastaste ₡5.000 esta semana."


@pytest.mark.asyncio
async def test_streaming_emits_toolbatch_between_iterations_and_usage_parity():
    script = [
        _Response(content=[_ToolUse(name="get_user_context")]),  # tool turn
        _Response(content=[_Text("Listo, ₡85.000.")]),  # terminal
    ]
    # Non-stream reference run.
    ref = await _client([_Response(content=c.content) for c in script]).run_query_loop(
        **_LOOP_KW
    )
    # Streaming run.
    events, cb = _collector()
    streamed = await _client(
        [_Response(content=c.content) for c in script]
    ).run_query_loop(**_LOOP_KW, on_event=cb)

    assert streamed.text == ref.text == "Listo, ₡85.000."
    # A ToolBatch fired for the tool turn, BEFORE the answer deltas.
    kinds = [type(e).__name__ for e in events]
    assert "ToolBatch" in kinds
    tb = next(e for e in events if isinstance(e, ToolBatch))
    assert tb.tool_names == ["get_user_context"]
    assert kinds.index("ToolBatch") < kinds.index("Delta")
    # Usage totals identical across modes (same script, same per-turn usage).
    assert streamed.total_input_tokens == ref.total_input_tokens
    assert streamed.total_output_tokens == ref.total_output_tokens
    assert streamed.cache_read_input_tokens == ref.cache_read_input_tokens
    assert streamed.cache_creation_input_tokens == ref.cache_creation_input_tokens


@pytest.mark.asyncio
async def test_stream_and_create_request_bodies_identical_prompt_cache_lock():
    """The cache-relevant request body is byte-identical between the blocking
    and streaming calls — only the transport `timeout` (not part of the request
    body / cache key) differs. Guards prompt-cache fragmentation."""
    blocking = _client([_Response(content=[_Text("x")])])
    await blocking.run_query_loop(**_LOOP_KW)
    streaming = _client([_Response(content=[_Text("x")])])
    await streaming.run_query_loop(**_LOOP_KW, on_event=_anoop)

    create_kw = dict(blocking._client.messages.create_calls[0])
    stream_kw = dict(streaming._client.messages.stream_calls[0])
    create_kw.pop("timeout", None)
    stream_kw.pop("timeout", None)
    assert create_kw == stream_kw
    # Sanity: the cache breakpoints are where we expect.
    assert create_kw["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert create_kw["tools"][-1]["cache_control"] == {"type": "ephemeral"}
    # And the streaming call did raise the SDK timeout above the blocking one.
    assert streaming._client.messages.stream_calls[0]["timeout"] == 90.0


# ── 2. SSE route (DB) ────────────────────────────────────────────────────────


def _override_db(session):
    from api.database import get_db
    from api.main import app

    async def _yield():
        yield session

    app.dependency_overrides[get_db] = _yield


def _clear_db_override():
    from api.database import get_db
    from api.main import app

    app.dependency_overrides.pop(get_db, None)


async def _bearer(ac: AsyncClient, session, user_id) -> str:
    from api.services.auth.magic_link import generate_link

    link = await generate_link(session, user_id=user_id, purpose="onboarding")
    exchange = await ac.post(
        "/api/v1/auth/magic-link/exchange", json={"token": link.raw_token}
    )
    return exchange.json()["token"]


def _parse_sse(body: str) -> list[tuple[str, str]]:
    """Return [(event, data)] for real event frames, skipping ':' comments."""
    out: list[tuple[str, str]] = []
    for frame in body.split("\n\n"):
        frame = frame.strip("\n")
        if not frame or frame.startswith(":"):
            continue
        event = None
        data = None
        for line in frame.split("\n"):
            if line.startswith("event:"):
                event = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data = line[len("data:") :].strip()
        if event is not None:
            out.append((event, data or ""))
    return out


@pytest.mark.asyncio
async def test_stream_endpoint_404_when_flag_off(db_with_user, monkeypatch):
    session, user_id = db_with_user
    monkeypatch.setattr(settings, "chat_streaming_enabled", False)
    _override_db(session)
    try:
        transport = ASGITransport(app=__import__("api.main", fromlist=["app"]).app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            token = await _bearer(ac, session, user_id)
            resp = await ac.post(
                "/api/v1/chat/message/stream",
                json={"text": "hola"},
                headers={"Authorization": f"Bearer {token}"},
                cookies={},
            )
        assert resp.status_code == 404
    finally:
        _clear_db_override()


@pytest.mark.asyncio
async def test_stream_endpoint_query_streams_deltas_then_final(
    db_with_user, monkeypatch
):
    from api.services.llm_extractor import FixtureLLMClient
    from bot.app import set_llm_client
    from tests.fixtures.extractor_responses import WEEKLY_BALANCE_QUERY

    session, user_id = db_with_user
    monkeypatch.setattr(settings, "chat_streaming_enabled", True)
    _reset_registry_for_tests()

    # Extractor routes the message to the query dispatcher.
    set_llm_client(FixtureLLMClient(default=WEEKLY_BALANCE_QUERY))
    # The query LLM streams a two-word answer.
    dispatcher.set_query_llm_client(
        _client([_Response(content=[_Text("Gastaste ₡5.000.")])])
    )
    _override_db(session)
    try:
        transport = ASGITransport(app=__import__("api.main", fromlist=["app"]).app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            token = await _bearer(ac, session, user_id)
            resp = await ac.post(
                "/api/v1/chat/message/stream",
                json={"text": "¿cuánto gasté esta semana?"},
                headers={"Authorization": f"Bearer {token}"},
                cookies={},
            )
        assert resp.status_code == 200, resp.text
        assert resp.headers["content-type"].startswith("text/event-stream")
        frames = _parse_sse(resp.text)
        events = [e for e, _ in frames]
        assert events[0] == "started"
        assert "delta" in events
        assert events[-1] == "final"
        # Deltas concatenate to the answer; final carries the canonical text.
        deltas = [json.loads(d)["text"] for e, d in frames if e == "delta"]
        assert "".join(deltas) == "Gastaste ₡5.000."
        final = json.loads(next(d for e, d in frames if e == "final"))
        assert final["reply_text"] == "Gastaste ₡5.000."
        assert final["error_class"] is None
    finally:
        dispatcher.set_query_llm_client(None)
        _clear_db_override()


@pytest.mark.asyncio
async def test_stream_endpoint_write_turn_is_lone_final(db_with_user, monkeypatch):
    """A deterministic write proposal emits NO deltas — just one terminal
    `final` carrying the confirm buttons."""
    from api.models.account import Account
    from api.services.llm_extractor import FixtureLLMClient
    from bot.app import set_llm_client
    from tests.fixtures.extractor_responses import BASIC_EXPENSE_CRC

    session, user_id = db_with_user
    monkeypatch.setattr(settings, "chat_streaming_enabled", True)

    session.add(
        Account(
            user_id=user_id,
            name="BAC",
            account_type="checking",
            currency="CRC",
            initial_balance=0,
            is_active=True,
        )
    )
    await session.commit()
    set_llm_client(FixtureLLMClient(default=BASIC_EXPENSE_CRC))
    _override_db(session)
    try:
        transport = ASGITransport(app=__import__("api.main", fromlist=["app"]).app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            token = await _bearer(ac, session, user_id)
            resp = await ac.post(
                "/api/v1/chat/message/stream",
                json={"text": "gasté 5000 colones en el super"},
                headers={"Authorization": f"Bearer {token}"},
                cookies={},
            )
        assert resp.status_code == 200, resp.text
        frames = _parse_sse(resp.text)
        events = [e for e, _ in frames]
        assert "delta" not in events  # a write turn streams no tokens
        assert events[-1] == "final"
        final = json.loads(next(d for e, d in frames if e == "final"))
        assert len(final["buttons"]) == 3  # Sí / No / Editar
    finally:
        _clear_db_override()


# ── 3. Contract ──────────────────────────────────────────────────────────────


def test_process_message_on_event_defaults_none():
    import inspect

    from bot.pipeline import process_message

    assert inspect.signature(process_message).parameters["on_event"].default is None


def test_telegram_callsites_never_pass_on_event():
    """Telegram must stay un-streamed / byte-identical: its process_message
    call sites (bot/handlers.py, api/routers/telegram.py) never pass on_event."""
    import pathlib

    for rel in ("bot/handlers.py", "api/routers/telegram.py"):
        src = pathlib.Path(rel).read_text()
        # crude but effective: no process_message( ... on_event ...) call
        for m in re.finditer(r"process_message\((.*?)\)", src, re.DOTALL):
            assert "on_event" not in m.group(1), f"{rel} passes on_event to process_message"
