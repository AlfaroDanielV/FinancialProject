"""Chat `/menu` + `/resumen` — deterministic command surfaces (no LLM).

These run through `process_message`'s command short-circuit, so they need no LLM
client. Covers the menu reply (chips + the `menu` open_screen marker), the
`/resumen` period picker, a populated expense table (monto/categoría/fecha/sobre
+ the sin-sobre emoji), and the empty-period copy.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from bot import messages_es
from bot.menu import _user_today
from bot.pipeline import process_message
from api.models.envelope import Envelope
from api.models.transaction import Transaction
from api.models.user import User
from api.redis_client import get_redis


async def _run(user, text, db):
    return await process_message(
        user=user,
        text=text,
        db=db,
        redis=get_redis(),
        llm_client=object(),  # never used: commands short-circuit before the LLM
        llm_model="x",
    )


@pytest.mark.asyncio
async def test_menu_lists_commands_and_marks_open_screen(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)

    reply = await _run(user, "/menu", session)

    assert reply.open_screen is not None
    assert reply.open_screen.screen == "menu"  # keeps the chips repeatable
    labels = [b.label for b in reply.buttons]
    assert "/resumen" in labels
    assert "¿Cuánto gasté esta semana?" in labels


@pytest.mark.asyncio
async def test_resumen_no_arg_offers_period_chips(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)

    reply = await _run(user, "/resumen", session)

    labels = [b.label for b in reply.buttons]
    assert labels == ["/resumen_mes", "/resumen_semana", "/resumen_hoy"]
    assert reply.open_screen.screen == "menu"


@pytest.mark.asyncio
async def test_resumen_week_table_lists_expenses_with_sobre(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    today = _user_today(user)

    sobre = Envelope(
        user_id=user_id,
        name="Comida",
        envelope_class="needs",
        limit_amount=Decimal("50000"),
    )
    session.add(sobre)
    await session.commit()
    await session.refresh(sobre)

    # One expense assigned to a sobre, one without (→ emoji in the Sobre column).
    session.add(
        Transaction(
            user_id=user_id, amount=Decimal("-5000"), currency="CRC",
            merchant="Súper", category="comida", transaction_date=today,
            envelope_id=sobre.id, status="confirmed", source="manual",
        )
    )
    session.add(
        Transaction(
            user_id=user_id, amount=Decimal("-3200"), currency="CRC",
            merchant="Bus", category="transporte", transaction_date=today,
            status="confirmed", source="manual",
        )
    )
    await session.commit()

    reply = await _run(user, "/resumen semana", session)
    text = reply.text

    assert "Resumen — esta semana" in text
    assert messages_es.RESUMEN_TABLE_HEADER in text
    assert "₡5.000" in text and "₡3.200" in text
    assert "comida" in text and "transporte" in text  # category cells
    assert "Comida" in text  # the envelope name (assigned row)
    assert messages_es.MENU_NO_ENVELOPE_EMOJI in text  # 📭 for the unassigned row
    assert "Total: ₡8.200" in text


@pytest.mark.asyncio
async def test_resumen_empty_period_copy(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)

    reply = await _run(user, "/resumen semana", session)

    assert reply.text == "Aún no tengo registros para esta semana."
