"""Phase 6e B6 — recurring bills + calendar backend contracts.

Covers:
- POST /recurring-bills/{id}/mark-paid auto-resolves the next pending
  occurrence, creates a transaction, links it, and flips status to paid.
- Idempotency: same idempotency_key replays the original response without
  duplicating the transaction.
- 404 when the bill has no pending/overdue occurrence (after a paid run).
- Pause via PATCH is_active=false leaves existing occurrences in place;
  resume via PATCH is_active=true regenerates new ones.
- Existing DELETE (archive) cancels future pending occurrences.
- Variable-amount bills require amount_paid in the body.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select as sa_select

from api.database import get_db
from api.dependencies import current_user
from api.main import app
from api.models.bill_occurrence import BillOccurrence
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


async def _create_account(ac: AsyncClient, name: str = "BAC"):
    response = await ac.post(
        "/api/v1/accounts",
        json={
            "name": name,
            "account_type": "checking",
            "currency": "CRC",
            "initial_balance": "0",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _create_bill(
    ac: AsyncClient,
    account_id: str,
    *,
    name: str = "Kolbi celular",
    amount_expected: str | None = "10000",
    is_variable_amount: bool = False,
    start_date: str | None = None,
):
    body = {
        "name": name,
        "category": "servicios",
        "currency": "CRC",
        "is_variable_amount": is_variable_amount,
        "frequency": "monthly",
        "day_of_month": 5,
        "start_date": start_date or (date.today().replace(day=5)).isoformat(),
        "lead_time_days": 0,
        "account_id": account_id,
    }
    if amount_expected and not is_variable_amount:
        body["amount_expected"] = float(amount_expected)
    response = await ac.post("/api/v1/recurring-bills", json=body)
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.asyncio
async def test_mark_bill_paid_creates_transaction_and_flips_occurrence(
    db_with_user,
):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            account = await _create_account(ac)
            bill = await _create_bill(ac, account["id"])

            response = await ac.post(
                f"/api/v1/recurring-bills/{bill['id']}/mark-paid",
                json={
                    "amount_paid": 10000,
                    "idempotency_key": "click-1234567890",
                },
            )
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["occurrence"]["status"] == "paid"
            assert body["transaction_id"]
            assert body["idempotent_replay"] is False

            txn_row = (
                await session.execute(
                    sa_select(Transaction).where(
                        Transaction.id == body["transaction_id"]
                    )
                )
            ).scalar_one()
            assert Decimal(str(txn_row.amount)) == Decimal("-10000")
            assert txn_row.account_id is not None
            assert str(txn_row.account_id) == account["id"]
            assert txn_row.category == "servicios"

            occurrence_row = (
                await session.execute(
                    sa_select(BillOccurrence).where(
                        BillOccurrence.id == body["occurrence"]["id"]
                    )
                )
            ).scalar_one()
            assert occurrence_row.status == "paid"
            assert str(occurrence_row.transaction_id) == body["transaction_id"]
    finally:
        _clear()


@pytest.mark.asyncio
async def test_mark_bill_paid_is_idempotent_for_same_key(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            account = await _create_account(ac)
            bill = await _create_bill(ac, account["id"])

            first = await ac.post(
                f"/api/v1/recurring-bills/{bill['id']}/mark-paid",
                json={"amount_paid": 10000, "idempotency_key": "abc-12345678"},
            )
            assert first.status_code == 200, first.text
            replay = await ac.post(
                f"/api/v1/recurring-bills/{bill['id']}/mark-paid",
                json={"amount_paid": 10000, "idempotency_key": "abc-12345678"},
            )
            assert replay.status_code == 200, replay.text
            assert replay.json()["transaction_id"] == first.json()["transaction_id"]
            assert replay.json()["idempotent_replay"] is True

            # only one transaction created
            txn_count = await session.execute(
                sa_select(Transaction).where(
                    Transaction.user_id == user_id,
                    Transaction.merchant == "Kolbi celular",
                )
            )
            assert len(list(txn_count.scalars().all())) == 1
    finally:
        _clear()


@pytest.mark.asyncio
async def test_mark_bill_paid_returns_404_when_no_pending_occurrence(
    db_with_user,
):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            account = await _create_account(ac)
            bill = await _create_bill(ac, account["id"])

            # Drain every pending occurrence by marking them paid one by one
            # under unique keys until the endpoint reports 404.
            for index in range(20):
                response = await ac.post(
                    f"/api/v1/recurring-bills/{bill['id']}/mark-paid",
                    json={
                        "amount_paid": 10000,
                        "idempotency_key": f"drain-{index:08d}",
                    },
                )
                if response.status_code == 404:
                    break
                assert response.status_code == 200, response.text
            else:
                pytest.fail("never exhausted the pending occurrences")

            assert response.status_code == 404
            assert "pendientes" in response.json()["detail"]
    finally:
        _clear()


@pytest.mark.asyncio
async def test_variable_bill_requires_amount_paid(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            account = await _create_account(ac)
            bill = await _create_bill(
                ac,
                account["id"],
                name="Agua",
                is_variable_amount=True,
                amount_expected=None,
            )

            blocked = await ac.post(
                f"/api/v1/recurring-bills/{bill['id']}/mark-paid",
                json={"idempotency_key": "needs-amount-1"},
            )
            assert blocked.status_code == 400, blocked.text
            assert "amount_paid" in blocked.json()["detail"]

            ok = await ac.post(
                f"/api/v1/recurring-bills/{bill['id']}/mark-paid",
                json={"amount_paid": 7250, "idempotency_key": "needs-amount-2"},
            )
            assert ok.status_code == 200, ok.text
            assert ok.json()["occurrence"]["status"] == "paid"
    finally:
        _clear()


@pytest.mark.asyncio
async def test_pause_keeps_occurrences_resume_regenerates(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            account = await _create_account(ac)
            bill = await _create_bill(ac, account["id"])

            before_pause = await ac.get(
                "/api/v1/bill-occurrences",
                params={"recurring_bill_id": bill["id"]},
            )
            assert before_pause.status_code == 200
            initial_count = len(before_pause.json())
            assert initial_count > 0

            paused = await ac.patch(
                f"/api/v1/recurring-bills/{bill['id']}",
                json={"is_active": False},
            )
            assert paused.status_code == 200
            assert paused.json()["is_active"] is False

            # Occurrences must NOT have been cancelled.
            after_pause = await ac.get(
                "/api/v1/bill-occurrences",
                params={"recurring_bill_id": bill["id"]},
            )
            assert len(after_pause.json()) == initial_count

            # Resume — generate_occurrences should be called and succeed.
            resumed = await ac.patch(
                f"/api/v1/recurring-bills/{bill['id']}",
                json={"is_active": True},
            )
            assert resumed.status_code == 200
            assert resumed.json()["is_active"] is True

            after_resume = await ac.get(
                "/api/v1/bill-occurrences",
                params={"recurring_bill_id": bill["id"]},
            )
            assert len(after_resume.json()) >= initial_count
    finally:
        _clear()


@pytest.mark.asyncio
async def test_archive_cancels_future_pending_occurrences(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            account = await _create_account(ac)
            bill = await _create_bill(ac, account["id"])

            archive = await ac.delete(f"/api/v1/recurring-bills/{bill['id']}")
            assert archive.status_code == 200
            assert archive.json()["is_active"] is False

            after = await ac.get(
                "/api/v1/bill-occurrences",
                params={"recurring_bill_id": bill["id"]},
            )
            today_iso = date.today().isoformat()
            future_pending = [
                row
                for row in after.json()
                if row["status"] == "pending" and row["due_date"] >= today_iso
            ]
            cancelled = [row for row in after.json() if row["status"] == "cancelled"]
            assert future_pending == []
            assert cancelled, "expected at least one cancelled future occurrence"
    finally:
        _clear()
