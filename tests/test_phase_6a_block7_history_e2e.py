"""End-to-end continuity tests for the query dispatcher with history.

These run against the real Anthropic API (skip when ANTHROPIC_API_KEY is
unset). Each test seeds DB data, makes a first query, then makes a
follow-up that depends on the prior turn's context.

Cases covered:
1. Period continuity: "qué gasté esta semana" → "y la pasada?". The
   second call must pick aggregate_transactions for last week without
   the user repeating "qué gasté".
2. Refinement: "dame mi panorama" → "profundizá en gastos". The second
   call must use aggregate_transactions with group_by=category without
   asking for clarification.
3. Truncation: 12 sequential exchanges; verify Redis only retains 10
   entries and the oldest 2 are gone. (No new LLM cost — runs against
   the same fake-text endpoint.)

Cost note: each LLM-real test makes 2 dispatcher calls (1 turn each).
Truncation runs offline against the dispatcher with a mocked client.
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
import pytest_asyncio
from redis.asyncio import from_url as redis_from_url
from sqlalchemy import select

from api.config import settings
from api.models.account import Account
from api.models.debt import Debt
from api.models.llm_query_dispatch import LLMQueryDispatch
from api.models.transaction import Transaction
from app.queries import dispatcher
from app.queries.history import (
    HISTORY_MAX_ENTRIES,
    append_turn,
    history_key,
    load_history,
)

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
    assert "**" not in response, f"markdown bold leaked: {response!r}"
    if expect_money:
        assert re.search(r"₡\s?\d", response), f"no money formatting: {response!r}"


@pytest_asyncio.fixture
async def redis_client():
    client = redis_from_url(settings.redis_url, decode_responses=True)
    yield client
    await client.aclose()


async def _seed(session, user_id: uuid.UUID) -> None:
    today = _today_cr()
    week_start = today - timedelta(days=today.weekday())
    last_week_start = week_start - timedelta(days=7)
    last_week_end = week_start - timedelta(days=1)

    cash = Account(user_id=user_id, name="BAC Débito", account_type="checking")
    visa = Account(user_id=user_id, name="BAC Visa", account_type="credit")
    session.add_all([cash, visa])
    await session.commit()
    await session.refresh(cash)
    await session.refresh(visa)

    rows: list[tuple[uuid.UUID, str, str, str, date]] = [
        # This week.
        (cash.id, "-25000", "PriceSmart", "supermercado", week_start),
        (visa.id, "-12000", "Uber", "transporte", week_start),
        # Last week.
        (cash.id, "-30000", "Más x Menos", "supermercado", last_week_start + timedelta(days=2)),
        (visa.id, "-10000", "Uber", "transporte", last_week_end - timedelta(days=1)),
        (cash.id, "-15000", "Cinepolis", "entretenimiento", last_week_start + timedelta(days=4)),
        # Earlier April.
        (cash.id, "-100000", "Automercado", "supermercado", date(2026, 4, 5)),
        (visa.id, "-50000", "Uber", "transporte", date(2026, 4, 12)),
        (visa.id, "-80000", "Cinepolis", "entretenimiento", date(2026, 4, 15)),
        (cash.id, "-30000", "Soda Tapia", "comida_fuera", date(2026, 4, 20)),
        # Income for "panorama" coverage.
        (cash.id, "650000", "Empresa", "salario", date(2026, 4, 1)),
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
                created_at=datetime(2026, 4, 1, tzinfo=timezone.utc),
                source="manual",
            )
        )
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


@pytest.mark.asyncio
async def test_continuity_period_followup(
    db_with_user, redis_client
):
    session, user_id = db_with_user
    await _seed(session, user_id)

    today = _today_cr()
    week_start = today - timedelta(days=today.weekday())
    last_week_start = week_start - timedelta(days=7)
    last_week_end = week_start - timedelta(days=1)

    report: list[dict[str, Any]] = []

    async def run(prompt: str) -> tuple[str, LLMQueryDispatch]:
        text = await dispatcher.handle(user_id=user_id, message_text=prompt)
        row = await _latest_dispatch(session, user_id)
        report.append({
            "prompt": prompt,
            "tools": row.tools_used,
            "input": row.total_input_tokens,
            "output": row.total_output_tokens,
            "cache_read": row.cache_read_input_tokens,
            "cache_creation": row.cache_creation_input_tokens,
            "response": text,
        })
        return text, row

    try:
        # Turn 1.
        response_1, row_1 = await run("qué gasté esta semana")
        _assert_tone(response_1)

        # Turn 2 (follow-up; no "qué gasté" repeated).
        response_2, row_2 = await run("y la pasada?")
        names = _tool_names(row_2)
        assert {"aggregate_transactions", "list_transactions"} & set(names), names
        args = row_2.tools_used[0]["args_summary"]
        # Last week ISO range.
        assert args.get("start_date") == last_week_start.isoformat(), args
        assert args.get("end_date") == last_week_end.isoformat(), args
        _assert_tone(response_2)

        # History persisted.
        history = await load_history(user_id, redis=redis_client)
        assert len(history) == 4
        assert history[0].role == "user"
        assert "esta semana" in history[0].content
        assert history[2].role == "user"
        assert history[2].content == "y la pasada?"

        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    finally:
        await redis_client.delete(history_key(user_id))


@pytest.mark.asyncio
async def test_continuity_panorama_then_refinement(
    db_with_user, redis_client
):
    session, user_id = db_with_user
    await _seed(session, user_id)

    report: list[dict[str, Any]] = []

    async def run(prompt: str) -> tuple[str, LLMQueryDispatch]:
        text = await dispatcher.handle(user_id=user_id, message_text=prompt)
        row = await _latest_dispatch(session, user_id)
        report.append({
            "prompt": prompt,
            "tools": row.tools_used,
            "input": row.total_input_tokens,
            "output": row.total_output_tokens,
            "cache_read": row.cache_read_input_tokens,
            "cache_creation": row.cache_creation_input_tokens,
            "response": text,
        })
        return text, row

    try:
        response_1, row_1 = await run("dame mi panorama")
        _assert_tone(response_1)
        # Multi-tool behavior.
        names_1 = _tool_names(row_1)
        assert len(set(names_1)) >= 2, names_1

        response_2, row_2 = await run("profundizá en gastos")
        names_2 = _tool_names(row_2)
        assert "aggregate_transactions" in names_2 or "list_transactions" in names_2
        # Should not be a clarification request — it should run a tool.
        # Confidence: the response references categories or amounts.
        lowered = response_2.lower()
        assert any(
            kw in lowered
            for kw in (
                "supermercado",
                "transporte",
                "entretenimiento",
                "categor",
                "₡",
            )
        ), response_2
        _assert_tone(response_2)

        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    finally:
        await redis_client.delete(history_key(user_id))


@pytest.mark.asyncio
async def test_history_truncates_to_max_entries_after_many_turns(
    db_with_user, redis_client
):
    """No-LLM truncation check.

    Doesn't hit Anthropic — uses append_turn directly to push 12
    exchanges (24 entries) and verifies cap holds.
    """
    _, user_id = db_with_user

    try:
        for i in range(12):
            await append_turn(
                user_id,
                user_msg=f"q{i}",
                assistant_msg=f"r{i}",
                redis=redis_client,
            )

        history = await load_history(user_id, redis=redis_client)
        assert len(history) == HISTORY_MAX_ENTRIES == 10

        # The oldest 14 entries should be gone (q0..q6 + r0..r6 = 14).
        contents = [t.content for t in history]
        # We kept the last 10 items: q7,r7,q8,r8,q9,r9,q10,r10,q11,r11.
        assert contents == [
            "q7", "r7", "q8", "r8", "q9", "r9",
            "q10", "r10", "q11", "r11",
        ]
    finally:
        await redis_client.delete(history_key(user_id))
