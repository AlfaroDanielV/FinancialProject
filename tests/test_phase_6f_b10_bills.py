"""Phase 6f B10 — Bills + calendar backend contracts for the native app.

Covers:
1. GET /recurring-bills returns active bills for the user.
2. GET /bill-occurrences filtered by from_date/to_date returns the right window.
3. POST /recurring-bills/{id}/mark-paid creates a transaction and flips status.
4. Idempotency: same key replays without a duplicate transaction.
5. 404 when no actionable occurrence exists (already paid).
6. PATCH is_active=false pauses; occurrences remain; is_active=true resumes.
7. DELETE archives the bill and cancels future pending occurrences.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select as sa_select

from api.database import get_db
from api.dependencies import current_user
from api.main import app
from api.models.bill_occurrence import BillOccurrence
from api.models.enums import BillOccurrenceStatus
from api.models.transaction import Transaction


def _override(session, user_id):
    class _StubUser:
        def __init__(self) -> None:
            self.id = user_id
            self.status = "active"
            self.currency = "CRC"
            self.display_currency = "CRC"

    async def _yield_session():
        yield session

    app.dependency_overrides[current_user] = lambda: _StubUser()
    app.dependency_overrides[get_db] = _yield_session


def _clear():
    app.dependency_overrides.pop(current_user, None)
    app.dependency_overrides.pop(get_db, None)


async def _create_account(ac: AsyncClient, name: str = "BAC") -> dict:
    resp = await ac.post(
        "/api/v1/accounts",
        json={"name": name, "account_type": "checking", "currency": "CRC", "initial_balance": "0"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _create_bill(
    ac: AsyncClient,
    account_id: str,
    *,
    name: str = "Kolbi celular",
    amount_expected: float = 10_000.0,
    is_variable_amount: bool = False,
    start_date: str | None = None,
) -> dict:
    today = date.today()
    body: dict = {
        "name": name,
        "category": "servicios",
        "currency": "CRC",
        "is_variable_amount": is_variable_amount,
        "frequency": "monthly",
        "day_of_month": today.day or 5,
        "start_date": start_date or today.isoformat(),
        "lead_time_days": 0,
        "account_id": account_id,
    }
    if not is_variable_amount:
        body["amount_expected"] = amount_expected
    resp = await ac.post("/api/v1/recurring-bills", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


# ── 1. GET /recurring-bills returns active bills ──────────────────────────────


@pytest.mark.asyncio
async def test_list_recurring_bills_returns_active(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            account = await _create_account(ac)
            await _create_bill(ac, account["id"], name="CNFL luz")

            resp = await ac.get("/api/v1/recurring-bills", params={"is_active": True})
            assert resp.status_code == 200, resp.text
            bills = resp.json()
            names = [b["name"] for b in bills]
            assert "CNFL luz" in names
    finally:
        _clear()


# ── 2. GET /bill-occurrences with date window ─────────────────────────────────


@pytest.mark.asyncio
async def test_list_bill_occurrences_within_date_window(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            account = await _create_account(ac, "Davivienda")
            await _create_bill(ac, account["id"], name="AyA agua")

            today = date.today().isoformat()
            future = (date.today() + timedelta(days=90)).isoformat()

            resp = await ac.get(
                "/api/v1/bill-occurrences",
                params={"from_date": today, "to_date": future},
            )
            assert resp.status_code == 200, resp.text
            occs = resp.json()
            assert len(occs) >= 1
            ids = {o["recurring_bill_id"] for o in occs}
            assert len(ids) >= 1

            far_past = (date.today() - timedelta(days=365)).isoformat()
            yesterday = (date.today() - timedelta(days=1)).isoformat()
            resp2 = await ac.get(
                "/api/v1/bill-occurrences",
                params={"from_date": far_past, "to_date": yesterday},
            )
            assert resp2.status_code == 200, resp2.text
            past_occs = [
                o for o in resp2.json()
                if o.get("due_date", "") >= far_past and o.get("due_date", "") <= yesterday
            ]
            assert all(o["due_date"] <= yesterday for o in past_occs)
    finally:
        _clear()


# ── 3. mark-paid creates transaction and flips occurrence status ───────────────


@pytest.mark.asyncio
async def test_mark_bill_paid_creates_transaction_and_flips_status(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            account = await _create_account(ac)
            bill = await _create_bill(ac, account["id"], name="INS seguro")

            resp = await ac.post(
                f"/api/v1/recurring-bills/{bill['id']}/mark-paid",
                json={
                    "amount_paid": 10_000,
                    "idempotency_key": "b10-test-idempkey-1234",
                },
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["occurrence"]["status"] == "paid"
            assert body["transaction_id"]
            assert body["idempotent_replay"] is False

            txn = await session.get(Transaction, body["transaction_id"])
            assert txn is not None
            assert float(txn.amount) < 0  # expenses are negative
    finally:
        _clear()


# ── 4. idempotency: same key replays without duplication ─────────────────────


@pytest.mark.asyncio
async def test_mark_bill_paid_idempotency_replays(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            account = await _create_account(ac, "Promerica")
            bill = await _create_bill(ac, account["id"], name="Netflix")

            payload = {"amount_paid": 7_000, "idempotency_key": "b10-idempkey-replay-xyz"}

            resp1 = await ac.post(f"/api/v1/recurring-bills/{bill['id']}/mark-paid", json=payload)
            assert resp1.status_code == 200
            txn_id_1 = resp1.json()["transaction_id"]
            assert resp1.json()["idempotent_replay"] is False

            resp2 = await ac.post(f"/api/v1/recurring-bills/{bill['id']}/mark-paid", json=payload)
            assert resp2.status_code == 200
            assert resp2.json()["idempotent_replay"] is True
            assert resp2.json()["transaction_id"] == txn_id_1
    finally:
        _clear()


# ── 5. 404 when no actionable occurrence remains ──────────────────────────────


@pytest.mark.asyncio
async def test_mark_bill_paid_404_when_already_paid(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            account = await _create_account(ac, "BCR")
            bill = await _create_bill(ac, account["id"], name="Spotify")

            await ac.post(
                f"/api/v1/recurring-bills/{bill['id']}/mark-paid",
                json={"amount_paid": 5_000, "idempotency_key": "b10-first-pay-key-1234"},
            )
            # flip all occurrences to paid manually so 2nd call has nothing
            result = await session.execute(
                sa_select(BillOccurrence).where(
                    BillOccurrence.recurring_bill_id == bill["id"]
                )
            )
            for occ in result.scalars().all():
                occ.status = BillOccurrenceStatus.PAID.value
            await session.commit()

            resp = await ac.post(
                f"/api/v1/recurring-bills/{bill['id']}/mark-paid",
                json={"amount_paid": 5_000, "idempotency_key": "b10-second-pay-key-1234"},
            )
            assert resp.status_code == 404, resp.text
    finally:
        _clear()


# ── 6. pause and resume ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pause_bill_sets_inactive_and_resume_regenerates(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            account = await _create_account(ac, "Coopealianza")
            bill = await _create_bill(ac, account["id"], name="ICE electric")

            pause_resp = await ac.patch(
                f"/api/v1/recurring-bills/{bill['id']}",
                json={"is_active": False},
            )
            assert pause_resp.status_code == 200, pause_resp.text
            assert pause_resp.json()["is_active"] is False

            resume_resp = await ac.patch(
                f"/api/v1/recurring-bills/{bill['id']}",
                json={"is_active": True},
            )
            assert resume_resp.status_code == 200, resume_resp.text
            assert resume_resp.json()["is_active"] is True
    finally:
        _clear()


# ── 7. archive (DELETE) cancels future pending occurrences ────────────────────


@pytest.mark.asyncio
async def test_archive_bill_cancels_future_occurrences(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            account = await _create_account(ac, "Scotia")
            bill = await _create_bill(ac, account["id"], name="Seguro dental")

            occs_before = await ac.get(
                "/api/v1/bill-occurrences",
                params={"recurring_bill_id": bill["id"]},
            )
            assert occs_before.status_code == 200
            assert len(occs_before.json()) > 0

            del_resp = await ac.delete(f"/api/v1/recurring-bills/{bill['id']}")
            assert del_resp.status_code == 200, del_resp.text

            occs_after_result = await session.execute(
                sa_select(BillOccurrence).where(
                    BillOccurrence.recurring_bill_id == bill["id"],
                    BillOccurrence.status == BillOccurrenceStatus.PENDING.value,
                )
            )
            pending = list(occs_after_result.scalars().all())
            assert len(pending) == 0
    finally:
        _clear()
