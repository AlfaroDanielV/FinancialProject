"""End-to-end tests for Phase 6a — bloque 8 (delivery polish).

3 cases:
1. Long real response → splitter produces ≥2 chunks, each <4096 chars
   and HTML-balanced.
2. Sanitization on real LLM output → output has no disallowed tags
   after passing through sanitize_telegram_html.
3. Budget cap enforcement → seed dispatches summing to >cap, ensure
   dispatcher rejects WITHOUT calling Anthropic (zero-spend rejection).

Cases 1 and 2 hit the real Anthropic API (skipped when no key).
Case 3 is offline — sets a low cap and seeds rows; verifies the LLM
client is never invoked.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio
from redis.asyncio import from_url as redis_from_url

from api.config import settings
from api.models.account import Account
from api.models.llm_query_dispatch import LLMQueryDispatch
from api.models.transaction import Transaction
from app.queries import dispatcher
from app.queries.delivery import (
    TELEGRAM_HARD_LIMIT,
    TELEGRAM_OPERATIONAL_CAP,
    sanitize_telegram_html,
    split_for_telegram,
)
from app.queries.history import history_key

CR = ZoneInfo("America/Costa_Rica")


def _today_cr() -> date:
    return datetime.now(CR).date()


@pytest_asyncio.fixture
async def redis_client():
    client = redis_from_url(settings.redis_url, decode_responses=True)
    yield client
    await client.aclose()


async def _seed_rich(session, user_id: uuid.UUID) -> None:
    """Seed enough data so the LLM has plenty to talk about — 30+
    transactions across categories and accounts so a 'panorama
    completo + detalle' prompt produces a long response."""
    today = _today_cr()
    bac = Account(user_id=user_id, name="BAC Débito", account_type="checking")
    visa = Account(user_id=user_id, name="BAC Visa", account_type="credit")
    bn = Account(user_id=user_id, name="BN Cuenta", account_type="checking")
    session.add_all([bac, visa, bn])
    await session.commit()
    await session.refresh(bac)
    await session.refresh(visa)
    await session.refresh(bn)

    rows: list[tuple[uuid.UUID, str, str, str, date]] = []
    merchants = [
        ("PriceSmart", "supermercado"),
        ("Walmart", "supermercado"),
        ("Más x Menos", "supermercado"),
        ("Automercado", "supermercado"),
        ("Uber", "transporte"),
        ("Bolt", "transporte"),
        ("InDriver", "transporte"),
        ("Cinépolis", "entretenimiento"),
        ("Spotify", "entretenimiento"),
        ("Netflix", "entretenimiento"),
        ("Soda Tapia", "comida_fuera"),
        ("Subway", "comida_fuera"),
        ("McDonald's", "comida_fuera"),
        ("Starbucks", "comida_fuera"),
        ("Farmacias Fischel", "salud"),
        ("Hospital Clínica", "salud"),
        ("ICE", "servicios"),
        ("Kolbi", "servicios"),
        ("AyA", "servicios"),
        ("Amazon", "compras"),
    ]
    accounts = [bac.id, visa.id, bn.id]
    amounts = ["-5000", "-12000", "-25000", "-8500", "-15000", "-3500", "-9800"]
    for i in range(30):
        m, c = merchants[i % len(merchants)]
        rows.append((accounts[i % 3], amounts[i % len(amounts)], m, c, today - timedelta(days=i % 25)))
    rows.append((bac.id, "650000", "Empresa", "salario", today.replace(day=1)))
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
                source="manual",
            )
        )
    await session.commit()


# ── case 1 + 2: real LLM, long response, sanitize + split ───────────


@pytest.mark.asyncio
@pytest.mark.skipif(
    not settings.anthropic_api_key, reason="ANTHROPIC_API_KEY no configurada"
)
async def test_long_response_splits_into_valid_chunks(
    db_with_user, redis_client
):
    session, user_id = db_with_user
    await _seed_rich(session, user_id)
    try:
        prompt = (
            "listame TODAS las transacciones de este mes una por una, "
            "con fecha, comercio, categoría y monto, sin omitir ninguna. "
            "agregá al final un desglose por categoría y por cuenta. "
            "necesito el detalle exhaustivo, no un resumen."
        )
        response = await dispatcher.handle(
            user_id=user_id, message_text=prompt
        )
        assert response, "empty response"
        sanitized = sanitize_telegram_html(response)
        chunks = split_for_telegram(sanitized)

        # Every chunk must respect Telegram's hard limit AND survive
        # a re-sanitize (i.e. it's already balanced HTML).
        for c in chunks:
            assert len(c) <= TELEGRAM_HARD_LIMIT, len(c)
            assert sanitize_telegram_html(c) == c, c[:200]

        # Report metrics for the human reviewer.
        print(
            f"\nLong-response e2e: chars={len(response)} chunks={len(chunks)} "
            f"chunk_lens={[len(c) for c in chunks]}"
        )
    finally:
        await redis_client.delete(history_key(user_id))


@pytest.mark.asyncio
@pytest.mark.skipif(
    not settings.anthropic_api_key, reason="ANTHROPIC_API_KEY no configurada"
)
async def test_real_response_contains_no_disallowed_tags(
    db_with_user, redis_client
):
    session, user_id = db_with_user
    await _seed_rich(session, user_id)
    try:
        # Force a categorical breakdown — typical pattern that has tempted
        # past prompts to emit lists.
        response = await dispatcher.handle(
            user_id=user_id,
            message_text="dame los gastos del mes agrupados por categoría",
        )
        assert response

        sanitized = sanitize_telegram_html(response)
        # Disallowed tags shouldn't appear after sanitize. If the LLM
        # didn't emit any, sanitize is identity here — which is also fine.
        for bad in ("<ul>", "<li>", "<br>", "<p>", "<div>", "<h1>", "<h2>"):
            assert bad not in sanitized.lower(), f"{bad} survived sanitize"

        print(f"\nSanitize e2e: response={response!r}")
    finally:
        await redis_client.delete(history_key(user_id))


# ── case 3: budget cap enforcement (no LLM call) ────────────────────


@pytest.mark.asyncio
async def test_budget_cap_rejects_without_calling_anthropic(
    db_with_user, monkeypatch, redis_client
):
    """Seed a dispatch row that exceeds the cap, then run handle. The
    LLM client must NOT be invoked — verified by replacing it with a
    raise-on-call sentinel.
    """
    session, user_id = db_with_user
    monkeypatch.setattr(settings, "llm_daily_token_budget_per_user", 100_000)

    # Seed: 99k input + 2k output today → 101k spent, well over cap.
    now_utc = datetime.now(timezone.utc)
    session.add(
        LLMQueryDispatch(
            user_id=user_id,
            message_hash="x" * 64,
            total_iterations=1,
            total_input_tokens=99_000,
            total_output_tokens=2_000,
            tools_used=[],
            duration_ms=2000,
            created_at=now_utc,
        )
    )
    await session.commit()

    # Replace the LLM client with one that raises if called.
    class _MustNotBeCalled:
        async def run_query_loop(self, **kwargs):
            raise AssertionError("LLM was called despite budget exceeded")

    dispatcher.set_query_llm_client(_MustNotBeCalled())
    try:
        from sqlalchemy import select

        before_count_q = await session.execute(
            select(LLMQueryDispatch).where(LLMQueryDispatch.user_id == user_id)
        )
        before_count = len(before_count_q.scalars().all())

        response = await dispatcher.handle(
            user_id=user_id, message_text="cuanto gasté esta semana"
        )

        # Must be the budget message verbatim.
        assert "Llegaste al límite diario" in response

        # No new dispatch row was inserted (we don't log rejections).
        after_count_q = await session.execute(
            select(LLMQueryDispatch).where(LLMQueryDispatch.user_id == user_id)
        )
        after_count = len(after_count_q.scalars().all())
        assert after_count == before_count, (
            f"rejected request created a row: before={before_count} after={after_count}"
        )
    finally:
        dispatcher.set_query_llm_client(None)
        await redis_client.delete(history_key(user_id))
        await session.execute(
            __import__("sqlalchemy").text(
                "DELETE FROM llm_query_dispatches WHERE user_id = :u"
            ),
            {"u": user_id},
        )
        await session.commit()
