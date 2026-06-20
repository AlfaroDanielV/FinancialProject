"""Forward-only anchor projection in `compute_account_balances` (A4/A5).

balance = latest_anchor.value + Σ(confirmed non-archived txns, date > effective_date)
with a pre-anchor fallback to `initial_balance + Σ(all)` when an account has no
anchor (so the cutover is byte-identical until a user anchors/re-anchors).
"""
from __future__ import annotations

import socket
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from urllib.parse import urlparse

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from api.config import settings
from api.database import get_db
from api.dependencies import current_user
from api.main import app
from api.models.account import Account
from api.models.account_anchor import AccountAnchor
from api.models.transaction import Transaction
from api.models.user import User
from api.services.accounts import compute_account_balances
from api.services.anchors import (
    AJUSTE_CATEGORY,
    apply_anchor,
    latest_anchor_sources,
    needs_balance_confirmation,
)
from api.services.dashboard.summary import get_dashboard_summary


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


async def _account(db, user_id, *, initial="0", account_type="checking") -> Account:
    a = Account(
        user_id=user_id,
        name=f"Acct {uuid.uuid4().hex[:8]}",
        account_type=account_type,
        currency="CRC",
        initial_balance=Decimal(initial),
    )
    db.add(a)
    await db.commit()
    await db.refresh(a)
    return a


async def _txn(db, user_id, account_id, amount, d, *, status="confirmed", archived=False):
    db.add(
        Transaction(
            user_id=user_id,
            account_id=account_id,
            amount=Decimal(amount),
            currency="CRC",
            merchant="seed",
            transaction_date=d,
            source="manual",
            status=status,
            archived=archived,
        )
    )
    await db.commit()


async def _anchor(db, user_id, account_id, value, eff, *, source="onboarding", created=None):
    an = AccountAnchor(
        user_id=user_id,
        account_id=account_id,
        value=Decimal(value),
        currency="CRC",
        effective_date=eff,
        source=source,
    )
    if created is not None:
        an.created_at = created
    db.add(an)
    await db.commit()


async def test_no_anchor_falls_back_to_initial_plus_sum(db_with_user):
    """An account with no anchor reproduces the pre-anchor behavior exactly."""
    db, user_id = db_with_user
    a = await _account(db, user_id, initial="100000")
    await _txn(db, user_id, a.id, "-25000", date(2026, 4, 10))
    await _txn(db, user_id, a.id, "5000", date(2026, 4, 12))
    bal = await compute_account_balances(db, user_id=user_id, account_ids=[a.id])
    assert bal[a.id].current == Decimal("80000")  # 100000 - 25000 + 5000


async def test_anchor_projects_forward_only(db_with_user):
    """The anchor value replaces initial_balance; pre-anchor txns are ignored."""
    db, user_id = db_with_user
    a = await _account(db, user_id, initial="999999")  # ignored once anchored
    await _txn(db, user_id, a.id, "-500000", date(2026, 3, 1))  # pre-anchor → out
    await _anchor(db, user_id, a.id, "200000", date(2026, 4, 1))
    await _txn(db, user_id, a.id, "-30000", date(2026, 4, 5))
    await _txn(db, user_id, a.id, "10000", date(2026, 4, 8))
    bal = await compute_account_balances(db, user_id=user_id, account_ids=[a.id])
    assert bal[a.id].current == Decimal("180000")  # 200000 - 30000 + 10000


async def test_same_day_as_anchor_is_excluded(db_with_user):
    """Strict `>` (rule #6): a txn dated ON the effective_date is pre-anchor."""
    db, user_id = db_with_user
    a = await _account(db, user_id, initial="0")
    await _anchor(db, user_id, a.id, "50000", date(2026, 4, 1))
    await _txn(db, user_id, a.id, "-7000", date(2026, 4, 1))  # same day → excluded
    await _txn(db, user_id, a.id, "-3000", date(2026, 4, 2))  # next day → counted
    bal = await compute_account_balances(db, user_id=user_id, account_ids=[a.id])
    assert bal[a.id].current == Decimal("47000")  # 50000 - 3000


async def test_latest_anchor_wins(db_with_user):
    """Re-anchor: the row with the greatest (effective_date, created_at) wins."""
    db, user_id = db_with_user
    a = await _account(db, user_id, initial="0")
    await _anchor(
        db, user_id, a.id, "100000", date(2026, 4, 1),
        created=datetime(2026, 4, 1, tzinfo=timezone.utc),
    )
    await _anchor(
        db, user_id, a.id, "75000", date(2026, 5, 1), source="reanchor",
        created=datetime(2026, 5, 1, tzinfo=timezone.utc),
    )
    await _txn(db, user_id, a.id, "-5000", date(2026, 5, 10))
    bal = await compute_account_balances(db, user_id=user_id, account_ids=[a.id])
    assert bal[a.id].current == Decimal("70000")  # 75000 - 5000


async def test_shadow_and_archived_excluded_with_anchor(db_with_user):
    """Only confirmed, non-archived txns count post-anchor."""
    db, user_id = db_with_user
    a = await _account(db, user_id, initial="0")
    await _anchor(db, user_id, a.id, "40000", date(2026, 4, 1))
    await _txn(db, user_id, a.id, "-1000", date(2026, 4, 5), status="shadow")
    await _txn(db, user_id, a.id, "-2000", date(2026, 4, 6), archived=True)
    await _txn(db, user_id, a.id, "-3000", date(2026, 4, 7))
    bal = await compute_account_balances(db, user_id=user_id, account_ids=[a.id])
    assert bal[a.id].current == Decimal("37000")  # 40000 - 3000 only


# ── A6/A7: re-anchor / heal-drift ───────────────────────────────────────────


def test_needs_balance_confirmation_pure():
    """Only the 0035 'migrated' backfill anchor nudges; a fresh (anchorless) or
    re-anchored account does not."""
    assert needs_balance_confirmation("migrated") is True
    assert needs_balance_confirmation(None) is False
    assert needs_balance_confirmation("onboarding") is False
    assert needs_balance_confirmation("reanchor") is False


async def test_reanchor_heals_balance_and_writes_ajuste(db_with_user):
    """State the real balance → new anchor + an ajuste for the gap. The balance
    becomes the stated value in one step; the ajuste is dated on the anchor day
    so it is excluded from the balance (B1)."""
    db, user_id = db_with_user
    user = await db.get(User, user_id)
    a = await _account(db, user_id, initial="100000")
    await _txn(db, user_id, a.id, "-25000", date.today())  # projected = 75000

    res = await apply_anchor(
        db, user=user, account=a, value=Decimal("90000"), source="reanchor"
    )
    await db.commit()

    assert res.delta == Decimal("15000")  # 90000 - 75000
    assert res.ajuste_transaction_id is not None
    bal = await compute_account_balances(db, user_id=user_id, account_ids=[a.id])
    assert bal[a.id].current == Decimal("90000")  # heals in one step


async def test_reanchor_ajuste_excluded_from_income_reports(db_with_user):
    """The +delta ajuste must NOT show as income in the dashboard (it is a
    balance reconciliation, not P&L)."""
    db, user_id = db_with_user
    user = await db.get(User, user_id)
    a = await _account(db, user_id, initial="0")
    await _txn(db, user_id, a.id, "-25000", date.today())  # a real expense

    await apply_anchor(
        db, user=user, account=a, value=Decimal("100000"), source="reanchor"
    )  # delta = +125000 ajuste, dated today
    await db.commit()

    summary = await get_dashboard_summary(db, user=user, period="month_current")
    # The +125000 ajuste is excluded; only the real -25000 expense counts.
    assert summary.income_total == Decimal("0")
    assert summary.expense_total == Decimal("25000")


async def test_apply_anchor_defaults_effective_date_to_user_today(
    db_with_user, monkeypatch
):
    """Without an explicit `today`, apply_anchor stamps the user's CR calendar
    day via clock.user_today — NOT a naive UTC date.today(). A near-midnight
    re-anchor must not land on the wrong side of the strict-`>` boundary. See
    `Decision - Timezone-Aware Date Boundaries (Server UTC vs CR)`."""
    db, user_id = db_with_user
    user = await db.get(User, user_id)
    a = await _account(db, user_id, initial="0")

    sentinel = date(2026, 6, 19)
    monkeypatch.setattr("api.services.anchors.user_today", lambda u: sentinel)

    await apply_anchor(
        db,
        user=user,
        account=a,
        value=Decimal("50000"),
        source="reanchor",
        write_ajuste=False,
    )
    await db.commit()

    row = (
        await db.execute(
            select(AccountAnchor).where(AccountAnchor.account_id == a.id)
        )
    ).scalar_one()
    assert row.effective_date == sentinel


async def test_migrated_anchor_nudges_until_reanchor(db_with_user):
    """A 'migrated' (backfill) anchor flags the account for confirmation; a
    re-anchor clears it."""
    db, user_id = db_with_user
    user = await db.get(User, user_id)
    a = await _account(db, user_id, initial="50000")
    await _anchor(db, user_id, a.id, "50000", date(2026, 1, 1), source="migrated")

    sources = await latest_anchor_sources(db, account_ids=[a.id])
    assert needs_balance_confirmation(sources.get(a.id)) is True

    await apply_anchor(
        db, user=user, account=a, value=Decimal("48000"), source="reanchor"
    )
    await db.commit()
    sources = await latest_anchor_sources(db, account_ids=[a.id])
    assert needs_balance_confirmation(sources.get(a.id)) is False


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
async def test_anchor_endpoint_heals_and_create_not_nudged(db_with_user):
    """POST /accounts → not nudged (anchorless). POST /{id}/anchor → balance
    becomes the stated value and the nudge stays off."""
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            created = await ac.post(
                "/api/v1/accounts",
                json={
                    "name": "BAC Anchor",
                    "account_type": "checking",
                    "currency": "CRC",
                    "initial_balance": "100000",
                },
            )
            assert created.status_code == 201, created.text
            body = created.json()
            assert body["needs_balance_confirmation"] is False  # fresh = confirmed
            account_id = body["id"]

            anchored = await ac.post(
                f"/api/v1/accounts/{account_id}/anchor",
                json={"value": "82500"},
            )
            assert anchored.status_code == 200, anchored.text
            ares = anchored.json()
            assert Decimal(ares["current_balance"]) == Decimal("82500")

            got = await ac.get(f"/api/v1/accounts/{account_id}")
            assert Decimal(got.json()["current_balance"]) == Decimal("82500")
            assert got.json()["needs_balance_confirmation"] is False
    finally:
        _clear()
