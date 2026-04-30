from __future__ import annotations

import json
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from api.config import settings
from api.models.account import Account
from api.models.bill_occurrence import BillOccurrence
from api.models.debt import Debt
from api.models.llm_query_dispatch import LLMQueryDispatch
from api.models.pending_confirmation import PendingConfirmation
from api.models.recurring_bill import RecurringBill
from api.models.transaction import Transaction
from app.queries import dispatcher
from app.queries.history import clear_history
from api.redis_client import get_redis

pytestmark = pytest.mark.skipif(
    not settings.anthropic_api_key,
    reason="ANTHROPIC_API_KEY no configurada",
)

_EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF]")


def _today() -> date:
    return datetime.now(ZoneInfo("America/Costa_Rica")).date()


async def _seed_data(session, user_id: uuid.UUID) -> None:
    today = _today()
    cash = Account(user_id=user_id, name="Efectivo", account_type="checking")
    bac = Account(user_id=user_id, name="BAC Débito", account_type="checking")
    visa = Account(user_id=user_id, name="BAC Visa Crédito", account_type="credit")
    invest = Account(user_id=user_id, name="Inversiones BCR", account_type="investment")
    session.add_all([cash, bac, visa, invest])
    await session.commit()
    for a in (cash, bac, visa, invest):
        await session.refresh(a)

    seed_txns = [
        (cash.id, "150000", "Empresa", "salario", today - timedelta(days=20)),
        (cash.id, "-12000", "Pulpería", "supermercado", today - timedelta(days=10)),
        (bac.id, "500000", "Empresa", "salario", today - timedelta(days=8)),
        (bac.id, "-35000", "ICE", "ice", today - timedelta(days=5)),
        (bac.id, "-8000", "Uber", "transporte", today - timedelta(days=3)),
        (visa.id, "-180000", "Más x Menos", "supermercado", today - timedelta(days=15)),
        (visa.id, "-65000", "Cinepolis", "entretenimiento", today - timedelta(days=4)),
        (invest.id, "1200000", "Aporte BCR", "inversion", today - timedelta(days=30)),
    ]
    for account_id, amount, merchant, category, txn_date in seed_txns:
        session.add(
            Transaction(
                user_id=user_id,
                account_id=account_id,
                amount=Decimal(amount),
                currency="CRC",
                merchant=merchant,
                category=category,
                transaction_date=txn_date,
                created_at=datetime(2026, 4, 1, tzinfo=timezone.utc),
                source="manual",
            )
        )
    await session.commit()

    mortgage = Debt(
        user_id=user_id,
        account_id=bac.id,
        name="Préstamo casa BAC",
        debt_type="mortgage",
        original_amount=Decimal("15000000"),
        current_balance=Decimal("12500000"),
        interest_rate=Decimal("0.0850"),
        minimum_payment=Decimal("185000"),
        payment_due_day=15,
        term_months=240,
        payments_made=14,
        currency="CRC",
        is_active=True,
    )
    primo = Debt(
        user_id=user_id,
        name="Plata a primo",
        debt_type="other",
        original_amount=Decimal("300000"),
        current_balance=Decimal("200000"),
        interest_rate=Decimal("0"),
        minimum_payment=Decimal("0"),
        payment_due_day=1,
        term_months=None,
        payments_made=0,
        currency="CRC",
        is_active=True,
    )
    auto = Debt(
        user_id=user_id,
        name="Carro pagado",
        debt_type="auto_loan",
        original_amount=Decimal("8000000"),
        current_balance=Decimal("0"),
        interest_rate=Decimal("0.10"),
        minimum_payment=Decimal("0"),
        payment_due_day=10,
        term_months=60,
        payments_made=60,
        currency="CRC",
        is_active=False,
    )
    session.add_all([mortgage, primo, auto])
    await session.commit()
    for d in (mortgage, primo, auto):
        await session.refresh(d)

    ice_bill = RecurringBill(
        user_id=user_id, name="ICE", category="ice", amount_expected=Decimal("35000"),
        currency="CRC", is_variable_amount=False, account_id=bac.id,
        frequency="monthly", day_of_month=15,
        start_date=date(2026, 1, 1), is_active=True,
    )
    aya_bill = RecurringBill(
        user_id=user_id, name="AyA", category="water", amount_expected=Decimal("12000"),
        currency="CRC", is_variable_amount=False, account_id=bac.id,
        frequency="monthly", day_of_month=10,
        start_date=date(2026, 1, 1), is_active=True,
    )
    netflix_bill = RecurringBill(
        user_id=user_id, name="Netflix", category="subscription",
        amount_expected=Decimal("8500"), currency="CRC", is_variable_amount=False,
        account_id=visa.id, frequency="monthly", day_of_month=20,
        start_date=date(2026, 1, 1), is_active=True,
    )
    gym_bill = RecurringBill(
        user_id=user_id, name="Gym Smart Fit", category="subscription",
        amount_expected=Decimal("18000"), currency="CRC", is_variable_amount=False,
        account_id=visa.id, frequency="monthly", day_of_month=25,
        start_date=date(2026, 1, 1), is_active=True,
    )
    rent_bill = RecurringBill(
        user_id=user_id, name="Alquiler", category="rent",
        amount_expected=Decimal("250000"), currency="CRC", is_variable_amount=False,
        account_id=bac.id, frequency="monthly", day_of_month=1,
        start_date=date(2026, 1, 1), is_active=True,
    )
    session.add_all([ice_bill, aya_bill, netflix_bill, gym_bill, rent_bill])
    await session.commit()
    for b in (ice_bill, aya_bill, netflix_bill, gym_bill, rent_bill):
        await session.refresh(b)

    occurrences = [
        # ICE — overdue
        (ice_bill.id, today - timedelta(days=5), "overdue", Decimal("35000"), None),
        # AyA — paid 4 days ago
        (
            aya_bill.id, today - timedelta(days=10), "paid",
            Decimal("12000"),
            datetime.now(timezone.utc) - timedelta(days=4),
        ),
        # Netflix — upcoming in 3 days
        (
            netflix_bill.id, today + timedelta(days=3), "pending",
            Decimal("8500"), None,
        ),
        # Gym — upcoming in 8 days
        (
            gym_bill.id, today + timedelta(days=8), "pending",
            Decimal("18000"), None,
        ),
        # Rent — upcoming in 5 days (within "this week")
        (
            rent_bill.id, today + timedelta(days=5), "pending",
            Decimal("250000"), None,
        ),
    ]
    for bill_id, due, status, amount, paid_at in occurrences:
        session.add(
            BillOccurrence(
                user_id=user_id,
                recurring_bill_id=bill_id,
                due_date=due,
                amount_expected=amount,
                status=status,
                paid_at=paid_at,
                amount_paid=amount if status == "paid" else None,
            )
        )
    await session.commit()

    pending_active = PendingConfirmation(
        user_id=user_id,
        short_id="abc999",
        channel="telegram",
        action_type="log_expense",
        proposed_action={
            "action_type": "log_expense",
            "summary_es": "Registrar gasto de ₡5000 en supermercado, cuenta Efectivo",
            "payload": {"amount": "-5000", "merchant": "super", "category": "supermercado"},
        },
        created_at=datetime.now(timezone.utc) - timedelta(hours=20),
    )
    pending_resolved = PendingConfirmation(
        user_id=user_id,
        short_id="zzz000",
        channel="telegram",
        action_type="log_income",
        proposed_action={
            "action_type": "log_income",
            "summary_es": "Registrar ingreso de ₡100000",
            "payload": {"amount": "100000"},
        },
        created_at=datetime.now(timezone.utc) - timedelta(hours=30),
        resolved_at=datetime.now(timezone.utc) - timedelta(hours=29),
        resolution="confirmed",
    )
    session.add_all([pending_active, pending_resolved])
    await session.commit()


async def _latest_dispatch(session, user_id: uuid.UUID) -> LLMQueryDispatch:
    res = await session.execute(
        select(LLMQueryDispatch)
        .where(LLMQueryDispatch.user_id == user_id)
        .order_by(LLMQueryDispatch.created_at.desc())
        .limit(1)
    )
    return res.scalar_one()


def _tool_names(row: LLMQueryDispatch) -> list[str]:
    return [t["name"] for t in row.tools_used]


def _tool_args(row: LLMQueryDispatch, name: str) -> list[dict[str, Any]]:
    return [
        t.get("args_summary", {}) for t in row.tools_used if t["name"] == name
    ]


def _assert_tone(response: str, *, expect_money: bool = True) -> None:
    lowered = response.lower()
    assert not _EMOJI_RE.search(response), f"emoji in: {response!r}"
    assert "soy tu asistente" not in lowered
    assert "soy un asistente" not in lowered
    assert "**" not in response, f"markdown bold leaked: {response!r}"
    if expect_money:
        assert re.search(r"₡\s?\d", response), f"no money formatting in: {response!r}"


@pytest.mark.asyncio
async def test_phase_6a_block5a_real_llm_uses_lookup_tools(db_with_user):
    session, user_id = db_with_user
    await _seed_data(session, user_id)

    report: list[dict[str, Any]] = []

    async def run(prompt: str) -> tuple[str, LLMQueryDispatch]:
        await clear_history(user_id, redis=get_redis())
        text = await dispatcher.handle(user_id=user_id, message_text=prompt)
        row = await _latest_dispatch(session, user_id)
        report.append(
            {
                "prompt": prompt,
                "tools": row.tools_used,
                "iterations": row.total_iterations,
                "input": row.total_input_tokens,
                "output": row.total_output_tokens,
                "ms": row.duration_ms,
                "response": text,
            }
        )
        return text, row

    # 1. saldo general
    response, row = await run("¿cuál es mi saldo?")
    assert "get_account_balance" in _tool_names(row)
    bal_args = _tool_args(row, "get_account_balance")
    assert bal_args and (bal_args[0].get("account_name") in (None, ""))
    _assert_tone(response)

    # 2. saldo BAC
    response, row = await run("¿cuánto tengo en BAC?")
    assert "get_account_balance" in _tool_names(row)
    bal_args = _tool_args(row, "get_account_balance")
    assert any("bac" in (a.get("account_name") or "").lower() for a in bal_args)
    _assert_tone(response)

    # 3. cuentas
    response, row = await run("¿qué cuentas tengo?")
    assert "list_accounts" in _tool_names(row)
    _assert_tone(response, expect_money=False)

    # 4. upcoming bills esta semana
    response, row = await run("¿qué se vence esta semana?")
    assert "list_recurring_bills" in _tool_names(row)
    bills_args = _tool_args(row, "list_recurring_bills")
    assert bills_args
    assert bills_args[0].get("status") in ("upcoming", "all")
    _assert_tone(response)

    # 5. ¿qué debo pagar?
    response, row = await run("¿qué debo pagar?")
    names = _tool_names(row)
    used_default = (
        "list_recurring_bills" in names
        or "list_debts" in names
    )
    lowered = response.lower()
    asked_for_clarification = (
        "?" in response and ("recurrentes" in lowered or "deudas" in lowered)
    )
    assert used_default or asked_for_clarification, {
        "names": names,
        "response": response,
    }
    _assert_tone(response, expect_money=used_default)

    # 6. ¿cuánto debo en total?
    response, row = await run("¿cuánto debo en total?")
    assert "list_debts" in _tool_names(row)
    _assert_tone(response)

    # 7. cuándo termino de pagar la casa
    response, row = await run("¿cuándo termino de pagar la casa?")
    assert "get_debt_details" in _tool_names(row)
    args = _tool_args(row, "get_debt_details")
    assert args
    assert "casa" in (args[0].get("debt_name") or "").lower()
    lowered = response.lower()
    has_year_or_date = bool(
        re.search(r"\b(20[3-7]\d|20[8-9]\d)\b", response)
        or "año" in lowered
        or "mes" in lowered
        or "fecha" in lowered
    )
    assert has_year_or_date, f"no payoff time mentioned: {response!r}"
    _assert_tone(response)

    # 8. pendientes
    response, row = await run("¿qué tenía pendiente?")
    assert "get_pending_confirmations" in _tool_names(row)
    _assert_tone(response, expect_money=False)
    assert "supermercado" in response.lower() or "₡5" in response

    # 9. resumen multi-tool
    response, row = await run("dame un resumen general de mi situación financiera")
    names = _tool_names(row)
    informative = {
        "get_account_balance",
        "list_debts",
        "list_recurring_bills",
        "list_accounts",
    }
    assert len(set(names) & informative) >= 2
    # Parallel tool calls in a single iteration are valid; just enforce the cap.
    assert 1 <= row.total_iterations <= 4
    assert len(row.tools_used) >= 2
    _assert_tone(response)

    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
