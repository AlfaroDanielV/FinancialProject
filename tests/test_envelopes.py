"""Envelope budgeting ("Sobres") — backend contracts.

Covers (Phase 6f post-B16, sub-plan E2):
- CRUD: create / list (active + include_archived) / get / patch / soft-delete.
- EnvelopeUpdate whitelist: `currency` is rejected (extra="forbid" → 422);
  `limit_amount` must be > 0 (422 on create with 0).
- GET /summary live spend math: only confirmed, non-archived, non-transfer
  EXPENSES (amount < 0) dated in the current calendar month count. Income,
  archived, transfer legs, shadow rows, and last-month rows are excluded.
- Over-limit flag + per-class subtotals + total_limit.
- Transaction PATCH envelope assignment: a valid envelope_id is accepted;
  a non-existent / foreign envelope_id is rejected 400 "Sobre inválido."
"""
from __future__ import annotations

from datetime import date

import pytest
from httpx import ASGITransport, AsyncClient

from api.database import get_db
from api.dependencies import current_user
from api.main import app


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


async def _create_envelope(
    ac: AsyncClient,
    *,
    name: str = "Súper",
    envelope_class: str = "needs",
    limit_amount: float = 100000,
    currency: str = "CRC",
):
    resp = await ac.post(
        "/api/v1/envelopes",
        json={
            "name": name,
            "envelope_class": envelope_class,
            "limit_amount": limit_amount,
            "currency": currency,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _this_month_day(day: int = 15) -> str:
    today = date.today()
    return date(today.year, today.month, day).isoformat()


def _last_month_day(day: int = 15) -> str:
    today = date.today()
    year = today.year - 1 if today.month == 1 else today.year
    month = 12 if today.month == 1 else today.month - 1
    return date(year, month, day).isoformat()


# ── CRUD ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_crud_roundtrip(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            env = await _create_envelope(ac, name="Restaurantes", envelope_class="wants")
            assert env["envelope_class"] == "wants"
            assert env["archived"] is False
            assert env["is_active"] is True
            assert env["period"] == "monthly"

            # list active
            listed = await ac.get("/api/v1/envelopes")
            assert listed.status_code == 200
            assert [e["id"] for e in listed.json()] == [env["id"]]

            # get
            got = await ac.get(f"/api/v1/envelopes/{env['id']}")
            assert got.status_code == 200
            assert got.json()["name"] == "Restaurantes"

            # patch name + limit + class
            patched = await ac.patch(
                f"/api/v1/envelopes/{env['id']}",
                json={"name": "Comidas fuera", "limit_amount": 80000, "envelope_class": "needs"},
            )
            assert patched.status_code == 200, patched.text
            assert patched.json()["name"] == "Comidas fuera"
            assert patched.json()["limit_amount"] == 80000
            assert patched.json()["envelope_class"] == "needs"

            # soft delete → archived, excluded from default list, present with flag
            deleted = await ac.delete(f"/api/v1/envelopes/{env['id']}")
            assert deleted.status_code == 200
            assert deleted.json()["archived"] is True
            assert deleted.json()["is_active"] is False

            assert (await ac.get("/api/v1/envelopes")).json() == []
            with_arch = await ac.get("/api/v1/envelopes?include_archived=true")
            assert [e["id"] for e in with_arch.json()] == [env["id"]]
    finally:
        _clear()


@pytest.mark.asyncio
async def test_hard_delete_removes_envelope_and_unlinks_transactions(db_with_user):
    """`DELETE /envelopes/{id}?hard=true` removes the row; a transaction tagged
    to it keeps existing with envelope_id set NULL (FK ON DELETE SET NULL)."""
    import uuid

    from sqlalchemy import select

    from api.models.envelope import Envelope
    from api.models.transaction import Transaction

    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            env = await _create_envelope(ac)
            env_id = uuid.UUID(env["id"])

            session.add(
                Transaction(
                    user_id=user_id,
                    amount=-1000,
                    currency="CRC",
                    transaction_date=date.fromisoformat(_this_month_day()),
                    source="manual",
                    status="confirmed",
                    envelope_id=env_id,
                )
            )
            await session.commit()

            resp = await ac.delete(f"/api/v1/envelopes/{env['id']}?hard=true")
            assert resp.status_code == 200, resp.text

        # Envelope row is gone.
        gone = (
            await session.execute(select(Envelope).where(Envelope.id == env_id))
        ).scalar_one_or_none()
        assert gone is None

        # The transaction survives with envelope_id unlinked.
        tx = (
            await session.execute(
                select(Transaction).where(Transaction.user_id == user_id)
            )
        ).scalar_one()
        assert tx.envelope_id is None
    finally:
        _clear()


@pytest.mark.asyncio
async def test_get_missing_returns_404(db_with_user):
    import uuid

    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get(f"/api/v1/envelopes/{uuid.uuid4()}")
            assert resp.status_code == 404
            assert resp.json()["detail"] == "Sobre no encontrado."
    finally:
        _clear()


@pytest.mark.asyncio
async def test_update_rejects_currency_and_create_rejects_zero_limit(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            env = await _create_envelope(ac)
            # extra="forbid": currency is immutable
            resp = await ac.patch(
                f"/api/v1/envelopes/{env['id']}", json={"currency": "USD"}
            )
            assert resp.status_code == 422
            # limit must be > 0
            bad = await ac.post(
                "/api/v1/envelopes",
                json={"name": "X", "envelope_class": "needs", "limit_amount": 0},
            )
            assert bad.status_code == 422
    finally:
        _clear()


# ── summary spend math ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_summary_spend_math_and_exclusions(db_with_user):
    from sqlalchemy import select

    from api.models.transaction import Transaction

    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            needs = await _create_envelope(
                ac, name="Súper", envelope_class="needs", limit_amount=100000
            )
            wants = await _create_envelope(
                ac, name="Ocio", envelope_class="wants", limit_amount=20000
            )
            import uuid

            needs_id = uuid.UUID(needs["id"])
            wants_id = uuid.UUID(wants["id"])

            def _tx(**kw):
                base = dict(
                    user_id=user_id,
                    amount=-10000,
                    currency="CRC",
                    transaction_date=date.fromisoformat(_this_month_day()),
                    source="manual",
                    status="confirmed",
                    archived=False,
                )
                base.update(kw)
                return Transaction(**base)

            session.add_all(
                [
                    # counts: 30000 on needs
                    _tx(envelope_id=needs_id, amount=-10000),
                    _tx(envelope_id=needs_id, amount=-20000),
                    # counts: 25000 on wants → OVER its 20000 limit
                    _tx(envelope_id=wants_id, amount=-25000),
                    # excluded: income (amount > 0)
                    _tx(envelope_id=needs_id, amount=15000),
                    # excluded: archived
                    _tx(envelope_id=needs_id, amount=-99999, archived=True),
                    # excluded: shadow row
                    _tx(envelope_id=needs_id, amount=-99999, status="shadow"),
                    # excluded: last month
                    _tx(
                        envelope_id=needs_id,
                        amount=-99999,
                        transaction_date=date.fromisoformat(_last_month_day()),
                    ),
                    # excluded: no envelope assigned
                    _tx(envelope_id=None, amount=-50000),
                ]
            )
            await session.commit()

            resp = await ac.get("/api/v1/envelopes/summary")
            assert resp.status_code == 200, resp.text
            body = resp.json()

            by_id = {e["id"]: e for e in body["envelopes"]}
            assert by_id[needs["id"]]["spent"] == 30000
            assert by_id[needs["id"]]["remaining"] == 70000
            assert by_id[needs["id"]]["over_limit"] is False
            assert by_id[wants["id"]]["spent"] == 25000
            assert by_id[wants["id"]]["over_limit"] is True
            assert by_id[wants["id"]]["pct"] == pytest.approx(1.25)

            # per-class subtotals
            by_class = {c["envelope_class"]: c for c in body["by_class"]}
            assert by_class["needs"]["spent_total"] == 30000
            assert by_class["needs"]["over_limit"] is False
            assert by_class["wants"]["spent_total"] == 25000
            assert by_class["wants"]["over_limit"] is True
            # class order is needs before wants
            assert [c["envelope_class"] for c in body["by_class"]] == ["needs", "wants"]

            assert body["total_limit"] == 120000
            assert body["period"] == date.today().strftime("%Y-%m")
    finally:
        _clear()


@pytest.mark.asyncio
async def test_summary_converts_foreign_currency_expense(db_with_user):
    """A US$ expense tagged to a CRC envelope is converted at the fixed
    reference rate (₡500/US$) before it counts: $30 → ₡15 000."""
    import uuid

    from api.models.transaction import Transaction
    from api.services.fx import FALLBACK_USD_TO_CRC

    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            env = await _create_envelope(
                ac, name="Súper", envelope_class="needs", limit_amount=100000, currency="CRC"
            )
            env_id = uuid.UUID(env["id"])

            # A CRC expense (₡10 000) + a USD expense ($30) on the same envelope.
            session.add_all(
                [
                    Transaction(
                        user_id=user_id,
                        amount=-10000,
                        currency="CRC",
                        transaction_date=date.fromisoformat(_this_month_day()),
                        source="manual",
                        status="confirmed",
                        envelope_id=env_id,
                    ),
                    Transaction(
                        user_id=user_id,
                        amount=-30,
                        currency="USD",
                        transaction_date=date.fromisoformat(_this_month_day()),
                        source="manual",
                        status="confirmed",
                        envelope_id=env_id,
                    ),
                ]
            )
            await session.commit()

            body = (await ac.get("/api/v1/envelopes/summary")).json()
            item = next(e for e in body["envelopes"] if e["id"] == env["id"])
            expected = 10000 + 30 * float(FALLBACK_USD_TO_CRC)  # 10000 + 15000
            assert item["spent"] == expected
            assert item["currency"] == "CRC"
    finally:
        _clear()


# ── transaction envelope assignment ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_transaction_patch_envelope_assignment(db_with_user):
    import uuid

    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            env = await _create_envelope(ac)
            tx = await ac.post(
                "/api/v1/transactions",
                json={
                    "amount": -5000,
                    "currency": "CRC",
                    "merchant": "Súper",
                    "transaction_date": _this_month_day(),
                    "source": "manual",
                },
            )
            assert tx.status_code == 201, tx.text
            tx_id = tx.json()["id"]

            # valid envelope assigned
            ok = await ac.patch(
                f"/api/v1/transactions/{tx_id}",
                json={"envelope_id": env["id"]},
            )
            assert ok.status_code == 200, ok.text
            assert ok.json()["envelope_id"] == env["id"]

            # non-existent envelope rejected
            bad = await ac.patch(
                f"/api/v1/transactions/{tx_id}",
                json={"envelope_id": str(uuid.uuid4())},
            )
            assert bad.status_code == 400
            assert bad.json()["detail"] == "Sobre inválido."
    finally:
        _clear()
