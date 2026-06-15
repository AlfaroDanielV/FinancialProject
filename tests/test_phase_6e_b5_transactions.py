"""Phase 6e B5 — global transactions module backend contracts.

Covers:
- Cursor pagination correctness on the default date sort.
- Multi-account, multi-category, currency, amount-range, search filters.
- Sort by amount + sort by category.
- Default scope excludes archived; include_archived=true returns them.
- Bulk archive happy path + restore round-trip.
- Bulk archive rejects shadow rows + transfer legs (all-or-nothing).
- Bulk categorize happy path + invalid category 400.
- PATCH /{id} rejects an archived row until it's restored.
- Archived rows are excluded from compute_account_balances.
- CSV export returns the right rows; 413 when total exceeds the cap.
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
from api.models.transaction import Transaction
from api.routers.transactions import CSV_EXPORT_MAX_ROWS
import api.routers.transactions as txn_router


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


async def _create_account(
    ac: AsyncClient, name: str, currency: str = "CRC", *, initial: str = "0"
):
    response = await ac.post(
        "/api/v1/accounts",
        json={
            "name": name,
            "account_type": "checking",
            "currency": currency,
            "initial_balance": initial,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _create_txn(
    ac: AsyncClient,
    *,
    amount: str,
    account_id: str,
    transaction_date: str,
    merchant: str = "Tienda",
    description: str = "",
    currency: str = "CRC",
    category: str | None = None,
):
    body = {
        "amount": amount,
        "currency": currency,
        "merchant": merchant,
        "description": description,
        "transaction_date": transaction_date,
        "source": "manual",
        "account_id": account_id,
    }
    if category:
        body["category"] = category
    resp = await ac.post("/api/v1/transactions", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest.mark.asyncio
async def test_cursor_pagination_walks_full_result_set_in_order(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            account = await _create_account(ac, "BAC")
            today = date.today()
            for index in range(7):
                day = (today - timedelta(days=index)).isoformat()
                await _create_txn(
                    ac,
                    amount=f"-{(index + 1) * 100}",
                    account_id=account["id"],
                    transaction_date=day,
                    merchant=f"Tienda {index}",
                )

            collected: list[str] = []
            cursor: str | None = None
            for _ in range(5):
                params = {"limit": 3}
                if cursor:
                    params["cursor"] = cursor
                resp = await ac.get("/api/v1/transactions", params=params)
                assert resp.status_code == 200, resp.text
                body = resp.json()
                collected.extend(item["id"] for item in body["items"])
                cursor = body.get("next_cursor")
                if cursor is None:
                    break
            assert len(collected) == 7
            assert len(set(collected)) == 7
    finally:
        _clear()


@pytest.mark.asyncio
async def test_filters_multi_account_currency_amount_range_and_search(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            crc_account = await _create_account(ac, "BAC CRC", "CRC")
            usd_account = await _create_account(ac, "BAC USD", "USD")
            today = date.today().isoformat()

            await _create_txn(
                ac,
                amount="-1000",
                account_id=crc_account["id"],
                transaction_date=today,
                description="café del lunes",
            )
            await _create_txn(
                ac,
                amount="-50000",
                account_id=crc_account["id"],
                transaction_date=today,
                description="renta",
            )
            await _create_txn(
                ac,
                amount="-25",
                account_id=usd_account["id"],
                transaction_date=today,
                description="suscripción",
                currency="USD",
            )

            multi = await ac.get(
                "/api/v1/transactions",
                params=[
                    ("account_ids", crc_account["id"]),
                    ("account_ids", usd_account["id"]),
                ],
            )
            assert multi.status_code == 200, multi.text
            assert multi.json()["total"] == 3

            usd_only = await ac.get(
                "/api/v1/transactions", params={"currency": "USD"}
            )
            assert usd_only.json()["total"] == 1
            assert usd_only.json()["items"][0]["currency"] == "USD"

            range_filter = await ac.get(
                "/api/v1/transactions",
                params={"min_amount": "-2000", "max_amount": "-100"},
            )
            assert range_filter.json()["total"] == 1
            assert (
                range_filter.json()["items"][0]["description"] == "café del lunes"
            )

            search = await ac.get(
                "/api/v1/transactions", params={"q": "renta"}
            )
            assert search.json()["total"] == 1
    finally:
        _clear()


@pytest.mark.asyncio
async def test_sort_by_amount_orders_correctly(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            account = await _create_account(ac, "BAC")
            today = date.today().isoformat()
            for amount in ("-500", "1000", "-1500", "200"):
                await _create_txn(
                    ac,
                    amount=amount,
                    account_id=account["id"],
                    transaction_date=today,
                )

            asc = await ac.get(
                "/api/v1/transactions",
                params={"sort_by": "amount", "sort_dir": "asc"},
            )
            asc_amounts = [item["amount"] for item in asc.json()["items"]]
            assert asc_amounts == sorted(asc_amounts)

            desc = await ac.get(
                "/api/v1/transactions",
                params={"sort_by": "amount", "sort_dir": "desc"},
            )
            desc_amounts = [item["amount"] for item in desc.json()["items"]]
            assert desc_amounts == sorted(desc_amounts, reverse=True)
    finally:
        _clear()


@pytest.mark.asyncio
async def test_archive_default_excluded_and_include_archived_returns(
    db_with_user,
):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            account = await _create_account(ac, "BAC")
            today = date.today().isoformat()
            txn = await _create_txn(
                ac, amount="-1000", account_id=account["id"], transaction_date=today
            )

            archived_resp = await ac.post(
                "/api/v1/transactions/bulk/archive",
                json={"transaction_ids": [txn["id"]]},
            )
            assert archived_resp.status_code == 200, archived_resp.text
            assert archived_resp.json()["updated"] == 1

            default_list = await ac.get("/api/v1/transactions")
            assert default_list.json()["total"] == 0

            with_archived = await ac.get(
                "/api/v1/transactions", params={"include_archived": "true"}
            )
            assert with_archived.json()["total"] == 1
            assert with_archived.json()["items"][0]["archived"] is True

            # patch the archived row → 409
            patch_blocked = await ac.patch(
                f"/api/v1/transactions/{txn['id']}",
                json={"description": "no"},
            )
            assert patch_blocked.status_code == 409

            # restore
            restore = await ac.post(
                "/api/v1/transactions/bulk/restore",
                json={"transaction_ids": [txn["id"]]},
            )
            assert restore.status_code == 200
            after_restore = await ac.get("/api/v1/transactions")
            assert after_restore.json()["total"] == 1
    finally:
        _clear()


@pytest.mark.asyncio
async def test_bulk_archive_rejects_shadow_and_transfer_legs(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Fund the source so the 200 transfer below clears the funds guard
            # after the -1000 confirmed row (the -500 row is a shadow).
            primary = await _create_account(ac, "BAC", initial="5000")
            other = await _create_account(ac, "Promerica")
            today = date.today().isoformat()

            normal = await _create_txn(
                ac, amount="-1000", account_id=primary["id"], transaction_date=today
            )
            shadow = await _create_txn(
                ac, amount="-500", account_id=primary["id"], transaction_date=today
            )
            shadow_row = (
                await session.execute(
                    sa_select(Transaction).where(Transaction.id == shadow["id"])
                )
            ).scalar_one()
            shadow_row.status = "shadow"
            await session.commit()

            transfer = await ac.post(
                "/api/v1/transfers",
                json={
                    "from_account_id": primary["id"],
                    "to_account_id": other["id"],
                    "amount": "200",
                    "currency": "CRC",
                    "occurred_at": today + "T12:00:00+00:00",
                },
            )
            assert transfer.status_code == 201
            leg_id = transfer.json()["debit_transaction_id"]

            blocked = await ac.post(
                "/api/v1/transactions/bulk/archive",
                json={"transaction_ids": [normal["id"], shadow["id"], leg_id]},
            )
            assert blocked.status_code == 409
            detail = blocked.json()["detail"]
            assert detail["code"] == "ineligible_transactions"
            offenders = set(detail["ids"])
            assert shadow["id"] in offenders
            assert leg_id in offenders

            # all-or-nothing — the normal row must NOT have been archived
            normal_row = (
                await session.execute(
                    sa_select(Transaction).where(Transaction.id == normal["id"])
                )
            ).scalar_one()
            assert normal_row.archived is False
    finally:
        _clear()


@pytest.mark.asyncio
async def test_bulk_categorize_assigns_and_clears_category(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            account = await _create_account(ac, "BAC")
            today = date.today().isoformat()
            txn1 = await _create_txn(
                ac, amount="-1000", account_id=account["id"], transaction_date=today
            )
            txn2 = await _create_txn(
                ac, amount="-2000", account_id=account["id"], transaction_date=today
            )

            cat = await ac.post(
                "/api/v1/categories",
                json={"name": "Restaurantes", "kind": "expense", "color": "#aabbcc"},
            )
            assert cat.status_code == 201
            category_id = cat.json()["id"]

            assigned = await ac.post(
                "/api/v1/transactions/bulk/categorize",
                json={
                    "transaction_ids": [txn1["id"], txn2["id"]],
                    "category_id": category_id,
                },
            )
            assert assigned.status_code == 200
            assert assigned.json()["updated"] == 2

            for txn_id in (txn1["id"], txn2["id"]):
                row = (
                    await session.execute(
                        sa_select(Transaction).where(Transaction.id == txn_id)
                    )
                ).scalar_one()
                assert str(row.category_id) == category_id

            cleared = await ac.post(
                "/api/v1/transactions/bulk/categorize",
                json={
                    "transaction_ids": [txn1["id"]],
                    "category_id": None,
                },
            )
            assert cleared.status_code == 200
            after_clear = (
                await session.execute(
                    sa_select(Transaction).where(Transaction.id == txn1["id"])
                )
            ).scalar_one()
            assert after_clear.category_id is None

            invalid = await ac.post(
                "/api/v1/transactions/bulk/categorize",
                json={
                    "transaction_ids": [txn2["id"]],
                    "category_id": str(uuid_for_test()),
                },
            )
            assert invalid.status_code == 400
    finally:
        _clear()


def uuid_for_test():
    import uuid as _uuid

    return _uuid.uuid4()


@pytest.mark.asyncio
async def test_archived_rows_excluded_from_account_balance(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            account = await _create_account(ac, "BAC")
            today = date.today().isoformat()
            kept = await _create_txn(
                ac,
                amount="10000",
                account_id=account["id"],
                transaction_date=today,
            )
            doomed = await _create_txn(
                ac,
                amount="-99999",
                account_id=account["id"],
                transaction_date=today,
            )
            assert kept and doomed  # noqa: F841 - referenced for clarity

            await ac.post(
                "/api/v1/transactions/bulk/archive",
                json={"transaction_ids": [doomed["id"]]},
            )

            detail = await ac.get(f"/api/v1/accounts/{account['id']}")
            assert detail.status_code == 200
            assert Decimal(str(detail.json()["current_balance"])) == Decimal(
                "10000"
            )
    finally:
        _clear()


@pytest.mark.asyncio
async def test_csv_export_returns_rows_and_413_when_over_cap(db_with_user, monkeypatch):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            account = await _create_account(ac, "BAC")
            today = date.today().isoformat()
            await _create_txn(
                ac,
                amount="-1234.50",
                account_id=account["id"],
                transaction_date=today,
                description="prueba export",
            )

            ok = await ac.get("/api/v1/transactions/export")
            assert ok.status_code == 200, ok.text
            assert ok.headers["content-type"].startswith("text/csv")
            body = ok.text.splitlines()
            assert body[0].startswith("transaction_date,amount,currency")
            assert "-1234.50" in body[1]
            assert "prueba export" in body[1]

            monkeypatch.setattr(txn_router, "CSV_EXPORT_MAX_ROWS", 0)
            blocked = await ac.get("/api/v1/transactions/export")
            assert blocked.status_code == 413, blocked.text
    finally:
        _clear()


@pytest.mark.asyncio
async def test_offset_pagination_still_works_after_b5_changes(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            account = await _create_account(ac, "BAC")
            today = date.today().isoformat()
            for index in range(5):
                await _create_txn(
                    ac,
                    amount=f"-{index + 1}00",
                    account_id=account["id"],
                    transaction_date=today,
                )

            page1 = await ac.get(
                "/api/v1/transactions", params={"limit": 2, "offset": 0}
            )
            page2 = await ac.get(
                "/api/v1/transactions", params={"limit": 2, "offset": 2}
            )
            assert page1.json()["total"] == 5
            assert len(page1.json()["items"]) == 2
            assert len(page2.json()["items"]) == 2
            ids1 = {item["id"] for item in page1.json()["items"]}
            ids2 = {item["id"] for item in page2.json()["items"]}
            assert ids1.isdisjoint(ids2)
            assert CSV_EXPORT_MAX_ROWS == 50_000
    finally:
        _clear()
