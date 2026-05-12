"""Phase 6d B7 — recurring bills onboarding contract."""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from api.database import get_db
from api.dependencies import current_user
from api.main import app
from api.models.bill_occurrence import BillOccurrence
from api.models.recurring_bill import RecurringBill
from api.services.recurrence import today_cr


def _override(session, user_id):
    class _StubUser:
        def __init__(self) -> None:
            self.id = user_id
            self.status = "active"

    async def _yield_session():
        yield session

    app.dependency_overrides[current_user] = lambda: _StubUser()
    app.dependency_overrides[get_db] = _yield_session


def _clear():
    app.dependency_overrides.pop(current_user, None)
    app.dependency_overrides.pop(get_db, None)


def _payload(**overrides):
    today = today_cr()
    payload = {
        "name": "Internet casa",
        "provider": "ICE",
        "category": "servicios",
        "amount_expected": 25000,
        "currency": "CRC",
        "is_variable_amount": False,
        "frequency": "monthly",
        "day_of_month": today.day,
        "start_date": today.isoformat(),
        "lead_time_days": 3,
        "notes": "B7 onboarding",
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_create_recurring_bill_accepts_phase_6d_default_category(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post("/api/v1/recurring-bills", json=_payload())
    finally:
        _clear()

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["category"] == "servicios"
    assert Decimal(str(body["amount_expected"])) == Decimal("25000.0")

    occurrences = await session.execute(
        select(BillOccurrence).where(
            BillOccurrence.recurring_bill_id == uuid.UUID(body["id"])
        )
    )
    assert len(list(occurrences.scalars().all())) >= 1


@pytest.mark.asyncio
async def test_recurring_bill_crud_for_onboarding_flow(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            created = await ac.post("/api/v1/recurring-bills", json=_payload())
            assert created.status_code == 201, created.text
            bill_id = created.json()["id"]

            listed = await ac.get("/api/v1/recurring-bills", params={"is_active": True})
            updated = await ac.patch(
                f"/api/v1/recurring-bills/{bill_id}",
                json={
                    "name": "Internet casa actualizado",
                    "category": "vivienda",
                    "amount_expected": 28000,
                    "frequency": "annual",
                    "day_of_month": today_cr().day,
                    "start_date": today_cr().isoformat(),
                },
            )
            deleted = await ac.delete(f"/api/v1/recurring-bills/{bill_id}")
            active_after_delete = await ac.get(
                "/api/v1/recurring-bills",
                params={"is_active": True},
            )
    finally:
        _clear()

    assert listed.status_code == 200, listed.text
    assert [item["id"] for item in listed.json()] == [bill_id]

    assert updated.status_code == 200, updated.text
    assert updated.json()["name"] == "Internet casa actualizado"
    assert updated.json()["category"] == "vivienda"
    assert updated.json()["frequency"] == "annual"

    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["is_active"] is False
    assert active_after_delete.status_code == 200, active_after_delete.text
    assert active_after_delete.json() == []

    row = await session.get(RecurringBill, uuid.UUID(bill_id))
    assert row is not None
    assert row.is_active is False


@pytest.mark.asyncio
async def test_recurring_bill_rejects_unknown_category(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/recurring-bills",
                json=_payload(category="categoria_inventada"),
            )
    finally:
        _clear()

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_custom_frequency_requires_rrule(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            missing_rule = await ac.post(
                "/api/v1/recurring-bills",
                json=_payload(frequency="custom", day_of_month=None),
            )
            valid = await ac.post(
                "/api/v1/recurring-bills",
                json=_payload(
                    name="AyA",
                    provider="AyA",
                    frequency="custom",
                    day_of_month=None,
                    recurrence_rule="FREQ=MONTHLY;INTERVAL=2",
                    is_variable_amount=True,
                    amount_expected=None,
                ),
            )
    finally:
        _clear()

    assert missing_rule.status_code == 422
    assert valid.status_code == 201, valid.text
    body = valid.json()
    assert body["frequency"] == "custom"
    assert body["recurrence_rule"] == "FREQ=MONTHLY;INTERVAL=2"
    assert body["is_variable_amount"] is True
    assert body["amount_expected"] is None
