"""Phase 6a — bloque 9.5: POST /api/v1/queries/test endpoint.

Covers:
- 401 when no auth header is present (current_user dependency rejects).
- 200 when the dispatcher returns a normal response (chunks + tokens
  surfaced from DispatchOutcome).
- 403 when the body Telegram user_id conflicts with the authenticated
  user's pairing.
- 200 when the dispatcher returns an iteration_cap error message —
  mapped via handle_query_error, so the user-facing text lands in
  `reply` and `error_category="iteration_cap"`.
- 429 when assert_within_budget raises BudgetExceeded BEFORE the
  dispatcher fires.

We override `current_user` via FastAPI's dependency_overrides instead
of seeding a user — the endpoint logic under test is the budget gate,
the dispatcher passthrough, and the chunks rendering, none of which
need a real DB user.
"""
from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from api.dependencies import current_user
from api.main import app
from api.routers import queries as queries_router
from app.queries import dispatcher as dispatch_module
from app.queries.delivery import BudgetExceeded
from app.queries.dispatcher import DispatchOutcome


class _StubUser:
    def __init__(self) -> None:
        self.id = uuid.uuid4()
        self.telegram_user_id = 123
        self.timezone = "America/Costa_Rica"
        self.currency = "CRC"
        self.status = "active"


@pytest.fixture
def stub_user():
    return _StubUser()


@pytest.fixture
def client_with_user(stub_user):
    """AsyncClient with current_user overridden to a stub.

    Cleans up the override on teardown so other tests aren't poisoned.
    """
    app.dependency_overrides[current_user] = lambda: stub_user
    transport = ASGITransport(app=app)
    yield AsyncClient(transport=transport, base_url="http://test")
    app.dependency_overrides.pop(current_user, None)


@pytest.fixture
def client_no_auth():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


# ── auth ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_queries_test_returns_401_without_auth(client_no_auth):
    async with client_no_auth as ac:
        resp = await ac.post(
            "/api/v1/queries/test",
            json={"user_id": 123, "query": "cuanto gasté esta semana"},
        )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_queries_test_rejects_mismatched_body_user_id(client_with_user):
    async with client_with_user as ac:
        resp = await ac.post(
            "/api/v1/queries/test",
            json={"user_id": 999, "query": "cuanto gasté esta semana"},
        )

    assert resp.status_code == 403


# ── success ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_queries_test_returns_dispatcher_outcome(
    monkeypatch, client_with_user, stub_user
):
    """A successful dispatch surfaces text, dispatch_id, iterations,
    tools_used and tokens. Chunks are produced from the same delivery
    helper the bot uses."""

    fake_dispatch_id = uuid.uuid4()
    fake_outcome = DispatchOutcome(
        text="Esta semana gastaste ₡45.000.",
        dispatch_id=fake_dispatch_id,
        total_iterations=2,
        total_input_tokens=1200,
        total_output_tokens=80,
        cache_read_input_tokens=500,
        cache_creation_input_tokens=0,
        tools_used=[{"name": "aggregate_transactions", "duration_ms": 12}],
        duration_ms=2500,
    )

    async def fake_assert_budget(**kwargs):
        return 0

    async def fake_run_dispatch(*, user_id, message_text, telegram_chat_id=None):
        assert user_id == stub_user.id
        assert message_text == "cuanto gasté esta semana"
        return fake_outcome

    monkeypatch.setattr(
        queries_router, "assert_within_budget", fake_assert_budget
    )
    monkeypatch.setattr(queries_router, "run_dispatch", fake_run_dispatch)

    async with client_with_user as ac:
        resp = await ac.post(
            "/api/v1/queries/test",
            json={"user_id": 123, "query": "cuanto gasté esta semana"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["reply"] == "Esta semana gastaste ₡45.000."
    assert body["chunks"] == ["Esta semana gastaste ₡45.000."]
    assert body["dispatch_id"] == str(fake_dispatch_id)
    assert body["iterations"] == 2
    assert body["tools_used"] == [
        {"name": "aggregate_transactions", "duration_ms": 12}
    ]
    assert body["tokens"]["input"] == 1200
    assert body["tokens"]["output"] == 80
    assert body["tokens"]["cache_read"] == 500
    assert body["error_category"] is None


# ── iteration cap surfaced in reply ──────────────────────────────────


@pytest.mark.asyncio
async def test_queries_test_iteration_cap_error_in_reply(
    monkeypatch, client_with_user, stub_user
):
    """When the dispatcher hits its iteration cap, run_dispatch returns
    an outcome with error_category='iteration_cap' and the Spanish
    user-facing message in `text`. Endpoint must surface 200 with that
    text in `reply` (NOT 5xx)."""

    fake_outcome = DispatchOutcome(
        text=(
            "No pude completar tu consulta en el tiempo esperado. "
            "Probá reformulando."
        ),
        dispatch_id=uuid.uuid4(),
        total_iterations=4,
        total_input_tokens=8000,
        total_output_tokens=400,
        tools_used=[{"name": "list_transactions"}, {"name": "list_transactions"}],
        duration_ms=18000,
        error_category="iteration_cap",
    )

    async def fake_assert_budget(**kwargs):
        return 0

    async def fake_run_dispatch(**kwargs):
        return fake_outcome

    monkeypatch.setattr(
        queries_router, "assert_within_budget", fake_assert_budget
    )
    monkeypatch.setattr(queries_router, "run_dispatch", fake_run_dispatch)

    async with client_with_user as ac:
        resp = await ac.post(
            "/api/v1/queries/test",
            json={"user_id": 123, "query": "haceme algo absurdamente largo"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert "tiempo esperado" in body["reply"]
    assert body["error_category"] == "iteration_cap"
    assert body["iterations"] == 4


# ── budget cap ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_queries_test_returns_429_when_budget_exhausted(
    monkeypatch, client_with_user, stub_user
):
    """The endpoint pre-checks budget; when assert_within_budget raises
    BudgetExceeded we must return 429 with the Spanish budget message
    in the detail. The dispatcher must not be called."""

    async def fake_assert_budget(**kwargs):
        raise BudgetExceeded("daily budget exceeded: spent=99500 cap=100000")

    dispatch_called = {"n": 0}

    async def must_not_run(*args, **kwargs):
        dispatch_called["n"] += 1
        raise AssertionError("run_dispatch invoked despite budget exceeded")

    monkeypatch.setattr(
        queries_router, "assert_within_budget", fake_assert_budget
    )
    monkeypatch.setattr(queries_router, "run_dispatch", must_not_run)

    async with client_with_user as ac:
        resp = await ac.post(
            "/api/v1/queries/test",
            json={"user_id": 123, "query": "cuánto gasté hoy"},
        )

    assert resp.status_code == 429
    body = resp.json()
    assert "límite diario" in body["detail"]
    assert dispatch_called["n"] == 0
