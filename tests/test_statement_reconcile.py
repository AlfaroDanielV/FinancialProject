"""Bank-statement reconciliation (PDF → balance anchor).

Covers:
1. `extract_statement()` parses a multi-product statement + logs the audit row.
2. `reconcile_products()` deposit → anchor at corte; pre-corte excluded, post-corte rides on top.
3. `reconcile_products()` credit → owed balance anchors NEGATIVE (owed = -current).
4. `reconcile_products()` loan → Debt.current_balance set + audit note appended.
5. `reconcile_products()` multi-account batch in one corte.
6. `build_reconcile_plan()` resolves a single match; the writer rejects a
   currency mismatch and self-stamps identity so the next statement matches.
7. `POST /accounts/parse-statement` 415 guard + happy path (no write).
8. `POST /accounts/reconcile-statement` happy path + 400 on a foreign target.

The deterministic plan builder + policy table + conservation have their own
DB-free suite in `test_statement_plan.py`.

FixtureLLMClient ignores the PDF bytes, so a minimal `%PDF-` header suffices.
"""
from __future__ import annotations

import socket
import uuid
from datetime import date
from decimal import Decimal
from urllib.parse import urlparse

import pytest
from httpx import ASGITransport, AsyncClient

from api.config import settings
from api.database import get_db
from api.dependencies import current_user
from api.main import app
from api.models.account import Account
from api.models.debt import Debt
from api.models.llm_extraction import LLMExtraction
from api.models.user import User
from api.services.accounts import compute_account_balances
from api.services.llm_extractor import FixtureLLMClient, RecordedLLMResponse
from api.services.llm_extractor.document import extract_statement
from api.services.statement_normalize import build_reconcile_plan, plan_to_extraction
from api.services.statements import (
    ReconcileError,
    reconcile_products,
)
from api.schemas.statements import (
    StatementExtractionV2,
    StatementReconcileItem,
)
from bot.app import set_llm_client
from sqlalchemy import select


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

_TINY_PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"

# A BAC-like consolidated statement (V2 semantic shape): 1 deposit + 1 loan.
_STATEMENT = RecordedLLMResponse(
    tool_input={
        "bank": "BAC",
        "period_end": "2026-05-31",
        "confidence": 0.9,
        "accounts": [
            {
                "account_type": "checking",
                "product_name": "Cuenta a la vista",
                "identifiers": [{"kind": "last4", "value": "2600"}],
                "currency_legs": [
                    {
                        "currency": "CRC",
                        "closing_candidates": [
                            {"amount": 268207.37, "role": "closing"}
                        ],
                    }
                ],
            },
            {
                "account_type": "loan",
                "product_name": "Crédito",
                "currency_legs": [
                    {
                        "currency": "CRC",
                        "closing_candidates": [
                            {"amount": 12119385.98, "role": "principal_outstanding"}
                        ],
                    }
                ],
            },
        ],
    },
)


async def _account(db, user_id, *, initial="0", account_type="checking", name=None):
    a = Account(
        user_id=user_id,
        name=name or f"Acct {uuid.uuid4().hex[:8]}",
        account_type=account_type,
        currency="CRC",
        initial_balance=Decimal(initial),
    )
    db.add(a)
    await db.commit()
    await db.refresh(a)
    return a


async def _txn(db, user_id, account_id, amount, d):
    from api.models.transaction import Transaction

    db.add(
        Transaction(
            user_id=user_id,
            account_id=account_id,
            amount=Decimal(amount),
            currency="CRC",
            merchant="seed",
            transaction_date=d,
            source="manual",
            status="confirmed",
        )
    )
    await db.commit()


async def _debt(db, user_id, *, balance="12245236.77", name="Préstamo BAC"):
    d = Debt(
        user_id=user_id,
        name=name,
        debt_type="personal",
        original_amount=Decimal("14977164.00"),
        current_balance=Decimal(balance),
        interest_rate=Decimal("0.089"),
        minimum_payment=Decimal("285981.95"),
        payment_due_day=1,
        currency="CRC",
    )
    db.add(d)
    await db.commit()
    await db.refresh(d)
    return d


# ── 1. extraction ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_extract_statement_parses_and_logs(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)

    class _Client:
        async def extract(self, **kwargs) -> RecordedLLMResponse:
            return _STATEMENT

    result = await extract_statement(
        user=user,
        pdf_bytes=_TINY_PDF,
        client=_Client(),
        haiku_model="claude-haiku-4-5",
        sonnet_model="claude-sonnet-4-5",
        db=session,
    )
    assert result.bank == "BAC"
    assert result.period_end == date(2026, 5, 31)
    assert len(result.accounts) == 2
    assert result.accounts[0].account_type == "checking"
    assert result.accounts[1].account_type == "loan"

    rows = (
        await session.execute(
            select(LLMExtraction).where(LLMExtraction.user_id == user_id)
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].intent == "parse_statement"
    assert rows[0].extraction["document"] is True
    assert "pdf_b64" in rows[0].extraction


@pytest.mark.asyncio
async def test_extract_statement_missing_confidence_does_not_crash(db_with_user):
    """Anthropic tool-use does not enforce `required`, so the model can omit
    `confidence` (the prod 500 on a Promerica statement, 2026-06-27). It must
    default to 0.0 — which forces the Sonnet retry — instead of raising."""
    session, user_id = db_with_user
    user = await session.get(User, user_id)

    no_conf = {k: v for k, v in _STATEMENT.tool_input.items() if k != "confidence"}
    assert "confidence" not in no_conf

    calls: list[str] = []

    class _Client:
        async def extract(self, **kwargs) -> RecordedLLMResponse:
            calls.append(kwargs["model"])
            return RecordedLLMResponse(tool_input=dict(no_conf))

    result = await extract_statement(
        user=user,
        pdf_bytes=_TINY_PDF,
        client=_Client(),
        haiku_model="claude-haiku-4-5",
        sonnet_model="claude-sonnet-4-5",
        db=session,
    )
    # No crash; a missing confidence is coerced to 0.0 ...
    assert result.confidence == 0.0
    assert len(result.accounts) == 2
    # ... and 0.0 < threshold forces the Haiku→Sonnet retry.
    assert calls == ["claude-haiku-4-5", "claude-sonnet-4-5"]


@pytest.mark.asyncio
async def test_extract_statement_empty_accounts_forces_sonnet_retry(db_with_user):
    """A Haiku pass returning accounts=[] (even with HIGH confidence) must force
    the Sonnet retry — the prod '0 productos' on a Promerica statement was Haiku
    reading zero accounts, which low-confidence alone would not catch."""
    session, user_id = db_with_user
    user = await session.get(User, user_id)

    calls: list[str] = []

    class _Client:
        async def extract(self, **kwargs) -> RecordedLLMResponse:
            calls.append(kwargs["model"])
            if kwargs["model"] == "claude-haiku-4-5":
                # Confidently returns nothing useful — the bug we now recover from.
                return RecordedLLMResponse(
                    tool_input={"bank": "Promerica", "accounts": [], "confidence": 0.9}
                )
            return _STATEMENT  # the Sonnet retry reads the real accounts

    result = await extract_statement(
        user=user,
        pdf_bytes=_TINY_PDF,
        client=_Client(),
        haiku_model="claude-haiku-4-5",
        sonnet_model="claude-sonnet-4-5",
        db=session,
    )
    assert calls == ["claude-haiku-4-5", "claude-sonnet-4-5"]
    assert len(result.accounts) == 2  # the Sonnet retry's non-empty result wins


# ── 2. deposit reconcile honors the corte (strict >) ──────────────────────────


@pytest.mark.asyncio
async def test_reconcile_deposit_anchors_at_corte(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    a = await _account(session, user_id, initial="0")
    await _txn(session, user_id, a.id, "-100000", date(2026, 5, 15))  # pre → out
    await _txn(session, user_id, a.id, "-5000", date(2026, 5, 31))  # on corte → out
    await _txn(session, user_id, a.id, "-8000", date(2026, 6, 5))  # post → rides

    results = await reconcile_products(
        session,
        user=user,
        corte_date=date(2026, 5, 31),
        items=[
            StatementReconcileItem(
                kind="deposit", target_id=a.id, closing_balance=Decimal("268207.37")
            )
        ],
    )
    await session.commit()

    assert results[0].new_balance == Decimal("268207.37")
    # antes→después delta = stated − previously-projected (0 - 113000)
    assert results[0].delta == Decimal("381207.37")
    bal = await compute_account_balances(session, user_id=user_id, account_ids=[a.id])
    # corte balance + only the post-corte txn
    assert bal[a.id].current == Decimal("260207.37")


# ── 3. credit reconcile anchors NEGATIVE (owed) ───────────────────────────────


@pytest.mark.asyncio
async def test_reconcile_credit_anchors_negative(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    card = await _account(session, user_id, account_type="credit")

    await reconcile_products(
        session,
        user=user,
        corte_date=date(2026, 5, 19),
        items=[
            StatementReconcileItem(
                kind="credit", target_id=card.id, closing_balance=Decimal("306595.11")
            )
        ],
    )
    await session.commit()

    bal = await compute_account_balances(session, user_id=user_id, account_ids=[card.id])
    # owed = -current → a card that owes ₡306 595 anchors at -306595.11
    assert bal[card.id].current == Decimal("-306595.11")


# ── 4. loan reconcile sets current_balance + appends an audit note ────────────


@pytest.mark.asyncio
async def test_reconcile_loan_sets_balance(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    debt = await _debt(session, user_id, balance="12245236.77")

    results = await reconcile_products(
        session,
        user=user,
        corte_date=date(2026, 5, 31),
        items=[
            StatementReconcileItem(
                kind="loan", target_id=debt.id, closing_balance=Decimal("12119385.98")
            )
        ],
    )
    await session.commit()
    await session.refresh(debt)

    assert Decimal(debt.current_balance) == Decimal("12119385.98")
    assert "reconciliad" in (debt.notes or "").lower()
    assert results[0].new_balance == Decimal("12119385.98")


# ── 5. multi-account batch ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reconcile_multi_account_batch(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    a1 = await _account(session, user_id, initial="0", name="BAC Principal")
    a2 = await _account(session, user_id, initial="0", name="BAC Ahorro")

    await reconcile_products(
        session,
        user=user,
        corte_date=date(2026, 5, 31),
        items=[
            StatementReconcileItem(kind="deposit", target_id=a1.id, closing_balance=Decimal("268207.37")),
            StatementReconcileItem(kind="deposit", target_id=a2.id, closing_balance=Decimal("1888854.13")),
        ],
    )
    await session.commit()

    bal = await compute_account_balances(
        session, user_id=user_id, account_ids=[a1.id, a2.id]
    )
    assert bal[a1.id].current == Decimal("268207.37")
    assert bal[a2.id].current == Decimal("1888854.13")


# ── 6. a foreign / mismatched target is rejected ──────────────────────────────


@pytest.mark.asyncio
async def test_reconcile_rejects_unknown_account(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    with pytest.raises(ReconcileError):
        await reconcile_products(
            session,
            user=user,
            corte_date=date(2026, 5, 31),
            items=[
                StatementReconcileItem(
                    kind="deposit", target_id=uuid.uuid4(), closing_balance=Decimal("1")
                )
            ],
        )


# ── 7. build_reconcile_plan resolves a single matching account ────────────────


@pytest.mark.asyncio
async def test_resolve_single_match(db_with_user):
    session, user_id = db_with_user
    a = await _account(session, user_id, name="BAC Principal")

    extraction = StatementExtractionV2.model_validate(
        {
            "bank": "BAC",
            "period_end": "2026-05-31",
            "confidence": 0.9,
            "accounts": [
                {
                    "account_type": "checking",
                    "product_name": "Cuenta a la vista",
                    "currency_legs": [
                        {
                            "currency": "CRC",
                            "closing_candidates": [
                                {"amount": 268207.37, "role": "closing"}
                            ],
                        }
                    ],
                }
            ],
        }
    )
    plan = build_reconcile_plan(extraction, accounts=[a], debts=[])
    out = plan_to_extraction(plan, confidence=extraction.confidence)
    assert out.products[0].suggested_account_id == a.id


# ── 7b. the writer rejects a currency mismatch (the silent-bug fix) ───────────


@pytest.mark.asyncio
async def test_reconcile_rejects_currency_mismatch(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    a = await _account(session, user_id, name="BAC CRC")  # CRC account
    with pytest.raises(ReconcileError):
        await reconcile_products(
            session,
            user=user,
            corte_date=date(2026, 5, 31),
            items=[
                StatementReconcileItem(
                    kind="deposit",
                    target_id=a.id,
                    closing_balance=Decimal("1"),
                    currency="USD",  # ≠ the account's CRC
                )
            ],
        )


# ── 7c. identity self-stamp → the NEXT statement matches deterministically ────


@pytest.mark.asyncio
async def test_identity_self_stamp_then_deterministic_match(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    a = await _account(session, user_id, account_type="credit", name="Promerica ₡")
    b = await _account(session, user_id, account_type="credit", name="Promerica Oro ₡")

    # First reconciliation carries the card's last4 → stamps it onto A.
    await reconcile_products(
        session,
        user=user,
        corte_date=date(2026, 5, 19),
        items=[
            StatementReconcileItem(
                kind="credit",
                target_id=a.id,
                closing_balance=Decimal("100000"),
                currency="CRC",
                last4="1234",
            )
        ],
    )
    await session.commit()
    await session.refresh(a)
    assert a.last4 == "1234"

    # A new statement whose card ends 1234 resolves to A outright — even though
    # both A and B fuzzy-match "Promerica" (which would otherwise be ambiguous).
    extraction = StatementExtractionV2.model_validate(
        {
            "bank": "Promerica",
            "period_end": "2026-06-19",
            "confidence": 0.9,
            "accounts": [
                {
                    "account_type": "credit_card",
                    "issuer": "Promerica",
                    "product_name": "VISA",
                    "identifiers": [{"kind": "last4", "value": "1234"}],
                    "currency_legs": [
                        {
                            "currency": "CRC",
                            "closing_candidates": [{"amount": 50000, "role": "payoff"}],
                        }
                    ],
                }
            ],
        }
    )
    plan = build_reconcile_plan(extraction, accounts=[a, b], debts=[])
    (leg,) = plan.legs
    assert leg.resolution.target_id == a.id
    assert leg.resolution.confidence == 1.0


# ── 8. endpoints ──────────────────────────────────────────────────────────────


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
async def test_parse_statement_endpoint(db_with_user):
    session, user_id = db_with_user
    set_llm_client(FixtureLLMClient(default=_STATEMENT))
    await _account(session, user_id, name="BAC Principal")
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            bad = await ac.post(
                "/api/v1/accounts/parse-statement",
                files={"file": ("x.png", b"\x89PNG", "image/png")},
            )
            assert bad.status_code == 415

            ok = await ac.post(
                "/api/v1/accounts/parse-statement",
                files={"file": ("estado.pdf", _TINY_PDF, "application/pdf")},
            )
            assert ok.status_code == 200, ok.text
            body = ok.json()
            assert body["bank"] == "BAC"
            assert len(body["products"]) == 2
            # single matching CRC deposit account → suggested
            assert body["products"][0]["suggested_account_id"] is not None
    finally:
        _clear()


@pytest.mark.asyncio
async def test_reconcile_statement_endpoint(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            created = await ac.post(
                "/api/v1/accounts",
                json={
                    "name": "BAC Recon",
                    "account_type": "checking",
                    "currency": "CRC",
                    "initial_balance": "0",
                },
            )
            assert created.status_code == 201, created.text
            account_id = created.json()["id"]

            resp = await ac.post(
                "/api/v1/accounts/reconcile-statement",
                json={
                    "corte_date": "2026-05-31",
                    "items": [
                        {
                            "kind": "deposit",
                            "target_id": account_id,
                            "closing_balance": "268207.37",
                        }
                    ],
                },
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["results"][0]["new_balance"] == "268207.37"

            got = await ac.get(f"/api/v1/accounts/{account_id}")
            assert Decimal(got.json()["current_balance"]) == Decimal("268207.37")

            foreign = await ac.post(
                "/api/v1/accounts/reconcile-statement",
                json={
                    "corte_date": "2026-05-31",
                    "items": [
                        {
                            "kind": "deposit",
                            "target_id": str(uuid.uuid4()),
                            "closing_balance": "1",
                        }
                    ],
                },
            )
            assert foreign.status_code == 400
    finally:
        _clear()


# ── 9. chat path (Telegram): send PDF → propose → confirm ─────────────────────


@pytest.mark.asyncio
async def test_chat_statement_proposes_and_confirms(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    from api.redis_client import get_redis
    from bot.pipeline import handle_pending_callback, handle_statement_document

    redis = get_redis()
    a = await _account(session, user_id, name="BAC Principal")  # sole CRC deposit

    reply = await handle_statement_document(
        user=user,
        pdf_bytes=_TINY_PDF,
        db=session,
        redis=redis,
        llm_client=FixtureLLMClient(default=_STATEMENT),
        haiku_model="claude-haiku-4-5",
        sonnet_model="claude-sonnet-4-5",
    )
    # the deposit matched; the loan has no debt → proposed as Sí/No
    assert reply.buttons, "expected Sí/No buttons on the proposal"
    cb = reply.buttons[0].callback_data
    assert cb.endswith(":yes")

    confirm = await handle_pending_callback(
        user=user, callback_data=cb, db=session, redis=redis
    )
    assert "reconcili" in confirm.text.lower()
    bal = await compute_account_balances(
        session, user_id=user_id, account_ids=[a.id]
    )
    assert bal[a.id].current == Decimal("268207.37")
