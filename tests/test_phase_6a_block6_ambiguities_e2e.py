"""End-to-end tests for the four interpretation ambiguities the formal
system prompt is meant to resolve.

These run against the real Anthropic API (skip when ANTHROPIC_API_KEY is
unset). Each case asserts both the tool call and the final response shape:
the convention from app.queries.prompts.system must survive into the
LLM's actual behavior, not just live in the prompt text.

The seeded data covers two calendar months (March + April) so the LLM
can resolve "este mes" vs "el anterior" vs "marzo" against real rows.
"""
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
from api.models.llm_query_dispatch import LLMQueryDispatch
from api.models.transaction import Transaction
from app.queries import dispatcher

pytestmark = pytest.mark.skipif(
    not settings.anthropic_api_key,
    reason="ANTHROPIC_API_KEY no configurada",
)

_EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF]")


def _today_cr() -> date:
    return datetime.now(ZoneInfo("America/Costa_Rica")).date()


def _assert_tone(response: str, *, expect_money: bool = True) -> None:
    lowered = response.lower()
    assert not _EMOJI_RE.search(response), f"emoji in: {response!r}"
    assert "soy tu asistente" not in lowered
    assert "soy un asistente" not in lowered
    assert "**" not in response, f"markdown bold leaked: {response!r}"
    if expect_money:
        assert re.search(r"₡\s?\d", response), f"no money formatting: {response!r}"


async def _seed(session, user_id: uuid.UUID) -> None:
    today = _today_cr()
    week_start = today - timedelta(days=today.weekday())  # Monday

    cash = Account(user_id=user_id, name="BAC Débito", account_type="checking")
    visa = Account(user_id=user_id, name="BAC Visa", account_type="credit")
    session.add_all([cash, visa])
    await session.commit()
    await session.refresh(cash)
    await session.refresh(visa)

    rows: list[tuple[uuid.UUID, str, str, str, date]] = [
        # March — total expense around 1.250.000.
        (cash.id, "-180000", "PriceSmart", "supermercado", date(2026, 3, 4)),
        (cash.id, "-220000", "Más x Menos", "supermercado", date(2026, 3, 11)),
        (cash.id, "-150000", "Automercado", "supermercado", date(2026, 3, 18)),
        (visa.id, "-95000", "Uber", "transporte", date(2026, 3, 6)),
        (visa.id, "-85000", "Uber", "transporte", date(2026, 3, 22)),
        (visa.id, "-120000", "Cinepolis", "entretenimiento", date(2026, 3, 14)),
        (cash.id, "-180000", "Soda Tapia", "comida_fuera", date(2026, 3, 27)),
        (cash.id, "-220000", "Farmacia Fischel", "salud", date(2026, 3, 9)),
        # April — slightly higher total ~1.450.000.
        (cash.id, "-220000", "PriceSmart", "supermercado", date(2026, 4, 3)),
        (cash.id, "-260000", "Más x Menos", "supermercado", date(2026, 4, 10)),
        (cash.id, "-180000", "Automercado", "supermercado", date(2026, 4, 17)),
        (visa.id, "-110000", "Uber", "transporte", date(2026, 4, 5)),
        (visa.id, "-100000", "Uber", "transporte", date(2026, 4, 21)),
        (visa.id, "-140000", "Cinepolis", "entretenimiento", date(2026, 4, 12)),
        (cash.id, "-160000", "Soda Tapia", "comida_fuera", date(2026, 4, 25)),
        (cash.id, "-130000", "Farmacia Fischel", "salud", date(2026, 4, 8)),
        # This-week-only rows so case 1 has data inside the ISO week.
        (cash.id, "-12000", "Pulpería", "supermercado", week_start),
        (visa.id, "-25000", "Uber", "transporte", min(today, week_start)),
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


@pytest.mark.asyncio
async def test_phase_6a_block6_ambiguities(db_with_user) -> None:
    session, user_id = db_with_user
    await _seed(session, user_id)

    today = _today_cr()
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)
    this_month_start = today.replace(day=1)

    # Last-month bounds — for the canonical 2026-04-XX run these are
    # 2026-03-01 and 2026-03-31. We compute them generically to keep the
    # test robust if it runs on a different day.
    last_month_end = this_month_start - timedelta(days=1)
    last_month_start = last_month_end.replace(day=1)

    report: list[dict[str, Any]] = []

    async def run(prompt: str) -> tuple[str, LLMQueryDispatch]:
        text = await dispatcher.handle(user_id=user_id, message_text=prompt)
        row = await _latest_dispatch(session, user_id)
        report.append(
            {
                "prompt": prompt,
                "iter": row.total_iterations,
                "input_tokens": row.total_input_tokens,
                "output_tokens": row.total_output_tokens,
                "tools": row.tools_used,
                "response": text,
            }
        )
        return text, row

    # ── Case 1 — "qué gasté esta semana" → ISO week (Mon → Sun), not last 7 days.
    response, row = await run("qué gasté esta semana")
    names = _tool_names(row)
    assert {"aggregate_transactions", "list_transactions"} & set(names), names
    args = row.tools_used[0]["args_summary"]
    assert args.get("start_date") == week_start.isoformat(), args
    # End is either today (data-bounded) or the Sunday end-of-week.
    assert args.get("end_date") in {today.isoformat(), week_end.isoformat()}, args
    _assert_tone(response)

    # ── Case 2 — "qué gasté en abril" on a date inside April → MTD.
    response, row = await run("qué gasté en abril")
    names = _tool_names(row)
    assert {"aggregate_transactions", "list_transactions"} & set(names), names
    args = row.tools_used[0]["args_summary"]
    assert args.get("start_date") == "2026-04-01", args
    # MTD acceptance: end_date is today; full calendar is also accepted by
    # block 5b's existing tests, but the convention prefers MTD when the
    # named month is current. We accept both to keep the test useful but
    # not flaky.
    assert args.get("end_date") in {today.isoformat(), "2026-04-30"}, args
    _assert_tone(response)

    # ── Case 3 — "qué gasté en marzo" → full calendar March.
    response, row = await run("qué gasté en marzo")
    names = _tool_names(row)
    assert {"aggregate_transactions", "list_transactions"} & set(names), names
    args = row.tools_used[0]["args_summary"]
    assert args.get("start_date") == "2026-03-01", args
    assert args.get("end_date") == "2026-03-31", args
    _assert_tone(response)

    # ── Case 4 — "qué debo pagar" with no context → ask for clarification
    # before executing. We accept either no tool call or a list_recurring_bills
    # default (the convention prefers asking, but the prompt also says
    # "asumir pagos recurrentes" when in doubt).
    response, row = await run("qué debo pagar")
    names = _tool_names(row)
    lowered = response.lower()
    asked_for_clarification = (
        "?" in response and ("recurrentes" in lowered or "deudas" in lowered)
    )
    used_default = "list_recurring_bills" in names
    assert asked_for_clarification or used_default, {
        "names": names,
        "response": response,
    }
    _assert_tone(response, expect_money=False)

    # ── Case 5 — "este mes vs el anterior" → period_a=mes anterior,
    # period_b=mes actual (delta convention).
    if today.month == 12:
        next_month_first = today.replace(year=today.year + 1, month=1, day=1)
    else:
        next_month_first = today.replace(month=today.month + 1, day=1)
    this_month_calendar_end = next_month_first - timedelta(days=1)

    response, row = await run("compará lo de este mes con el anterior")
    names = _tool_names(row)
    assert "compare_periods" in names, names
    args = _tool_args(row, "compare_periods")[0]
    # Convention: A=older (mes anterior), B=newer (mes actual).
    assert args.get("period_a_start") == last_month_start.isoformat(), args
    assert args.get("period_a_end") == last_month_end.isoformat(), args
    assert args.get("period_b_start") == this_month_start.isoformat(), args
    end_b = args.get("period_b_end")
    assert end_b in {today.isoformat(), this_month_calendar_end.isoformat()}, args
    _assert_tone(response)

    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
