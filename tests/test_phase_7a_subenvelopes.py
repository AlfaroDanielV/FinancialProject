"""Phase 7a — nested envelopes (sub-sobres).

Covers the nesting contract on top of the flat envelope feature:
- a child inherits the parent's class + currency, depth = parent.depth + 1;
- the 5-level depth cap (create under a level-5 node → 422);
- class is tree-wide: editable only on a root (cascades to the subtree),
  rejected on a child;
- spend rolls up the tree (parent.spent = own + descendants) while
  `direct_spent` stays the node's own; class subtotals count each transaction
  once and count limits for ROOTS only (no double-count);
- soft allocation is reported (`unallocated` can go negative) but never blocks;
- soft-archiving a parent cascades to the subtree; hard-deleting cascades the
  rows and unlinks tagged transactions;
- the read-only query tool returns the rolled-up total + a children breakdown.
"""
from __future__ import annotations

import uuid
from datetime import date

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from api.database import get_db
from api.dependencies import current_user
from api.main import app
from api.models.envelope import Envelope
from api.models.transaction import Transaction


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


def _this_month_day(day: int = 15) -> str:
    today = date.today()
    return date(today.year, today.month, day).isoformat()


# ── inheritance + depth cap ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_child_inherits_class_currency_and_depth(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            root = (await _create(
                ac, name="Viaje", envelope_class="wants", currency="CRC",
                limit_amount=1000000,
            )).json()
            assert root["depth"] == 1
            assert root["parent_id"] is None

            # Child asks for needs/USD but inherits the parent's wants/CRC.
            child = await _create(
                ac, name="Comida", envelope_class="needs", currency="USD",
                limit_amount=300000, parent_id=root["id"],
            )
            assert child.status_code == 201, child.text
            cj = child.json()
            assert cj["parent_id"] == root["id"]
            assert cj["depth"] == 2
            assert cj["envelope_class"] == "wants"  # inherited, not 'needs'
            assert cj["currency"] == "CRC"  # inherited, not 'USD'
    finally:
        _clear()


@pytest.mark.asyncio
async def test_depth_cap_at_five_levels(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            parent_id = None
            for level in range(1, 6):  # create depths 1..5
                resp = await _create(
                    ac, name=f"N{level}", limit_amount=10000, parent_id=parent_id
                )
                assert resp.status_code == 201, resp.text
                assert resp.json()["depth"] == level
                parent_id = resp.json()["id"]

            # A 6th level under the level-5 node is rejected.
            sixth = await _create(ac, name="N6", limit_amount=10000, parent_id=parent_id)
            assert sixth.status_code == 422
            assert "5 niveles" in sixth.json()["detail"]
    finally:
        _clear()


@pytest.mark.asyncio
async def test_foreign_parent_rejected(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await _create(
                ac, name="Hijo", limit_amount=10000, parent_id=str(uuid.uuid4())
            )
            assert resp.status_code == 404
    finally:
        _clear()


# ── class is a tree-wide property ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_root_class_change_cascades_child_class_locked(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            root = (await _create(ac, name="Viaje", envelope_class="wants")).json()
            child = (await _create(
                ac, name="Comida", parent_id=root["id"]
            )).json()

            # Changing the root's class cascades to the child.
            patched = await ac.patch(
                f"/api/v1/envelopes/{root['id']}", json={"envelope_class": "needs"}
            )
            assert patched.status_code == 200, patched.text
            got_child = await ac.get(f"/api/v1/envelopes/{child['id']}")
            assert got_child.json()["envelope_class"] == "needs"

            # Changing a child's class directly is rejected.
            rej = await ac.patch(
                f"/api/v1/envelopes/{child['id']}",
                json={"envelope_class": "savings"},
            )
            assert rej.status_code == 422
            assert "hereda" in rej.json()["detail"]
    finally:
        _clear()


# ── roll-up + class subtotals ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rollup_and_class_subtotals_no_double_count(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            viaje = (await _create(
                ac, name="Viaje", envelope_class="wants", limit_amount=1000000
            )).json()
            comida = (await _create(
                ac, name="Comida", limit_amount=300000, parent_id=viaje["id"]
            )).json()
            hoteles = (await _create(
                ac, name="Hoteles", limit_amount=500000, parent_id=viaje["id"]
            )).json()
            tours = (await _create(
                ac, name="Tours", limit_amount=150000, parent_id=viaje["id"]
            )).json()

            def _tx(env_id, amount):
                return Transaction(
                    user_id=user_id,
                    amount=amount,
                    currency="CRC",
                    transaction_date=date.fromisoformat(_this_month_day()),
                    source="manual",
                    status="confirmed",
                    archived=False,
                    envelope_id=uuid.UUID(env_id),
                )

            session.add_all([
                _tx(comida["id"], -50000),
                _tx(hoteles["id"], -200000),
                _tx(tours["id"], -30000),
                _tx(viaje["id"], -100000),  # direct (flights) on the parent
            ])
            await session.commit()

            body = (await ac.get("/api/v1/envelopes/summary")).json()
            by_id = {e["id"]: e for e in body["envelopes"]}

            # Leaves: rolled-up == direct.
            assert by_id[comida["id"]]["spent"] == 50000
            assert by_id[comida["id"]]["direct_spent"] == 50000

            # Parent: rolled-up = own 100k + 50k + 200k + 30k = 380k.
            v = by_id[viaje["id"]]
            assert v["direct_spent"] == 100000
            assert v["spent"] == 380000
            assert v["remaining"] == 620000
            assert v["over_limit"] is False
            # Allocation: 300k+500k+150k = 950k of the 1,000,000 budget.
            assert v["allocated"] == 950000
            assert v["unallocated"] == 50000
            assert v["over_allocated"] is False

            # Class subtotal: spent counts every node once (380k); limit counts
            # the ROOT only (1,000,000), NOT the children's sub-allocations.
            wants = next(c for c in body["by_class"] if c["envelope_class"] == "wants")
            assert wants["spent_total"] == 380000
            assert wants["limit_total"] == 1000000
            assert body["total_limit"] == 1000000
    finally:
        _clear()


@pytest.mark.asyncio
async def test_child_limit_capped_at_parent_budget(db_with_user):
    """A sub-sobre's limit can't exceed the parent's remaining budget, and a
    parent can't shrink below what's already split into its children."""
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            root = (await _create(ac, name="Viaje", limit_amount=100000)).json()

            # A single child over the parent total → rejected.
            over = await _create(
                ac, name="Grande", limit_amount=150000, parent_id=root["id"]
            )
            assert over.status_code == 422
            assert "disponible" in over.json()["detail"]

            # First child of 60k fits (available 100k).
            a = await _create(ac, name="A", limit_amount=60000, parent_id=root["id"])
            assert a.status_code == 201
            # Only 40k remains: a 50k second child is rejected, 40k is accepted.
            b_over = await _create(ac, name="B", limit_amount=50000, parent_id=root["id"])
            assert b_over.status_code == 422
            b_ok = await _create(ac, name="B", limit_amount=40000, parent_id=root["id"])
            assert b_ok.status_code == 201

            # Editing A up beyond the now-zero remaining is rejected.
            edit = await ac.patch(
                f"/api/v1/envelopes/{a.json()['id']}", json={"limit_amount": 70000}
            )
            assert edit.status_code == 422

            # Parent can't shrink below the 100k already allocated.
            shrink = await ac.patch(
                f"/api/v1/envelopes/{root['id']}", json={"limit_amount": 90000}
            )
            assert shrink.status_code == 422

            # The summary reflects a fully-allocated parent (no over-allocation).
            body = (await ac.get("/api/v1/envelopes/summary")).json()
            v = next(e for e in body["envelopes"] if e["id"] == root["id"])
            assert v["allocated"] == 100000
            assert v["unallocated"] == 0
            assert v["over_allocated"] is False
    finally:
        _clear()


# ── archive / delete cascade ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_soft_archive_parent_cascades_subtree(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            root = (await _create(ac, name="Viaje", limit_amount=100000)).json()
            child = (await _create(
                ac, name="Comida", limit_amount=50000, parent_id=root["id"]
            )).json()

            deleted = await ac.delete(f"/api/v1/envelopes/{root['id']}")
            assert deleted.status_code == 200
            assert deleted.json()["archived"] is True

            # Both gone from the active list; child carries the archived flag.
            assert (await ac.get("/api/v1/envelopes")).json() == []
            got_child = await ac.get(f"/api/v1/envelopes/{child['id']}")
            assert got_child.json()["archived"] is True
            assert got_child.json()["is_active"] is False
    finally:
        _clear()


@pytest.mark.asyncio
async def test_hard_delete_parent_cascades_and_unlinks_transactions(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            root = (await _create(ac, name="Viaje", limit_amount=100000)).json()
            child = (await _create(
                ac, name="Comida", limit_amount=50000, parent_id=root["id"]
            )).json()
            child_id = uuid.UUID(child["id"])

            session.add(
                Transaction(
                    user_id=user_id,
                    amount=-5000,
                    currency="CRC",
                    transaction_date=date.fromisoformat(_this_month_day()),
                    source="manual",
                    status="confirmed",
                    envelope_id=child_id,
                )
            )
            await session.commit()

            resp = await ac.delete(f"/api/v1/envelopes/{root['id']}?hard=true")
            assert resp.status_code == 200, resp.text

        # Both rows gone (subtree cascade); the transaction survives, unlinked.
        remaining = (
            await session.execute(
                select(Envelope).where(Envelope.user_id == user_id)
            )
        ).scalars().all()
        assert remaining == []
        tx = (
            await session.execute(
                select(Transaction).where(Transaction.user_id == user_id)
            )
        ).scalar_one()
        assert tx.envelope_id is None
    finally:
        _clear()


# ── query tool ───────────────────────────────────────────────────────────────


class _SessionContext:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return None


@pytest.mark.asyncio
async def test_query_tool_returns_rollup_and_children(db_with_user, monkeypatch):
    from app.queries.tools.envelopes import get_envelope_spending

    session, user_id = db_with_user
    monkeypatch.setattr(
        "app.queries.tools.envelopes.AsyncSessionLocal",
        lambda: _SessionContext(session),
    )

    viaje = Envelope(
        user_id=user_id, name="Viaje", envelope_class="wants",
        limit_amount=1000000, currency="CRC", depth=1,
    )
    session.add(viaje)
    await session.flush()
    comida = Envelope(
        user_id=user_id, name="Comida", envelope_class="wants",
        limit_amount=300000, currency="CRC", depth=2, parent_id=viaje.id,
    )
    session.add(comida)
    await session.flush()
    session.add_all([
        Transaction(
            user_id=user_id, amount=-50000, currency="CRC",
            transaction_date=date.fromisoformat(_this_month_day()),
            source="manual", status="confirmed", envelope_id=comida.id,
        ),
        Transaction(
            user_id=user_id, amount=-100000, currency="CRC",
            transaction_date=date.fromisoformat(_this_month_day()),
            source="manual", status="confirmed", envelope_id=viaje.id,
        ),
    ])
    await session.commit()

    result = await get_envelope_spending(envelope_name="viaje", user_id=user_id)
    assert result["matched_count"] == 1
    node = result["envelopes"][0]
    assert node["name"] == "Viaje"
    assert node["spent"] == "150000.00"  # rolled-up: 100k own + 50k child
    assert node["direct_spent"] == "100000.00"
    assert "children" in node
    assert node["children"][0]["name"] == "Comida"
    assert node["children"][0]["spent"] == "50000.00"
