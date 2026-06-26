"""Phase 8 B4 — reactive advisor: reallocation between envelopes.

Moving budget from one sobre to another, deterministically. The CRITICAL
invariant (byte-lock): committed_outflows = total_limit = Σ over ROOT envelopes
of root.limit must stay invariant across a reallocation — which holds iff
from.parent_id == to.parent_id (both roots, or both children of the SAME
parent). Root↔child is rejected because it would silently shrink the budget.

Covers:
- REST root↔root: both limits move; Σ(all limits) AND total_limit invariant.
- the 422 guards (cross-currency, root↔child, child↔child of different parents,
  from-side floor, amount ≤ 0, from == to).
- REST child↔sibling: both move; parent allocation + total_limit invariant.
- chat: ExtractionResult → _dispatch_reallocate → ProposeAction → commit moves
  both limits.
- the read-only suggest_reallocation_candidates tool ranks by available DESC and
  excludes archived.
"""
from __future__ import annotations

import socket
from datetime import date
from decimal import Decimal
from urllib.parse import urlparse

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from api.config import settings
from api.database import get_db
from api.dependencies import current_user
from api.main import app
from api.models.envelope import Envelope
from api.models.user import User
from api.redis_client import get_redis
from api.services.llm_extractor import ExtractionResult, Intent
from api.services.telegram_dispatcher import (
    AskClarification,
    ProposeAction,
    Reject,
    dispatch,
)
from bot.commit import commit_pending
from bot.pending import PendingAction, new_short_id


def _db_reachable() -> bool:
    try:
        url = urlparse(settings.database_url.replace("+asyncpg", ""))
        with socket.create_connection(
            (url.hostname or "localhost", url.port or 5432), timeout=0.5
        ):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    not _db_reachable(), reason="Postgres not reachable"
)


# ── helpers ───────────────────────────────────────────────────────────────────


def _override(session, user_id):
    class _StubUser:
        def __init__(self) -> None:
            self.id = user_id
            self.status = "active"
            self.currency = "CRC"
            self.display_currency = "CRC"
            self.timezone = "America/Costa_Rica"

    async def _yield_session():
        yield session

    app.dependency_overrides[current_user] = lambda: _StubUser()
    app.dependency_overrides[get_db] = _yield_session


def _clear():
    app.dependency_overrides.pop(current_user, None)
    app.dependency_overrides.pop(get_db, None)


async def _create(ac, *, name, envelope_class="wants", limit_amount=100000,
                  currency="CRC", parent_id=None):
    body = {
        "name": name,
        "envelope_class": envelope_class,
        "limit_amount": limit_amount,
        "currency": currency,
    }
    if parent_id is not None:
        body["parent_id"] = parent_id
    resp = await ac.post("/api/v1/envelopes", json=body)
    return resp


async def _sum_limits(ac) -> float:
    rows = (await ac.get("/api/v1/envelopes")).json()
    return sum(e["limit_amount"] for e in rows)


async def _total_limit(ac) -> float:
    return (await ac.get("/api/v1/envelopes/summary")).json()["total_limit"]


async def _insert_envelope(session, user_id, *, name, limit, currency="CRC",
                           envelope_class="wants", parent_id=None, depth=1,
                           archived=False):
    env = Envelope(
        user_id=user_id,
        name=name,
        envelope_class=envelope_class,
        limit_amount=Decimal(str(limit)),
        currency=currency,
        parent_id=parent_id,
        depth=depth,
        archived=archived,
        is_active=not archived,
    )
    session.add(env)
    await session.flush()
    return env


# ── 1. REST root↔root: both move + byte-lock total_limit invariant ────────────


@pytest.mark.asyncio
async def test_rest_root_to_root_moves_and_preserves_total_limit(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            ahorro = (await _create(ac, name="Ahorro", limit_amount=100000)).json()
            gustos = (await _create(ac, name="Gustos", limit_amount=80000)).json()

            sum_before = await _sum_limits(ac)
            total_before = await _total_limit(ac)

            resp = await ac.post(
                "/api/v1/envelopes/reallocate",
                json={
                    "from_id": ahorro["id"],
                    "to_id": gustos["id"],
                    "amount": 15000,
                },
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            # Response serializes the source side under the JSON key `from`.
            assert body["from"]["limit_amount"] == 85000
            assert body["to"]["limit_amount"] == 95000

            sum_after = await _sum_limits(ac)
            total_after = await _total_limit(ac)

            # BYTE-LOCK: Σ(all limits) AND total_limit identical before/after.
            assert sum_after == sum_before == 180000
            assert total_after == total_before == 180000
    finally:
        _clear()


# ── 2. 422 guards ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rest_cross_currency_rejected(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            crc = (await _create(ac, name="Colones", limit_amount=100000,
                                 currency="CRC")).json()
            usd = (await _create(ac, name="Dólares", limit_amount=100000,
                                 currency="USD")).json()
            resp = await ac.post(
                "/api/v1/envelopes/reallocate",
                json={"from_id": crc["id"], "to_id": usd["id"], "amount": 10000},
            )
            assert resp.status_code == 422
            assert "misma moneda" in resp.json()["detail"]
    finally:
        _clear()


@pytest.mark.asyncio
async def test_rest_root_to_child_rejected(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            root_a = (await _create(ac, name="A", limit_amount=100000)).json()
            root_b = (await _create(ac, name="B", limit_amount=100000)).json()
            child = (await _create(ac, name="Hijo", limit_amount=40000,
                                   parent_id=root_b["id"])).json()
            resp = await ac.post(
                "/api/v1/envelopes/reallocate",
                json={"from_id": root_a["id"], "to_id": child["id"],
                      "amount": 10000},
            )
            assert resp.status_code == 422
            assert "mismo nivel" in resp.json()["detail"]
    finally:
        _clear()


@pytest.mark.asyncio
async def test_rest_child_to_child_different_parents_rejected(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            root1 = (await _create(ac, name="R1", limit_amount=100000)).json()
            root2 = (await _create(ac, name="R2", limit_amount=100000)).json()
            c1 = (await _create(ac, name="C1", limit_amount=40000,
                                parent_id=root1["id"])).json()
            c2 = (await _create(ac, name="C2", limit_amount=40000,
                                parent_id=root2["id"])).json()
            resp = await ac.post(
                "/api/v1/envelopes/reallocate",
                json={"from_id": c1["id"], "to_id": c2["id"], "amount": 10000},
            )
            assert resp.status_code == 422
            assert "mismo nivel" in resp.json()["detail"]
    finally:
        _clear()


@pytest.mark.asyncio
async def test_rest_from_below_children_allocation_rejected(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Root A has 100k with an 80k child allocated; B is another root.
            root_a = (await _create(ac, name="A", limit_amount=100000)).json()
            await _create(ac, name="Hijo", limit_amount=80000,
                          parent_id=root_a["id"])
            root_b = (await _create(ac, name="B", limit_amount=100000)).json()
            # Moving 30k out of A would drop it to 70k < the 80k already split.
            resp = await ac.post(
                "/api/v1/envelopes/reallocate",
                json={"from_id": root_a["id"], "to_id": root_b["id"],
                      "amount": 30000},
            )
            assert resp.status_code == 422
            assert "máximo disponible" in resp.json()["detail"]
    finally:
        _clear()


@pytest.mark.asyncio
async def test_rest_amount_not_positive_rejected(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            a = (await _create(ac, name="A", limit_amount=100000)).json()
            b = (await _create(ac, name="B", limit_amount=100000)).json()
            resp = await ac.post(
                "/api/v1/envelopes/reallocate",
                json={"from_id": a["id"], "to_id": b["id"], "amount": 0},
            )
            assert resp.status_code == 422
    finally:
        _clear()


@pytest.mark.asyncio
async def test_rest_from_equals_to_rejected(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            a = (await _create(ac, name="A", limit_amount=100000)).json()
            resp = await ac.post(
                "/api/v1/envelopes/reallocate",
                json={"from_id": a["id"], "to_id": a["id"], "amount": 10000},
            )
            assert resp.status_code == 422
            assert "no pueden ser el mismo" in resp.json()["detail"]
    finally:
        _clear()


# ── 3. REST child↔sibling: both move; parent + total_limit invariant ──────────


@pytest.mark.asyncio
async def test_rest_child_to_sibling_moves_and_preserves_invariants(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            root = (await _create(ac, name="Viaje", limit_amount=100000)).json()
            c1 = (await _create(ac, name="C1", limit_amount=40000,
                                parent_id=root["id"])).json()
            c2 = (await _create(ac, name="C2", limit_amount=30000,
                                parent_id=root["id"])).json()

            total_before = await _total_limit(ac)
            resp = await ac.post(
                "/api/v1/envelopes/reallocate",
                json={"from_id": c1["id"], "to_id": c2["id"], "amount": 10000},
            )
            assert resp.status_code == 200, resp.text

            # Both siblings moved.
            envs = {e["id"]: e for e in (await ac.get("/api/v1/envelopes")).json()}
            assert envs[c1["id"]]["limit_amount"] == 30000
            assert envs[c2["id"]]["limit_amount"] == 40000

            # Parent _parent_available (limit − Σ children = 100k − 70k) and
            # total_limit (roots only) are both unchanged.
            summary = (await ac.get("/api/v1/envelopes/summary")).json()
            v = next(e for e in summary["envelopes"] if e["id"] == root["id"])
            assert v["allocated"] == 70000
            assert v["unallocated"] == 30000
            assert summary["total_limit"] == total_before == 100000
    finally:
        _clear()


# ── 4. chat: extraction → dispatch → propose → commit moves both limits ───────


def _ext(**overrides) -> ExtractionResult:
    base = dict(
        intent=Intent.REALLOCATE_ENVELOPE,
        dispatcher="write",
        amount=Decimal("15000"),
        reallocate_from_hint="Ahorro",
        reallocate_to_hint="Gustos",
        confidence=0.93,
    )
    base.update(overrides)
    return ExtractionResult(**base)


@pytest.mark.asyncio
async def test_chat_dispatch_proposes_reallocation(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    await _insert_envelope(session, user_id, name="Ahorro", limit=100000)
    await _insert_envelope(session, user_id, name="Gustos", limit=80000)
    await session.commit()

    res = await dispatch(
        extraction=_ext(), user=user, today=date.today(),
        db=session,
    )
    assert isinstance(res, ProposeAction)
    assert res.action_type == "reallocate_envelope"
    assert res.payload["amount"] == "15000"
    assert res.payload["from_name"] == "Ahorro"
    assert res.payload["to_name"] == "Gustos"
    assert "Ahorro" in res.summary_es and "Gustos" in res.summary_es


@pytest.mark.asyncio
async def test_chat_missing_from_clarifies_with_options(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    await _insert_envelope(session, user_id, name="Ahorro", limit=100000)
    await _insert_envelope(session, user_id, name="Gustos", limit=80000)
    await session.commit()

    res = await dispatch(
        extraction=_ext(reallocate_from_hint=None), user=user,
        today=date.today(), db=session,
    )
    assert isinstance(res, AskClarification)
    assert res.awaiting_field == "reallocate_from"
    assert set(res.options) >= {"Ahorro", "Gustos"}


@pytest.mark.asyncio
async def test_chat_cross_currency_rejected(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    await _insert_envelope(session, user_id, name="Ahorro", limit=100000,
                           currency="CRC")
    await _insert_envelope(session, user_id, name="Gustos", limit=100000,
                           currency="USD")
    await session.commit()

    res = await dispatch(
        extraction=_ext(), user=user,
        today=date.today(), db=session,
    )
    assert isinstance(res, Reject)
    assert "misma moneda" in res.message_es


@pytest.mark.asyncio
async def test_chat_commit_moves_both_limits(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    ahorro = await _insert_envelope(session, user_id, name="Ahorro", limit=100000)
    gustos = await _insert_envelope(session, user_id, name="Gustos", limit=80000)
    await session.commit()
    ahorro_id, gustos_id = ahorro.id, gustos.id

    res = await dispatch(
        extraction=_ext(), user=user,
        today=date.today(), db=session,
    )
    assert isinstance(res, ProposeAction)

    pending = PendingAction(
        short_id=new_short_id(),
        action_type=res.action_type,
        payload=res.payload,
        summary_es=res.summary_es,
    )
    await commit_pending(
        user=user, pending=pending, db=session, redis=get_redis()
    )

    rows = {
        e.id: e
        for e in (
            await session.execute(
                select(Envelope).where(Envelope.user_id == user_id)
            )
        ).scalars().all()
    }
    assert rows[ahorro_id].limit_amount == Decimal("85000")
    assert rows[gustos_id].limit_amount == Decimal("95000")


# ── 5. read-only suggest_reallocation_candidates tool ─────────────────────────


class _SessionContext:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return None


@pytest.mark.asyncio
async def test_suggest_tool_ranks_by_available_and_excludes_archived(
    db_with_user, monkeypatch
):
    from app.queries.tools.reallocation import suggest_reallocation_candidates

    session, user_id = db_with_user
    monkeypatch.setattr(
        "app.queries.tools.reallocation.AsyncSessionLocal",
        lambda: _SessionContext(session),
    )

    # Most slack first: Ahorro (100k free) > Gustos (20k free after 80k spent).
    # The archived envelope must not appear at all.
    ahorro = await _insert_envelope(session, user_id, name="Ahorro", limit=100000)
    gustos = await _insert_envelope(session, user_id, name="Gustos", limit=100000)
    await _insert_envelope(session, user_id, name="Viejo", limit=999999,
                           archived=True)

    from datetime import date

    from api.models.transaction import Transaction

    session.add(
        Transaction(
            user_id=user_id, amount=Decimal("-80000"), currency="CRC",
            transaction_date=date(date.today().year, date.today().month, 10),
            source="manual", status="confirmed", archived=False,
            envelope_id=gustos.id,
        )
    )
    await session.commit()

    result = await suggest_reallocation_candidates(user_id=user_id)
    names = [c["name"] for c in result["candidates"]]
    assert "Viejo" not in names  # archived excluded
    assert names[0] == "Ahorro"  # most unused budget ranks first
    assert names.index("Ahorro") < names.index("Gustos")
    # Amounts are Decimal-as-string.
    ahorro_row = next(c for c in result["candidates"] if c["name"] == "Ahorro")
    assert ahorro_row["available"] == "100000.00"
    assert ahorro_row["spent"] == "0.00"
    _ = ahorro  # silence unused
