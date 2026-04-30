from __future__ import annotations

import json
import re
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from api.config import settings
from api.models.account import Account
from api.models.debt import Debt
from api.models.llm_query_dispatch import LLMQueryDispatch
from api.models.transaction import Transaction
from api.redis_client import get_redis
from app.queries import dispatcher
from app.queries.history import clear_history

pytestmark = pytest.mark.skipif(
    not settings.anthropic_api_key,
    reason="ANTHROPIC_API_KEY no configurada",
)

_EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF]")


def _assert_tone(response: str, *, expect_money: bool = True) -> None:
    lowered = response.lower()
    assert not _EMOJI_RE.search(response), f"emoji in: {response!r}"
    assert "soy tu asistente" not in lowered
    assert "soy un asistente" not in lowered
    assert "**" not in response, f"markdown bold leaked: {response!r}"
    if expect_money:
        assert re.search(r"₡\s?\d", response), f"no money formatting: {response!r}"


async def _seed_two_months(session, user_id: uuid.UUID) -> None:
    acct = Account(user_id=user_id, name="BAC Débito", account_type="checking")
    visa = Account(user_id=user_id, name="BAC Visa", account_type="credit")
    session.add_all([acct, visa])
    await session.commit()
    await session.refresh(acct)
    await session.refresh(visa)

    rows = [
        # March — total expense 1.250.000
        (acct.id, "-180000", "PriceSmart", "supermercado", date(2026, 3, 4)),
        (acct.id, "-220000", "Más x Menos", "supermercado", date(2026, 3, 11)),
        (acct.id, "-150000", "Automercado", "supermercado", date(2026, 3, 18)),
        (visa.id, "-95000", "Uber", "transporte", date(2026, 3, 6)),
        (visa.id, "-85000", "Uber", "transporte", date(2026, 3, 22)),
        (visa.id, "-120000", "Cinepolis", "entretenimiento", date(2026, 3, 14)),
        (acct.id, "-180000", "Soda Tapia", "comida_fuera", date(2026, 3, 27)),
        (acct.id, "-220000", "Farmacia Fischel", "salud", date(2026, 3, 9)),
        # March income
        (acct.id, "650000", "Empresa", "salario", date(2026, 3, 30)),
        # April — total expense 1.450.000 (~+16%)
        (acct.id, "-220000", "PriceSmart", "supermercado", date(2026, 4, 3)),
        (acct.id, "-260000", "Más x Menos", "supermercado", date(2026, 4, 10)),
        (acct.id, "-180000", "Automercado", "supermercado", date(2026, 4, 17)),
        (visa.id, "-110000", "Uber", "transporte", date(2026, 4, 5)),
        (visa.id, "-100000", "Uber", "transporte", date(2026, 4, 21)),
        (visa.id, "-140000", "Cinepolis", "entretenimiento", date(2026, 4, 12)),
        (acct.id, "-160000", "Soda Tapia", "comida_fuera", date(2026, 4, 25)),
        (acct.id, "-130000", "Farmacia Fischel", "salud", date(2026, 4, 8)),
        # New category in April
        (acct.id, "-150000", "Gimnasio", "salud_gym", date(2026, 4, 19)),
        # April income (smaller)
        (acct.id, "650000", "Empresa", "salario", date(2026, 4, 28)),
    ]
    for account_id, amount, merchant, category, txn_date in rows:
        session.add(
            Transaction(
                user_id=user_id,
                account_id=account_id,
                amount=Decimal(amount),
                currency="CRC",
                merchant=merchant,
                category=category,
                transaction_date=txn_date,
                created_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
                source="manual",
            )
        )
    # One simple debt so the composition test (case 4) has something for list_debts
    session.add(
        Debt(
            user_id=user_id,
            name="Préstamo personal",
            debt_type="personal_loan",
            original_amount=Decimal("2000000"),
            current_balance=Decimal("1500000"),
            interest_rate=Decimal("0.1200"),
            minimum_payment=Decimal("80000"),
            payment_due_day=10,
            term_months=36,
            payments_made=8,
            currency="CRC",
            is_active=True,
        )
    )
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
    return [t.get("args_summary", {}) for t in row.tools_used if t["name"] == name]


def _april_2026_end_candidates() -> set[str]:
    candidates = {"2026-04-30"}
    today = datetime.now(ZoneInfo("America/Costa_Rica")).date()
    if today.year == 2026 and today.month == 4:
        candidates.add(today.isoformat())
    return candidates


@pytest.mark.asyncio
async def test_phase_6a_block5b_real_llm_compares_periods(db_with_user):
    session, user_id = db_with_user
    await _seed_two_months(session, user_id)

    report: list[dict[str, Any]] = []

    async def run(prompt: str) -> tuple[str, LLMQueryDispatch]:
        await clear_history(user_id, redis=get_redis())
        text = await dispatcher.handle(user_id=user_id, message_text=prompt)
        row = await _latest_dispatch(session, user_id)
        report.append({
            "prompt": prompt,
            "iter": row.total_iterations,
            "in": row.total_input_tokens,
            "out": row.total_output_tokens,
            "ms": row.duration_ms,
            "tools": row.tools_used,
            "response": text,
        })
        return text, row

    # 1. Compare marzo vs abril, sin group_by. Both calendar-full and MTD
    # interpretations of "abril" are acceptable for case 1 — "abril" can
    # plausibly mean month-to-date when today is mid-April.
    response, row = await run("compará marzo con abril")
    assert "compare_periods" in _tool_names(row)
    args = _tool_args(row, "compare_periods")[0]
    assert args.get("period_a_start") == "2026-03-01"
    assert args.get("period_a_end") == "2026-03-31"
    assert args.get("period_b_start") == "2026-04-01"
    assert args.get("period_b_end") in _april_2026_end_candidates()
    _assert_tone(response)

    # 2. "Este mes vs el anterior". The convention is A=reference/older,
    # B=current, but accept either ordering as long as both months are covered.
    response, row = await run("cómo voy este mes vs el anterior")
    assert "compare_periods" in _tool_names(row)
    args = _tool_args(row, "compare_periods")[0]
    starts = {args.get("period_a_start"), args.get("period_b_start")}
    assert starts == {"2026-03-01", "2026-04-01"}, args
    ends_april = _april_2026_end_candidates()
    march_end = "2026-03-31"
    a_end = args.get("period_a_end")
    b_end = args.get("period_b_end")
    assert {a_end, b_end} & ends_april, args
    assert march_end in {a_end, b_end}, args
    _assert_tone(response)

    # 3. Crecimiento por categoría.
    response, row = await run("qué categorías crecieron de marzo a abril")
    assert "compare_periods" in _tool_names(row)
    args = _tool_args(row, "compare_periods")[0]
    assert args.get("group_by") == "category"
    lowered = response.lower()
    # The new category in April is salud_gym; the LLM should pick it up.
    assert "salud_gym" in lowered or "gym" in lowered or "nuev" in lowered
    _assert_tone(response)

    # 4. Composition check — should NOT use compare_periods.
    response, row = await run("cuánto gasté en marzo y cuánto debo en total")
    names = _tool_names(row)
    assert "compare_periods" not in names, (
        f"compare_periods used inappropriately: {names}"
    )
    informative = {"aggregate_transactions", "list_transactions"}
    assert "list_debts" in names, names
    assert set(names) & informative, names
    _assert_tone(response)

    # 5. Binary question — más o menos que el mes pasado.
    response, row = await run("¿gasté más o menos que el mes pasado?")
    assert "compare_periods" in _tool_names(row)
    lowered = response.lower()
    has_binary = (
        "más" in lowered
        or "menos" in lowered
        or "igual" in lowered
        or "similar" in lowered
    )
    assert has_binary, response
    _assert_tone(response)

    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
