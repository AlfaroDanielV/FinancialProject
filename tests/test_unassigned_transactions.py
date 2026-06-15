"""Movimientos sin cuenta — the orphan read tool + the deterministic
open_screen handoff to the native 'Sin cuenta' screen.

Context: in dogfooding the in-app agent confused "sin cuenta" (account) with
"sin categoría" (category) because no tool surfaced `account_id IS NULL` rows.
`list_unassigned_transactions` answers that query; when it runs, `run_dispatch`
attaches an `assign_account` open_screen handoff (deterministic — the LLM never
sets it).
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app.queries.dispatcher import _handoff_open_screen
from app.queries.tools.transactions import list_unassigned_transactions
from api.models.account import Account
from api.models.transaction import Transaction


class _SessionContext:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return None


async def _seed_txn(
    session,
    *,
    user_id,
    account_id,
    amount: str,
    merchant: str,
    status: str = "confirmed",
) -> Transaction:
    txn = Transaction(
        user_id=user_id,
        account_id=account_id,
        amount=Decimal(amount),
        currency="CRC",
        merchant=merchant,
        transaction_date=date(2026, 6, 1),
        created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        status=status,
        source="manual",
    )
    session.add(txn)
    await session.commit()
    await session.refresh(txn)
    return txn


@pytest.mark.asyncio
async def test_list_unassigned_returns_only_confirmed_orphan_rows(
    db_with_user, monkeypatch
):
    session, user_id = db_with_user
    monkeypatch.setattr(
        "app.queries.tools.transactions.AsyncSessionLocal",
        lambda: _SessionContext(session),
    )
    account = Account(user_id=user_id, name="BAC", account_type="checking")
    session.add(account)
    await session.commit()
    await session.refresh(account)

    # Has an account → excluded.
    await _seed_txn(
        session, user_id=user_id, account_id=account.id, amount="-1000",
        merchant="Con cuenta",
    )
    # Confirmed orphans → included.
    await _seed_txn(
        session, user_id=user_id, account_id=None, amount="-3000",
        merchant="Sin cuenta A",
    )
    await _seed_txn(
        session, user_id=user_id, account_id=None, amount="-2000",
        merchant="Sin cuenta B",
    )
    # Orphan but shadow (not assignable until promoted) → excluded.
    await _seed_txn(
        session, user_id=user_id, account_id=None, amount="-500",
        merchant="Shadow", status="shadow",
    )

    result = await list_unassigned_transactions(user_id=user_id)

    assert result["total_matched"] == 2
    assert result["total_amount"] == "5000"  # abs(3000) + abs(2000)
    assert {t["merchant"] for t in result["transactions"]} == {
        "Sin cuenta A",
        "Sin cuenta B",
    }
    assert all(t["transaction_type"] == "expense" for t in result["transactions"])


def test_handoff_open_screen_only_for_orphan_tool():
    screen = {"screen": "assign_account", "prefill": {"filter": "no_account"}}
    # The orphan tool fired → handoff (dict or plain-string tool entries).
    assert _handoff_open_screen([{"name": "list_unassigned_transactions"}]) == screen
    assert _handoff_open_screen(["list_unassigned_transactions"]) == screen
    # Any other answer → no handoff.
    assert _handoff_open_screen([{"name": "list_transactions"}]) is None
    assert _handoff_open_screen([]) is None
    assert _handoff_open_screen(None) is None
