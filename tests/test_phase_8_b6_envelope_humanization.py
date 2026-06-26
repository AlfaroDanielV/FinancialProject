"""Phase 8 B6 — envelope humanization.

Covers the backend surface of B6:
- POST /envelopes/starter-pack bulk-creates roots deterministically (all
  parent_id IS NULL, currency = user.currency) and validates each item
  (class / limit > 0) + the ≤ 8 list cap.
- EnvelopeCreate.envelope_class defaults to "wants" when omitted on a ROOT,
  while a sub-sobre still inherits the parent's class.
- Over-limit reallocation reuses the B4 primitive end-to-end: moving budget from
  a same-level sobre covers the shortfall and keeps total_limit byte-invariant.
- _maybe_append_unassigned_suggestion fires once per conversation.
- No merchant→sobre auto-tag exists (a repeated merchant does NOT inherit an
  envelope from a prior assignment — the no-synonym-maps rule stands).
"""
from __future__ import annotations

import socket
import uuid
from datetime import date
from urllib.parse import urlparse

import pytest
from httpx import ASGITransport, AsyncClient

from api.config import settings
from api.database import get_db
from api.dependencies import current_user
from api.main import app


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


def _this_month_day(day: int = 15) -> str:
    today = date.today()
    return date(today.year, today.month, day).isoformat()


async def _create_envelope(ac, *, name, envelope_class="needs", limit_amount=100000,
                           currency="CRC", parent_id=None):
    body = {"name": name, "limit_amount": limit_amount, "currency": currency}
    if envelope_class is not None:
        body["envelope_class"] = envelope_class
    if parent_id is not None:
        body["parent_id"] = parent_id
    return await ac.post("/api/v1/envelopes", json=body)


_STARTER_ITEMS = [
    {"name": "Comida", "envelope_class": "needs", "limit_amount": 150000},
    {"name": "Servicios", "envelope_class": "needs", "limit_amount": 100000},
    {"name": "Gustos", "envelope_class": "wants", "limit_amount": 100000},
    {"name": "Ahorro", "envelope_class": "savings", "limit_amount": 80000},
    {"name": "Inversión", "envelope_class": "investing", "limit_amount": 50000},
]


# ── starter pack ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_starter_pack_creates_five_roots(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/envelopes/starter-pack", json={"items": _STARTER_ITEMS}
            )
            assert resp.status_code == 201, resp.text
            created = resp.json()
            assert len(created) == 5
            # Every item is a ROOT in the user's currency.
            assert all(e["parent_id"] is None for e in created)
            assert all(e["depth"] == 1 for e in created)
            assert all(e["currency"] == "CRC" for e in created)
            assert {e["name"] for e in created} == {
                "Comida", "Servicios", "Gustos", "Ahorro", "Inversión"
            }
            assert {e["envelope_class"] for e in created} == {
                "needs", "wants", "savings", "investing"
            }

            # They show up in the listing.
            listed = (await ac.get("/api/v1/envelopes")).json()
            assert len(listed) == 5
    finally:
        _clear()


@pytest.mark.asyncio
async def test_starter_pack_rejects_invalid_class(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/envelopes/starter-pack",
                json={"items": [{"name": "X", "envelope_class": "luxuries",
                                 "limit_amount": 1000}]},
            )
            assert resp.status_code == 422
    finally:
        _clear()


@pytest.mark.asyncio
async def test_starter_pack_rejects_nonpositive_limit(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/envelopes/starter-pack",
                json={"items": [{"name": "X", "envelope_class": "needs",
                                 "limit_amount": 0}]},
            )
            assert resp.status_code == 422
    finally:
        _clear()


@pytest.mark.asyncio
async def test_starter_pack_rejects_list_over_eight(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            nine = [
                {"name": f"S{i}", "envelope_class": "needs", "limit_amount": 1000}
                for i in range(9)
            ]
            resp = await ac.post(
                "/api/v1/envelopes/starter-pack", json={"items": nine}
            )
            assert resp.status_code == 422
            # And the cap doesn't partially create anything.
            assert (await ac.get("/api/v1/envelopes")).json() == []
    finally:
        _clear()


# ── EnvelopeCreate class default + child inheritance ─────────────────────────


@pytest.mark.asyncio
async def test_create_root_defaults_class_to_wants(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Omit envelope_class entirely → defaults to "wants" on a root.
            resp = await _create_envelope(ac, name="Sin tipo", envelope_class=None)
            assert resp.status_code == 201, resp.text
            assert resp.json()["envelope_class"] == "wants"
    finally:
        _clear()


@pytest.mark.asyncio
async def test_subsobre_inherits_parent_class_when_omitted(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            parent = (await _create_envelope(
                ac, name="Casa", envelope_class="needs", limit_amount=100000
            )).json()
            # Child omits class → inherits the parent's "needs" (NOT the default).
            child = await _create_envelope(
                ac, name="Súper", envelope_class=None, limit_amount=40000,
                parent_id=parent["id"],
            )
            assert child.status_code == 201, child.text
            assert child.json()["envelope_class"] == "needs"
            assert child.json()["parent_id"] == parent["id"]
            assert child.json()["depth"] == 2
    finally:
        _clear()


# ── over-limit reallocation reuses B4 end-to-end ─────────────────────────────


@pytest.mark.asyncio
async def test_over_limit_reallocation_covers_shortfall_byte_lock(db_with_user):
    from api.models.transaction import Transaction

    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            gustos = (await _create_envelope(
                ac, name="Gustos", envelope_class="wants", limit_amount=20000
            )).json()
            ahorro = (await _create_envelope(
                ac, name="Ahorro", envelope_class="wants", limit_amount=100000
            )).json()

            # Spend ₡25 000 on Gustos → over its ₡20 000 limit by ₡5 000.
            session.add(
                Transaction(
                    user_id=user_id, amount=-25000, currency="CRC",
                    transaction_date=date.fromisoformat(_this_month_day()),
                    source="manual", status="confirmed", archived=False,
                    envelope_id=uuid.UUID(gustos["id"]),
                )
            )
            await session.commit()

            before = (await ac.get("/api/v1/envelopes/summary")).json()
            g_before = next(e for e in before["envelopes"] if e["id"] == gustos["id"])
            assert g_before["over_limit"] is True
            shortfall = round(g_before["spent"] - g_before["limit_amount"])
            assert shortfall == 5000
            total_before = before["total_limit"]

            # Cover by moving exactly the shortfall from Ahorro (B4 primitive).
            resp = await ac.post(
                "/api/v1/envelopes/reallocate",
                json={"from_id": ahorro["id"], "to_id": gustos["id"],
                      "amount": shortfall},
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["from"]["limit_amount"] == 95000
            assert resp.json()["to"]["limit_amount"] == 25000

            after = (await ac.get("/api/v1/envelopes/summary")).json()
            g_after = next(e for e in after["envelopes"] if e["id"] == gustos["id"])
            # Shortfall covered → no longer over-limit.
            assert g_after["over_limit"] is False
            # BYTE-LOCK: total_limit unchanged by the reallocation.
            assert after["total_limit"] == total_before == 120000
    finally:
        _clear()


# ── unassigned-expenses chat suggestion (dispatcher, once per conversation) ──


class _FakeRedis:
    def __init__(self) -> None:
        self._store: dict = {}

    async def get(self, key):
        return self._store.get(key)

    async def setex(self, key, ttl, value):
        self._store[key] = value


@pytest.mark.asyncio
async def test_unassigned_suggestion_fires_once_per_conversation(db_with_user):
    from api.models.transaction import Transaction
    from api.models.user import User
    from app.queries.dispatcher import _maybe_append_unassigned_suggestion

    session, user_id = db_with_user
    user = await session.get(User, user_id)

    # One current-month confirmed expense with NO envelope.
    session.add(
        Transaction(
            user_id=user_id, amount=-12000, currency="CRC",
            transaction_date=date.fromisoformat(_this_month_day()),
            source="manual", status="confirmed", archived=False,
            envelope_id=None,
        )
    )
    await session.commit()

    redis = _FakeRedis()
    tools = [{"name": "assess_purchase"}]

    first = await _maybe_append_unassigned_suggestion(
        "Listo.", db=session, user=user, redis=redis, tools_used=tools
    )
    assert "sin sobre" in first
    assert first != "Listo."

    # Second turn in the same conversation: rate-limited, no extra append.
    second = await _maybe_append_unassigned_suggestion(
        "Otra respuesta.", db=session, user=user, redis=redis, tools_used=tools
    )
    assert second == "Otra respuesta."


@pytest.mark.asyncio
async def test_unassigned_suggestion_silent_without_cashflow_tool(db_with_user):
    from api.models.transaction import Transaction
    from api.models.user import User
    from app.queries.dispatcher import _maybe_append_unassigned_suggestion

    session, user_id = db_with_user
    user = await session.get(User, user_id)
    session.add(
        Transaction(
            user_id=user_id, amount=-12000, currency="CRC",
            transaction_date=date.fromisoformat(_this_month_day()),
            source="manual", status="confirmed", archived=False,
            envelope_id=None,
        )
    )
    await session.commit()

    redis = _FakeRedis()
    # A non-cashflow tool (e.g. a plain balance read) must NOT trigger it.
    out = await _maybe_append_unassigned_suggestion(
        "Tu saldo es ₡100.", db=session, user=user, redis=redis,
        tools_used=[{"name": "get_account_balance"}],
    )
    assert out == "Tu saldo es ₡100."


@pytest.mark.asyncio
async def test_count_unassigned_excludes_ajuste(db_with_user):
    """A reconciliation ajuste (confirmed, negative, envelope_id IS NULL) is a
    balance correction, not a gasto — it must NOT be counted as an unassigned
    expense, or the chat would prompt the user to file it into a sobre."""
    from api.models.transaction import Transaction
    from api.models.user import User
    from api.services.anchors import AJUSTE_CATEGORY
    from api.services.envelopes import count_unassigned_month_expenses

    session, user_id = db_with_user
    user = await session.get(User, user_id)
    today = date.fromisoformat(_this_month_day())
    session.add_all(
        [
            # A real unassigned expense — counts.
            Transaction(
                user_id=user_id, amount=-12000, currency="CRC",
                transaction_date=today, source="manual", status="confirmed",
                archived=False, envelope_id=None,
            ),
            # An unassigned reconciliation ajuste — must be excluded.
            Transaction(
                user_id=user_id, amount=-3000, currency="CRC",
                transaction_date=today, source="manual", status="confirmed",
                archived=False, envelope_id=None, category=AJUSTE_CATEGORY,
            ),
        ]
    )
    await session.commit()

    assert await count_unassigned_month_expenses(session, user=user) == 1


# ── no merchant→sobre auto-tag (the no-synonym-maps rule stands) ─────────────


@pytest.mark.asyncio
async def test_no_merchant_to_envelope_auto_tag(db_with_user):
    """A repeated merchant must NOT auto-inherit an envelope from a prior
    assignment — there is deliberately NO merchant→sobre memory in B6."""
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            env = (await _create_envelope(ac, name="Súper")).json()

            # First expense at "Auto Mercado" → manually assigned to the sobre.
            tx1 = await ac.post(
                "/api/v1/transactions",
                json={"amount": -5000, "currency": "CRC", "merchant": "Auto Mercado",
                      "transaction_date": _this_month_day(), "source": "manual"},
            )
            tx1_id = tx1.json()["id"]
            assigned = await ac.patch(
                f"/api/v1/transactions/{tx1_id}", json={"envelope_id": env["id"]}
            )
            assert assigned.json()["envelope_id"] == env["id"]

            # A SECOND expense at the same merchant must land with NO envelope.
            tx2 = await ac.post(
                "/api/v1/transactions",
                json={"amount": -7000, "currency": "CRC", "merchant": "Auto Mercado",
                      "transaction_date": _this_month_day(), "source": "manual"},
            )
            assert tx2.status_code == 201, tx2.text
            assert tx2.json()["envelope_id"] is None
    finally:
        _clear()
