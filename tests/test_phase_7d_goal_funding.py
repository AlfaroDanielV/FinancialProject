"""Phase 7d — goal funding from accounts.

Contract (vault `Decision - Goal Funding From Accounts`):
- An aporte REQUIRES a source account (same currency, fund accounts only),
  is validated against the live balance, and creates a goal-marked negative
  transaction (`transactions.goal_id`) — the account balance drops.
- Goal flows are excluded from income/expense analytics (dashboard, kind
  filters, chat tools) but count in balances; they're machine-managed
  (PATCH/bulk 409).
- Cancel refunds per source account (idempotent via refund_transaction_id);
  achieved never refunds; PATCH status=abandoned is rejected (side door);
  reaching the target does NOT auto-achieve.
- DELETE is a true delete, blocked while money is still refundable.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from api.database import get_db
from api.dependencies import current_user
from api.main import app
from api.models.account import Account
from api.models.goal import Goal
from api.models.goal_contribution import GoalContribution
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


async def _create_account(
    ac: AsyncClient,
    *,
    name: str,
    balance: str = "300000",
    account_type: str = "checking",
    currency: str = "CRC",
) -> dict:
    resp = await ac.post(
        "/api/v1/accounts",
        json={
            "name": name,
            "account_type": account_type,
            "currency": currency,
            "initial_balance": balance,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _create_goal(
    ac: AsyncClient, *, name: str = "Vacaciones", target: str = "500000"
) -> dict:
    resp = await ac.post(
        "/api/v1/goals",
        json={"name": name, "target_amount": target, "target_currency": "CRC"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _balance(ac: AsyncClient, account_id: str) -> Decimal:
    resp = await ac.get(f"/api/v1/accounts/{account_id}")
    assert resp.status_code == 200, resp.text
    return Decimal(str(resp.json()["current_balance"]))


# ── contribution validations ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_contribution_requires_account(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            goal = await _create_goal(ac)
            resp = await ac.post(
                f"/api/v1/goals/{goal['id']}/contributions",
                json={"amount": "10000"},
            )
            assert resp.status_code == 422  # account_id is required
    finally:
        _clear()


@pytest.mark.asyncio
async def test_contribution_rejects_bad_sources(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            goal = await _create_goal(ac)
            credit = await _create_account(
                ac, name="Visa", account_type="credit", balance="0"
            )
            usd = await _create_account(
                ac, name="Dólares", currency="USD", balance="1000"
            )

            foreign = await ac.post(
                f"/api/v1/goals/{goal['id']}/contributions",
                json={"amount": "10000", "account_id": str(uuid.uuid4())},
            )
            assert foreign.status_code == 400

            from_credit = await ac.post(
                f"/api/v1/goals/{goal['id']}/contributions",
                json={"amount": "10000", "account_id": credit["id"]},
            )
            assert from_credit.status_code == 400
            assert "tarjeta" in from_credit.json()["detail"]

            mismatched = await ac.post(
                f"/api/v1/goals/{goal['id']}/contributions",
                json={"amount": "100", "account_id": usd["id"]},
            )
            assert mismatched.status_code == 400
            assert "monedas distintas" in mismatched.json()["detail"]
    finally:
        _clear()


@pytest.mark.asyncio
async def test_contribution_rejects_insufficient_funds(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            goal = await _create_goal(ac)
            account = await _create_account(ac, name="Corriente", balance="50000")

            resp = await ac.post(
                f"/api/v1/goals/{goal['id']}/contributions",
                json={"amount": "60000", "account_id": account["id"]},
            )
            assert resp.status_code == 400
            detail = resp.json()["detail"]
            assert "Fondos insuficientes" in detail
            assert "50.000" in detail  # names the available balance
    finally:
        _clear()


# ── happy path: money moves; analytics don't ──────────────────────────────────


@pytest.mark.asyncio
async def test_contribution_deducts_and_is_not_an_expense(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            goal = await _create_goal(ac)
            account = await _create_account(ac, name="Corriente", balance="300000")

            before = await ac.get("/api/v1/dashboard/summary?period=month_current")
            expense_before = Decimal(str(before.json()["expense_total"]))

            resp = await ac.post(
                f"/api/v1/goals/{goal['id']}/contributions",
                json={"amount": "100000", "account_id": account["id"]},
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert Decimal(str(body["goal"]["current_amount"])) == Decimal("100000")
            assert body["contribution"]["source_account_id"] == account["id"]
            assert body["contribution"]["transaction_id"] is not None

            # The account balance dropped...
            assert await _balance(ac, account["id"]) == Decimal("200000")

            # ...the transaction is goal-marked and negative...
            txn = (
                await session.execute(
                    select(Transaction).where(
                        Transaction.id
                        == uuid.UUID(body["contribution"]["transaction_id"])
                    )
                )
            ).scalar_one()
            assert str(txn.goal_id) == goal["id"]
            assert Decimal(str(txn.amount)) == Decimal("-100000")
            assert "Aporte a meta" in (txn.description or "")

            # ...but "gastos" did NOT move (saving isn't spending)...
            after = await ac.get("/api/v1/dashboard/summary?period=month_current")
            assert Decimal(str(after.json()["expense_total"])) == expense_before

            # ...and kind=expense hides it while kind=all shows it.
            expenses = await ac.get("/api/v1/transactions", params={"kind": "expense"})
            ids = [t["id"] for t in expenses.json()["items"]]
            assert body["contribution"]["transaction_id"] not in ids
            everything = await ac.get("/api/v1/transactions", params={"kind": "all"})
            ids = [t["id"] for t in everything.json()["items"]]
            assert body["contribution"]["transaction_id"] in ids
    finally:
        _clear()


@pytest.mark.asyncio
async def test_chat_tool_aggregate_excludes_goal_flows(db_with_user):
    from app.queries.tools.transactions import aggregate_transactions

    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            goal = await _create_goal(ac)
            account = await _create_account(ac, name="Corriente", balance="300000")
            resp = await ac.post(
                f"/api/v1/goals/{goal['id']}/contributions",
                json={"amount": "100000", "account_id": account["id"]},
            )
            assert resp.status_code == 200

        today = date.today()
        result = await aggregate_transactions(
            start_date=today,
            end_date=today,
            transaction_type="expense",
            group_by="category",
            user_id=user_id,
        )
        # The aporte must not show up as spending in the chat answer.
        assert Decimal(str(result["grand_total"] or 0)) == Decimal("0")
    finally:
        _clear()


# ── no auto-achieve + guards ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_goal_transactions_are_machine_managed(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            goal = await _create_goal(ac)
            account = await _create_account(ac, name="Corriente")
            resp = await ac.post(
                f"/api/v1/goals/{goal['id']}/contributions",
                json={"amount": "10000", "account_id": account["id"]},
            )
            txn_id = resp.json()["contribution"]["transaction_id"]

            patched = await ac.patch(
                f"/api/v1/transactions/{txn_id}", json={"merchant": "x"}
            )
            assert patched.status_code == 409
            assert "meta" in patched.json()["detail"]

            bulk = await ac.post(
                "/api/v1/transactions/bulk/archive",
                json={"transaction_ids": [txn_id]},
            )
            assert bulk.status_code == 409
    finally:
        _clear()


@pytest.mark.asyncio
async def test_patch_status_abandoned_is_rejected(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            goal = await _create_goal(ac)
            resp = await ac.patch(
                f"/api/v1/goals/{goal['id']}", json={"status": "abandoned"}
            )
            assert resp.status_code == 400
            assert "Cancelar meta" in resp.json()["detail"]
    finally:
        _clear()


# ── cancel: preview + refunds per account + idempotency ───────────────────────


@pytest.mark.asyncio
async def test_cancel_refunds_to_source_accounts(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            goal = await _create_goal(ac)
            a = await _create_account(ac, name="Corriente", balance="300000")
            b = await _create_account(ac, name="Ahorros", balance="100000")

            for account, amount in ((a, "60000"), (a, "40000"), (b, "30000")):
                resp = await ac.post(
                    f"/api/v1/goals/{goal['id']}/contributions",
                    json={"amount": amount, "account_id": account["id"]},
                )
                assert resp.status_code == 200, resp.text

            # Legacy sourceless contribution (pre-7d) → unrefundable bucket.
            session.add(
                GoalContribution(
                    goal_id=uuid.UUID(goal["id"]),
                    amount=Decimal("5000"),
                )
            )
            goal_row = (
                await session.execute(
                    select(Goal).where(Goal.id == uuid.UUID(goal["id"]))
                )
            ).scalar_one()
            goal_row.current_amount = Decimal(str(goal_row.current_amount)) + Decimal(
                "5000"
            )
            await session.commit()

            preview = await ac.get(f"/api/v1/goals/{goal['id']}/cancel-preview")
            assert preview.status_code == 200, preview.text
            pv = preview.json()
            assert Decimal(str(pv["refundable_total"])) == Decimal("130000")
            assert Decimal(str(pv["unrefundable_total"])) == Decimal("5000")
            by_name = {r["account_name"]: Decimal(str(r["amount"])) for r in pv["refunds"]}
            assert by_name == {"Corriente": Decimal("100000"), "Ahorros": Decimal("30000")}

            cancelled = await ac.post(f"/api/v1/goals/{goal['id']}/cancel")
            assert cancelled.status_code == 200, cancelled.text
            result = cancelled.json()
            assert result["goal"]["status"] == "abandoned"
            assert Decimal(str(result["refunded_total"])) == Decimal("130000")
            assert Decimal(str(result["unrefundable_total"])) == Decimal("5000")
            # current_amount clamps to the unrefunded remainder.
            assert Decimal(str(result["goal"]["current_amount"])) == Decimal("5000")

            # The money is back where it came from.
            assert await _balance(ac, a["id"]) == Decimal("300000")
            assert await _balance(ac, b["id"]) == Decimal("100000")

            # Idempotent: a second cancel refunds nothing more.
            again = await ac.post(f"/api/v1/goals/{goal['id']}/cancel")
            assert again.status_code == 200
            assert Decimal(str(again.json()["refunded_total"])) == Decimal("0")
            assert await _balance(ac, a["id"]) == Decimal("300000")
    finally:
        _clear()


@pytest.mark.asyncio
async def test_achieved_goal_never_refunds(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            goal = await _create_goal(ac, target="100000")
            account = await _create_account(ac, name="Corriente", balance="150000")
            resp = await ac.post(
                f"/api/v1/goals/{goal['id']}/contributions",
                json={"amount": "100000", "account_id": account["id"]},
            )
            assert resp.status_code == 200
            # No auto-achieve: still active at 100%.
            assert resp.json()["goal"]["status"] == "active"

            # Explicit achieve (the app double-confirms before calling this).
            achieved = await ac.patch(
                f"/api/v1/goals/{goal['id']}", json={"status": "completed"}
            )
            assert achieved.status_code == 200
            assert achieved.json()["status"] == "achieved"

            # Cancel is blocked — cumplida never refunds.
            blocked = await ac.post(f"/api/v1/goals/{goal['id']}/cancel")
            assert blocked.status_code == 400
            assert "cumplida" in blocked.json()["detail"]
            assert await _balance(ac, account["id"]) == Decimal("50000")
    finally:
        _clear()


@pytest.mark.asyncio
async def test_delete_blocked_until_cancelled(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            goal = await _create_goal(ac)
            account = await _create_account(ac, name="Corriente")
            resp = await ac.post(
                f"/api/v1/goals/{goal['id']}/contributions",
                json={"amount": "10000", "account_id": account["id"]},
            )
            txn_id = resp.json()["contribution"]["transaction_id"]

            blocked = await ac.delete(f"/api/v1/goals/{goal['id']}")
            assert blocked.status_code == 409
            assert "Cancelá la meta primero" in blocked.json()["detail"]

            await ac.post(f"/api/v1/goals/{goal['id']}/cancel")
            deleted = await ac.delete(f"/api/v1/goals/{goal['id']}")
            assert deleted.status_code == 204

        session.expire_all()
        # Goal + contributions gone; the account movements survive unlinked.
        assert (
            await session.execute(
                select(Goal).where(Goal.id == uuid.UUID(goal["id"]))
            )
        ).scalar_one_or_none() is None
        assert (
            await session.execute(
                select(GoalContribution).where(
                    GoalContribution.goal_id == uuid.UUID(goal["id"])
                )
            )
        ).scalars().all() == []
        survivor = (
            await session.execute(
                select(Transaction).where(Transaction.id == uuid.UUID(txn_id))
            )
        ).scalar_one()
        assert survivor.goal_id is None
    finally:
        _clear()
