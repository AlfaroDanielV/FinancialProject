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

from app.queries import dispatcher
from api.config import settings
from api.models.account import Account
from api.models.llm_query_dispatch import LLMQueryDispatch
from api.models.transaction import Transaction

pytestmark = pytest.mark.skipif(
    not settings.anthropic_api_key,
    reason="ANTHROPIC_API_KEY no configurada",
)

_EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF]")


async def _seed_account(session, user_id: uuid.UUID, name: str) -> Account:
    account = Account(user_id=user_id, name=name, account_type="checking")
    session.add(account)
    await session.commit()
    await session.refresh(account)
    return account


async def _seed_txn(
    session,
    *,
    user_id: uuid.UUID,
    account_id: uuid.UUID,
    amount: str,
    merchant: str,
    category: str,
    transaction_date: date,
) -> None:
    session.add(
        Transaction(
            user_id=user_id,
            account_id=account_id,
            amount=Decimal(amount),
            currency="CRC",
            merchant=merchant,
            category=category,
            description=None,
            transaction_date=transaction_date,
            created_at=datetime(2026, 4, 1, tzinfo=timezone.utc),
            source="manual",
        )
    )
    await session.commit()


async def _seed_e2e_data(session, user_id: uuid.UUID) -> None:
    cash = await _seed_account(session, user_id, "Efectivo")
    card = await _seed_account(session, user_id, "Promerica Visa")

    rows = [
        ("-12000", "PriceSmart", "supermercado", date(2026, 3, 3)),
        ("-8500", "Uber", "transporte", date(2026, 3, 4)),
        ("-22000", "Más x Menos", "supermercado", date(2026, 3, 8)),
        ("-7000", "Uber", "transporte", date(2026, 3, 9)),
        ("-18000", "Automercado", "supermercado", date(2026, 3, 12)),
        ("-9500", "Cinepolis", "entretenimiento", date(2026, 3, 15)),
        ("-31000", "PriceSmart", "supermercado", date(2026, 3, 20)),
        ("-6000", "Farmacia Fischel", "salud", date(2026, 3, 21)),
        ("-4300", "Uber", "transporte", date(2026, 3, 26)),
        ("250000", "Empresa", "salario", date(2026, 3, 30)),
        ("-14000", "Más x Menos", "supermercado", date(2026, 4, 1)),
        ("-5200", "Uber", "transporte", date(2026, 4, 2)),
        ("-16500", "Automercado", "supermercado", date(2026, 4, 3)),
        ("-7600", "Uber", "transporte", date(2026, 4, 4)),
        ("-11200", "Cinepolis", "entretenimiento", date(2026, 4, 5)),
        ("-99000", "PriceSmart", "supermercado", date(2026, 4, 10)),
        ("-12500", "Farmacia Fischel", "salud", date(2026, 4, 11)),
        ("-8300", "Uber", "transporte", date(2026, 4, 12)),
        ("350000", "Empresa", "salario", date(2026, 4, 15)),
        ("-24000", "Más x Menos", "supermercado", date(2026, 4, 16)),
        ("-6200", "Uber", "transporte", date(2026, 4, 17)),
        ("-17800", "Automercado", "supermercado", date(2026, 4, 18)),
        ("-4500", "Uber", "transporte", date(2026, 4, 19)),
        ("-13600", "Cinepolis", "entretenimiento", date(2026, 4, 20)),
        ("-5800", "Uber", "transporte", date(2026, 4, 21)),
        ("-28000", "PriceSmart", "supermercado", date(2026, 4, 22)),
        ("-7400", "Uber", "transporte", date(2026, 4, 23)),
        ("-9100", "Farmacia Fischel", "salud", date(2026, 4, 24)),
        ("-12345", "PriceSmart", "supermercado", date(2026, 4, 27)),
        ("-8000", "Uber", "transporte", date(2026, 4, 27)),
    ]
    for idx, (amount, merchant, category, txn_date) in enumerate(rows):
        await _seed_txn(
            session,
            user_id=user_id,
            account_id=card.id if idx % 2 else cash.id,
            amount=amount,
            merchant=merchant,
            category=category,
            transaction_date=txn_date,
        )


async def _latest_dispatch(session, user_id: uuid.UUID) -> LLMQueryDispatch:
    result = await session.execute(
        select(LLMQueryDispatch)
        .where(LLMQueryDispatch.user_id == user_id)
        .order_by(LLMQueryDispatch.created_at.desc())
        .limit(1)
    )
    return result.scalar_one()


def _assert_tone(response: str, *, expect_money: bool = True) -> None:
    lowered = response.lower()
    assert not _EMOJI_RE.search(response)
    assert "soy tu asistente" not in lowered
    assert "soy un asistente" not in lowered
    assert "**" not in response
    if expect_money:
        assert re.search(r"₡\d", response)


def _tool_names(row: LLMQueryDispatch) -> list[str]:
    return [tool["name"] for tool in row.tools_used]


def _tool_args(row: LLMQueryDispatch, name: str) -> list[dict[str, Any]]:
    return [
        tool.get("args_summary", {})
        for tool in row.tools_used
        if tool["name"] == name
    ]


@pytest.mark.asyncio
async def test_phase_6a_block4_real_llm_uses_transaction_tools(db_with_user):
    session, user_id = db_with_user
    await _seed_e2e_data(session, user_id)

    today = datetime.now(ZoneInfo("America/Costa_Rica")).date()
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)

    report: list[dict[str, Any]] = []

    response = await dispatcher.handle(
        user_id=user_id,
        message_text="cuánto gasté esta semana",
        telegram_chat_id=123,
    )
    row = await _latest_dispatch(session, user_id)
    names = _tool_names(row)
    assert set(names) & {"aggregate_transactions", "list_transactions"}
    args = row.tools_used[0]["args_summary"]
    assert args.get("transaction_type") == "expense"
    assert args.get("start_date") == week_start.isoformat()
    assert args.get("end_date") in {today.isoformat(), week_end.isoformat()}
    assert 1 <= row.total_iterations <= 3
    _assert_tone(response)
    report.append({"message": "cuánto gasté esta semana", "tools": row.tools_used, "response": response})

    response = await dispatcher.handle(
        user_id=user_id,
        message_text="dame el desglose por categoría de abril",
        telegram_chat_id=123,
    )
    row = await _latest_dispatch(session, user_id)
    aggregate_args = _tool_args(row, "aggregate_transactions")
    assert aggregate_args
    assert aggregate_args[0].get("group_by") == "category"
    assert aggregate_args[0].get("start_date") == "2026-04-01"
    # Block 6 convention: when the user names the *current* month, the LLM
    # uses month-to-date (today). The full calendar end is also accepted in
    # case the convention is interpreted strictly. See
    # app/queries/prompts/system.py for the rule.
    assert aggregate_args[0].get("end_date") in {"2026-04-30", today.isoformat()}
    assert 1 <= row.total_iterations <= 3
    _assert_tone(response)
    report.append({"message": "dame el desglose por categoría de abril", "tools": row.tools_used, "response": response})

    response = await dispatcher.handle(
        user_id=user_id,
        message_text="muéstrame las transacciones en PriceSmart",
        telegram_chat_id=123,
    )
    row = await _latest_dispatch(session, user_id)
    list_args = _tool_args(row, "list_transactions")
    assert list_args
    assert any(
        "price" in merchant.lower()
        for merchant in (list_args[0].get("merchants") or [])
    )
    assert 1 <= row.total_iterations <= 3
    _assert_tone(response)
    report.append({"message": "muéstrame las transacciones en PriceSmart", "tools": row.tools_used, "response": response})

    response = await dispatcher.handle(
        user_id=user_id,
        message_text="cuál fue mi gasto más alto del mes",
        telegram_chat_id=123,
    )
    row = await _latest_dispatch(session, user_id)
    list_args = _tool_args(row, "list_transactions")
    assert list_args
    assert list_args[0].get("sort") == "amount_desc"
    assert int(list_args[0].get("limit", 10)) <= 3
    assert 1 <= row.total_iterations <= 3
    _assert_tone(response)
    report.append({"message": "cuál fue mi gasto más alto del mes", "tools": row.tools_used, "response": response})

    response = await dispatcher.handle(
        user_id=user_id,
        message_text="cuánto gasté en supermercado vs transporte",
        telegram_chat_id=123,
    )
    row = await _latest_dispatch(session, user_id)
    names = _tool_names(row)
    assert set(names) & {"aggregate_transactions", "list_transactions"}
    serialized_args = json.dumps([tool["args_summary"] for tool in row.tools_used])
    aggregate_args = _tool_args(row, "aggregate_transactions")
    if aggregate_args:
        assert aggregate_args[0].get("group_by") == "category"
    else:
        assert "supermercado" in serialized_args.lower()
        assert "transporte" in serialized_args.lower()
    assert 1 <= row.total_iterations <= 3
    _assert_tone(response)
    report.append({"message": "cuánto gasté en supermercado vs transporte", "tools": row.tools_used, "response": response})

    response = await dispatcher.handle(
        user_id=user_id,
        message_text="qué gasté ayer entre 6 y 9 de la noche",
        telegram_chat_id=123,
    )
    row = await _latest_dispatch(session, user_id)
    names = _tool_names(row)
    assert not names or names == ["list_transactions"]
    lowered = response.lower()
    assert "hora" in lowered or "día" in lowered or "granularidad" in lowered
    assert row.total_iterations <= 3
    _assert_tone(response, expect_money=False)
    report.append({"message": "qué gasté ayer entre 6 y 9 de la noche", "tools": row.tools_used, "response": response})

    print(json.dumps(report, ensure_ascii=False, indent=2))
