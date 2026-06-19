"""Phase 6e B2 — Centro Financiero base backend contracts."""
from __future__ import annotations

import time
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import insert, select, text

from api.database import get_db
from api.dependencies import current_user
from api.main import app
from api.models.transaction import Transaction
from api.services.dashboard.materialized import refresh_dashboard_materialized_views


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


@pytest.mark.asyncio
async def test_goals_crud_and_contribution_endpoint(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            account = await ac.post(
                "/api/v1/accounts",
                json={
                    "name": "Ahorro metas",
                    "account_type": "savings",
                    "currency": "CRC",
                    # Phase 7d: contributions are funded — the account must
                    # hold the money the aporte moves.
                    "initial_balance": "100000",
                },
            )
            assert account.status_code == 201, account.text
            account_id = account.json()["id"]

            created = await ac.post(
                "/api/v1/goals",
                json={
                    "name": "Fondo emergencia",
                    "target_amount": "1000000",
                    "target_currency": "CRC",
                    "target_date": "2026-12-31",
                    "linked_account_id": account_id,
                },
            )
            assert created.status_code == 201, created.text
            goal = created.json()
            assert goal["target_date"] == "2026-12-31"
            assert goal["deadline"] == "2026-12-31"

            patched = await ac.patch(
                f"/api/v1/goals/{goal['id']}",
                json={"status": "paused"},
            )
            assert patched.status_code == 200, patched.text
            assert patched.json()["status"] == "paused"

            resumed = await ac.patch(
                f"/api/v1/goals/{goal['id']}",
                json={"status": "active"},
            )
            assert resumed.status_code == 200, resumed.text

            contribution = await ac.post(
                f"/api/v1/goals/{goal['id']}/contributions",
                json={"amount": "25000", "account_id": account_id},
            )
            assert contribution.status_code == 200, contribution.text
            body = contribution.json()
            assert Decimal(str(body["goal"]["current_amount"])) == Decimal("25000")
            assert Decimal(str(body["contribution"]["amount"])) == Decimal("25000")

            # Phase 7d: a goal holding money can't be deleted — cancel first
            # (the refund goes back to the source account), then delete.
            blocked = await ac.delete(f"/api/v1/goals/{goal['id']}")
            assert blocked.status_code == 409, blocked.text

            cancelled = await ac.post(f"/api/v1/goals/{goal['id']}/cancel")
            assert cancelled.status_code == 200, cancelled.text
            assert cancelled.json()["goal"]["status"] == "abandoned"
            assert Decimal(
                str(cancelled.json()["refunded_total"])
            ) == Decimal("25000")

            deleted = await ac.delete(f"/api/v1/goals/{goal['id']}")
            assert deleted.status_code == 204, deleted.text
    finally:
        _clear()


@pytest.mark.asyncio
async def test_transfer_with_fx_creates_linked_transactions_and_dashboard_excludes_it(
    db_with_user,
):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            crc = await ac.post(
                "/api/v1/accounts",
                json={
                    "name": "BAC CRC",
                    "account_type": "checking",
                    "currency": "CRC",
                    "initial_balance": "100000",
                },
            )
            usd = await ac.post(
                "/api/v1/accounts",
                json={
                    "name": "BAC USD",
                    "account_type": "savings",
                    "currency": "USD",
                    "initial_balance": "0",
                },
            )
            assert crc.status_code == 201, crc.text
            assert usd.status_code == 201, usd.text

            transfer = await ac.post(
                "/api/v1/transfers",
                json={
                    "from_account_id": crc.json()["id"],
                    "to_account_id": usd.json()["id"],
                    "amount": "1000",
                    "currency": "CRC",
                    # Canonical fx_rate = funding(CRC) per 1 destination(USD).
                    # Mode B (amount in CRC): applied = 1000 ÷ 500 = $2.00.
                    "fx_rate": "500",
                    "occurred_at": datetime.now(timezone.utc).isoformat(),
                    "notes": "Compra de dólares",
                },
            )
            assert transfer.status_code == 201, transfer.text
            body = transfer.json()
            assert body["debit_transaction_id"]
            assert body["credit_transaction_id"]

            txns = await session.execute(
                select(Transaction).where(Transaction.transfer_id == body["id"])
            )
            amounts = sorted(Decimal(txn.amount) for txn in txns.scalars().all())
            assert amounts == [Decimal("-1000.00"), Decimal("2.00")]

            summary = await ac.get(
                "/api/v1/dashboard/summary", params={"period": "month_current"}
            )
            assert summary.status_code == 200, summary.text
            dashboard = summary.json()
            assert Decimal(str(dashboard["income_total"])) == Decimal("0")
            assert Decimal(str(dashboard["expense_total"])) == Decimal("0")
            assert dashboard["transfer_rows_excluded"] == 2
    finally:
        _clear()


@pytest.mark.asyncio
async def test_categories_archive_custom_category_preserves_transaction_count(
    db_with_user,
):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            created = await ac.post(
                "/api/v1/categories",
                json={
                    "name": "Marchamo",
                    "kind": "expense",
                    "color": "#123abc",
                    "icon": "car-front",
                },
            )
            assert created.status_code == 201, created.text
            category_id = created.json()["id"]

            txn = await ac.post(
                "/api/v1/transactions",
                json={
                    "amount": "-125000",
                    "currency": "CRC",
                    "merchant": "INS",
                    "category": "Marchamo",
                    "category_id": category_id,
                    "transaction_date": date.today().isoformat(),
                    "source": "manual",
                },
            )
            assert txn.status_code == 201, txn.text

            archived = await ac.patch(
                f"/api/v1/categories/{category_id}", json={"archived": True}
            )
            assert archived.status_code == 200, archived.text
            assert archived.json()["archived"] is True
            assert archived.json()["transaction_count"] == 1

            visible = await ac.get("/api/v1/categories")
            assert visible.status_code == 200, visible.text
            assert category_id not in {item["id"] for item in visible.json()}

            all_categories = await ac.get(
                "/api/v1/categories", params={"include_archived": "true"}
            )
            archived_rows = [
                item for item in all_categories.json() if item["id"] == category_id
            ]
            assert archived_rows[0]["transaction_count"] == 1
    finally:
        _clear()


@pytest.mark.asyncio
async def test_dashboard_summary_cash_flow_and_materialized_view_with_10k_rows(
    db_with_user,
):
    session, user_id = db_with_user
    today = date.today()
    rows = []
    for index in range(10_000):
        rows.append(
            {
                "user_id": user_id,
                "amount": Decimal("1000") if index % 2 == 0 else Decimal("-200"),
                "currency": "CRC",
                "merchant": "Seed",
                "description": "stress seed",
                "category": "ingreso_extra" if index % 2 == 0 else "ocio",
                "transaction_date": today,
                "source": "manual",
                "parse_status": "confirmed",
                "status": "confirmed",
                "is_duplicate": False,
            }
        )
    await session.execute(insert(Transaction), rows)
    await session.commit()

    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            start = time.perf_counter()
            summary = await ac.get(
                "/api/v1/dashboard/summary", params={"period": "month_current"}
            )
            elapsed = time.perf_counter() - start
            assert summary.status_code == 200, summary.text
            assert elapsed < 0.5
            body = summary.json()
            assert Decimal(str(body["income_total"])) == Decimal("5000000")
            assert Decimal(str(body["expense_total"])) == Decimal("1000000")
            assert Decimal(str(body["net_flow"])) == Decimal("4000000")

            month = today.strftime("%Y-%m")
            cash_flow = await ac.get(
                "/api/v1/dashboard/cash-flow",
                params={"from": month, "to": month},
            )
            assert cash_flow.status_code == 200, cash_flow.text
            assert cash_flow.json()["points"][0]["month"] == month

        await refresh_dashboard_materialized_views(session)
        await session.commit()
        mv_result = await session.execute(
            text(
                "SELECT income_total, expense_total "
                "FROM mv_monthly_summary_by_user "
                "WHERE user_id = :user_id AND month = :month"
            ),
            {"user_id": user_id, "month": date(today.year, today.month, 1)},
        )
        mv_row = mv_result.one()
        assert Decimal(mv_row[0]) == Decimal("5000000.00")
        assert Decimal(mv_row[1]) == Decimal("1000000.00")
    finally:
        _clear()
